"""Primitive mode: photos that draw themselves out of geometric shapes.

Inspired by fogleman/primitive (MIT), and by the Primitive Twitter bot's
recipe. A background worker fetches a random photo or artwork (by default
a mix of Wikimedia Commons Picture of the Day, Art Institute of Chicago,
and Met Open Access -- none need an API key; Flickr interestingness and
Picsum also supported), then runs the `primitive` binary to approximate
it with ~200 shapes at 128px working resolution.

The binary writes every shape to disk as it works, so playback streams:
the display starts showing the build-up within seconds and follows a few
shapes behind the generator, resting on the latest shape whenever it
catches up. The render loop never blocks on network or generation.

The matrix process and this module share state only through the cache
directory; generation runs at low priority (nice + limited workers).
"""

import datetime
import glob
import hashlib
import json
import os
import random
import shutil
import subprocess
import threading
import time

import requests
from PIL import Image

CANVAS = 64
IMPL_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
CACHE_DIR = os.path.join(IMPL_DIR, '.primitive_cache')
# Which sequence is on screen, for the webapp's source-image viewer.
CURRENT_STATE_PATH = os.path.join(IMPL_DIR, '.primitive_current')
MODE_NAMES = {
    0: 'combo', 1: 'triangles', 2: 'rectangles', 3: 'ellipses', 4: 'circles',
    5: 'rotated rectangles', 6: 'beziers', 7: 'rotated ellipses', 8: 'polygons',
}
# Mode pool randomized per photo (subset of the Primitive bot's choices).
# Beziers (6) excluded: their thin strokes vanish at 64x64.
BOT_MODES = [1, 5, 7]
UA_HEADERS = {'User-Agent': 'MatrixDisplay/1.0 (personal LED display; primitive mode)'}
HTTP_TIMEOUT = 20          # seconds, per photo-source request
GENERATION_TIMEOUT = 1200  # seconds, per picture
QUEUE_TARGET = 2           # upcoming pictures to keep ready
CACHE_KEEP = 6             # finished sequences kept on disk
SHAPE_COUNTS = range(200, 231, 10)  # ~200 shapes per picture
ALPHA = 128                # shape opacity passed to the binary
SOURCES = ('mix', 'wikimedia', 'museum', 'met', 'flickr', 'picsum')
# Subject terms that favor paintings over object photography.
ART_SUBJECTS = ['landscape', 'portrait', 'still life', 'seascape',
                'cityscape', 'mountains', 'river', 'garden', 'flowers',
                'ocean', 'forest']


def _find_binary():
    env = os.environ.get('PRIMITIVE_BIN')
    if env and os.path.isfile(env):
        return env
    bundled = os.path.join(IMPL_DIR, 'vendor', 'primitive')
    if os.path.isfile(bundled):
        return bundled
    return shutil.which('primitive')


def _set_affinity(cpus):
    """Pin the calling thread to the given CPUs (Linux only, best effort)."""
    try:
        if cpus and hasattr(os, 'sched_setaffinity'):
            os.sched_setaffinity(0, set(cpus))
            return True
    except OSError:
        pass
    return False


