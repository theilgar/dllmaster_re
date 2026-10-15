"""
🎙 İfaçı axtarışı — bot/plugins/artist_plugin.py

  /artist <ad>  (/ifaci)   — ifaçını YouTube Music-də tapır, BÜTÜN mahnılarını (mahnılar siyahısı + albomlar
                             + sinqllar, təkrarsız) bir kartda göstərir. Ad yazılmasa — soruşur.
  Kartda:
    📋 Mahnılar        — səhifə-səhifə siyahı, istənilən mahnıya toxun → yüklənir (📦 = depoda var, dərhal)
    🔥 Top             — ən populyar mahnılar
    🤝 feat.           — başqa ifaçıların mahnılarındakı iştirakları da siyahıdadır
Eyni adlı bir neçə profil (rəsmi ifaçı + kanal) çıxanda — avtomatik BİRLƏŞİR, seçim soruşulmur.
    ⬇️ Hamısını yüklə  — toplu yükləmə (🎛 İmkanlar: "⬇️ Hamısını yüklə"), ELƏ BU MESAJDA:
                         📊 Proses (mərhələlər: depo yoxlanır / YouTube → server / server → depo /
                         depo → çat / ⏳ flood gözləməsi, depodan vs yüklənən sayı, qalan vaxt)
                         ⇄ 🎙 Kart — yükləmə dayanmadan kartda gəzmək olur
    💾 Playlist kimi saxla — 🎶 playlist_plugin

Bir neçə ifaçı uyğun gəlsə — seçim düymələri. Nəticələr 30 dəq. yaddaşda qalır.
🎛 İmkanlar: "🎙 İfaçı axtarışı" (artist_search) — 👑 / 💎 / 🆓 üçün ayrıca.
Tələb: pip install ytmusicapi
"""
import asyncio
import contextlib
import logging
import re
import threading
import time
from html import escape

from aiogram import F, types
from aiogram.filters import Command, CommandObject
from aiogram.types import InlineKeyboardMarkup

from common import tiers
from common.formatting import fmt_duration_words
from common.i18n import _ as tr
from bot.core import song_names as names
from bot.core.panel import btn, pager, show as panel_show

logger = logging.getLogger(__name__)


PRIORITY = 75
PAGE = 8
MAX_DURATION = 15 * 60           # 15 dəq.-dən uzun (mix / kompilyasiya) — siyahıya düşmür
MAX_ALBUMS = 80                  # albom + sinql (çox böyük diskoqrafiyada limit)
PAUSE = 0.3                      # ytmusicapi sorğuları arası — IP bloklanmasın
CACHE_TTL = 30 * 60
SESSION_TTL = 60 * 60
WAIT_TTL = 300

try:
    from ytmusicapi import YTMusic
except ImportError:
    YTMusic = None

# Hər thread öz client-i: ytmusicapi / requests.Session thread-safe deyil (to_thread hovuzunda paralel işləyir)
_local = threading.local()
_LOADS = threading.BoundedSemaphore(3)          # eyni anda ən çox 3 ağır ifaçı oxunuşu


HTTP_TIMEOUT = 20                # ytmusicapi sorğularında timeout yoxdur → asılı qalırdı ("oxunur..." ilişirdi)
STALL_AFTER = 45                 # bu qədər irəliləyiş yoxdursa — "ilişib" + 🔄 düyməsi
LOAD_LIMIT = 6 * 60              # bundan uzun — dayandırılır


def _session():
    try:
        import requests

        class _TimeoutSession(requests.Session):
            def request(self, *a, **kw):
                kw.setdefault("timeout", HTTP_TIMEOUT)
                return super().request(*a, **kw)
        return _TimeoutSession()
    except Exception:
        return None


def client():
    c = getattr(_local, "client", None)
    if c is None and YTMusic is not None:
        sess = _session()
        for kw in ({"language": "en", "location": "AZ", "requests_session": sess},
                   {"language": "en", "location": "AZ"}, {}):
            try:
                c = YTMusic(**{k: v for k, v in kw.items() if v is not None})
                break
            except TypeError:                           # köhnə ytmusicapi
                continue
        _local.client = c
    return c


class LoadCancelled(Exception):
    pass


def _tick(prog, **kw):
    """İrəliləyiş: mərhələ / saylar yenilənir; ⏹ ləğv və ⏭ "olanı göstər" yoxlanılır."""
    if prog is None:
        return
    if prog.get("cancel"):
        raise LoadCancelled()
    if kw:
        prog.update(kw)
        prog["ts"] = time.time()




