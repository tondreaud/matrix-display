"""Primitive mode: photos that draw themselves out of geometric shapes.

Inspired by fogleman/primitive (MIT), and by the Primitive Twitter bot's
recipe. A background worker fetches a random interesting photo (Wikimedia
Commons Picture of the Day by default -- no API key needed; Flickr
interestingness and Picsum also supported), then runs the `primitive`
binary to approximate it with ~100-200 shapes at 128px working resolution.

The binary writes every shape to disk as it works, so playback streams:
the display starts showing the build-up within seconds and follows a few
shapes behind the generator, resting on the latest shape whenever it
catches up. The render loop never blocks on network or generation.

The matrix process and this module share state only through the cache
directory; generation runs at low priority (nice + limited workers).
"""

import datetime
import glob
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
MODE_NAMES = {
    0: 'combo', 1: 'triangles', 2: 'rectangles', 3: 'ellipses', 4: 'circles',
    5: 'rotated rectangles', 6: 'beziers', 7: 'rotated ellipses', 8: 'polygons',
}
# Mode pool randomized per photo (subset of the Primitive bot's choices).
# Beziers (6) excluded: their thin strokes vanish at 64x64.
BOT_MODES = [1, 5, 7]
UA_HEADERS = {'User-Agent': 'MatrixDisplay/1.0 (personal LED display; primitive mode)'}


def _find_binary():
    env = os.environ.get('PRIMITIVE_BIN')
    if env and os.path.isfile(env):
        return env
    bundled = os.path.join(IMPL_DIR, 'vendor', 'primitive')
    if os.path.isfile(bundled):
        return bundled
    return shutil.which('primitive')