def _cpu_plan():
    """Split usable CPUs into a display core and generator cores.

    The LED refresh thread already owns the isolated last core
    (isolcpus); this reserves the highest *usable* core for the render
    loop and leaves the rest to generation, so the two stop competing.
    Returns (display_cpu, generator_cpus) or (None, set()) when there
    aren't enough cores to bother.
    """
    try:
        usable = sorted(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        return None, set()
    if len(usable) < 3:
        return None, set()
    return usable[-1], set(usable[:-1])


class PrimitiveScreen:
    def __init__(self, config):
        self.canvas_width = CANVAS
        self.canvas_height = CANVAS
        self.binary = _find_binary()

        def cfg_num(name, fallback, cast, lo, hi):
            """Config value cast to a number and clamped; fallback on typos."""
            try:
                raw = config.get('Primitive', name, fallback=fallback) \
                    if config is not None else fallback
                return max(lo, min(hi, cast(raw)))
            except Exception:
                return cast(fallback)

        self.workers = cfg_num('workers', '2', int, 1, 4)
        self.work_res = cfg_num('resize', '128', int, 32, 512)
        self.hold_seconds = cfg_num('hold_seconds', '25', float, 0, 3600)
        self.shape_seconds = cfg_num('shape_seconds', '0.5', float, 0.01, 60)
        try:
            self.source = str(config.get('Primitive', 'source', fallback='mix')
                              if config is not None else 'mix').strip().lower()
            self.flickr_api_key = str(config.get(
                'Primitive', 'flickr_api_key', fallback='')
                if config is not None else '').strip()
        except Exception:
            self.source, self.flickr_api_key = 'mix', ''
        if self.source not in SOURCES:
            print(f"[Primitive] Unknown source '{self.source}'; using mix. "
                  f"Valid: {', '.join(SOURCES)}", flush=True)
            self.source = 'mix'

        self.black = Image.new("RGB", (CANVAS, CANVAS), (0, 0, 0))
        self.sequences = []       # seq dicts: {dir, meta, done, total}
        self.current = None
        self.frame_idx = 0        # next frame index to show
        self.last_advance = 0.0
        self.hold_until = 0.0
        self.current_img = self.black
        self._has_shown = False   # a real picture has reached the matrix
        self._frontier_seq = None  # seq the cached frontier count belongs to
        self._frontier_count = 0
        self._frontier_checked_at = 0.0
        self._seen_photo_ids = set()
        self._warned_no_key = False
        self._lock = threading.Lock()

        # Core separation (see _cpu_plan): this runs on the controller's
        # main thread, which becomes the render loop -- pin it to the
        # display core. The worker thread + generator binary get the rest.
        self.display_cpu, self.generator_cpus = _cpu_plan()
        if self.display_cpu is not None:
            _set_affinity({self.display_cpu})

        if self.binary is None:
            print("[Primitive] primitive binary not found "
                  "(looked in impl/vendor/, $PRIMITIVE_BIN, PATH). Mode will stay black.",
                  flush=True)
        else:
            os.makedirs(CACHE_DIR, exist_ok=True)
            self._adopt_existing_cache()
            thread = threading.Thread(target=self._worker, daemon=True)
            thread.start()
            if self.display_cpu is not None:
                print(f"[Primitive] CPU plan: display on core {self.display_cpu}, "
                      f"generation on cores {sorted(self.generator_cpus)}.",
                      flush=True)
            print("[Primitive] Mode started; first picture will appear in a few "
                  "seconds and build up live.", flush=True)

    # ----- sequence bookkeeping -----

    @staticmethod
    def _frames_in(seq_dir):
        return sorted(glob.glob(os.path.join(seq_dir, 'frames', '*.png')))

    def _load_sequence(self, seq_dir):
        frames = self._frames_in(seq_dir)
        if not frames:
            return None
        meta = {}
        try:
            with open(os.path.join(seq_dir, 'meta.json')) as f:
                meta = json.load(f)
        except Exception:
            pass
        return {'dir': seq_dir, 'meta': meta, 'done': True,
                'total': len(frames)}

    def _adopt_existing_cache(self):
        """Reuse finished sequences from earlier runs (streaming ones lack meta)."""
        for meta_path in sorted(glob.glob(os.path.join(CACHE_DIR, '*', 'meta.json'))):
            seq = self._load_sequence(os.path.dirname(meta_path))
            if seq:
                with self._lock:
                    self.sequences.append(seq)
                if seq['meta'].get('photo_id'):
                    # Don't immediately re-draw what we showed last run.
                    self._seen_photo_ids.add(seq['meta']['photo_id'])

    def _available_frames(self, seq):
        """Frames safe to show. While generating, stay 1 frame behind the
        frontier so we never read a PNG that is still being written.
        The count is cached briefly: the render loop asks ~12x/second,
        and globbing the frames directory each time competes with the
        generator for CPU and SD-card I/O."""
        if seq['done']:
            return seq['total'] or 0
        now = time.time()
        if self._frontier_seq is not seq or now - self._frontier_checked_at >= 0.25:
            self._frontier_seq = seq
            self._frontier_checked_at = now
            self._frontier_count = max(0, len(self._frames_in(seq['dir'])) - 1)
        return self._frontier_count

    # ----- generation (background thread) -----

    def _worker(self):
        # Fetching/downloading/cropping happens here; keep it (and the
        # generator child it spawns) off the display core.
        _set_affinity(self.generator_cpus)
        while True:
            with self._lock:
                queued = len(self.sequences)
            if queued >= QUEUE_TARGET:
                time.sleep(5)
                continue
            try:
                self._generate_sequence()
            except Exception as e:
                print(f"[Primitive] generation failed: {e}", flush=True)
                time.sleep(15)

    def _generate_sequence(self):
        photo_id, photo_path, source_url = self._fetch_photo()
        mode = random.choice(BOT_MODES)
        shapes = random.choice(SHAPE_COUNTS)
        name = f"{str(photo_id).replace(':', '_')}_m{mode}_n{shapes}_{int(time.time())}"
        seq_dir = os.path.join(CACHE_DIR, name)
        frames_dir = os.path.join(seq_dir, 'frames')
        os.makedirs(frames_dir, exist_ok=True)
        # Keep the (square-cropped) source next to its frames so the
        # webapp can show what the picture is being built from.
        source_path = os.path.join(seq_dir, 'source.jpg')
        shutil.move(photo_path, source_path)
        photo_path = source_path

        meta = {'photo_id': photo_id, 'source_url': source_url,
                'mode': mode, 'mode_name': MODE_NAMES[mode], 'shapes': shapes}
        seq = {'dir': seq_dir, 'meta': meta, 'done': False, 'total': None}
        with self._lock:
            self.sequences.append(seq)  # visible to playback immediately

        # Nicest possible priority, a cap on Go runtime threads, and
        # confinement to the generator cores: building the next picture
        # must never starve the display loop (that contention shows up
        # as flicker on the matrix).
        cmd = []
        if self.generator_cpus and shutil.which('taskset'):
            cmd += ['taskset', '-c',
                    ','.join(str(c) for c in sorted(self.generator_cpus))]
        cmd += ['nice', '-n', '19', self.binary,
               '-i', photo_path,
               '-o', os.path.join(frames_dir, '%03d.png'),
               '-n', str(shapes), '-m', str(mode),
               '-r', str(self.work_res), '-s', str(CANVAS),
               '-a', str(ALPHA), '-j', str(self.workers)]
        print(f"[Primitive] Generating {shapes} {MODE_NAMES[mode]} from "
              f"{source_url} ...", flush=True)
        start = time.time()
        try:
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    timeout=GENERATION_TIMEOUT,
                                    env={**os.environ,
                                         'GOMAXPROCS': str(self.workers)})
            if result.returncode != 0:
                print(f"[Primitive] binary failed: {result.stderr[-300:]}", flush=True)
        except Exception as e:
            print(f"[Primitive] binary failed: {e}", flush=True)

        frames = self._frames_in(seq_dir)
        seq['done'] = True
        seq['total'] = len(frames)
        if frames:
            meta['generated_seconds'] = round(time.time() - start, 1)
            with open(os.path.join(seq_dir, 'meta.json'), 'w') as f:
                json.dump(meta, f)
            print(f"[Primitive] Finished {shapes} {MODE_NAMES[mode]} in "
                  f"{meta['generated_seconds']}s ({source_url})", flush=True)
            self._prune_cache()
        else:
            with self._lock:
                if seq in self.sequences:
                    self.sequences.remove(seq)
            shutil.rmtree(seq_dir, ignore_errors=True)

    def _prune_cache(self, keep=CACHE_KEEP):
        dirs = [p for p in glob.glob(os.path.join(CACHE_DIR, '*')) if os.path.isdir(p)]
        dirs.sort(key=lambda p: os.path.getmtime(p))
        with self._lock:
            protected = {s['dir'] for s in self.sequences}
        if self.current:
            protected.add(self.current['dir'])
        for old in dirs[:-keep]:
            if old not in protected:
                shutil.rmtree(old, ignore_errors=True)
        # Downloads are deleted right after generation; sweep any orphans
        # (e.g. left behind by a crash). Only the worker thread downloads,
        # and it is the only caller of this method, so none are in flight.
        for orphan in glob.glob(os.path.join(CACHE_DIR, '_dl_*')):
            try:
                os.remove(orphan)
            except OSError:
                pass

    # ----- photo sources -----

    def _attempt(self, label, fetcher):
        """Run one source fetcher; None (with a log line) on failure."""
        try:
            return fetcher()
        except Exception as e:
            print(f"[Primitive] {label} failed ({e})", flush=True)
            return None

    def _fetch_photo(self):
        """Get the next photo/artwork from the configured source.

        'mix' (default) rotates roughly evenly: Wikimedia photos, Art
        Institute of Chicago art, and Met art. Failures fall back down
        the chain to Picsum, which needs no key and rarely fails.
        """
        fetchers = {
            'wikimedia': ('Wikimedia', self._fetch_wikimedia),
            'museum': ('Art Institute', self._fetch_museum),
            'met': ('Met', self._fetch_met),
            'picsum': ('Picsum', self._fetch_picsum),
        }
        if self.source == 'mix':
            chain = [random.choice(['met', 'museum', 'wikimedia']),
                     'wikimedia', 'picsum']
        elif self.source == 'flickr':
            if not self.flickr_api_key:
                if not self._warned_no_key:
                    self._warned_no_key = True
                    print("[Primitive] No flickr_api_key in config; using Picsum. "
                          "Add one under [Primitive] to enable Flickr.", flush=True)
                return self._fetch_picsum()
            result = self._attempt('Flickr', self._fetch_flickr)
            if result is not None:
                return result
            return self._fetch_picsum()
        else:
            chain = [self.source, 'picsum']

        tried = set()
        for name in chain:
            if name in tried:
                continue
            tried.add(name)
            result = self._attempt(*fetchers[name])
            if result is not None:
                return result
        raise RuntimeError("all photo sources failed")

    def _fetch_wikimedia(self):
        """Random date -> that day's Wikimedia Commons Picture of the Day.

        Commons' featured-picture archive goes back ~20 years and the API
        needs no key -- the closest free equivalent of Flickr interestingness.
        """
        for _ in range(6):
            date = (datetime.date.today()
                    - datetime.timedelta(days=random.randint(30, 7000)))
            date_str = date.strftime('%Y-%m-%d')
            resp = requests.get('https://commons.wikimedia.org/w/api.php', params={
                'action': 'query', 'format': 'json',
                'generator': 'images', 'titles': f'Template:Potd/{date_str}',
                'prop': 'imageinfo', 'iiprop': 'url|size|mime',
                'iiurlwidth': 640,
            }, headers=UA_HEADERS, timeout=HTTP_TIMEOUT)
            pages = resp.json().get('query', {}).get('pages', {})
            candidates = []
            for p in pages.values():
                info = (p.get('imageinfo') or [{}])[0]
                if info.get('mime') in ('image/jpeg', 'image/png') \
                        and info.get('width', 0) >= 640 and info.get('thumburl'):
                    candidates.append((info.get('width', 0) * info.get('height', 0),
                                       p.get('title', ''), info))
            if not candidates:
                continue
            candidates.sort(key=lambda c: c[0], reverse=True)
            _area, title, info = candidates[0]
            key = f"wikimedia:{title}"
            if key in self._seen_photo_ids:
                continue
            digest = hashlib.md5(title.encode()).hexdigest()[:8]
            path = self._download_square(info['thumburl'],
                                         f"_dl_wiki_{digest}.jpg")
            if path is None:
                continue
            self._seen_photo_ids.add(key)
            return key, path, info.get('descriptionurl', 'https://commons.wikimedia.org')
        raise RuntimeError("could not get a Wikimedia photo after several tries")

    def _fetch_museum(self):
        """Random public-domain artwork from the Art Institute of Chicago.

        No API key; CC0 collection. Subject terms rotate to favor paintings
        over object photography. Images via their IIIF server at 640px.
        """
        for _ in range(5):
            term = random.choice(ART_SUBJECTS)
            resp = requests.get('https://api.artic.edu/api/v1/artworks/search', params={
                'q': term,
                'query[term][is_public_domain]': 'true',
                'fields': 'id,title,image_id,artist_title',
                'page': random.randint(1, 30), 'limit': 10,
            }, headers=UA_HEADERS, timeout=HTTP_TIMEOUT)
            data = resp.json()
            items = [d for d in data.get('data', []) if d.get('image_id')]
            if not items:
                continue
            item = random.choice(items)
            key = f"museum:{item['id']}"
            if key in self._seen_photo_ids:
                continue
            iiif = data.get('config', {}).get('iiif_url', 'https://www.artic.edu/iiif/2')
            url = f"{iiif}/{item['image_id']}/full/640,/0/default.jpg"
            path = self._download_square(url, f"_dl_aic_{item['id']}.jpg")
            if path is None:
                continue
            self._seen_photo_ids.add(key)
            credit = f"https://www.artic.edu/artworks/{item['id']}"
            return key, path, credit
        raise RuntimeError("could not get a museum artwork after several tries")

    def _fetch_met(self):
        """Random public-domain artwork from The Met (Open Access API).

        No API key; CC0 collection. Search has no public-domain filter,
        so candidates are verified per-object via isPublicDomain.
        primaryImageSmall (~600px) is plenty for a 64px matrix.
        """
        # v1 search was retired 2026-10-01; v1.1 is paginated, so
        # sample a random offset into the result set.
        search_url = 'https://collectionapi.metmuseum.org/public/collection/v1.1/search'
        objects_url = 'https://collectionapi.metmuseum.org/public/collection/v1/objects'
        for _ in range(5):
            term = random.choice(ART_SUBJECTS)
            resp = requests.get(search_url, params={
                'q': term, 'hasImages': 'true', 'limit': 1,
            }, headers=UA_HEADERS, timeout=HTTP_TIMEOUT)
            total = resp.json().get('total') or 0
            if not total:
                continue
            resp = requests.get(search_url, params={
                'q': term, 'hasImages': 'true', 'limit': 20,
                'offset': random.randint(0, max(0, total - 20)),
            }, headers=UA_HEADERS, timeout=HTTP_TIMEOUT)
            ids = resp.json().get('objectIDs') or []
            if not ids:
                continue
            random.shuffle(ids)
            candidates = []
            for oid in ids[:8]:
                key = f"met:{oid}"
                if key in self._seen_photo_ids:
                    continue
                obj_resp = requests.get(f'{objects_url}/{oid}',
                                        headers=UA_HEADERS, timeout=HTTP_TIMEOUT)
                if obj_resp.status_code != 200:
                    continue
                obj = obj_resp.json()
                if not obj.get('isPublicDomain'):
                    continue
                url = obj.get('primaryImageSmall') or obj.get('primaryImage')
                if not url:
                    continue
                candidates.append((oid, key, url, obj))
                # Paintings are full-bleed and colorful; mounted photos
                # and works on paper waste most of a 64px matrix.
                if obj.get('classification') == 'Paintings':
                    break
            paintings = [c for c in candidates
                         if c[3].get('classification') == 'Paintings']
            for oid, key, url, obj in (paintings or candidates):
                path = self._download_square(url, f"_dl_met_{oid}.jpg")
                if path is None:
                    continue
                self._seen_photo_ids.add(key)
                credit = obj.get('objectURL') or 'https://www.metmuseum.org'
                return key, path, credit
        raise RuntimeError("could not get a Met artwork after several tries")

    def _fetch_flickr(self):
        """Bot recipe: random date -> that day's interestingness list -> random photo."""
        for _ in range(5):
            date = (datetime.date.today()
                    - datetime.timedelta(days=random.randint(1, 1000)))
            resp = requests.get('https://api.flickr.com/services/rest/', params={
                'method': 'flickr.interestingness.getList',
                'api_key': self.flickr_api_key,
                'format': 'json', 'nojsoncallback': 1,
                'date': date.strftime('%Y-%m-%d'),
                'per_page': 100,
            }, timeout=HTTP_TIMEOUT)
            data = resp.json()
            if data.get('stat') != 'ok':
                raise RuntimeError(f"Flickr API: {data.get('message', data)}")
            photos = data['photos']['photo']
            random.shuffle(photos)
            for p in photos:
                key = f"flickr:{p['id']}"
                if key in self._seen_photo_ids:
                    continue
                url = (f"https://farm{p['farm']}.staticflickr.com/"
                       f"{p['server']}/{p['id']}_{p['secret']}_z.jpg")
                path = self._download_square(url, f"_dl_flickr_{p['id']}.jpg")
                if path is None:
                    continue
                self._seen_photo_ids.add(key)
                credit = (f"https://www.flickr.com/photos/"
                          f"{p.get('owner', '')}/{p['id']}")
                return key, path, credit
        raise RuntimeError("could not get a Flickr photo after several tries")

    def _fetch_picsum(self):
        """Download a random square photo from Picsum (no API key needed)."""
        for _ in range(5):
            photo_id = random.randint(0, 1084)
            key = f"picsum:{photo_id}"
            if key in self._seen_photo_ids:
                continue
            path = self._download_square(
                f"https://picsum.photos/id/{photo_id}/640/640",
                f"_dl_picsum_{photo_id}.jpg")
            if path is None:
                continue
            self._seen_photo_ids.add(key)
            return key, path, f"https://picsum.photos/id/{photo_id}"
        raise RuntimeError("could not download a photo after several tries")

    def _download_square(self, url, filename):
        """Download an image and center-crop it square. Returns path or None."""
        resp = requests.get(url, timeout=HTTP_TIMEOUT, headers=UA_HEADERS)
        if resp.status_code != 200:
            return None
        tmp = os.path.join(CACHE_DIR, filename)
        with open(tmp, 'wb') as f:
            f.write(resp.content)
        try:
            with Image.open(tmp) as img:
                rgb = img.convert('RGB')
            side = min(rgb.size)
            left = (rgb.size[0] - side) // 2
            top = (rgb.size[1] - side) // 2
            rgb.crop((left, top, left + side, top + side)).save(tmp)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            return None
        return tmp

    # ----- playback (render loop) -----

    def _write_current_state(self, seq):
        """Publish what's on screen for the webapp's source viewer."""
        state = {
            'photo_id': seq['meta'].get('photo_id', ''),
            'source_url': seq['meta'].get('source_url', ''),
            'mode_name': seq['meta'].get('mode_name', ''),
            'shapes': seq['meta'].get('shapes', 0),
            'seq_dir': os.path.basename(seq['dir']),
            'has_source': os.path.exists(os.path.join(seq['dir'], 'source.jpg')),
        }
        try:
            tmp = CURRENT_STATE_PATH + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(state, f)
            os.replace(tmp, CURRENT_STATE_PATH)  # atomic; webapp reads anytime
        except OSError:
            pass

    def _start_next(self):
        with self._lock:
            self.current = self.sequences.pop(0) if self.sequences else None
        if self.current is not None:
            self.frame_idx = 0
            self.last_advance = 0.0
            self.hold_until = 0.0
            self._write_current_state(self.current)
        return self.current is not None

    def generate(self):
        """Return (frame, is_active). Streams frames as they are generated;
        advances one shape every shape_seconds, resting at the frontier."""
        if self.binary is None:
            return (self.black, False)

        if self.current is None:
            if not self._start_next():
                # Nothing ready. Before the first picture that's a black
                # screen; afterwards, hold the last good frame -- a dry
                # queue (slow network, failed fetch) must never blank
                # the display. (Same lesson as the old Spotify flicker.)
                if self._has_shown:
                    return (self.current_img, True)
                return (self.black, False)

        seq = self.current
        available = self._available_frames(seq)

        if self.frame_idx < available:
            now = time.time()
            if self.frame_idx == 0 or now - self.last_advance >= self.shape_seconds:
                frame_path = os.path.join(
                    seq['dir'], 'frames', f"{self.frame_idx + 1:03d}.png")
                try:
                    with Image.open(frame_path) as img:
                        self.current_img = img.convert("RGB")
                    self.frame_idx += 1
                    self.last_advance = now
                    self._has_shown = True
                except Exception:
                    pass  # frame still being written; try again next tick
            return (self.current_img, True)

        if not seq['done']:
            # Caught up with the generator: rest on the latest shape.
            return (self.current_img, True)

        # Finished building. Hold, then move on when a next picture exists.
        if self.hold_until == 0.0:
            self.hold_until = time.time() + self.hold_seconds
        if time.time() >= self.hold_until and self._start_next():
            return self.generate()
        return (self.current_img, True)