def _dur(t) -> int:
    if t.get("duration_seconds"):
        return int(t["duration_seconds"])
    parts = str(t.get("duration") or "").split(":")
    try:
        sec = 0
        for p in parts:
            sec = sec * 60 + int(p)
        return sec
    except ValueError:
        return 0


def _fmt(sec: int) -> str:
    return fmt_duration_words(sec)


def clean_title(title: str, artist: str = "") -> str:
    """Video adı → mahnı adı: "(Official Video)", "[Lyrics]" atılır, əvvəldəki "İfaçı - " kəsilir."""
    t = names.clean_track(title or "")
    if artist and " - " in t:
        left, right = t.split(" - ", 1)
        if artist.casefold() in left.casefold():
            t = right
    return t.strip(" -|")



def _key(title: str) -> str:
    """Təkrar yoxlaması üçün: mötərizəli əlavələr (feat., remaster, live ...) atılır."""
    t = re.sub(r"[\(\[].*?[\)\]]", "", title or "").casefold()
    return re.sub(r"[^\w]+", "", t)


def _item(t: dict, fallback_artists=None, fallback_thumbs=None, is_video=False):
    vid = t.get("videoId")
    title = (t.get("title") or "").strip()
    if not vid or not title or t.get("isAvailable") is False:
        return None
    arts = [a["name"] for a in (t.get("artists") or fallback_artists or []) if a.get("name")]
    artist = ", ".join(arts[:3])
    if is_video:
        title = clean_title(title, arts[0] if arts else "") or title
    dur = _dur(t)
    if dur > MAX_DURATION:
        return None
    thumbs = t.get("thumbnails") or fallback_thumbs or []
    return {"title": f"{artist} - {title}" if artist else title,
            "query": f"{arts[0] if arts else ''} {title}".strip(),
            "url": f"https://www.youtube.com/watch?v={vid}", "vid": vid, "raw_duration": dur,
            "meta": {"artist": artist, "track": title} if artist else None,
            "thumb": thumbs[-1].get("url") if thumbs else None, "_k": _key(title)}


# ═════════════════════ ytmusicapi (sinxron — to_thread-də) ═════════════════════
def _norm(name: str) -> str:
    return re.sub(r"\W+", "", (name or "").casefold())


def search_sync(name: str) -> list:
    """→ [{"ids": [...], "name", "subs"}] — EYNİ adlı nəticələr (bir ifaçının iki kanalı / profili) birləşir."""
    yt = client()
    res = _retry(yt.search, name, filter="artists", limit=8) or []
    groups = {}
    for r in res:
        aid, nm = r.get("browseId"), r.get("artist")
        if not (aid and nm):
            continue
        g = groups.setdefault(_norm(nm), {"ids": [], "name": nm, "subs": r.get("subscribers") or ""})
        if aid not in g["ids"]:
            g["ids"].append(aid)
    return list(groups.values())[:6]


_cache = {}                       # aid → (ts, data)


def _is_net_error(e) -> bool:
    t = f"{type(e).__name__} {e}".lower()
    return any(k in t for k in ("connection", "reset by peer", "timed out", "timeout", "remotedisconnected",
                                "aborted", "temporarily", "502", "503", "504"))


def _fresh_client():
    """Köhnəlmiş keep-alive bağlantısı ('Connection reset by peer') — yeni sessiya ilə yeni client."""
    _local.client = None
    return client()


def _retry(fn, *a, **kw):
    """Şəbəkə xətasında 3 cəhd (1.5 → 4 san.), hər dəfə YENİ bağlantı ilə."""
    for i in range(3):
        try:
            return fn(*a, **kw)
        except Exception as e:
            if i == 2 or not _is_net_error(e):
                if i == 0 and not _is_net_error(e):
                    time.sleep(1.5)                          # adi xəta — bir dəfə təkrar
                    return fn(*a, **kw)
                raise
            logger.info(f"🎙 YouTube Music bağlantısı qırıldı ({e}) — yenidən ({i + 1}/2)")
            yt = _fresh_client()
            owner = getattr(fn, "__self__", None)
            if owner is not None and yt is not None and hasattr(yt, fn.__name__):
                fn = getattr(yt, fn.__name__)                # metod yeni client-ə keçir
            time.sleep(1.5 if i == 0 else 4)


def _full_playlist(yt, browse_id) -> list:
    pid = browse_id[2:] if browse_id.startswith("VL") else browse_id
    return (_retry(yt.get_playlist, pid, limit=None) or {}).get("tracks") or []


