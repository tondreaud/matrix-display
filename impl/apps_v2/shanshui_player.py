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
rendered at 256px and downscaled to the 64px matrix. Strips are
hand-colored like a woodblock print (see _colorize): dark ink ridge
lines, washes of color in the mountain slopes, and a sky-to-water
gradient in the open paper, in rotating Hasui-inspired schemes.
"""

import os
import re
import shutil
import subprocess
import threading
import time
from collections import deque

from PIL import Image, ImageFilter

CANVAS = 64
IMPL_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
VENDOR_DIR = os.path.join(IMPL_DIR, 'vendor', 'shanshui')
CACHE_DIR = os.path.join(IMPL_DIR, '.shanshui_cache')

VIEW_WORLD = 700        # square window into the landscape (world units)
STRIP_WORLD = 2048      # world width of one pre-rendered strip
RENDER_H = 256          # strips are rendered this tall, then downscaled
QUEUE_TARGET = 3        # strips to keep ready (including the current one)
GENERATION_TIMEOUT = 600
# Hand-colored print schemes, by landscape element: a sky-to-water
# gradient fills the open paper, a wash of the mountain color fills
# the slopes (the space enclosed by ridge lines), and the ridge lines
# / ink work stay dark. Chosen from Kawase Hasui's print palettes and
# viewer favorites; strips rotate through them (config: palette).
# Each: (sky, water, mountain, ink)
SCHEMES = {
    'classic': ((243, 235, 216), (30, 102, 112), (84, 114, 148),
                (22, 26, 38)),    # Classic Hasui
    'sunrise': ((255, 216, 172), (216, 112, 92), (112, 88, 142),
                (50, 32, 54)),    # Sunrise
    'autumn': ((247, 227, 189), (54, 86, 78), (174, 98, 58),
               (44, 30, 24)),     # Autumn Dusk
}
ROTATION = ['classic', 'sunrise', 'autumn']


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


def _display_cpu():
    """Highest usable CPU, for the render loop (see _generator_cpus)."""
    try:
        usable = sorted(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        return None
    return usable[-1] if len(usable) >= 3 else None


def _open_mask(gray):
    """Mask (255 = open) of paper reachable from the image borders.

    Ridge lines and washes (gray < 190, slightly dilated to seal
    gaps) are barriers. Paper the flood can reach is sky/water;
    paper it cannot reach is enclosed by ridge lines -- mountain
    slopes. Pure Python BFS: ~200k pixels per strip, run once in
    the background at generation time.
    """
    w, h = gray.size
    barrier = gray.point(lambda v: 255 if v < 190 else 0)
    barrier = barrier.filter(ImageFilter.MaxFilter(5)).tobytes()
    open_bytes = bytearray(w * h)
    queue = deque()

    def seed(x, y):
        i = y * w + x
        if not barrier[i] and not open_bytes[i]:
            open_bytes[i] = 255
            queue.append(i)

    for x in range(w):
        seed(x, 0)
        seed(x, h - 1)
    for y in range(h):
        seed(0, y)
        seed(w - 1, y)
    while queue:
        i = queue.popleft()
        x = i % w
        for j in (i - w if i >= w else -1,
                  i + w if i < w * (h - 1) else -1,
                  i - 1 if x > 0 else -1,
                  i + 1 if x < w - 1 else -1):
            if j >= 0 and not barrier[j] and not open_bytes[j]:
                open_bytes[j] = 255
                queue.append(j)
    return Image.frombytes('L', (w, h), bytes(open_bytes))


def _colorize(img, scheme):
    """Hand-color an ink-on-paper strip by landscape element.

    - open paper: vertical bokashi gradient, sky at top to water
      at the base (smoothstep between 30% and 85% of the height)
    - mountain slopes (enclosed paper + light wash): pale tint of
      the mountain color, like ink wash over paper
    - heavier wash: the mountain color, deepening with wash strength
    - ridge lines and ink work (< 175): dark ink, un-inverted
    """
    sky, water, mountain, ink = scheme
    gray = img.convert('L')
    w, h = gray.size
    open_m = _open_mask(gray)

    # Sky-to-water gradient for the open field.
    rows = []
    for y in range(h):
        t = min(1.0, max(0.0, (y / h - 0.30) / 0.55))
        t = t * t * (3 - 2 * t)
        rows.append(tuple(round(sky[c] + (water[c] - sky[c]) * t)
                          for c in range(3)))
    out = Image.new('RGB', (w, h))
    out.putdata([rows[y] for y in range(h) for _ in range(w)])

    tint = tuple(round(mountain[c] * 0.45 + sky[c] * 0.55)
                 for c in range(3))
    enclosed = gray.point(lambda v: 255 if v >= 175 else 0)
    enclosed.paste(0, mask=open_m)  # slopes = not open
    out.paste(Image.new('RGB', (w, h), tint), mask=enclosed)

    def shade_lut(channel):
        lut = []
        for v in range(256):
            t = min(1.0, max(0.0, (208 - v) / 33.0))
            base = mountain[channel] * (1 - 0.35 * t) + \
                mountain[channel] * 0.3 * t
            lut.append(round(base))
        return lut

    wash = gray.point(lambda v: 255 if 175 <= v < 208 else 0)
    wash.paste(0, mask=open_m)
    shaded = Image.merge('RGB', [gray.point(shade_lut(c))
                                 for c in range(3)])
    out.paste(shaded, mask=wash)

    ink_m = gray.point(lambda v: 255 if v < 175 else 0)
    out.paste(Image.new('RGB', (w, h), ink), mask=ink_m)
    return out


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
        palette = str(cfg('palette', 'rotate')).strip().lower()
        self.scheme_names = [palette] if palette in SCHEMES else ROTATION
        self._scheme_idx = 0

        self.black = Image.new("RGB", (CANVAS, CANVAS), (0, 0, 0))
        self.current_img = self.black
        self.strips = []            # [{'x': world_x, 'img': PIL 749x256}]
        self.current = None
        self.pan = 0.0              # pixels into the current strip (fractional)
        self._last_advance = 0.0  # last pan update (wall clock)
        self._gen_cpus = _generator_cpus()
        self._lock = threading.Lock()

        if self.qjs is None or self.resvg is None:
            print("[Shanshui] qjs/resvg not found in impl/vendor/shanshui/. "
                  "Mode will stay black.", flush=True)
        else:
            # This runs on the controller's main (render) thread; keep
            # it on the display core, generation on the others.
            display_cpu = _display_cpu()
            if display_cpu is not None:
                _set_affinity({display_cpu})
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
                strip = self._make_strip(proc, next_x)
                if strip is not None:
                    with self._lock:
                        self.strips.append(strip)
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
        """Generate one strip and color it for its place in the cycle.

        Strips alternate HOLD and BLEND: a hold strip is colored in a
        single scheme; a blend strip is colored in two schemes (the
        current and the next) and the render loop crossfades between
        them as it pans, so palettes melt into each other instead of
        cutting. One strip is ~3 minutes of scrolling at defaults.
        """
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
                gray = img.convert('L').copy()

            seq = self._scheme_idx
            self._scheme_idx += 1
            names = self.scheme_names
            if len(names) == 1:
                return {'x': world_x, 'label': names[0],
                        'img': _colorize(gray, SCHEMES[names[0]])}
            current = names[(seq // 2) % len(names)]
            if seq % 2 == 0:
                print(f"[Shanshui] strip {world_x}: holding "
                      f"'{current}'", flush=True)
                return {'x': world_x, 'label': current,
                        'img': _colorize(gray, SCHEMES[current])}
            nxt = names[((seq // 2) + 1) % len(names)]
            print(f"[Shanshui] strip {world_x}: blending "
                  f"'{current}' -> '{nxt}'", flush=True)
            return {'x': world_x, 'label': f'{current}->{nxt}',
                    'img': _colorize(gray, SCHEMES[current]),
                    'img2': _colorize(gray, SCHEMES[nxt])}
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
            self.pan = 0.0
            self._last_advance = 0.0  # last pan update (wall clock)

        strip = self.current
        max_pan = strip['img'].size[0] - RENDER_H

        # Continuous sub-pixel pan: the view window slides a fraction
        # of a source pixel per display tick and the downscale samples
        # that fractional window, instead of jumping a whole pixel
        # every scroll_seconds (which read as jerkiness).
        now = time.time()
        if self._last_advance == 0.0:
            self._last_advance = now
        elapsed = now - self._last_advance
        self._last_advance = now
        if elapsed > 0:
            self.pan = min(max_pan, self.pan + elapsed / self.scroll_seconds)

        if self.pan >= max_pan:
            with self._lock:
                nxt = self.strips.pop(0) if self.strips else None
            if nxt is not None:
                self.current = nxt
                self.pan = 0.0
                return self.generate()

        box = (self.pan, 0, self.pan + RENDER_H, RENDER_H)
        self.current_img = strip['img'].resize(
            (CANVAS, CANVAS), Image.LANCZOS, box=box)
        if strip.get('img2') is not None and max_pan > 0:
            # Blend strip: crossfade to the next scheme across the pan.
            other = strip['img2'].resize(
                (CANVAS, CANVAS), Image.LANCZOS, box=box)
            self.current_img = Image.blend(
                self.current_img, other, self.pan / max_pan)
        return (self.current_img, True)
