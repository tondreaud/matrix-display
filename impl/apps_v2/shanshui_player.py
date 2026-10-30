"""Shan Shui mode: an endlessly scrolling Chinese landscape.

Uses Lingdong Huang's shan-shui-inf generator (MIT; see
impl/vendor/shanshui/LICENSE and shanshui_core.js -- his procedural
landscape code, extracted from the browser page and run headlessly).

Architecture mirrors Primitive mode: a background worker runs the
generator (QuickJS) and an SVG rasterizer (resvg) to pre-render wide
landscape strips, while the render loop simply pans across them.
Generation happens on the non-display cores at the lowest priority;
the render loop never blocks on it.

The view is a square 700x700-unit window of his 3000x800 world,
rendered at 256px and downscaled to the 64px matrix, preserving the
original soft ink-on-white look.
"""

import os
import re
import shutil
import subprocess
import threading
import time

from PIL import Image

CANVAS = 64
IMPL_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
VENDOR_DIR = os.path.join(IMPL_DIR, 'vendor', 'shanshui')
CACHE_DIR = os.path.join(IMPL_DIR, '.shanshui_cache')

VIEW_WORLD = 700        # square window into the landscape (world units)
STRIP_WORLD = 2048      # world width of one pre-rendered strip
RENDER_H = 256          # strips are rendered this tall, then downscaled
QUEUE_TARGET = 3        # strips to keep ready (including the current one)
GENERATION_TIMEOUT = 600


def _find(name):
    env = os.environ.get(f'SHANSHUI_{name.upper()}')
    if env and os.path.isfile(env):
        return env
    bundled = os.path.join(VENDOR_DIR, name)
    return bundled if os.path.isfile(bundled) else shutil.which(name)


def _set_affinity(cpus):
    """Pin the calling thread to the given CPUs (Linux only, best effort)."""
    try:
        if cpus and hasattr(os, 'sched_setaffinity'):
            os.sched_setaffinity(0, set(cpus))
            return True
    except OSError:
        pass
    return False