def _artist_albums(yt, sec: dict) -> list:
    albums = sec.get("results") or []
    if sec.get("browseId") and sec.get("params"):
        try:
            albums = _retry(yt.get_artist_albums, sec["browseId"], sec["params"], limit=None) or albums
        except TypeError:                                   # köhnə ytmusicapi (limit yoxdur)
            try:
                albums = _retry(yt.get_artist_albums, sec["browseId"], sec["params"]) or albums
            except Exception as e:
                logger.debug(f"🎙 albomlar: {e}")
        except Exception as e:
            logger.debug(f"🎙 albomlar: {e}")
        time.sleep(PAUSE)
    return albums


def _collect(yt, aid: str, st: dict, prog=None):
    """Bir kanal/profil → (ad, [(trek, fa, ft, video?)], top_raw)"""
    _tick(prog, stage="profile")
    a = _retry(yt.get_artist, aid) or {}
    name = a.get("name") or "?"
    me = [{"name": name, "id": aid}]
    tracks = []
    songs = a.get("songs") or {}
    top_raw = songs.get("results") or []
    if songs.get("browseId"):
        _tick(prog, stage="songs", name=name)
        try:
            got = _full_playlist(yt, songs["browseId"])
            st["songs"] += len(got)
            tracks += [(t, me, None, False) for t in got]
        except Exception as e:
            logger.info(f"🎙 {name}: mahnılar siyahısı alınmadı: {e}")
        time.sleep(PAUSE)
    tracks += [(t, me, None, False) for t in top_raw]
    _tick(prog, stage="album_list", songs=st["songs"])
    album_ids = []
    for key in ("albums", "singles"):
        albums = _artist_albums(yt, a.get(key) or {})
        st[key] += len(albums)
        album_ids += [al["browseId"] for al in albums if al.get("browseId")]
    st["total_albums"] += len(album_ids)
    todo = album_ids[:MAX_ALBUMS]
    for n_alb, bid in enumerate(todo):
        _tick(prog, stage="albums", a_done=n_alb, a_total=len(todo), tracks=len(tracks))
        if prog and prog.get("finish"):
            break
        try:
            alb = _retry(yt.get_album, bid) or {}
            st["read"] += 1
        except Exception:
            st["failed"] += 1
            continue
        for t in alb.get("tracks") or []:
            tracks.append((t, alb.get("artists") or me, alb.get("thumbnails"), False))
        time.sleep(PAUSE)
    videos = a.get("videos") or {}                        # 🎬 yerli ifaçılarda mahnılar çox vaxt yalnız video kimidir
    vids_raw = videos.get("results") or []
    if videos.get("browseId") and not (prog and prog.get("finish")):
        _tick(prog, stage="videos")
        try:
            vids_raw = _full_playlist(yt, videos["browseId"]) or vids_raw
        except Exception as e:
            logger.debug(f"🎙 {name}: videolar: {e}")
        time.sleep(PAUSE)
    st["videos"] += len(vids_raw)
    tracks += [(t, me, None, True) for t in vids_raw]
    return name, tracks, top_raw


def _feat_tracks(yt, name: str) -> list:
    """🤝 feat.: başqa ifaçıların mahnılarında iştirak — YT Music mahnı axtarışında ifaçılar arasında bu ad."""
    want = _norm(name)
    out = []
    try:
        res = _retry(yt.search, name, filter="songs", limit=100) or []
    except Exception as e:
        logger.debug(f"🎙 feat axtarışı: {e}")
        return out
    for t in res:
        arts = [x.get("name") or "" for x in t.get("artists") or []]
        if any(_norm(n) == want for n in arts):
            out.append(t)
    return out


def load_sync(ids, prog=None) -> dict:
    with _LOADS:
        return _load_sync(ids, prog)


