import os, math, time, spotipy
from queue import LifoQueue
from spotipy.exceptions import SpotifyException

# Token cache and status live next to the controller (impl/), regardless of
# the working directory the service happens to start in.
CACHE_PATH = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.cache'))
STATUS_PATH = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.spotify_status'))

SCOPE = "user-read-currently-playing, user-read-playback-state, user-modify-playback-state"


def _is_auth_error(error):
    """True when Spotify says the saved login is dead and cannot be refreshed."""
    text = str(error).lower()
    return 'invalid_grant' in text or 'refresh token revoked' in text


class SpotifyModule:
    def __init__(self, config):
        self.invalid = False
        self.needs_reauth = False
        self.calls = 0
        self.queue = LifoQueue()
        self.config = config
        self.consecutive_401s = 0
        self._status = None

        if config is not None and 'Spotify' in config and 'client_id' in config['Spotify'] \
            and 'client_secret' in config['Spotify'] and 'redirect_uri' in config['Spotify']:

            client_id = config['Spotify']['client_id']
            client_secret = config['Spotify']['client_secret']
            redirect_uri = config['Spotify']['redirect_uri']
            if client_id != "" and client_secret != "" and redirect_uri != "":
                try:
                    os.environ["SPOTIPY_CLIENT_ID"] = client_id
                    os.environ["SPOTIPY_CLIENT_SECRET"] = client_secret
                    os.environ["SPOTIPY_REDIRECT_URI"] = redirect_uri

                    self.auth_manager = spotipy.SpotifyOAuth(
                        scope=SCOPE, cache_path=CACHE_PATH, open_browser=False)
                    self.sp = spotipy.Spotify(auth_manager=self.auth_manager, requests_timeout=10)
                    self.isPlaying = False
                    self._check_cached_token()
                except Exception as e:
                    if _is_auth_error(e):
                        self._mark_needs_reauth()
                    else:
                        print(e)
                        self.invalid = True
            else:
                print("[Spotify Module] Empty Spotify client id or secret")
                self.invalid = True
        else:
            print("[Spotify Module] Missing config parameters")
            self.invalid = True

    def _check_cached_token(self):
        """Validate the saved token once at startup (refreshes it if needed)."""
        try:
            token_info = self.auth_manager.get_cached_token()
        except Exception as e:
            if _is_auth_error(e):
                self._mark_needs_reauth()
                return
            raise
        if token_info is None:
            self._mark_needs_reauth()

    def _write_status(self, state):
        if self._status == state:
            return
        self._status = state
        try:
            with open(STATUS_PATH, 'w') as f:
                f.write(state)
        except Exception:
            pass

    def _mark_needs_reauth(self):
        # Log once, then stop polling. Spotify refresh tokens now expire
        # after 6 months; retrying a dead one only spams the journal.
        if not self.invalid:
            print("[Spotify] Login expired or revoked. "
                  "Open the Matrix Display web page and tap Reconnect Spotify.",
                  flush=True)
        self.invalid = True
        self.needs_reauth = True
        self._write_status('needs_reauth')

    def _mark_connected(self):
        self.needs_reauth = False
        self._write_status('connected')

    def isDeviceWhitelisted(self):
        if self.config is not None and 'Spotify' in self.config and 'device_whitelist' in self.config['Spotify']:
            try:
                devices = self.sp.devices()
            except Exception as e:
                if _is_auth_error(e):
                    self._mark_needs_reauth()
                else:
                    print(e)
                return False

            device_whitelist = self.config['Spotify']['device_whitelist']
            for device in devices['devices']:
                if device['name'] in device_whitelist and device['is_active']:
                    return True
            return False
        else:
            return True

    def getCurrentPlayback(self):
        if self.invalid:
            return
        try:
            track = self.sp.current_user_playing_track()

            if (track is not None and self.isDeviceWhitelisted()):
                self._mark_connected()
                item = track['item']
                if (item is None):
                    artist = None
                    title = None
                    art_url = None
                    duration_ms = None
                else:
                    artist = item['artists'][0]['name']
                    if len(item['artists']) >= 2:
                        artist = artist + ", " + item['artists'][1]['name']
                    title = item['name']
                    images = item['album'].get('images') or []
                    art_url = images[0]['url'] if images else None
                    duration_ms = item.get('duration_ms')
                self.isPlaying = track['is_playing']

                self.queue.put((artist, title, art_url, self.isPlaying,
                                track.get("progress_ms"), duration_ms))
                self.consecutive_401s = 0  # Reset on success
            elif (track is None):
                self._mark_connected()
                self.queue.put(None)
                self.consecutive_401s = 0  # Reset on success
        except SpotifyException as e:
            if _is_auth_error(e):
                self._mark_needs_reauth()
            elif e.http_status == 401:
                self.consecutive_401s += 1
                if self.consecutive_401s <= 3:
                    # Try forcing a token refresh - don't trust expires_at
                    print(f"[Spotify] 401 error, forcing token refresh (attempt {self.consecutive_401s})")
                    try:
                        self.auth_manager.get_access_token(as_dict=False, check_cache=False)
                    except Exception as refresh_error:
                        if _is_auth_error(refresh_error):
                            self._mark_needs_reauth()
                        else:
                            print(f"[Spotify] Refresh failed: {refresh_error}")
                else:
                    # Refreshing is not helping; treat as a dead login
                    # instead of polling forever.
                    self._mark_needs_reauth()
            else:
                print(e)
        except Exception as e:
            if _is_auth_error(e):
                self._mark_needs_reauth()
            else:
                print(e)
