# spotify_handler.py
import spotipy
from common.utilities import format_duration
import logging

logger = logging.getLogger(__name__)

# spotipy hər HTTP xətasını (403/404/429) özü ERROR kimi loglayır — biz onları tutub idarə edirik,
# ona görə 🚨 ani xəta bildirişi və log spamı yaranmasın deyə yalnız CRITICAL buraxılır.
logging.getLogger("spotipy.client").setLevel(logging.CRITICAL)

# 403 verən endpoint-lər (Spotify bəzi tətbiqlərə məhdudiyyət qoyur) — bir müddət çağırılmasın
_FORBIDDEN = {}                      # endpoint adı → bitmə vaxtı
FORBID_TTL = 6 * 3600


def endpoint_blocked(name: str) -> bool:
    import time
    until = _FORBIDDEN.get(name)
    if until and until > time.time():
        return True
    _FORBIDDEN.pop(name, None)
    return False


def mark_forbidden(name: str, err) -> bool:
    """403 idisə endpoint-i 6 saatlıq bağla. Qaytarır: 403 idimi."""
    import time
    status = getattr(err, "http_status", None) or getattr(err, "status", None)
    if status == 403 or "403" in str(err):
        if name not in _FORBIDDEN:
            logger.warning(f"Spotify '{name}' endpoint-i bu tətbiq üçün bağlıdır (403) — {FORBID_TTL // 3600} "
                           f"saat çağırılmayacaq, alternativ mənbə işlədiləcək.")
        _FORBIDDEN[name] = time.time() + FORBID_TTL
        return True
    return False

from pathlib import Path
import os
from spotipy.oauth2 import SpotifyClientCredentials, SpotifyOAuth, CacheFileHandler

SPOTIFY_SCOPES = "playlist-read-private playlist-read-collaborative user-library-read user-read-private playlist-modify-public playlist-modify-private"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8888/callback"
CACHE_PATH = str(Path(__file__).resolve().parent.parent.parent / "data" / ".spotify_cache")