def _load_sync(ids, prog=None) -> dict:
    """Bir və ya bir neçə profil (eyni ifaçı) → mahnılar + albomlar + sinqllar + videolar + 🤝 feat.
    prog — canlı irəliləyiş dict-i (open_artist göstərir)."""
    ids = tuple(ids if isinstance(ids, (list, tuple)) else [ids])
    hit = _cache.get(ids)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    yt = client()
    st = {"songs": 0, "albums": 0, "singles": 0, "read": 0, "failed": 0, "videos": 0, "feat": 0,
          "total_albums": 0, "profiles": len(ids)}
    name, tracks, top_raw = None, [], []
    for k, aid in enumerate(ids):
        _tick(prog, profile=k + 1, profiles=len(ids))
        try:
            nm, tr_, top_ = _collect(yt, aid, st, prog)
        except LoadCancelled:
            raise
        except Exception as e:
            logger.info(f"🎙 profil {aid}: {e}")
            continue
        name = name or nm
        tracks += tr_
        top_raw = top_raw or top_
    if name is None:
        raise RuntimeError("ifaçı açılmadı")
    want = _norm(name)

    items, seen = [], {}                                  # açar → [müddətlər] (eyni adlı fərqli mahnılar qalsın)

    def add(it, feat=False):
        if not it or not it["_k"]:
            return
        durs = seen.setdefault(it["_k"], [])
        if any(not d or not it["raw_duration"] or abs(d - it["raw_duration"]) <= 20 for d in durs):
            return
        durs.append(it["raw_duration"])
        if feat:
            it["feat"] = True
            st["feat"] += 1
        items.append(it)

    for t, fa, ft, is_video in tracks:
        add(_item(t, fa, ft, is_video))
    feat = [] if prog and prog.get("finish") else (_tick(prog, stage="feat") or _feat_tracks(yt, name))
    for t in feat:                                        # öz mahnıları artıq var — yalnız yenilər əlavə olunur
        it = _item(t)
        arts = [x.get("name") or "" for x in t.get("artists") or []]
        add(it, feat=bool(arts) and _norm(arts[0]) != want)
    top, tseen = [], set()
    for t in top_raw:
        it = _item(t, [{"name": name}])
        if it and it["_k"] not in tseen:
            tseen.add(it["_k"])
            top.append(it)
    data = {"id": ids[0], "name": name, "items": items, "top": top, "albums": st["albums"],
            "singles": st["singles"], "partial": st["total_albums"] > MAX_ALBUMS * len(ids), "stats": st}
    logger.info(f"🎙 {name}: {len(items)} mahnı ({len(ids)} profil · siyahı {st['songs']} · albom+sinql "
                f"{st['read']}/{st['total_albums']} · video {st['videos']} · feat {st['feat']} · "
                f"alınmayan {st['failed']})")
    if prog and prog.get("finish"):
        data["partial"] = True                            # ⏭ yarımçıq — keşə yazılmır
        return data
    _cache[ids] = (time.time(), data)
    while len(_cache) > 50:
        _cache.pop(next(iter(_cache)))
    return data


def depo_flags(items: list) -> list:
    """Hər mahnı depodadırmı (video ID və ya ad üzrə)."""
    from bot.core import audio_cache
    try:
        cached = audio_cache.cached_ids([it["vid"] for it in items])
    except Exception:
        cached = set()
    out = []
    for it in items:
        ok = it["vid"] in cached
        if not ok and it.get("meta"):
            try:
                ok = bool(audio_cache.find_same(it["meta"]["artist"], it["meta"]["track"], it["raw_duration"]))
            except Exception:
                ok = False
        out.append(ok)
    return out