class PrimitiveScreen:
    def __init__(self, config):
        self.canvas_width = CANVAS
        self.canvas_height = CANVAS
        self.binary = _find_binary()

        def cfg_get(name, fallback):
            try:
                if config is not None and 'Primitive' in config:
                    return config.get('Primitive', name, fallback=fallback)
            except Exception:
                pass
            return fallback

        self.workers = int(cfg_get('workers', '2'))
        self.work_res = int(cfg_get('resize', '128'))
        self.hold_seconds = float(cfg_get('hold_seconds', '25'))
        self.shape_seconds = float(cfg_get('shape_seconds', '0.5'))
        self.source = str(cfg_get('source', 'mix')).strip().lower()
        self.flickr_api_key = str(cfg_get('flickr_api_key', '')).strip()

        self.black = Image.new("RGB", (CANVAS, CANVAS), (0, 0, 0))
        self.sequences = []       # seq dicts: {dir, meta, done, total}
        self.current = None
        self.frame_idx = 0        # next frame index to show
        self.last_advance = 0.0
        self.hold_until = 0.0
        self.current_img = self.black
        self._seen_photo_ids = set()
        self._warned_no_key = False
        self._lock = threading.Lock()

        if self.binary is None:
            print("[Primitive] primitive binary not found "
                  "(looked in impl/vendor/, $PRIMITIVE_BIN, PATH). Mode will stay black.",
                  flush=True)
        else:
            os.makedirs(CACHE_DIR, exist_ok=True)
            self._adopt_existing_cache()
            thread = threading.Thread(target=self._worker, daemon=True)
            thread.start()
            print("[Primitive] Mode started; first picture will appear in a few "
                  "seconds and build up live.", flush=True)

    # ----- sequence bookkeeping -----

    @staticmethod
    def _frames_in(seq_dir):
        return sorted(glob.glob(os.path.join(seq_dir, 'frames', '*.png')))

    def _load_sequence(self, seq_dir, done=True):
        frames = self._frames_in(seq_dir)
        if not frames:
            return None
        meta = {}
        try:
            with open(os.path.join(seq_dir, 'meta.json')) as f:
                meta = json.load(f)
        except Exception:
            pass
        return {'dir': seq_dir, 'meta': meta, 'done': done,
                'total': len(frames) if done else None}

    def _adopt_existing_cache(self):
        """Reuse finished sequences from earlier runs (streaming ones lack meta)."""
        for meta_path in sorted(glob.glob(os.path.join(CACHE_DIR, '*', 'meta.json'))):
            seq = self._load_sequence(os.path.dirname(meta_path), done=True)
            if seq:
                with self._lock:
                    self.sequences.append(seq)

    def _available_frames(self, seq):
        """Frames safe to show. While generating, stay 1 frame behind the
        frontier so we never read a PNG that is still being written."""
        if seq['done']:
            return seq['total'] or 0
        return max(0, len(self._frames_in(seq['dir'])) - 1)

    # ----- generation (background thread) -----

    def _worker(self):
        while True:
            with self._lock:
                queued = len(self.sequences)
            if queued >= 2:
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
        shapes = random.randint(10, 20) * 10  # 100-200, bot range scaled to 64px
        name = f"{str(photo_id).replace(':', '_')}_m{mode}_n{shapes}_{int(time.time())}"
        seq_dir = os.path.join(CACHE_DIR, name)
        frames_dir = os.path.join(seq_dir, 'frames')
        os.makedirs(frames_dir, exist_ok=True)

        meta = {'photo_id': photo_id, 'source_url': source_url,
                'mode': mode, 'mode_name': MODE_NAMES[mode], 'shapes': shapes}
        seq = {'dir': seq_dir, 'meta': meta, 'done': False, 'total': None}
        with self._lock:
            self.sequences.append(seq)  # visible to playback immediately

        cmd = ['nice', '-n', '10', self.binary,
               '-i', photo_path,
               '-o', os.path.join(frames_dir, '%03d.png'),
               '-n', str(shapes), '-m', str(mode),
               '-r', str(self.work_res), '-s', str(CANVAS),
               '-a', '128', '-j', str(self.workers)]
        print(f"[Primitive] Generating {shapes} {MODE_NAMES[mode]} from "
              f"{source_url} ...", flush=True)
        start = time.time()
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
            ok = result.returncode == 0
            if not ok:
                print(f"[Primitive] binary failed: {result.stderr[-300:]}", flush=True)
        except Exception as e:
            ok = False
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

    def _prune_cache(self, keep=6):
        dirs = [p for p in glob.glob(os.path.join(CACHE_DIR, '*')) if os.path.isdir(p)]
        dirs.sort(key=lambda p: os.path.getmtime(p))
        current_dir = self.current['dir'] if self.current else None
        for old in dirs[:-keep]:
            if old != current_dir:
                shutil.rmtree(old, ignore_errors=True)

    # ----- photo sources -----

    def _fetch_photo(self):
        """Get the next photo/artwork from the configured source.

        'mix' (default) rotates: mostly Wikimedia photos, ~1 in 3 museum art.
        Any source failure falls back down the chain to Picsum.
        """
        if self.source == 'mix':
            if random.random() < 0.34:
                try:
                    return self._fetch_museum()
                except Exception as e:
                    print(f"[Primitive] Museum failed ({e}); using Wikimedia", flush=True)
            try:
                return self._fetch_wikimedia()
            except Exception as e:
                print(f"[Primitive] Wikimedia failed ({e}); falling back to Picsum",
                      flush=True)
            return self._fetch_picsum()
        if self.source == 'museum':
            try:
                return self._fetch_museum()
            except Exception as e:
                print(f"[Primitive] Museum failed ({e}); falling back to Picsum",
                      flush=True)
            return self._fetch_picsum()
        if self.source == 'wikimedia':
            try:
                return self._fetch_wikimedia()
            except Exception as e:
                print(f"[Primitive] Wikimedia failed ({e}); falling back to Picsum",
                      flush=True)
            return self._fetch_picsum()
        if self.source == 'flickr':
            if self.flickr_api_key:
                try:
                    return self._fetch_flickr()
                except Exception as e:
                    print(f"[Primitive] Flickr failed ({e}); falling back to Picsum",
                          flush=True)
            elif not self._warned_no_key:
                self._warned_no_key = True
                print("[Primitive] No flickr_api_key in config; using Picsum. "
                      "Add one under [Primitive] to enable Flickr.", flush=True)
        return self._fetch_picsum()

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
            }, headers=UA_HEADERS, timeout=20)
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
            candidates.sort(reverse=True)
            _area, title, info = candidates[0]
            key = f"wikimedia:{title}"
            if key in self._seen_photo_ids:
                continue
            path = self._download_square(info['thumburl'],
                                         f"_dl_wiki_{abs(hash(title)) % 999999}.jpg")
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
        subjects = ['landscape', 'portrait', 'still life', 'seascape',
                    'cityscape', 'mountains', 'river', 'garden']
        for _ in range(5):
            term = random.choice(subjects)
            resp = requests.get('https://api.artic.edu/api/v1/artworks/search', params={
                'q': term,
                'query[term][is_public_domain]': 'true',
                'fields': 'id,title,image_id,artist_title',
                'page': random.randint(1, 30), 'limit': 10,
            }, headers=UA_HEADERS, timeout=20)
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
            }, timeout=20)
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
        resp = requests.get(url, timeout=20, headers=UA_HEADERS)
        if resp.status_code != 200:
            return None
        tmp = os.path.join(CACHE_DIR, filename)
        with open(tmp, 'wb') as f:
            f.write(resp.content)
        try:
            img = Image.open(tmp).convert('RGB')
            side = min(img.size)
            left = (img.size[0] - side) // 2
            top = (img.size[1] - side) // 2
            img.crop((left, top, left + side, top + side)).save(tmp)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            return None
        return tmp

    # ----- playback (render loop) -----

    def _start_next(self):
        with self._lock:
            self.current = self.sequences.pop(0) if self.sequences else None
        if self.current is not None:
            self.frame_idx = 0
            self.last_advance = 0.0
            self.hold_until = 0.0
        return self.current is not None

    def generate(self):
        """Return (frame, is_active). Streams frames as they are generated;
        advances one shape every shape_seconds, resting at the frontier."""
        if self.binary is None:
            return (self.black, False)

        if self.current is None:
            if not self._start_next():
                return (self.black, False)  # first picture not ready yet

        seq = self.current
        available = self._available_frames(seq)

        if self.frame_idx < available:
            now = time.time()
            if self.frame_idx == 0 or now - self.last_advance >= self.shape_seconds:
                frame_path = os.path.join(
                    seq['dir'], 'frames', f"{self.frame_idx + 1:03d}.png")
                try:
                    self.current_img = Image.open(frame_path).convert("RGB")
                    self.frame_idx += 1
                    self.last_advance = now
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