class SpotifyManager:
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str = None):
        self.client_id = (client_id or "").strip()
        self.client_secret = (client_secret or "").strip()
        self.redirect_uri = (redirect_uri or os.getenv("SPOTIFY_REDIRECT_URI", DEFAULT_REDIRECT_URI)).strip()
        self.sp = None
        self.oauth_manager = None
        self.auth_mode = "none"  # "user" | "app" | "none"
        self.user_info = None
        self.last_error = None
        self.reload(client_id, client_secret)

    def is_connected(self) -> bool:
        return self.sp is not None

    def is_user_auth(self) -> bool:
        return self.auth_mode == "user" and self.sp is not None

    def get_oauth_manager(self) -> SpotifyOAuth:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        handler = CacheFileHandler(cache_path=CACHE_PATH)
        redirect = self.redirect_uri or os.getenv("SPOTIFY_REDIRECT_URI", DEFAULT_REDIRECT_URI)
        return SpotifyOAuth(
            client_id=self.client_id,
            client_secret=self.client_secret,
            redirect_uri=redirect,
            scope=SPOTIFY_SCOPES,
            cache_handler=handler,
            open_browser=False,
        )

    def get_authorize_url(self) -> str:
        oauth = self.get_oauth_manager()
        return oauth.get_authorize_url()

    def complete_oauth(self, response_text_or_url: str) -> tuple[bool, str]:
        """Kodu və ya yönləndirmə URL-ini qəbul edir və istifadəçi sessiyasını saxlayır."""
        raw = (response_text_or_url or "").strip()
        if not raw:
            return False, "Kod və ya URL boşdur."
        if "code=" in raw:
            code = raw.split("code=")[1].split("&")[0].strip()
        else:
            oauth = self.get_oauth_manager()
            code = oauth.parse_response_code(raw)

        if not code:
            return False, "Göndərilən mətndə 'code' parametri tapılmadı."

        try:
            oauth = self.get_oauth_manager()
            token_info = oauth.get_access_token(code, as_dict=True, check_cache=False)
            if not token_info:
                return False, "Spotify access token alına bilmədi."

            sp = spotipy.Spotify(auth_manager=oauth)
            me = sp.current_user()
            self.sp = sp
            self.oauth_manager = oauth
            self.auth_mode = "user"
            self.user_info = me
            name = me.get("display_name") or me.get("id") or "İstifadəçi"
            prod = me.get("product") or "user"

            ref_token = token_info.get("refresh_token")
            if ref_token:
                try:
                    from common.database import get_db
                    get_db().set_setting("spotify:refresh_token", ref_token)
                except Exception as _e:
                    logger.debug("udulan xəta [bot/core/spotify_handler.py:117]: %r", _e)

            logger.info(f"✅ Spotify OAuth login uğurludur: {name} ({prod})")
            return True, f"Qoşuldu: {name} ({prod.capitalize()})"
        except Exception as e:
            logger.error(f"Spotify OAuth tamamlama xətası: {e}")
            return False, f"Giriş xətası: {str(e)[:150]}"

    def logout_oauth(self) -> tuple[bool, str]:
        """İstifadəçi sessiyasını silir və Client Credentials rejiminə qayıdır."""
        try:
            if os.path.exists(CACHE_PATH):
                os.remove(CACHE_PATH)
        except Exception as e:
            logger.warning(f"Spotify cache silinmədi: {e}")
        try:
            from common.database import get_db
            get_db().set_setting("spotify:refresh_token", None)
        except Exception as _e:
            logger.debug("udulan xəta [bot/core/spotify_handler.py:136]: %r", _e)
        self.auth_mode = "none"
        self.user_info = None
        self.reload()
        return True, "Spotify istifadəçi hesabından çıxış edildi."

    def reload(self, client_id: str = None, client_secret: str = None) -> tuple[bool, str]:
        """Açarları yeniləyir və Spotify bağlantısını yenidən qurur (əvvəl OAuth, sonra App)."""
        if client_id is not None:
            self.client_id = (client_id or "").strip()
        if client_secret is not None:
            self.client_secret = (client_secret or "").strip()
        self.sp = None
        self.last_error = None
        self.auth_mode = "none"
        self.user_info = None

        if not self.client_id or not self.client_secret:
            msg = "Spotify açarları yoxdur (SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET)"
            self.last_error = msg
            logger.warning(f"{msg} — Spotify linkləri və metadata söndürüldü.")
            return False, msg

        # 1. Əvvəlcə istifadəçi OAuth sessiyası varmı yoxla
        try:
            oauth = self.get_oauth_manager()
            cached_token = oauth.cache_handler.get_cached_token()
            token_info = oauth.validate_token(cached_token)
            if token_info:
                sp = spotipy.Spotify(auth_manager=oauth)
                me = sp.current_user()
                self.sp = sp
                self.oauth_manager = oauth
                self.auth_mode = "user"
                self.user_info = me
                name = me.get("display_name") or me.get("id") or "İstifadəçi"
                prod = me.get("product") or "user"
                logger.info(f"✅ Spotify User OAuth connection established: {name} ({prod})")
                return True, f"İstifadəçi hesabı: {name} ({prod.capitalize()})"
        except Exception as e:
            logger.info(f"Spotify User OAuth aktiv deyil ({e}), Client Credentials yoxlanılır...")

        # 2. İkinci seçim: Client Credentials (tətbiq hesabı)
        try:
            sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
                client_id=self.client_id,
                client_secret=self.client_secret,
            ))
            sp.search(q="test", type="track", limit=1)      # açarı həqiqətən yoxla
            self.sp = sp
            self.auth_mode = "app"
            logger.info("Spotify API (Client Credentials) connection established.")
            return True, "Spotify API (Client Credentials) aktivdir."
        except Exception as e:
            self.sp = None
            self.auth_mode = "none"
            self.last_error = str(e)
            logger.error(f"Spotify açarları işləmir: {str(e)[:200]}")
            return False, str(e)

    def test_connection(self) -> tuple[bool, str]:
        """Bağlantını yoxlayır və nəticə qaytarır (True/False, mesaj)."""
        if not self.client_id or not self.client_secret:
            return False, "Spotify Client ID və ya Client Secret təyin edilməyib."
        try:
            if not self.sp:
                ok, msg = self.reload(self.client_id, self.client_secret)
                if not ok:
                    return False, msg
            if self.is_user_auth():
                me = self.sp.current_user()
                name = me.get("display_name") or me.get("id") or "İstifadəçi"
                prod = me.get("product") or "user"
                return True, f"İstifadəçi OAuth aktivdir: {name} ({prod.capitalize()})"
            res = self.sp.search(q="test", type="track", limit=1)
            if res and res.get("tracks", {}).get("items"):
                return True, "Spotify API aktivdir (Client Credentials)."
            return True, "Spotify API aktivdir."
        except Exception as e:
            self.last_error = str(e)
            return False, str(e)


    # ── Spotify sorğuları: 401 (token vaxtı keçib) olanda bir dəfə yenidən qoşulub təkrar ──
    def _call(self, label: str, fetch):
        if not self.sp:
            raise RuntimeError("Spotify API qoşulmayıb (Client ID / Secret yoxdur)")
        try:
            logger.info(f"Fetching {label}")
            return fetch()
        except Exception as e:
            is_401 = getattr(e, "http_status", None) == 401 or "401" in str(e)
            if is_401 and self.client_id and self.client_secret:
                logger.warning(f"Spotify token expired or 401 ({e}), re-authenticating...")
                try:
                    self.reload(self.client_id, self.client_secret)
                    if self.sp:
                        return fetch()
                except Exception as retry_err:
                    logger.error(f"Spotify retry failed: {retry_err}")
            logger.error(f"Spotify API error: {str(e)}")
            raise RuntimeError(f"Spotify API error: {str(e)}")

    def _all_items(self, page: dict) -> list:
        """Səhifələnmiş Spotify cavabının bütün elementləri."""
        items = list(page.get('items', []))
        while page.get('next'):
            page = self.sp.next(page)
            items.extend(page.get('items', []))
        return items

    def get_playlist_data(self, playlist_id: str) -> dict:
        def fetch():
            playlist = self.sp.playlist(playlist_id)
            tracks = self._all_items(self.sp.playlist_items(playlist_id, additional_types=['track']))
            return {
                "name": playlist.get('name', 'Unknown Playlist'),
                "tracks": self._process_tracks({"items": tracks}),
                "total": len(tracks),
                "duration": self._format_duration({"items": tracks})
            }
        return self._call(f"playlist data: {playlist_id}", fetch)

    def _process_tracks(self, tracks: dict) -> list:
        res = []
        for raw in tracks.get('items', []):
            item = raw.get('track') or raw.get('item') or raw
            if not isinstance(item, dict):
                continue
            name = item.get('name')
            if not name:
                continue
            artists = [{"name": a.get('name', '')} for a in item.get('artists', []) if isinstance(a, dict)]
            duration_ms = item.get('duration_ms', 0) or 0
            album = item.get('album', {}) or {}
            res.append({
                "name": name,
                "artists": artists,
                "duration_ms": duration_ms,
                "duration_formatted": format_duration(duration_ms // 1000),
                "album": {
                    "name": album.get('name', '') if isinstance(album, dict) else '',
                    "images": album.get('images', []) if isinstance(album, dict) else []
                }
            })
        return res

    def _format_duration(self, tracks: dict) -> str:
        total_ms = 0
        for raw in tracks.get('items', []):
            item = raw.get('track') or raw.get('item') or raw
            if isinstance(item, dict):
                total_ms += item.get('duration_ms', 0) or 0
        total_seconds = total_ms // 1000
        return format_duration(total_seconds)


    def get_track_data(self, track_id: str) -> dict:
        def fetch():
            track = self.sp.track(track_id)
            return {
                "name": track.get('name', 'Unknown Track'),
                "artists": [{"name": a.get('name', '')} for a in track.get('artists', [])],
                "duration_ms": track.get('duration_ms', 0) or 0,
                "album": {
                    "name": track.get('album', {}).get('name', ''),
                    "images": track.get('album', {}).get('images', [])
                }
            }
        return self._call(f"track data: {track_id}", fetch)

    def get_album_data(self, album_id: str) -> dict:
        def fetch():
            album = self.sp.album(album_id)
            tracks = self._all_items(self.sp.album_tracks(album_id))
            info = {"name": album.get('name', ''), "images": album.get('images', [])}
            for t in tracks:                   # albom treklərində "album" sahəsi yoxdur — valideyndən
                t.setdefault('album', info)
            return {
                "name": album.get('name', 'Unknown Album'),
                "artist": album.get('artists', [{}])[0].get('name', 'Unknown Artist'),
                "tracks": self._process_tracks({"items": tracks}),
                "total": len(tracks),
                "duration": self._format_duration({"items": tracks}),
                "images": album.get('images', [])
            }
        return self._call(f"album data: {album_id}", fetch)