# ═════════════════════ plugin ═════════════════════
def setup(context):
    dp = context.dp
    sessions = {}       # (chat_id, msg_id) → {"uid", "data", "ts"}
    loads = {}          # (chat_id, msg_id) → {"prog", "uid", "aid"} — hazırda oxunan ifaçı
    last_aid = {}       # (chat_id, msg_id) → (uid, aid) — xəta / timeout-dan sonra 🔄 üçün
    picks = {}          # (chat_id, msg_id) → {"uid", "found", "ts"}
    waiting = {}        # uid → ts (ad gözlənilir)

    def is_waiting(uid) -> bool:
        ts = waiting.get(uid)
        if ts and time.time() - ts > WAIT_TTL:
            waiting.pop(uid, None)
            return False
        return bool(ts)

    from bot.core.input_wait import chain_menu_text
    chain_menu_text(context, is_waiting)

    def purge():
        now = time.time()
        for d in (sessions, picks):
            for k in [k for k, v in d.items() if now - v["ts"] > SESSION_TTL]:
                d.pop(k, None)

    def premium_kb():
        return InlineKeyboardMarkup(inline_keyboard=[[btn(tr("💎 Premium al"), "sg:premium")]])

    # ───────────── görünüşlər ─────────────
    def card_view(s: dict, flags=None):
        d, uid = s["data"], s["uid"]
        items = d["items"]
        dur = sum(it["raw_duration"] for it in items)
        lines = [f"🎙 <b>{escape(d['name'])}</b>",
                 f"💿 {d['albums']} albom · 🎵 {d['singles']} sinql/EP"
                 + (f" · <i>ən çox {MAX_ALBUMS}-i oxundu</i>" if d.get("partial") else ""),
                 f"📊 <b>{len(items)}</b> mahnı · ⏳ {_fmt(dur)}"]
        st = d.get("stats") or {}
        src = [f"📃 siyahı {st.get('songs', 0)}", f"💿 {st.get('read', 0)} albom/sinql"]
        if st.get("videos"):
            src.append(f"🎬 {st['videos']} video")
        if st.get("feat"):
            src.append(f"🤝 {st['feat']} feat.")
        if st.get("profiles", 1) > 1:
            src.append(f"👥 {st['profiles']} profil birləşdi")
        if st.get("failed"):
            src.append(f"⚠️ {st['failed']} alınmadı")
        lines.append("<i>" + tr("Mənbələr") + ": " + " · ".join(src) + "</i>")
        if flags is not None:
            n = sum(flags)
            if n:
                lines.append(tr("📦 {n} mahnı artıq depoda — dərhal gəlir", n=n))
        if d["top"]:
            lines.append("\n🔥 <b>Top:</b>")
            lines += [f"{i}. {escape(it['title'][:55])}" for i, it in enumerate(d["top"][:5], 1)]
        rows = [[btn(tr("📋 Mahnılar ({n})", n=len(items)), "art:l:0")]]
        if d["top"]:
            rows[0].append(btn(tr("🔥 Top ({n})", n=len(d["top"])), "art:t:0"))
        if tiers.visible("dlall", uid):
            badge = " 💎" if tiers.locked("dlall", uid) else ""
            rows.append([btn(tr("⬇️ Hamısını yüklə ({n})", n=len(items)) + badge, "art:all")])
        if getattr(context, "playlist_import", None) and tiers.visible("playlists", uid) \
                and tiers.visible("playlist_import", uid):
            badge = " 💎" if tiers.locked("playlist_import", uid) else ""
            rows.append([btn(tr("💾 Playlist kimi saxla") + badge, "art:save")])
        rows.append([btn(tr("✖️ Bağla"), "art:x")])
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    def list_view(s: dict, mode: str, page: int, flags: list):
        d = s["data"]
        items = d["items"] if mode == "l" else d["top"]
        pages = max(1, -(-len(items) // PAGE))
        page = max(0, min(page, pages - 1))
        chunk = items[page * PAGE:(page + 1) * PAGE]
        head = (f"🎙 <b>{escape(d['name'])}</b> — " + (tr("bütün mahnılar") if mode == "l" else tr("top"))
                + f" · {page + 1}/{pages}\n" + tr("<i>Mahnıya toxun — yüklənir. 📦 = depoda var.</i>"))
        rows = []
        for i, (it, dep) in enumerate(zip(chunk, flags), page * PAGE):
            label = f"{'📦' if dep else '🎵'}{'🤝' if it.get('feat') else ''} {it['title']}"[:60]
            rows.append([btn(label, f"art:s:{mode}:{i}")])
        nav = pager(f"art:{mode}:", page, pages)
        if nav:
            rows.append(nav)
        rows.append([btn(tr("⬅️ Geri"), "art:card")])
        return head, InlineKeyboardMarkup(inline_keyboard=rows), page

    def running(msg) -> list:
        f = getattr(context, "music_batch_rows", None)
        return f(msg.chat.id, msg.message_id) if f and msg else []

    def err_text(e) -> str:
        if _is_net_error(e):
            return tr("❌ <b>YouTube Music cavab vermədi</b> — bağlantı qırıldı (şəbəkə / YouTube tərəfi). "
                      "Bir azdan yenidən yoxla.") + f"\n<i>{escape(str(e))[:120]}</i>"
        return f"❌ <code>{escape(str(e))[:200]}</code>"

    async def edit(msg, text, markup):
        rows = running(msg)                 # bu kartda toplu yükləmə gedir → 📊 Proses / ⏹ düymələri qalsın
        if rows:
            context.music_batch_alt(msg.chat.id, msg.message_id)
            markup = InlineKeyboardMarkup(inline_keyboard=list(markup.inline_keyboard if markup else []) + rows)
        await panel_show(msg, text, markup)

    # ───────────── axtarış ─────────────
    async def run_search(message: types.Message, name: str, status: types.Message = None):
        uid = message.from_user.id
        if not await asyncio.to_thread(tiers.allowed, "artist_search", uid):
            await message.reply(tiers.gate_text("artist_search", uid), parse_mode="HTML",
                                reply_markup=premium_kb() if tiers.get("artist_search", "premium") else None)
            return
        if YTMusic is None:
            await message.reply("❌ <code>pip install ytmusicapi</code> lazımdır.", parse_mode="HTML")
            return
        purge()
        status = status or await message.reply(tr("🔎 <i>İfaçı axtarılır...</i>"), parse_mode="HTML")
        try:
            found = await asyncio.to_thread(search_sync, name)
        except Exception as e:
            await edit(status, err_text(e), None)
            return
        if not found:
            await edit(status, tr("❌ <b>{q}</b> adlı ifaçı tapılmadı.", q=escape(name)), None)
            return
        exact = [f for f in found if _norm(f["name"]) == _norm(name)]
        if len(found) == 1 or len(exact) == 1:             # eyni adlı profillər artıq birləşib
            await open_artist(status, uid, (exact or found)[0]["ids"])
            return
        picks[(status.chat.id, status.message_id)] = {"uid": uid, "found": found, "ts": time.time()}
        rows = [[btn(f"🎙 {f['name']}" + (f" · {f['subs']}" if f["subs"] else "")
                     + (f" · 👥{len(f['ids'])}" if len(f["ids"]) > 1 else ""), f"art:p:{i}")]
                for i, f in enumerate(found)]
        rows.append([btn(tr("✖️ Bağla"), "art:x")])
        await edit(status, tr("🎙 <b>{q}</b> — hansı ifaçı?", q=escape(name)), InlineKeyboardMarkup(inline_keyboard=rows))

    STAGES = {"profile": "👤 İfaçı profili açılır", "songs": "📃 Mahnılar siyahısı oxunur",
              "album_list": "💿 Albomlar siyahısı alınır", "albums": "💿 Albomlar oxunur",
              "videos": "🎬 Videolar oxunur", "feat": "🤝 feat. mahnılar axtarılır"}

    def load_view(prog, started) -> tuple:
        now = time.time()
        idle = now - prog.get("ts", started)
        stage = STAGES.get(prog.get("stage"), "⏳ Başlanır")
        lines = [f"⏳ <b>{escape(prog.get('name') or tr('İfaçı'))}</b> — " + tr("yüklənir"), "",
                 f"{stage}..."]
        if prog.get("profiles", 1) > 1:
            lines.append("👥 " + tr("profil") + f" {prog.get('profile', 1)}/{prog['profiles']}")
        if prog.get("songs"):
            lines.append("📃 " + tr("siyahıda") + f" {prog['songs']} " + tr("mahnı"))
        if prog.get("a_total"):
            a, t = prog.get("a_done", 0), prog["a_total"]
            from common.formatting import progress_bar
            bar = progress_bar(a, t, 10)                      # UI zolaq üslubuna uyğun (/ui → bar)
            lines.append(f"💿 {bar} {a}/{t} · 🎵 {prog.get('tracks', 0)}")
        lines.append(f"⏱ {int(now - started)} " + tr("san."))
        rows = []
        if idle > STALL_AFTER:
            lines += ["", tr("⚠️ <b>{n} san.-dir irəliləyiş yoxdur — ilişib kimi görünür</b>", n=int(idle)),
                      tr("<i>YouTube Music cavab vermir. Yenidən başlat və ya toplananı göstər.</i>")]
            rows.append([btn(tr("🔄 Yenidən başlat"), "art:lr")])
        row = []
        if prog.get("stage") in ("albums", "videos", "feat") and (prog.get("songs") or prog.get("tracks")):
            row.append(btn(tr("⏭ Olanı göstər"), "art:lf"))
        row.append(btn(tr("✖️ Ləğv et"), "art:lc"))
        rows.append(row)
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    async def open_artist(status: types.Message, uid: int, aid):
        key = (status.chat.id, status.message_id)
        old = loads.get(key)
        if old:
            old["prog"]["cancel"] = True                       # 🔄 — köhnə oxuma dayansın
        prog = {"ts": time.time(), "stage": "profile"}
        started = time.time()
        loads[key] = {"prog": prog, "uid": uid, "aid": aid}
        last_aid[key] = (uid, aid)
        task = asyncio.ensure_future(asyncio.to_thread(load_sync, aid, prog))
        last = None
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=3)
                if task.done() or loads.get(key, {}).get("prog") is not prog:
                    break
                if time.time() - started > LOAD_LIMIT:
                    prog["cancel"] = True
                    break
                view = load_view(prog, started)
                if view[0] != last:
                    last = view[0]
                    await edit(status, *view)
            if loads.get(key, {}).get("prog") is not prog:
                return                                         # 🔄 ilə yenisi başladı
            if not task.done():
                await edit(status, tr("❌ <b>Çox uzun çəkdi</b> — YouTube Music cavab vermir."),
                           InlineKeyboardMarkup(inline_keyboard=[[btn(tr("🔄 Yenidən başlat"), "art:lr"),
                                                                  btn(tr("✖️ Bağla"), "art:x")]]))
                return
            data = task.result()
        except LoadCancelled:
            if loads.get(key, {}).get("prog") is prog:
                await edit(status, tr("✖️ Ləğv edildi."), None)
            return
        except Exception as e:
            await edit(status, err_text(e), InlineKeyboardMarkup(inline_keyboard=[[
                btn(tr("🔄 Yenidən başlat"), "art:lr"), btn(tr("✖️ Bağla"), "art:x")]]))
            return
        finally:
            if loads.get(key, {}).get("prog") is prog and task.done():
                loads.pop(key, None)
        if not data["items"]:
            await edit(status, tr("❌ Bu ifaçının mahnıları tapılmadı."), None)
            return
        s = {"uid": uid, "data": data, "ts": time.time()}
        sessions[(status.chat.id, status.message_id)] = s
        flags = await asyncio.to_thread(depo_flags, data["items"])
        await edit(status, *card_view(s, flags))

    @dp.message(Command("artist", "ifaci"))
    async def artist_cmd(message: types.Message, command: CommandObject):
        if not message.from_user:
            return
        name = (command.args or "").strip()
        if not name:
            if message.chat.type != "private":
                await message.reply(tr("✍️ İstifadə: <code>/artist ifaçının adı</code>"), parse_mode="HTML")
                return
            waiting[message.from_user.id] = time.time()
            await message.answer(tr("🎙 İfaçının adını yaz:"), reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[btn(tr("✖️ Ləğv et"), "art:cancel")]]))
            return
        await run_search(message, name[:80])

    @dp.message(F.chat.type == "private", F.text, ~F.text.startswith("/"),
                F.from_user.func(lambda u: is_waiting(u.id)))
    async def name_input(message: types.Message):
        waiting.pop(message.from_user.id, None)
        await run_search(message, message.text.strip()[:80])

    # ───────────── düymələr ─────────────
    @dp.callback_query(F.data.startswith("art:"))
    async def art_cb(cb: types.CallbackQuery):
        parts = cb.data.split(":")
        act = parts[1]
        uid = cb.from_user.id
        if act == "cancel":
            waiting.pop(uid, None)
            await cb.answer(tr("Ləğv edildi"))
            try:
                await cb.message.delete()
            except Exception as _e:
                logger.debug("udulan xəta [bot/plugins/artist_plugin.py:651]: %r", _e)
            return
        key = (cb.message.chat.id, cb.message.message_id)
        if act in ("lc", "lr", "lf"):                       # ⏳ oxuma zamanı: ləğv / yenidən / olanı göstər
            ld = loads.get(key)
            owner, aid = last_aid.get(key, (None, None))
            if uid not in (owner, context.creator_id):
                await cb.answer("⛔", show_alert=True)
                return
            if act == "lr":
                if aid is None:
                    await cb.answer(tr("Köhnəlib — /artist ilə yenidən axtar"), show_alert=True)
                    return
                await cb.answer(tr("🔄 Yenidən başlayır (yeni bağlantı)..."))
                await asyncio.to_thread(_fresh_client)
                await open_artist(cb.message, owner, aid)
                return
            if not ld:
                await cb.answer()
                return
            ld["prog"]["cancel" if act == "lc" else "finish"] = True
            await cb.answer(tr("✖️ Ləğv edilir...") if act == "lc" else tr("⏭ Toplanan göstərilir..."))
            return
        if act == "p":
            pk = picks.get(key)
            if not pk:
                await cb.answer(tr("Köhnəlib — yenidən axtar"), show_alert=True)
                return
            if uid not in (pk["uid"], context.creator_id):
                await cb.answer(tr("⛔ Yalnız axtaran seçə bilər"), show_alert=True)
                return
            picks.pop(key, None)
            await cb.answer()
            await open_artist(cb.message, pk["uid"], pk["found"][int(parts[2])]["ids"])
            return
        s = sessions.get(key)
        if act == "x":
            if s and uid not in (s["uid"], context.creator_id):
                await cb.answer("⛔", show_alert=True)
                return
            if running(cb.message):
                await cb.answer(tr("Əvvəl ⏹ Dayandır — yükləmə bu kartda gedir"), show_alert=True)
                return
            sessions.pop(key, None)
            picks.pop(key, None)
            await cb.answer()
            try:
                await cb.message.delete()
            except Exception as _e:
                logger.debug("udulan xəta [bot/plugins/artist_plugin.py:700]: %r", _e)
            return
        if not s:
            await cb.answer(tr("Köhnəlib — /artist ilə yenidən axtar"), show_alert=True)
            return
        if uid not in (s["uid"], context.creator_id):
            await cb.answer(tr("⛔ Yalnız axtaran seçə bilər"), show_alert=True)
            return
        s["ts"] = time.time()
        d = s["data"]

        if act == "card":
            await cb.answer()
            flags = await asyncio.to_thread(depo_flags, d["items"])
            await edit(cb.message, *card_view(s, flags))
            return

        if act in ("l", "t"):
            items = d["items"] if act == "l" else d["top"]
            page = int(parts[2])
            pages = max(1, -(-len(items) // PAGE))
            page = max(0, min(page, pages - 1))
            flags = await asyncio.to_thread(depo_flags, items[page * PAGE:(page + 1) * PAGE])
            text, markup, _ = list_view(s, act, page, flags)
            await cb.answer()
            await edit(cb.message, text, markup)
            return

        run_batch = getattr(context, "music_run_batch", None)
        if act == "s":
            items = d["items"] if parts[2] == "l" else d["top"]
            try:
                item = items[int(parts[3])]
            except (IndexError, ValueError):
                await cb.answer()
                return
            if not run_batch:
                await cb.answer(tr("Yükləmə modulu yüklənməyib"), show_alert=True)
                return
            await cb.answer("⏳ " + item["title"][:60])
            status = await cb.message.answer("⏳", parse_mode="HTML")
            try:
                await run_batch(status, [dict(item)], item["title"], s["uid"], "artist", single=True)
            except Exception as e:
                logger.warning(f"Artist tək mahnı yükləmə xətası: {e}")
            finally:
                with contextlib.suppress(Exception):
                    await status.delete()
            return

        if act == "all":
            if running(cb.message):
                await cb.answer(tr("Yükləmə artıq gedir — 📊 Proses"), show_alert=True)
                return
            if not await asyncio.to_thread(tiers.allowed, "dlall", uid):
                await cb.answer(tr("💎 Hamısını yükləmək Premium üçündür"))
                await cb.message.answer(tiers.gate_text("dlall", uid), parse_mode="HTML", reply_markup=premium_kb())
                return
            busy = getattr(context, "music_batch_busy", None)
            if busy and busy(uid):
                await cb.answer(tr("Artıq bir toplu yükləmə gedir — bitməsini gözlə və ya ⏹ Dayandır"),
                                show_alert=True)
                return
            if not run_batch:
                await cb.answer(tr("Yükləmə modulu yüklənməyib"), show_alert=True)
                return
            await cb.answer(tr("⬇️ {n} mahnı yüklənir...", n=len(d["items"])))
            # eyni mesajda: 📊 Proses ⇄ 🎙 Kart keçidi (yükləmə arxada davam edir)
            try:
                await run_batch(cb.message, [dict(it) for it in d["items"]], f"🎙 {d['name']}", s["uid"], "artist",
                                alt_view=lambda: card_view(s), alt_label=tr("🎙 Kart"))
            except Exception as e:
                logger.warning(f"Artist toplu yükləmə xətası: {e}")
            return

        if act == "save":
            imp = getattr(context, "playlist_import", None)
            if not imp:
                await cb.answer()
                return
            if s.get("saved"):
                await cb.answer(tr("ℹ️ Artıq saxlanılıb — /playlist"), show_alert=True)
                return
            await cb.answer(tr("💾 Saxlanılır..."))
            ok, text = await imp(uid, f"🎙 {d['name']}"[:48], [dict(it) for it in d["items"]])
            if ok:
                s["saved"] = True
            await cb.message.answer(text, parse_mode="HTML", disable_web_page_preview=True)
            return

        await cb.answer()

    logger.info("🎙 İfaçı axtarışı yükləndi (/artist)")