def _generator_cpus():
    """All usable CPUs except the highest (reserved for the render loop
    by Primitive mode's CPU plan; the LED refresh thread owns the
    isolated core beyond those). Empty set = don't bother."""
    try:
        usable = sorted(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        return set()
    return set(usable[:-1]) if len(usable) >= 3 else set()


def _nice_prefix(cpus):
    cmd = []
    if cpus and shutil.which('taskset'):
        cmd += ['taskset', '-c', ','.join(str(c) for c in sorted(cpus))]
    return cmd + ['nice', '-n', '19']


class ShanshuiScreen:
    def __init__(self, config):
        self.canvas_width = CANVAS
        self.canvas_height = CANVAS
        self.qjs = _find('qjs')
        self.resvg = _find('resvg')

        def cfg(name, fallback):
            try:
                if config is not None and 'Shanshui' in config:
                    return config.get('Shanshui', name, fallback=fallback)
            except Exception:
                pass
            return fallback

        seed = re.sub(r'[^A-Za-z0-9-]', '', str(cfg('seed', '')).strip())[:64]
        self.seed = seed or f"matrix-{int(time.time())}"
        try:
            self.scroll_seconds = max(0.05, min(5.0, float(cfg('scroll_seconds', '0.35'))))
        except (TypeError, ValueError):
            self.scroll_seconds = 0.35

        self.black = Image.new("RGB", (CANVAS, CANVAS), (0, 0, 0))
        self.current_img = self.black
        self.strips = []            # [{'x': world_x, 'img': PIL 749x256}]
        self.current = None
        self.pan = 0                # pixels into the current strip
        self._last_advance = 0.0
        self._frame_key = None      # (id(strip), pan) of current_img's source
        self._gen_cpus = _generator_cpus()
        self._lock = threading.Lock()

        if self.qjs is None or self.resvg is None:
            print("[Shanshui] qjs/resvg not found in impl/vendor/shanshui/. "
                  "Mode will stay black.", flush=True)
        else:
            os.makedirs(CACHE_DIR, exist_ok=True)
            thread = threading.Thread(target=self._worker, daemon=True)
            thread.start()
            print(f"[Shanshui] Mode started (seed '{self.seed}'); first "
                  "landscape strip is being generated.", flush=True)

    # ----- generation (background thread) -----

    def _strip_px_width(self):
        return round(STRIP_WORLD * RENDER_H / VIEW_WORLD)

    def _stride_world(self):
        """World distance between strip starts, so consecutive strips
        overlap by exactly one square view and panning is seamless."""
        return int((self._strip_px_width() - RENDER_H) * VIEW_WORLD / RENDER_H)

    def _worker(self):
        _set_affinity(self._gen_cpus)  # keep generation off the display core
        proc = None
        next_x = 0
        while True:
            with self._lock:
                queued = len(self.strips)
            if queued >= QUEUE_TARGET:
                time.sleep(2)
                continue
            try:
                if proc is None or proc.poll() is not None:
                    proc = self._start_runner()
                    next_x = 0  # a restarted runner begins a fresh world
                img = self._make_strip(proc, next_x)
                if img is not None:
                    with self._lock:
                        self.strips.append({'x': next_x, 'img': img})
                    next_x += self._stride_world()
            except Exception as e:
                print(f"[Shanshui] generation failed: {e}", flush=True)
                if proc is not None:
                    try:
                        proc.kill()
                    except OSError:
                        pass
                    proc = None
                time.sleep(10)

    def _start_runner(self):
        cmd = _nice_prefix(self._gen_cpus) + [
            self.qjs, '--std',
            os.path.join(VENDOR_DIR, 'runner.js'),
            os.path.join(VENDOR_DIR, 'shanshui_core.js'),
            self.seed,
        ]
        return subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, text=True, bufsize=1)

    def _make_strip(self, proc, world_x):
        svg_path = os.path.join(CACHE_DIR, f'strip_{world_x}.svg')
        png_path = os.path.join(CACHE_DIR, f'strip_{world_x}.png')
        try:
            proc.stdin.write(f"STRIP {world_x} {STRIP_WORLD} {svg_path}\n")
            proc.stdin.flush()
            # Skip any stray non-protocol lines on the channel.
            response = proc.stdout.readline()
            while response and not response.startswith(('OK', 'ERR')):
                response = proc.stdout.readline()
            if not response.startswith('OK'):
                raise RuntimeError(f"generator said: {response.strip() or 'nothing'}")
            cmd = _nice_prefix(self._gen_cpus) + [
                self.resvg, '--width', str(self._strip_px_width()),
                '--height', str(RENDER_H), svg_path, png_path]
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    timeout=GENERATION_TIMEOUT)
            if result.returncode != 0:
                raise RuntimeError(f"resvg failed: {result.stderr[-300:]}")
            with Image.open(png_path) as img:
                return img.convert('RGB')
        finally:
            for p in (svg_path, png_path):
                try:
                    os.remove(p)
                except OSError:
                    pass

    # ----- playback (render loop) -----

    def generate(self):
        """Return (frame, is_active): a square view panning slowly
        across the current strip; holds at the strip's end if the
        next strip isn't ready yet."""
        if self.qjs is None or self.resvg is None:
            return (self.black, False)

        if self.current is None:
            with self._lock:
                self.current = self.strips.pop(0) if self.strips else None
            if self.current is None:
                return (self.current_img, self.current_img is not self.black)
            self.pan = 0
            self._last_advance = 0.0
            self._frame_key = None

        strip = self.current
        max_pan = strip['img'].size[0] - RENDER_H

        now = time.time()
        if self._last_advance == 0.0:
            self._last_advance = now
        steps = int((now - self._last_advance) / self.scroll_seconds)
        if steps:
            self._last_advance += steps * self.scroll_seconds
            self.pan = min(max_pan, self.pan + steps)

        if self.pan >= max_pan:
            with self._lock:
                nxt = self.strips.pop(0) if self.strips else None
            if nxt is not None:
                self.current = nxt
                self.pan = 0
                self._last_advance = 0.0
                self._frame_key = None
                return self.generate()

        key = (id(strip), self.pan)
        if key != self._frame_key:
            frame = strip['img'].crop(
                (self.pan, 0, self.pan + RENDER_H, RENDER_H))
            self.current_img = frame.resize((CANVAS, CANVAS), Image.LANCZOS)
            self._frame_key = key
        return (self.current_img, True)
