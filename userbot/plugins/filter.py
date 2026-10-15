"""🎯 .filter — açar sözə və ya cümləyə görə avtomatik cavab (Lokal və Qlobal dəstəkli).

  .filter <söz/cümlə> <cavab>      — bu çatda yeni filtr (başqasına reply olanda cavab vermir)
  .filter -nr <söz> <cavab>        — 🚫 tam reply-sız (heç bir reply-da cavab vermir)
  .filter -g <söz/cümlə> <cavab>   — 🌐 Qlobal filtr (bütün qruplarda və çatlarda aktiv olur)
  .filters                         — filtrlər kartı (🌐 Qlobal və 💬 Bu çat tabları)
  .stopfilter <söz/cümlə>          — filtri sil (lokal və ya qlobal)
  .clearfilters                    — bu çatdakı BÜTÜN filtrləri sil
  .copyfilters <global|@qrup|id>   — başqa qrupun və ya qlobal bazanın filtrlərini bu çata kopyala
  .tagcavab                        — 🔔 Tağ cavabı kartı
  .fsleep                          — 😴 Anti-spam (yuxu) ayarları kartı
  .fwake <reply|@user|id|all>      — ⏰ yatırılmış istifadəçini bu çatda oyat

😴 Yuxu (anti-spam): eyni adam eyni filtri təkrar-təkrar işlədəndə cavab verilmir (təkrar fasiləsi);
qısa müddətdə limitdən çox filtr işlətsə, bu çatda filtrlər ondan müəyyən müddət "yatır".

Bayraqlar (.filter-dən sonra, istənilən sırada):
  -g qlobal · -l lokal
  -e tam uyğun · -c içində
  -a başqasına reply-sız · -nr tam reply-sız · -r mənə reply · -any hər vəziyyətdə
"""
import asyncio
import atexit
import contextlib
import json
import threading
from collections import deque
import re
import time
import unicodedata

from telethon import events
from telethon.tl.types import (
    DocumentAttributeSticker,
    InputDocument,
    InputStickerSetID,
    MessageEntityMention,
    MessageEntityMentionName,
)
from userbot.core.userbot_api import Out, ScopedList, ff, get_db, logger
from userbot.core.userbot_db import get_udb
from common.utilities import truncate_text
from common.formatting import fmt_duration_words


DONE_VISIBLE = 4
NS = "filters"
DB_KEY = "userbot:filters"
DEF_KEY = "userbot:filters_def"
PAGE_SIZE = 8
GLOBAL_CHAT = 0

MODES = {"exact": "🎯 Tam uyğun", "contains": "🔍 İçində"}
SCOPES = {
    "all": "👥 Başqasına reply-sız",
    "noreply": "🚫 Tam reply-sız",
    "reply": "↩️ Mənə reply",
    "any": "💬 Hər vəziyyətdə",
}
MODE_ICON = {"exact": "🎯", "contains": "🔍"}
SCOPE_ICON = {"all": "👥", "noreply": "🚫", "reply": "↩️", "any": "💬"}
FLAGS = {
    "-e": ("m", "exact"),
    "-c": ("m", "contains"),
    "-r": ("s", "reply"),
    "-a": ("s", "all"),
    "-nr": ("s", "noreply"),
    "-any": ("s", "any"),
    "-g": ("g", "global"),
    "-l": ("g", "local"),
}

_FILTERS = {}
_SAVED = {}          # (chat_id, açar) -> JSON — bazadakı son vəziyyət (inkremental yazma üçün)
_RX = {}
_WAITING_ADD = {}
_DEF = {"m": "exact", "s": "all", "g": "local"}
_WAITING_STICKER = {}
_WAITING_FILTER_VAL = {}

MENTION_NS = "mention"
REACTIONS = ["👍", "❤️", "🔥", "😁", "🤔", "👀", "🥰", "👏", "😎", "🙏", "💯", "🤝"]
MENTION_DEF = {"on": False, "st": None, "react": "👍", "cd": 60, "rep": False}
_MENTION_LAST = {}

# ───────────────────────── 😴 yuxu (anti-spam) ─────────────────────────
SLEEP_KEY = "antispam"
SLEEP_DEF = {"on": True, "cd": 30, "burst": 4, "win": 60, "dur": 600, "note": True}
_SL = dict(SLEEP_DEF)
_SLEEP = {}          # (chat_id, uid) -> (until, ad)
_HITS = {}           # (chat_id, uid) -> deque[ts]
_LAST_KW = {}        # (chat_id, uid, kw) -> ts


# ───────────────────────── saxlama ─────────────────────────
def _entry(v) -> dict:
    if isinstance(v, dict):
        e = {
            "r": str(v.get("r", "")),
            "m": v.get("m") if v.get("m") in MODES or v.get("m") == "sticker" else "contains",
            "s": v.get("s") if v.get("s") in SCOPES else "all",
        }
        if v.get("st"):
            e["st"] = v["st"]
        if v.get("trig"):
            e["trig"] = v["trig"]
        return e
    return {"r": str(v), "m": "contains", "s": "all"}


def _snap(f) -> str:
    return json.dumps(f, sort_keys=True, ensure_ascii=False)


def _load():
    _FILTERS.clear()
    _SAVED.clear()
    udb = get_udb()
    try:
        rows = udb.rec_list(NS)
        # Köhnə JSON-dan köçürmə YALNIZ bir dəfə: əks halda bütün filtrləri silib restart edəndə
        # köhnə bazadakı filtrlər "dirilirdi".
        migrated = bool((udb.kv_get(NS, "migrated") or {}).get("at"))
        if not rows and not migrated:
            raw = get_db().get_setting(DB_KEY)
            data = json.loads(raw) if raw else {}
            rows = [
                (int(cid), str(k), _entry(v))
                for cid, fl in data.items()
                if isinstance(fl, dict)
                for k, v in fl.items()
            ]
            if rows:
                udb.rec_replace(NS, rows)
                logger.info(f"🗄 .filter: {len(rows)} filtr userbot bazasına köçürüldü")
        if not migrated:
            udb.kv_set(NS, "migrated", {"at": int(time.time())})
        for cid, kw, f in rows:
            e = _entry(f)
            _FILTERS.setdefault(int(cid), {})[kw] = e
            _SAVED[(int(cid), kw)] = _snap(e)
    except Exception as e:
        logger.warning(f".filter yüklənmədi: {e}")
    d = udb.kv_get(NS, "defaults") or {}
    if not d:
        try:
            d = json.loads(get_db().get_setting(DEF_KEY) or "{}")
        except Exception:
            d = {}
    if d.get("m") in MODES:
        _DEF["m"] = d["m"]
    if d.get("s") in SCOPES:
        _DEF["s"] = d["s"]
    if d.get("g") in ("global", "local"):
        _DEF["g"] = d["g"]


_save_lock = threading.Lock()
_save_pending = False

# 🔇 Yaza bilmədiyimiz çatlar (qadağa / mute / yalnız adminlər yazır): cavab cəhdi hər mesajda təkrar xəta verirdi.
# İlk xətadan sonra çat NOWRITE_TTL saniyə susdurulur (vaxt bitəndə yenidən yoxlanır — icazə qayıda bilər).
NOWRITE_TTL = 6 * 3600
_NOWRITE: dict = {}
_NOWRITE_MARKERS = ("can't write in this chat", "cannot write", "chatwriteforbidden", "chat_write_forbidden",
                    "chat_send_plain_forbidden", "chat_send_text_forbidden", "userbannedinchannel",
                    "you're banned", "you are banned", "chat_guest_send_forbidden", "chatadminrequired")


def _mark_nowrite(chat_id, exc) -> bool:
    """Xəta «yaza bilmirəm» növündədirsə çatı susdurur və True qaytarır."""
    text = f"{type(exc).__name__} {exc}".lower()
    if not any(m in text for m in _NOWRITE_MARKERS):
        return False
    if _NOWRITE.get(chat_id, 0) <= time.time():
        logger.info(f"🔇 filter: {chat_id} çatında yaza bilmirəm — {NOWRITE_TTL // 3600} saat susdurulur")
    _NOWRITE[chat_id] = time.time() + NOWRITE_TTL
    return True


def _save():
    """Filtrləri saxlayır. Event loop daxilindən çağırılanda yazı arxa thread-də (≈0.3 san. birləşdirilmiş) gedir —
    SQLite commit-i loop-u dondurmasın; loop yoxdursa dərhal."""
    global _save_pending
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _save_now()
        return
    if _save_pending:
        return
    _save_pending = True

    def _run():
        global _save_pending
        _save_pending = False
        _save_now()

    loop.call_later(0.3, lambda: loop.run_in_executor(None, _run))


def _save_now():
    """Yalnız dəyişən filtrləri yazır (əvvəl hər dəfə bütün cədvəl silinib yenidən yazılırdı)."""
    with _save_lock:
        _save_locked()


atexit.register(lambda: _save_pending and _save_now())


def _save_locked():
    udb = get_udb()
    for _try in range(3):                               # thread-də oxuyarkən loop dict-i dəyişə bilər
        try:
            cur = {(int(c), kw): f for c, fl in list(_FILTERS.items()) for kw, f in list(fl.items())}
            break
        except RuntimeError:
            continue
    else:
        return
    try:
        for key in [k for k in _SAVED if k not in cur]:
            udb.rec_del(NS, key[0], key[1])
            _SAVED.pop(key, None)
        for key, f in cur.items():
            s = _snap(f)
            if _SAVED.get(key) != s:
                udb.rec_put(NS, key[0], key[1], f)
                _SAVED[key] = s
    except Exception as e:
        logger.warning(f".filter inkremental saxlanmadı ({e}) — tam yazılır")
        try:
            udb.rec_replace(NS, [(c, kw, f) for (c, kw), f in cur.items()])
            _SAVED.clear()
            _SAVED.update({k: _snap(f) for k, f in cur.items()})
        except Exception as e2:
            logger.warning(f".filter saxlanmadı: {e2}")


def _save_def():
    try:
        get_udb().kv_set(NS, "defaults", dict(_DEF))
    except Exception as e:
        logger.warning(f".filter standartları saxlanmadı: {e}")


# ───────────────────────── mətn ─────────────────────────
def _norm(text: str) -> str:
    t = unicodedata.normalize("NFC", text or "").replace("İ", "i").replace("I", "ı").lower()
    t = t.replace("\u0307", "")
    return " ".join(t.split())


def _strip_edges(t: str) -> str:
    return re.sub(r"^[\W_]+|[\W_]+$", "", t)


def _rx(keyword):
    r = _RX.get(keyword)
    if r is None:
        words = keyword.split()
        pattern = r"\s+".join(re.escape(w) for w in words) if words else re.escape(keyword)
        r = _RX[keyword] = re.compile(rf"(?<!\w){pattern}(?!\w)", re.IGNORECASE)
    return r


def _match(kw: str, f: dict, norm_text: str, bare: str) -> bool:
    if f["m"] == "exact":
        return bare == _strip_edges(kw)
    return bool(_rx(kw).search(norm_text))


def _pick(fl: dict, text: str, is_reply: bool, is_reply_to_me: bool):
    norm_text = _norm(text)
    bare = _strip_edges(norm_text)
    best, best_key = None, None
    for kw, f in fl.items():
        if f["m"] == "sticker":
            continue
        s = f.get("s", "all")
        # 1. Yalnız mənə reply rejimi
        if s == "reply" and not is_reply_to_me:
            continue
        # 2. Tam reply-sız rejim (heç bir reply-da cavab vermir)
        if s == "noreply" and is_reply:
            continue
        # 3. Başqasına reply-sız rejim (kimsə başqasına reply edibsə cavab vermir)
        if s == "all" and (is_reply and not is_reply_to_me):
            continue

        if not _match(kw, f, norm_text, bare):
            continue
        key = (1 if f["m"] == "exact" else 0, len(kw.split()), len(kw))
        if best_key is None or key > best_key:
            best, best_key = (kw, f), key
    return best


def _pick_sticker(fl: dict, doc_id: int, is_reply: bool, is_reply_to_me: bool):
    for kw, f in fl.items():
        if f["m"] != "sticker":
            continue
        if (f.get("trig") or {}).get("doc") != doc_id:
            continue
        s = f.get("s", "all")
        if s == "reply" and not is_reply_to_me:
            continue
        if s == "noreply" and is_reply:
            continue
        if s == "all" and (is_reply and not is_reply_to_me):
            continue
        return kw, f
    return None


# ───────────────────────── 🖼 stikerlər ─────────────────────────
def _sticker_attr(doc):
    return next(
        (a for a in getattr(doc, "attributes", []) or [] if isinstance(a, DocumentAttributeSticker)),
        None,
    )


def sticker_ref(msg, src=None) -> dict:
    doc = msg.document
    attr = _sticker_attr(doc)
    sset = getattr(attr, "stickerset", None)
    return {
        "id": doc.id,
        "ah": doc.access_hash,
        "fr": (doc.file_reference or b"").hex(),
        "src": src,
        "set": [sset.id, sset.access_hash] if isinstance(sset, InputStickerSetID) else None,
        "emoji": getattr(attr, "alt", "") or "🖼",
    }


async def capture_sticker(client, msg) -> dict:
    src = None
    try:
        saved = await client.send_file("me", msg.document, silent=True)
        src = ["me", saved.id]
        msg = saved
    except Exception as e:
        logger.debug(f"stiker Saxlanılanlara göndərilmədi: {e}")
        src = [msg.chat_id, msg.id]
    return sticker_ref(msg, src)


async def _refresh_ref(client, ref) -> bool:
    try:
        if ref.get("src"):
            chat, mid = ref["src"]
            m = await client.get_messages(chat, ids=mid)
            if m and m.document and m.document.id == ref["id"]:
                ref.update(ah=m.document.access_hash, fr=(m.document.file_reference or b"").hex())
                return True
        if ref.get("set"):
            from telethon.tl.functions.messages import GetStickerSetRequest

            res = await client(GetStickerSetRequest(InputStickerSetID(*ref["set"]), hash=0))
            for d in res.documents:
                if d.id == ref["id"]:
                    ref.update(ah=d.access_hash, fr=(d.file_reference or b"").hex())
                    return True
    except Exception as e:
        logger.debug(f"stiker təzələnmədi: {e}")
    return False


async def send_sticker(client, chat_id, ref, reply_to=None):
    changed = False
    for attempt in (1, 2):
        try:
            await client.send_file(
                chat_id,
                InputDocument(ref["id"], ref["ah"], bytes.fromhex(ref["fr"] or "")),
                reply_to=reply_to,
            )
            return True, changed
        except Exception as e:
            err = str(e).upper()
            if attempt == 1 and "FILE_REFERENCE" in err and await _refresh_ref(client, ref):
                changed = True
                continue
            logger.info(f"stiker göndərilmədi ({chat_id}): {e}")
            return False, changed
    return False, changed


async def send_reaction(client, chat_id, msg_id, emoji) -> bool:
    from telethon.tl.functions.messages import SendReactionRequest
    from telethon.tl.types import ReactionEmoji

    try:
        await client(
            SendReactionRequest(peer=chat_id, msg_id=msg_id, reaction=[ReactionEmoji(emoticon=emoji)])
        )
        return True
    except TypeError:
        try:
            await client(SendReactionRequest(peer=chat_id, msg_id=msg_id, reaction=emoji))
            return True
        except Exception as e:
            logger.info(f"reaksiya qoyulmadı: {e}")
    except Exception as e:
        logger.info(f"reaksiya qoyulmadı ({chat_id}): {e}")
    return False


# ───────────────────────── 🔔 tağ cavabı ayarları ─────────────────────────
def mention_cfg(chat_id=None, effective=True):
    udb = get_udb()
    if chat_id is not None:
        c = udb.rec_get(MENTION_NS, chat_id, "cfg")
        if c is not None or not effective:
            return dict(MENTION_DEF, **(c or {})) if c is not None else None
    return dict(MENTION_DEF, **(udb.kv_get(MENTION_NS, "global") or {}))


def mention_save(target, cfg):
    udb = get_udb()
    if target == "global":
        udb.kv_set(MENTION_NS, "global", cfg)
    elif cfg is None:
        udb.rec_del(MENTION_NS, target, "cfg")
    else:
        udb.rec_put(MENTION_NS, target, "cfg", cfg)


def _is_tag(msg, me, include_reply: bool) -> bool:
    if not getattr(msg, "mentioned", False):
        return False
    names = {
        u.lower()
        for u in [getattr(me, "username", None)]
        + [getattr(x, "username", None) for x in (getattr(me, "usernames", None) or [])]
        if u
    }
    try:
        for ent, txt in msg.get_entities_text():
            if isinstance(ent, MessageEntityMentionName) and ent.user_id == me.id:
                return True
            if isinstance(ent, MessageEntityMention) and txt.lstrip("@").lower() in names:
                return True
    except Exception as _e:
        logger.debug("udulan xəta [userbot/plugins/filter.py:427]: %r", _e)
    return include_reply and bool(msg.reply_to)


# ───────────────────────── 😴 yuxu köməkçiləri ─────────────────────────
def _span(sec) -> str:
    return fmt_duration_words(sec) if int(sec) > 0 else "yox"


def _load_sleep():
    try:
        d = get_udb().kv_get(NS, SLEEP_KEY) or {}
    except Exception:
        d = {}
    for k, v in d.items():
        if k in SLEEP_DEF and type(v) is type(SLEEP_DEF[k]):
            _SL[k] = v


def _save_sleep():
    try:
        get_udb().kv_set(NS, SLEEP_KEY, dict(_SL))
    except Exception as e:
        logger.warning(f".filter yuxu ayarları saxlanmadı: {e}")


def _sleep_left(chat_id, uid) -> float:
    rec = _SLEEP.get((chat_id, uid))
    if not rec:
        return 0
    left = rec[0] - time.time()
    if left <= 0:
        _SLEEP.pop((chat_id, uid), None)
        return 0
    return left


def _sleepers(chat_id) -> list:
    """Bu çatda hazırda yatanlar: [(uid, ad, qalan_san), ...]"""
    out = []
    for (cid, uid), (until, name) in list(_SLEEP.items()):
        if cid != chat_id:
            continue
        left = until - time.time()
        if left <= 0:
            _SLEEP.pop((cid, uid), None)
        else:
            out.append((uid, name, left))
    return sorted(out, key=lambda x: -x[2])


def _wake(chat_id, uid=None) -> int:
    keys = [k for k in _SLEEP if k[0] == chat_id and (uid is None or k[1] == uid)]
    for k in keys:
        _SLEEP.pop(k, None)
        _HITS.pop(k, None)
    return len(keys)


def _gate(chat_id, uid, kw) -> str:
    """Filtr işə düşəndə: "ok" cavab ver · "cd" təkrar fasiləsi · "sleep" yatır · "fell" indi yuxuya getdi."""
    if not _SL["on"] or not uid:
        return "ok"
    if _sleep_left(chat_id, uid):
        return "sleep"
    now = time.time()
    key = (chat_id, uid)
    q = _HITS.setdefault(key, deque())
    while q and now - q[0] > _SL["win"]:
        q.popleft()
    q.append(now)                                       # cd-yə düşən cəhdlər də sayılır — zorlama budur
    if len(q) > _SL["burst"]:
        q.clear()
        _SLEEP[key] = (now + _SL["dur"], None)          # ad sonra handler-də əlavə olunur
        return "fell"
    lk = (chat_id, uid, kw)
    if _SL["cd"] and now - _LAST_KW.get(lk, 0) < _SL["cd"]:
        return "cd"
    _LAST_KW[lk] = now
    if len(_LAST_KW) > 5000:                            # yaddaş təmizliyi
        for k in [k for k, t in _LAST_KW.items() if now - t > 3600]:
            _LAST_KW.pop(k, None)
        for k in [k for k, d in _HITS.items() if not d or now - d[-1] > 3600]:
            _HITS.pop(k, None)
    return "ok"


# ───────────────────────── əmr arqumentləri ─────────────────────────
def _take_flags(text: str):
    opts = {}
    while True:
        m = re.match(r"^(-[ecragl]|-nr|-any)(?:\s+|$)", text)
        if not m:
            return text, opts
        k, v = FLAGS[m.group(1)]
        opts[k] = v
        text = text[m.end():]


def _parse_filter_args(raw_text):
    if not raw_text:
        return None, None
    text = raw_text.strip()
    if text.startswith(".filter"):
        text = text[7:].strip()
    if not text:
        return None, None

    m = re.match(r'''^["'“]([^"'”]+)["'”](?:\s+([\s\S]+))?$''', text)
    if m:
        kw = m.group(1).strip()
        rep = (m.group(2) or "").strip()
        rep_m = re.match(r'''^["'“]([\s\S]*?)["'”]$''', rep)
        if rep_m:
            rep = rep_m.group(1)
        return kw, rep

    parts = text.split(None, 1)
    kw = parts[0].strip()
    rep = parts[1].strip() if len(parts) > 1 else ""
    if rep:
        rep_m = re.match(r'''^["'“]([\s\S]*?)["'”]$''', rep)
        if rep_m:
            rep = rep_m.group(1)
    return kw, rep


def _preview(text, n=60):
    return truncate_text(text, n, normalize_spaces=True)



def _tags(f, is_global=False) -> str:
    m = "🖼" if f["m"] == "sticker" else MODE_ICON[f["m"]]
    glob = "🌐" if is_global else ""
    return f"{glob}{m}{SCOPE_ICON.get(f.get('s', 'all'), '')}" + ("🎴" if f.get("st") else "")


def _label(kw, f) -> str:
    if f["m"] == "sticker":
        return f"stiker {(f.get('trig') or {}).get('emoji', '🖼')}"
    return kw


def _answer_preview(f, n=36) -> str:
    parts = []
    if f.get("st"):
        parts.append(f"🎴 stiker {f['st'].get('emoji', '')}")
    if f.get("r"):
        parts.append(_preview(f["r"], n))
    return " + ".join(parts) or "—"


def _put(chat_id, keyword, reply_text, opts=None, st=None, trig=None) -> tuple:
    kw = f"🖼{trig['doc']}" if trig else _norm(keyword).strip()
    fl = _FILTERS.setdefault(chat_id, {})
    old = fl.get(kw)
    opts = opts or {}
    fl[kw] = {
        "r": (reply_text or "").strip(),
        "m": "sticker"
        if trig
        else (opts.get("m") or (old["m"] if old and old["m"] != "sticker" else _DEF["m"])),
        "s": opts.get("s") or (old["s"] if old else _DEF["s"]),
    }
    if st or (old and old.get("st") and not reply_text):
        fl[kw]["st"] = st or old["st"]
    if trig:
        fl[kw]["trig"] = trig
    _RX.pop(kw, None)
    _save()
    return kw, old is not None


def _clear_chat(chat_id) -> int:
    fl = _FILTERS.pop(chat_id, {}) or {}
    for kw in fl:
        _RX.pop(kw, None)
    if fl:
        _save()
    return len(fl)


def register(ub):
    _load()
    _load_sleep()

    def card(rows, footer=None):
        return ff(ub.title("filter"), rows, footer=footer)

    def get_view_cid(out, chat_id):
        return out.scoped.cid(out)

    def list_body(chat_id, out):
        sl = out.scoped
        is_glob = sl.is_global(out)
        target_cid = sl.cid(out)
        fl = _FILTERS.get(target_cid, {})
        n = sl.pages(out)
        page = sl.clamp(out)

        c_name = "🌐 Qlobal (bütün çatlarda aktiv)" if is_glob else f"💬 Bu çat ({chat_id})"
        g_count = len(_FILTERS.get(GLOBAL_CHAT, {}))
        l_count = len(_FILTERS.get(chat_id, {}))

        body = [
            ("Görünüş", c_name),
            ("Filtrlər", f"💬 Bu çat: {l_count} · 🌐 Qlobal: {g_count}"),
            ("Yeni standart", f"{MODES[_DEF['m']]} · {SCOPES[_DEF['s']]}"),
        ]
        if fl:
            body.append(
                f"# Siyahı ({'Qlobal' if is_glob else 'Lokal'})"
                + (f" — {page + 1}/{n}" if n > 1 else "")
            )
            body += [
                (
                    f"{i}. {_tags(f, is_global=is_glob)}",
                    f"{_label(kw, f)} → {_answer_preview(f)}",
                )
                for i, (kw, f) in enumerate(sl.window(out, fl.items()), page * PAGE_SIZE + 1)
            ]
            body.append("# İşarələr")
            body.append(("🎯 / 🔍 / 🖼", "tam uyğun / içində / stiker"))
            body.append(("👥 / 🚫 / ↩️", "başqasına reply-sız / tam reply-sız / mənə reply"))
            body.append(("🌐", "qlobal filtr (bütün qruplarda işləyir)"))

        sl_n = len(_sleepers(chat_id))
        body.append("# 😴 Yuxu (anti-spam)")
        body.append(
            (
                "Vəziyyət",
                (f"🟢 {_SL['burst']} / {_span(_SL['win'])} · yuxu {_span(_SL['dur'])}" if _SL["on"] else "🔴 söndürülüb")
                + (f" · yatan: {sl_n}" if sl_n else ""),
            )
        )

        mc = mention_cfg(chat_id)
        body.append("# 🔔 Tağ cavabı")
        body.append(
            (
                "Bu çatda",
                ("🟢 " if mc["on"] else "🔴 ")
                + ("çat ayarı" if mention_cfg(chat_id, False) else "qlobal")
                + (f" · 🎴 {mc['st'].get('emoji', '')}" if mc.get("st") else "")
                + f" · {mc['react']}",
            )
        )
        return body

    def list_rows(out, chat_id):
        sl = out.scoped
        is_glob = sl.is_global(out)
        target_cid = sl.cid(out)
        page = sl.clamp(out)
        kws = list(_FILTERS.get(target_cid, {}))
        btns = []
        for i, k in enumerate(sl.window(out, kws), page * PAGE_SIZE):
            sl.ensure(i, out.register_index)
            f = _FILTERS[target_cid][k]
            btns.append(out.btn(f"{i + 1}. {_tags(f, is_global=is_glob)} {_label(k, f)[:12]}", f"o{i}"))
        grid = sl.grid(btns)
        pager = sl.pager_row(out)
        if pager:
            grid.append(pager)
        grid.append(sl.tab_row(out))
        grid.append(
            [
                out.btn(f"➕ {'Qlobal' if is_glob else 'Lokal'} filtr", "add_fl"),
                out.btn("⚙️ Standart", "defs"),
            ]
        )
        grid.append([out.btn("🔔 Tağ cavabı", "men"), out.btn("😴 Yuxu", "slp")])
        if kws:
            grid.append([out.btn(f"🧹 Təmizlə ({len(kws)})", "clr")])
        return grid

    def foot(chat_id, out, extra=None):
        if extra:
            return extra
        return (
            "filtrə bas → rejim / Qlobal et / sil | ➕ əlavə et"
            if _FILTERS.get(get_view_cid(out, chat_id))
            else f"Bu bölmədə filtr yoxdur. Əlavə: .filter {'-g ' if out.scoped.is_global(out) else ''}<söz> <cavab>"
        )

    def list_view(out, chat_id, note=None):
        rows = list_rows(out, chat_id)                       # əvvəl düymələr: səhifəni sığdırır
        return card(list_body(chat_id, out), footer=foot(chat_id, out, note)), rows

    def detail_body(kw, f, is_global):
        when = {
            "exact": "mesaj yalnız bu söz/cümlədir",
            "contains": "söz/cümlə mesajın içində keçir",
            "sticker": "kimsə bu stikeri göndərir",
        }[f["m"]]
        scope_desc = {
            "all": "başqasına reply edilmədikdə (birbaşa yazıldıqda və ya mənə reply olanda)",
            "noreply": "yalnız tək yazıldıqda (heç bir reply olmadan)",
            "reply": "yalnız mənim mesajıma reply edildikdə",
            "any": "hər vəziyyətdə (hətta başqasına reply olsa belə)",
        }.get(f.get("s", "all"), "—")
        return [
            ("Filtr", _label(kw, f)),
            ("Əhatə", "🌐 Qlobal (bütün çatlarda aktiv)" if is_global else "💬 Yalnız bu çat"),
            ("Cavab", _answer_preview(f, 80)),
            ("Uyğunluq", "🖼 Stiker" if f["m"] == "sticker" else MODES[f["m"]]),
            ("Kimə", SCOPES.get(f.get("s", "all"), "—")),
            ("# Nə vaxt cavab verir", ""),
            ("", f"{when} və {scope_desc}"),
        ]

    def detail_rows(out, i, f, is_global):
        rows = []
        if f["m"] != "sticker":
            rows.append(
                [
                    out.btn(("✅ " if f["m"] == "exact" else "") + out.tr("🎯 Tam uyğun"), f"me{i}"),
                    out.btn(("✅ " if f["m"] == "contains" else "") + out.tr("🔍 İçində"), f"mc{i}"),
                ]
            )

        cur_s = f.get("s", "all")
        rows.append(
            [
                out.btn(("✅ " if cur_s == "all" else "") + out.tr("👥 Başqasına reply-sız"), f"sa{i}"),
                out.btn(("✅ " if cur_s == "noreply" else "") + out.tr("🚫 Tam reply-sız"), f"sn{i}"),
            ]
        )
        rows.append(
            [
                out.btn(("✅ " if cur_s == "reply" else "") + out.tr("↩️ Mənə reply"), f"sr{i}"),
                out.btn(("✅ " if cur_s == "any" else "") + out.tr("💬 Hər vəziyyətdə"), f"sy{i}"),
            ]
        )

        if is_global:
            rows.append([out.btn(out.tr("💬 Yalnız bu çata keçir"), f"tgloc{i}")])
        else:
            rows.append([out.btn(out.tr("🌐 Bütün qruplarda aktiv et (Qlobal)"), f"tgglob{i}")])

        st_label = out.tr("🎴 Cavab stikeri (dəyiş)") if f.get("st") else out.tr("🎴 Cavab stikeri")
        rows.append(
            [out.btn(st_label, f"st{i}")]
            + ([out.btn(out.tr("🗑 Stikeri sil"), f"sx{i}")] if f.get("st") and f.get("r") else [])
        )
        rows.append([out.btn(out.tr("🗑 Sil"), f"d{i}"), out.btn(out.tr("⬅️ Siyahı"), "back")])
        return rows

    def defs_body():
        return [
            ("Rejim", "⚙️ Yeni filtrlərin standartı"),
            ("Uyğunluq", MODES[_DEF["m"]]),
            ("Kimə", SCOPES[_DEF["s"]]),
            ("Əhatə", "🌐 Qlobal" if _DEF.get("g") == "global" else "💬 Lokal (bu çat)"),
            ("# Qeyd", ""),
            ("", ".filter -g/-l/-e/-c/-nr/-r/-a bayraqları ilə hər dəfə seçmək də mümkündür"),
        ]

    def defs_rows(out):
        return [
            [
                out.btn(("✅ " if _DEF["m"] == "exact" else "") + "🎯 Tam uyğun", "dme"),
                out.btn(("✅ " if _DEF["m"] == "contains" else "") + "🔍 İçində", "dmc"),
            ],
            [
                out.btn(("✅ " if _DEF["s"] == "all" else "") + "👥 Başqasına reply-sız", "dsa"),
                out.btn(("✅ " if _DEF["s"] == "noreply" else "") + "🚫 Tam reply-sız", "dsn"),
            ],
            [
                out.btn(("✅ " if _DEF["s"] == "reply" else "") + "↩️ Mənə reply", "dsr"),
                out.btn(("✅ " if _DEF["s"] == "any" else "") + "💬 Hər vəziyyətdə", "dsy"),
            ],
            [
                out.btn(("✅ " if _DEF.get("g") == "local" else "") + "💬 Lokal standart", "dgl"),
                out.btn(("✅ " if _DEF.get("g") == "global" else "") + "🌐 Qlobal standart", "dgg"),
            ],
            [out.btn("⬅️ Siyahı", "back")],
        ]

    async def ask_answer(o, target_cid, keyword, opts, back, cancel_action="fcx", after_id=0):
        kw_view = _norm(keyword).strip()
        _WAITING_STICKER[target_cid] = {
            "o": o,
            "kind": "answer",
            "target": keyword,
            "opts": opts,
            "back": back,
            "after": after_id,
        }
        exists = kw_view in _FILTERS.get(target_cid, {})
        is_glob = target_cid == GLOBAL_CHAT
        await o.update(
            card(
                [
                    ("Filtr", kw_view),
                    ("Əhatə", "🌐 Qlobal (bütün çatlarda)" if is_glob else "💬 Bu çat"),
                    ("Uyğunluq", MODES[opts.get("m") or _DEF["m"]]),
                    ("Kimə", SCOPES[opts.get("s") or _DEF["s"]]),
                    ("# Cavab nə olsun?", ""),
                    ("✍️ Mətn", "cavabı bu çata yaz"),
                    ("🎴 Stiker", "və ya istədiyin stikeri bu çata göndər"),
                ]
                + ([("Qeyd", "bu filtr artıq var — cavabı yenilənəcək")] if exists else []),
                footer="ləğv: 'imtina' yaz və ya düyməyə bas",
            ),
            rows=[[o.btn("✖️ Ləğv et", cancel_action)]],
        )

    # ── 🔔 Tağ cavabı bağlayıcı ──
    def attach_mention(out, chat_id, back):
        state = {"target": "chat"}

        def mention_view(c_id, target):
            g = mention_cfg(None)
            c = mention_cfg(c_id, effective=False)
            cur = g if target == "global" else (c or dict(g))
            eff = mention_cfg(c_id)

            def line(cfg):
                if cfg is None:
                    return "— (qlobal işləyir)"
                return (
                    ("🟢 açıq" if cfg["on"] else "🔴 söndürülüb")
                    + (f" · 🎴 {cfg['st'].get('emoji', '')}" if cfg.get("st") else " · stiker yox")
                    + f" · {cfg['react']}"
                )

            body = [
                ("Nə edir", "məni @tağ edənə stiker (reply); alınmasa — reaksiya"),
                ("🌐 Qlobal", line(g)),
                ("💬 Bu çat", line(c)),
                ("İndi bu çatda", "🟢 işləyir" if eff["on"] else "🔴 işləmir"),
                ("# Redaktə: " + ("🌐 Qlobal" if target == "global" else "💬 Bu çat"), ""),
                ("Vəziyyət", "🟢 açıq" if cur["on"] else "🔴 söndürülüb"),
                ("Stiker", f"🎴 {cur['st'].get('emoji', '')}" if cur.get("st") else "yoxdur"),
                ("Reaksiya", cur["react"]),
                ("Təkrar", f"{cur['cd']} san." if cur["cd"] else "hər dəfə"),
                ("Reply də", "✅ bəli" if cur["rep"] else "❌ yalnız @tağ"),
            ]
            return body, cur

        def mention_rows(o, c_id, target, cur):
            st_row = [o.btn("🎴 Stiker seç" + (" (dəyiş)" if cur.get("st") else ""), "mst")]
            if cur.get("st"):
                st_row.append(o.btn("🗑 Sil", "msx"))

            rows = [
                [
                    o.btn(("✅ " if target == "global" else "") + "🌐 Qlobal", "mtg"),
                    o.btn(("✅ " if target != "global" else "") + "💬 Bu çat", "mtc"),
                ],
                [o.btn("🏷 Tağ cavabı: " + ("🟢 Aktiv" if cur["on"] else "🔴 Deaktiv"), "mon")],
                st_row,
            ]
            reacts = [o.btn(("✅" if cur["react"] == e else "") + e, f"mr{i}") for i, e in enumerate(REACTIONS)]
            rows += [reacts[i : i + 6] for i in range(0, len(reacts), 6)]
            rows.append(
                [
                    o.btn(f"⏱ {cur['cd']} san." if cur["cd"] else "⏱ hər dəfə", "mcd"),
                    o.btn(f"↩️ Reply: {'🟢' if cur['rep'] else '🔴'}", "mrp"),
                ]
            )
            rows.append([o.btn("⬅️ Geri", "back")])
            return rows

        async def show(o, note=None):
            body, cur = mention_view(chat_id, state["target"])
            await o.update(
                card(body, footer=note or "stiker göndərilməsə reaksiya qoyulur"),
                rows=mention_rows(o, chat_id, state["target"], cur),
            )

        def tkey():
            return "global" if state["target"] == "global" else chat_id

        def edit(fn):
            async def h(o, cb):
                _, cur = mention_view(chat_id, state["target"])
                note = fn(cur)
                mention_save(tkey(), cur)
                await show(o, note or "✓ yadda saxlandı")

            return h

        async def pick_sticker(o, cb):
            _WAITING_STICKER[chat_id] = {"o": o, "kind": "mention", "target": tkey(), "back": show}
            await o.update(
                card(
                    [
                        ("Rejim", "🎴 Tağ cavabı üçün stiker"),
                        ("Necə", "istədiyin stikeri BU çata göndər"),
                        ("Qeyd", "mesajın dərhal silinir, stiker Saxlanılanlarda saxlanır"),
                    ],
                    footer="ləğv: 'imtina' yaz",
                ),
                rows=[[o.btn("✖️ Ləğv et", "men")]],
            )

        def mention_picker_view(o, note=None):
            _, cur = mention_view(chat_id, state["target"])
            cd_val = cur.get("cd", 60)
            body = [
                ("Ayar", "⏱ Tağ cavabı fasiləsi"),
                ("Cari dəyər", f"{cd_val} san." if cd_val else "⏱ hər dəfə (0 san.)"),
                ("# İzah", "Eyni istifadəçinin tağlarına cavab verilməsi arası minimum fasilə"),
            ]
            rows = [
                [
                    o.btn("-5", "mcd_step_-5"),
                    o.btn("-1", "mcd_step_-1"),
                    o.btn("+1", "mcd_step_1"),
                    o.btn("+5", "mcd_step_5"),
                ],
                [o.btn("✏️ Öz rəqəmini yaz", "mcd_inp")],
                [o.btn("⬅️ Geri", "men")],
            ]
            return card(body, footer=note or "+ / - düymələri ilə dəyiş"), rows

        async def show_mcd_picker(o, note=None):
            await o.update(*mention_picker_view(o, note))

        out.on("men")(lambda o, cb: show(o))
        out.on("mtg")(lambda o, cb: state.update(target="global") or show(o))
        out.on("mtc")(lambda o, cb: state.update(target="chat") or show(o))
        out.on("mon")(edit(lambda c: c.update(on=not c["on"]) or ("🟢 açıldı" if c["on"] else "🔴 söndürüldü")))
        out.on("mst")(pick_sticker)
        out.on("msx")(edit(lambda c: c.update(st=None) or "🗑 stiker silindi"))
        out.on("mcd")(lambda o, cb: show_mcd_picker(o))
        for d in (-5, -1, 1, 5):
            def _make_mcd_step(delta):
                async def _h(o, cb):
                    _, cur = mention_view(chat_id, state["target"])
                    cur["cd"] = max(0, min(1800, cur.get("cd", 60) + delta))
                    mention_save(tkey(), cur)
                    await show_mcd_picker(o, f"✓ {cur['cd']} san.")
                return _h
            out.on(f"mcd_step_{d}")(_make_mcd_step(d))

        async def on_mcd_inp(o, cb):
            _WAITING_FILTER_VAL[chat_id] = {
                "target": "mcd", "tkey": tkey(),
                "back": lambda o, note: show_mcd_picker(o, note), "o": o
            }
            await o.update(
                card(
                    [
                        ("Ayar", "⏱ Tağ cavabı fasiləsi"),
                        ("Cari dəyər", f"{mention_cfg(chat_id).get('cd', 60)} san."),
                        ("# Daxiletmə", "İstədiyiniz saniyəni, '+' və ya '-' yazın"),
                    ],
                    footer="ləğv: 'imtina' yaz",
                ),
                rows=[[o.btn("✖️ Ləğv et", "mcd_pick")]],
            )
        out.on("mcd_inp")(on_mcd_inp)
        out.on("mcd_pick")(lambda o, cb: show_mcd_picker(o))
        out.on("mrp")(edit(lambda c: c.update(rep=not c["rep"]) or None))
        for i, e in enumerate(REACTIONS):
            out.on(f"mr{i}")(edit(lambda c, e=e: c.update(react=e) or f"reaksiya: {e}"))
        if back is not None:
            out.on("back")(back)
        return show

    # ── 😴 Yuxu (anti-spam) bağlayıcı ──
    def attach_sleep(out, chat_id, back):
        def body():
            sl = _sleepers(chat_id)
            b = [
                ("Nə edir", "filtri zorlayanı müvəqqəti yatırır — ona cavab verilmir"),
                ("Vəziyyət", "🟢 aktiv" if _SL["on"] else "🔴 deaktiv"),
                ("Təkrar", f"eyni filtr eyni adama {_span(_SL['cd'])}-dən bir" if _SL["cd"] else "fasiləsiz"),
                ("Limit", f"{_SL['burst']} filtr / {_span(_SL['win'])}"),
                ("Aşsa", f"😴 {_span(_SL['dur'])} yatır"),
                ("Bildiriş", "✅ yuxuya gedəndə xəbər verilir" if _SL["note"] else "🔕 səssiz"),
                (f"# Bu çatda yatanlar ({len(sl)})", ""),
            ]
            b += [(_preview(name or str(uid), 14), f"{_span(left)} qalıb") for uid, name, left in sl[:6]]
            if len(sl) > 6:
                b.append(("…", f"daha {len(sl) - 6} nəfər"))
            if not sl:
                b.append(("", "heç kim yatmır"))
            return b, sl

        def rows(o, sl):
            r = [
                [o.btn("😴 Yuxu rejimi: " + ("🟢 Aktiv" if _SL["on"] else "🔴 Deaktiv"), "slon")],
                [o.btn(f"⏱ Təkrar: {_span(_SL['cd'])}", "slcd"), o.btn(f"🔁 Limit: {_SL['burst']}", "slburst")],
                [o.btn(f"🪟 Pəncərə: {_span(_SL['win'])}", "slwin"), o.btn(f"😴 Yuxu: {_span(_SL['dur'])}", "sldur")],
                [o.btn(f"🔔 Bildiriş: {'🟢' if _SL['note'] else '🔴'}", "slno")],
            ]
            if sl:
                r.append([o.btn(f"⏰ Hamısını oyat ({len(sl)})", "slwk")])
            r.append([o.btn("⬅️ Geri", "back")])
            return r

        def view(o, note=None):
            b, sl = body()
            return (
                card(b, footer=note or "dəyəri dəyişmək üçün düyməyə bas"),
                rows(o, sl),
            )

        async def show(o, note=None):
            text, r = view(o, note)
            await o.update(text, rows=r)

        show.view = view

        SLEEP_META = {
            "cd": {"title": "⏱ Təkrar fasiləsi", "min": 0, "max": 1800, "unit": "san.", "delta": 5, "desc": "Eyni adam + eyni filtr arası fasilə (0 = limitsiz)"},
            "burst": {"title": "🔁 Mesaj limiti", "min": 1, "max": 50, "unit": "mesaj", "delta": 1, "desc": "Pəncərə ərzində icazə verilən filtr sayı"},
            "win": {"title": "🪟 Pəncərə müddəti", "min": 5, "max": 600, "unit": "san.", "delta": 5, "desc": "Limit üçün yoxlama pəncərəsi (san.)"},
            "dur": {"title": "😴 Yuxu müddəti", "min": 10, "max": 86400, "unit": "san.", "delta": 30, "desc": "Spam edənin susdurulma (yuxu) müddəti"},
        }

        def sleep_picker_view(o, field, note=None):
            meta = SLEEP_META[field]
            val = _SL[field]
            val_str = _span(val) if field in ("cd", "win", "dur") else f"{val} {meta['unit']}"
            body = [
                ("Ayar", meta["title"]),
                ("Cari dəyər", val_str),
                ("# İzah", meta["desc"]),
            ]
            rows = [
                [
                    o.btn("-5", f"slstep_{field}_-5"),
                    o.btn("-1", f"slstep_{field}_-1"),
                    o.btn("+1", f"slstep_{field}_1"),
                    o.btn("+5", f"slstep_{field}_5"),
                ],
                [o.btn("✏️ Öz rəqəmini yaz", f"slinp_{field}")],
                [o.btn("⬅️ Geri", "slp")],
            ]
            return card(body, footer=note or "+ / - düymələri ilə dəyiş"), rows

        async def show_sl_picker(o, field, note=None):
            await o.update(*sleep_picker_view(o, field, note))

        for field in ("cd", "burst", "win", "dur"):
            handler = (lambda f=field: (lambda o, cb: show_sl_picker(o, f)))()
            out.on(f"sl{field}")(handler)
            out.on(f"slpick_{field}")(handler)
            short_alias = {"burst": "bu", "win": "wi", "dur": "du"}.get(field)
            if short_alias:
                out.on(f"sl{short_alias}")(handler)
                out.on(f"slpick_{short_alias}")(handler)
            for d in (-5, -1, 1, 5):
                def _make_sl_step(f, delta):
                    async def _h(o, cb):
                        meta = SLEEP_META[f]
                        step = delta * meta["delta"]
                        _SL[f] = max(meta["min"], min(meta["max"], _SL[f] + step))
                        _save_sleep()
                        val_str = _span(_SL[f]) if f in ("cd", "win", "dur") else f"{_SL[f]} {meta['unit']}"
                        await show_sl_picker(o, f, f"✓ {val_str}")
                    return _h
                out.on(f"slstep_{field}_{d}")(_make_sl_step(field, d))

            def _make_sl_inp(f):
                async def _h(o, cb):
                    meta = SLEEP_META[f]
                    _WAITING_FILTER_VAL[chat_id] = {
                        "target": f"sl_{f}",
                        "meta": meta,
                        "back": lambda o, note: show_sl_picker(o, f, note),
                        "o": o,
                    }
                    val_str = _span(_SL[f]) if f in ("cd", "win", "dur") else f"{_SL[f]} {meta['unit']}"
                    await o.update(
                        card(
                            [
                                ("Ayar", meta["title"]),
                                ("Cari dəyər", val_str),
                                ("Həddlər", f"Min: {meta['min']} · Maks: {meta['max']}"),
                                ("Daxiletmə", "Rəqəm, '+' və ya '-' yazın"),
                            ],
                            footer="ləğv: 'imtina' yaz",
                        ),
                        rows=[[o.btn("✖️ Ləğv et", f"slpick_{f}")]],
                    )
                return _h
            out.on(f"slinp_{field}")(_make_sl_inp(field))

        async def toggle(o, cb, field):
            _SL[field] = not _SL[field]
            _save_sleep()
            await show(o, "✓ yadda saxlandı")

        async def wake_all(o, cb):
            n = _wake(chat_id)
            await show(o, f"⏰ {n} nəfər oyadıldı")

        out.on("slp")(lambda o, cb: show(o))
        out.on("slon")(lambda o, cb: toggle(o, cb, "on"))
        out.on("slno")(lambda o, cb: toggle(o, cb, "note"))
        out.on("slwk")(wake_all)
        if back is not None:
            out.on("back")(back)
        return show

    # ── .filter əmri ──
    @ub.command(
        "filter",
        pattern=r"^\.filter(?:\s+([\s\S]+))?$",
        help=(
            "açar söz və ya cümlə filtri əlavə et",
            "<b>İstifadə:</b>\n"
            "• <code>.filter salam Əleykum salam!</code> — başqasına reply olanda susur\n"
            "• <code>.filter -nr salam Əleykum salam!</code> — 🚫 heç bir reply-da işləmir\n"
            "• <code>.filter -g salam Əleykum salam!</code> — 🌐 bütün qruplarda aktiv\n\n"
            "<b>Bayraqlar:</b>\n"
            "• <code>-a</code> 👥 başqasına reply-sız (default)\n"
            "• <code>-nr</code> 🚫 tam reply-sız (yalnız tək mesaj)\n"
            "• <code>-r</code> ↩️ yalnız mənə reply\n"
            "• <code>-any</code> 💬 hər vəziyyətdə (hətta başqasına reply olsa da)\n"
            "• <code>-g</code> 🌐 Qlobal · <code>-l</code> 💬 Lokal",
        ),
    )
    async def on_add_filter(event):
        raw_args = (event.pattern_match.group(1) or "").strip()
        raw_args, opts = _take_flags(raw_args)
        keyword, reply_text = _parse_filter_args(raw_args)

        is_global = opts.get("g") == "global" or (
            opts.get("g") is None and _DEF.get("g") == "global"
        )
        target_cid = GLOBAL_CHAT if is_global else event.chat_id

        st = None
        if not reply_text and event.is_reply:
            reply_msg = await event.get_reply_message()
            if reply_msg and reply_msg.sticker:
                st = await capture_sticker(event.client, reply_msg)
            reply_text = (reply_msg.raw_text or "") if reply_msg else ""

        if keyword and not ((reply_text or "").strip() or st):
            out = Out(ub, event)

            async def cancel(o, cb):
                _WAITING_STICKER.pop(target_cid, None)
                await o.update(
                    card([("Filtr", _norm(keyword).strip()), ("Status", "❌ ləğv edildi")]),
                    rows=[],
                )
                await o.close(DONE_VISIBLE)

            out.on("fcx")(cancel)

            async def done(o, note=None):
                kw = _norm(keyword).strip()
                f = _FILTERS.get(target_cid, {}).get(kw)
                body = [("Filtr", kw), ("Status", note or "—")]
                if f and note and note.startswith("✓"):
                    body = [
                        ("Filtr", kw),
                        ("Əhatə", "🌐 Qlobal" if is_global else "💬 Bu çat"),
                        ("Cavab", _answer_preview(f, 80)),
                        ("Uyğunluq", MODES[f["m"]]),
                        ("Kimə", SCOPES.get(f.get("s", "all"), "—")),
                        ("Status", note),
                    ]
                await o.update(card(body), rows=[])
                await o.close(DONE_VISIBLE)

            await out.open(card([("Filtr", _norm(keyword).strip())]), [])
            await ask_answer(out, target_cid, keyword, opts, done, after_id=event.id)
            return

        if not keyword or not ((reply_text or "").strip() or st):
            await ub.out(
                event,
                card(
                    [
                        ("Əlavə (lokal)", ".filter <söz> <cavab>"),
                        ("Əlavə (qlobal)", ".filter -g <söz> <cavab>"),
                        ("Tam reply-sız", ".filter -nr <söz> <cavab>"),
                        ("Bayraqlar", "-nr reply-sız · -r mənə reply · -e tam"),
                        ("Siyahı", ".filters"),
                    ],
                    footer="söz/cümlə və cavab daxil edilməlidir",
                ),
            )
            return

        kw, existed = _put(target_cid, keyword, reply_text, opts, st=st)
        f = _FILTERS[target_cid][kw]
        out = await ub.out(
            event,
            card(
                [
                    ("Filtr", kw),
                    ("Əhatə", "🌐 Qlobal" if is_global else "💬 Bu çat"),
                    ("Cavab", _answer_preview(f, 80)),
                    ("Uyğunluq", MODES[f["m"]]),
                    ("Kimə", SCOPES.get(f.get("s", "all"), "—")),
                    ("Status", "✓ yeniləndi" if existed else "✓ əlavə olundu"),
                ]
            ),
        )
        await out.close(DONE_VISIBLE)

    # ── .sfilter əmri (Stiker göndəriləndə cavab) ──
    @ub.command(
        "sfilter",
        pattern=r"^\.sfilter(?:\s+([\s\S]+))?$",
        help=(
            "stiker göndəriləndə cavab",
            "Kimsə müəyyən stikeri göndərəndə avtomatik cavab.\n\n"
            "<b>İstifadə:</b> stikerə reply edib <code>.sfilter Cavab mətni</code>\n"
            "Bayraqlar: <code>-g</code> qlobal · <code>-nr</code> tam reply-sız · <code>-r</code> mənə reply",
        ),
    )
    async def on_add_sticker_filter(event):
        raw, opts = _take_flags((event.pattern_match.group(1) or "").strip())
        reply_msg = await event.get_reply_message() if event.is_reply else None
        if not reply_msg or not reply_msg.sticker:
            await ub.out(
                event,
                card(
                    [
                        ("İstifadə", "stikerə reply → .sfilter <cavab>"),
                        ("Nümunə", ".sfilter -g Bu stikeri sevirəm 😄"),
                    ],
                    footer="stikerə reply etmək lazımdır",
                ),
            )
            return
        text = re.sub(r"^[\"'“]([\s\S]*?)[\"'”]$", r"\1", raw).strip()
        if not text:
            await ub.out(event, card([("Xəta", "cavab mətni yoxdur"), ("İstifadə", "stikerə reply → .sfilter <cavab>")]))
            return

        is_global = opts.get("g") == "global" or (
            opts.get("g") is None and _DEF.get("g") == "global"
        )
        target_cid = GLOBAL_CHAT if is_global else event.chat_id

        attr = _sticker_attr(reply_msg.document)
        trig = {"doc": reply_msg.document.id, "emoji": getattr(attr, "alt", "") or "🖼"}
        kw, existed = _put(target_cid, "", text, opts, trig=trig)
        f = _FILTERS[target_cid][kw]
        out = await ub.out(
            event,
            card(
                [
                    ("Filtr", _label(kw, f)),
                    ("Əhatə", "🌐 Qlobal" if is_global else "💬 Bu çat"),
                    ("Cavab", _answer_preview(f, 80)),
                    ("Kimə", SCOPES.get(f.get("s", "all"), "—")),
                    ("Status", "✓ yeniləndi" if existed else "✓ əlavə olundu"),
                ]
            ),
        )
        await out.close(DONE_VISIBLE)

    # ── .copyfilters əmri ──
    @ub.command(
        "copyfilters",
        pattern=r"^\.copyfilters(?:\s+(\S+))?$",
        help=(
            "filtrləri bu çata kopyala",
            "Bütün qlobal filtrləri və ya başqa qrupun filtrlərini bu çata kopyalayır.\n\n"
            "<b>İstifadə:</b>\n"
            "• <code>.copyfilters global</code> — bütün qlobal filtrləri bu çata kopyala\n"
            "• <code>.copyfilters @qrup_linki</code> — qrupun filtrlərini bu çata kopyala",
        ),
    )
    async def on_copy_filters(event):
        target = (event.pattern_match.group(1) or "").strip().lower()
        if not target:
            await ub.out(
                event,
                card(
                    [
                        ("İstifadə", ".copyfilters <global|@qrup|id>"),
                        ("Nümunə 1", ".copyfilters global"),
                        ("Nümunə 2", ".copyfilters @dostlar_qrupu"),
                    ],
                    footer="kopyalanacaq mənbəni qeyd edin",
                ),
            )
            return

        source_cid = None
        if target in ("global", "g", "qlobal"):
            source_cid = GLOBAL_CHAT
            source_name = "🌐 Qlobal baza"
        else:
            try:
                ent = await event.client.get_entity(int(target) if target.lstrip("-").isdigit() else target)
                source_cid = ent.id
                source_name = getattr(ent, "title", str(source_cid))
            except Exception as e:
                await ub.out(event, card([("Xəta", f"Mənbə tapılmadı: {str(e)[:100]}")]), footer="ID və ya username-i yoxlayın")
                return

        source_filters = _FILTERS.get(source_cid, {})
        if not source_filters:
            await ub.out(event, card([("Mənbə", source_name), ("Status", "Kopyalanacaq heç bir filtr tapılmadı")]))
            return

        dest_cid = event.chat_id
        dest_fl = _FILTERS.setdefault(dest_cid, {})
        copied = 0
        for kw, f in source_filters.items():
            dest_fl[kw] = dict(f)
            copied += 1
        _save()

        out = await ub.out(
            event,
            card(
                [
                    ("Mənbə", source_name),
                    ("Hədəf", f"Bu çat ({dest_cid})"),
                    ("Kopyalandı", f"✅ {copied} filtr uğurla əlavə edildi"),
                    ("Cəmi", f"{len(dest_fl)} filtr bu çatda"),
                ]
            ),
        )
        await out.close(DONE_VISIBLE + 2)

    # ── .filters kartı ──
    @ub.command(
        "filters",
        pattern=r"^\.filters(?:\s+(-g|global))?$",
        help=(
            "aktiv filtrləri göstər",
            "Filtrlər kartı. Filtrə basıb 'Qlobal et' seçməklə onu dərhal bütün qruplarda aktiv edə bilərsiniz.",
        ),
    )
    async def on_list_filters(event):
        chat_id = event.chat_id
        out = Out(ub, event)
        arg = (event.pattern_match.group(1) or "").strip().lower()

        async def show_list(o, note=None):
            _WAITING_ADD.pop(chat_id, None)
            _WAITING_STICKER.pop(chat_id, None)
            _WAITING_STICKER.pop(GLOBAL_CHAT, None)
            text, rows = list_view(o, chat_id, note)
            await o.update(text, rows=rows)

        ScopedList(out, chat_id, GLOBAL_CHAT, lambda c: len(_FILTERS.get(c, {})), show_list,
                   page_size=PAGE_SIZE, view="global" if arg in ("-g", "global") else "chat", full_pager=True)

        def key_at(i):
            target_cid = get_view_cid(out, chat_id)
            kws = list(_FILTERS.get(target_cid, {}))
            return kws[i] if i < len(kws) else None

        async def show_detail(o, i, note=None):
            kw = key_at(i)
            target_cid = get_view_cid(o, chat_id)
            if kw is None:
                return await show_list(o)
            f = _FILTERS[target_cid][kw]
            is_glob = target_cid == GLOBAL_CHAT
            await o.update(
                card(detail_body(kw, f, is_glob), footer=note or "rejimi seç, Qlobal et və ya sil"),
                rows=detail_rows(o, i, f, is_glob),
            )

        def register_index(i):
            async def open_detail(o, cb, i=i):
                _WAITING_ADD.pop(chat_id, None)
                await show_detail(o, i)

            async def set_opt(o, cb, i=i, field=None, value=None):
                target_cid = get_view_cid(o, chat_id)
                kw = key_at(i)
                if kw is None:
                    return await show_list(o)
                _FILTERS[target_cid][kw][field] = value
                _save()
                await show_detail(o, i, "✓ yadda saxlandı")

            async def make_global(o, cb, i=i):
                target_cid = get_view_cid(o, chat_id)
                kw = key_at(i)
                if kw is None or target_cid == GLOBAL_CHAT:
                    return await show_list(o)
                f_data = _FILTERS[target_cid].pop(kw)
                if not _FILTERS[target_cid]:
                    _FILTERS.pop(target_cid, None)
                _FILTERS.setdefault(GLOBAL_CHAT, {})[kw] = f_data
                _save()
                o.view = "global"
                await show_list(o, f"🌐 '{kw}' bütün qruplarda aktiv edildi!")

            async def make_local(o, cb, i=i):
                target_cid = get_view_cid(o, chat_id)
                kw = key_at(i)
                if kw is None or target_cid != GLOBAL_CHAT:
                    return await show_list(o)
                f_data = _FILTERS[GLOBAL_CHAT].pop(kw)
                if not _FILTERS[GLOBAL_CHAT]:
                    _FILTERS.pop(GLOBAL_CHAT, None)
                _FILTERS.setdefault(chat_id, {})[kw] = f_data
                _save()
                o.view = "chat"
                await show_list(o, f"💬 '{kw}' yalnız bu çata keçirildi!")

            async def delete(o, cb, i=i):
                target_cid = get_view_cid(o, chat_id)
                kw = key_at(i)
                if kw is not None:
                    _FILTERS[target_cid].pop(kw, None)
                    _RX.pop(kw, None)
                    if not _FILTERS[target_cid]:
                        _FILTERS.pop(target_cid, None)
                    _save()
                await show_list(o, f"🗑 silindi: {kw}")

            async def pick_reply_sticker(o, cb, i=i):
                target_cid = get_view_cid(o, chat_id)
                kw = key_at(i)
                if kw is None:
                    return await show_list(o)

                async def back(o2, note=None, i=i):
                    await show_detail(o2, i, note)

                _WAITING_STICKER[target_cid] = {
                    "o": o,
                    "kind": "filter",
                    "target": kw,
                    "back": back,
                }
                await o.update(
                    card(
                        [
                            ("Filtr", _label(kw, _FILTERS[target_cid][kw])),
                            ("Rejim", "🎴 cavab stikeri seç"),
                            ("Necə", "istədiyin stikeri BU çata göndər"),
                        ],
                        footer="ləğv: 'imtina' yaz",
                    ),
                    rows=[[o.btn("✖️ Ləğv et", f"o{i}")]],
                )

            async def drop_reply_sticker(o, cb, i=i):
                target_cid = get_view_cid(o, chat_id)
                kw = key_at(i)
                if kw is not None:
                    _FILTERS[target_cid][kw].pop("st", None)
                    _save()
                await show_detail(o, i, "🗑 stiker silindi")

            out.on(f"o{i}")(open_detail)
            out.on(f"d{i}")(delete)
            out.on(f"tgglob{i}")(make_global)
            out.on(f"tgloc{i}")(make_local)
            out.on(f"st{i}")(pick_reply_sticker)
            out.on(f"sx{i}")(drop_reply_sticker)
            for act, field, value in (
                ("me", "m", "exact"),
                ("mc", "m", "contains"),
                ("sa", "s", "all"),
                ("sn", "s", "noreply"),
                ("sr", "s", "reply"),
                ("sy", "s", "any"),
            ):

                async def handler(o, cb, i=i, field=field, value=value):
                    await set_opt(o, cb, i=i, field=field, value=value)

                out.on(f"{act}{i}")(handler)

        async def ask_clear(o, cb):
            target_cid = get_view_cid(o, chat_id)
            n = len(_FILTERS.get(target_cid, {}))
            if not n:
                return await show_list(o, "təmizlənəcək filtr yoxdur")
            name = "🌐 Qlobal bazadakı" if target_cid == GLOBAL_CHAT else "bu çatdakı"
            await o.update(
                card(
                    [
                        ("Diqqət", f"🧹 {name} {n} filtrin hamısı silinsin?"),
                        ("Qeyd", "geri qaytarmaq olmur"),
                    ],
                    footer="təsdiqlə və ya ləğv et",
                ),
                rows=[
                    [
                        o.btn(f"✅ Bəli, {n} filtri sil", "clr_ok"),
                        o.btn("✖️ İmtina", "back"),
                    ]
                ],
            )

        async def do_clear(o, cb):
            target_cid = get_view_cid(o, chat_id)
            n = _clear_chat(target_cid)
            o.page = 0
            await show_list(o, f"🧹 {n} filtr silindi")

        out.on("clr")(ask_clear)
        out.on("clr_ok")(do_clear)

        async def show_defs(o, note=None):
            await o.update(
                card(defs_body(), footer=note or "yeni filtrlər bu standartla yaranacaq"),
                rows=defs_rows(o),
            )

        out.on("defs")(lambda o, cb: show_defs(o))
        for act, field, value in (
            ("dme", "m", "exact"),
            ("dmc", "m", "contains"),
            ("dsa", "s", "all"),
            ("dsn", "s", "noreply"),
            ("dsr", "s", "reply"),
            ("dsy", "s", "any"),
            ("dgl", "g", "local"),
            ("dgg", "g", "global"),
        ):

            async def set_def(o, cb, field=field, value=value):
                _DEF[field] = value
                _save_def()
                await show_defs(o, "✓ yadda saxlandı")

            out.on(act)(set_def)

        async def on_click_add(o, cb):
            target_cid = get_view_cid(o, chat_id)
            _WAITING_ADD[chat_id] = (o, target_cid)
            is_glob = target_cid == GLOBAL_CHAT
            body = [
                ("Rejim", f"➕ Yeni {'🌐 Qlobal' if is_glob else '💬 Lokal'} filtr"),
                ("Format", '"söz və ya cümlə" "cavab"'),
                ("Əhatə", "🌐 Bütün qruplarda aktiv olacaq" if is_glob else "💬 Yalnız bu çat"),
                ("Nümunə", 'salam "Aleykum salam"'),
            ]
            await o.update(
                card(body, footer="Mətni bu çata göndərin (və ya 'imtina' yazın)"),
                rows=[[o.btn("✖️ Ləğv et", "back")]],
            )

        out.on("add_fl")(on_click_add)
        attach_mention(out, chat_id, None)
        attach_sleep(out, chat_id, None)
        out.on("back")(lambda o, cb: show_list(o))

        async def refresh(o):
            await show_list(o)

        out.refresh = refresh

        out.register_index = register_index
        await out.open(*list_view(out, chat_id))

    # ── .tagcavab əmri ──
    @ub.command(
        "tagcavab",
        pattern=r"^\.(?:tagcavab|mention)$",
        help=(
            "tağ edənə stiker / reaksiya",
            "Kimsə səni @tağ edəndə ona seçilmiş stiker (reply) göndərilir; stiker göndərilə bilməsə reaksiya qoyulur.",
        ),
    )
    async def on_mention_card(event):
        chat_id = event.chat_id
        out = Out(ub, event)

        async def close(o, cb):
            _WAITING_STICKER.pop(chat_id, None)
            await o.close()

        show = attach_mention(out, chat_id, close)

        async def refresh(o):
            await show(o)

        out.refresh = refresh
        await show(out)

    # ── .fsleep əmri ──
    @ub.command(
        "fsleep",
        pattern=r"^\.fsleep$",
        help=(
            "😴 filtr anti-spam (yuxu) ayarları",
            "Kimsə filtri zorlayanda (eyni sözü təkrar-təkrar yazanda) ona cavab verilmir.\n\n"
            "• <b>Təkrar</b> — eyni filtr eyni adama neçə saniyədən bir cavab verir\n"
            "• <b>Limit / Pəncərə</b> — məs. 60 san. ərzində 4-dən çox filtr işlətsə...\n"
            "• <b>Yuxu</b> — ...bu çatda filtrlər ondan bu qədər müddət yatır\n\n"
            "Oyatmaq: <code>.fwake</code> (reply / @user / id / all)",
        ),
    )
    async def on_sleep_card(event):
        out = Out(ub, event)
        show = attach_sleep(out, event.chat_id, lambda o, cb: o.close())

        async def refresh(o):
            await show(o)

        out.refresh = refresh
        await out.open(*show.view(out))

    # ── .fwake əmri ──
    @ub.command(
        "fwake",
        pattern=r"^\.fwake(?:\s+(\S+))?$",
        help=("⏰ yatırılmış istifadəçini oyat", "<code>.fwake</code> reply · <code>.fwake @user</code> · "
              "<code>.fwake 123</code> · <code>.fwake all</code> — bu çatda"),
    )
    async def on_wake(event):
        chat_id = event.chat_id
        arg = (event.pattern_match.group(1) or "").strip()
        uid, name = None, None
        try:
            if arg.lower() == "all":
                n = _wake(chat_id)
                out = await ub.out(event, card([("⏰ Oyadıldı", f"{n} nəfər"), ("Çat", str(chat_id))]))
                await out.close(DONE_VISIBLE)
                return
            if event.is_reply:
                rep_msg = await event.get_reply_message()
                uid = rep_msg.sender_id if rep_msg else None
            elif arg.lstrip("-").isdigit():
                uid = int(arg)
            elif arg:
                ent = await event.client.get_entity(arg)
                uid, name = ent.id, getattr(ent, "first_name", None)
        except Exception as e:
            await ub.out(event, card([("Xəta", f"istifadəçi tapılmadı: {str(e)[:80]}")]))
            return
        if uid is None:
            sl = _sleepers(chat_id)
            out = await ub.out(event, card(
                [("İstifadə", ".fwake reply | @user | id | all"), ("Bu çatda yatan", str(len(sl)))]
                + [(_preview(nm or str(u), 14), f"{_span(left)} qalıb") for u, nm, left in sl[:6]]
            ))
            await out.close(DONE_VISIBLE + 4)
            return
        n = _wake(chat_id, uid)
        out = await ub.out(event, card([
            ("İstifadəçi", name or str(uid)),
            ("Status", "⏰ oyadıldı" if n else "ℹ️ onsuz da yatmırdı"),
        ]))
        await out.close(DONE_VISIBLE)

    # ── ➕ Düyməsi ilə gələn mesaj ──
    @ub.raw(events.NewMessage(outgoing=True))
    async def on_outgoing_add_filter(event):
        chat_id = event.chat_id
        if chat_id not in _WAITING_ADD:
            return

        text = (event.raw_text or "").strip()
        if not text or text.startswith("."):
            return

        o, target_cid = _WAITING_ADD.get(chat_id)

        if text.lower() in ("imtina", "cancel", "çıx", "yox"):
            _WAITING_ADD.pop(chat_id, None)
            try:
                await event.delete()
            except Exception as _e:
                logger.debug("udulan xəta [userbot/plugins/filter.py:1691]: %r", _e)
            text, rows = list_view(o, chat_id)
            await o.update(text, rows=rows)
            return

        rest, opts = _take_flags(text)
        keyword, reply_text = _parse_filter_args(rest)
        if keyword and not reply_text:
            _WAITING_ADD.pop(chat_id, None)
            try:
                await event.delete()
            except Exception as _e:
                logger.debug("udulan xəta [userbot/plugins/filter.py:1703]: %r", _e)

            async def back(o2, note=None, kw=_norm(keyword).strip()):
                kws = list(_FILTERS.get(target_cid, {}))
                if kw in kws:
                    o2.page = kws.index(kw) // PAGE_SIZE
                text, rows = list_view(o2, chat_id, note)
                await o2.update(text, rows=rows)

            await ask_answer(
                o, target_cid, keyword, opts, back, cancel_action="back", after_id=event.id
            )
            return

        if not keyword or not reply_text:
            body = [
                ("Xəta", "Format düzgün deyil!"),
                ("Nümunə", '"salam aleykum" "aleykuma salam"'),
            ]
            await o.update(
                card(body, footer="Yenidən yazın və ya 'imtina' göndərin"),
                rows=[[o.btn("✖️ Ləğv et", "back")]],
            )
            return

        _WAITING_ADD.pop(chat_id, None)
        kw, _ = _put(target_cid, keyword, reply_text, opts)

        try:
            await event.delete()
        except Exception as _e:
            logger.debug("udulan xəta [userbot/plugins/filter.py:1734]: %r", _e)

        f = _FILTERS[target_cid][kw]
        o.page = list(_FILTERS[target_cid]).index(kw) // PAGE_SIZE
        is_glob = target_cid == GLOBAL_CHAT
        text, rows = list_view(o, chat_id, f"✓ əlavə edildi: {kw} {_tags(f, is_glob)}")
        await o.update(text, rows=rows)

    # ── Dəyər daxiletməsi (cooldown / yuxu) ──
    @ub.raw(events.NewMessage(outgoing=True))
    async def on_outgoing_filter_val(event):
        chat_id = event.chat_id
        if chat_id not in _WAITING_FILTER_VAL:
            return
        raw = (event.raw_text or "").strip()
        if not raw or raw.startswith("."):
            return
        info = _WAITING_FILTER_VAL.pop(chat_id)
        o = info["o"]
        with contextlib.suppress(Exception):
            await event.delete()
        if raw.lower() in ("imtina", "cancel", "çıx", "yox"):
            await info["back"](o, "❌ ləğv edildi")
            return

        target = info["target"]
        low = raw.lower()
        if target == "mcd":
            g = mention_cfg(None)     # mention_view() attach_mention-un içindədir — burada görünmür (NameError idi)
            cur = g if info["tkey"] == "global" else (mention_cfg(chat_id, effective=False) or dict(g))
            note = ""
            if low in ("-", "0", "min", "off"):
                cur["cd"] = 0
                note = "✅ fasilə: 0 san. (hər dəfə)"
            elif low in ("+", "max"):
                cur["cd"] = 1800
                note = "✅ fasilə: 1800 san. (maksimum)"
            elif raw.isdigit():
                cur["cd"] = max(0, min(1800, int(raw)))
                note = f"✅ fasilə: {cur['cd']} san."
            else:
                note = "⚠️ düzgün rəqəm deyil"
            mention_save(info["tkey"], cur)
            await info["back"](o, note)
        elif target.startswith("sl_"):
            f = target[3:]
            meta = info["meta"]
            note = ""
            if low in ("-", "min", "off"):
                _SL[f] = meta["min"]
                note = f"✅ {meta['title']}: {meta['min']} (minimum)"
            elif low in ("+", "max"):
                _SL[f] = meta["max"]
                note = f"✅ {meta['title']}: {meta['max']} (maksimum)"
            elif raw.isdigit():
                _SL[f] = max(meta["min"], min(meta["max"], int(raw)))
                note = f"✅ {meta['title']}: {_SL[f]}"
            else:
                note = "⚠️ düzgün rəqəm deyil"
            _save_sleep()
            await info["back"](o, note)

    # ── Stikerlə cavab gözləməsi ──
    @ub.raw(events.NewMessage(outgoing=True))
    async def on_outgoing_sticker(event):
        chat_id = event.chat_id
        target_cid = chat_id if chat_id in _WAITING_STICKER else GLOBAL_CHAT
        w = _WAITING_STICKER.get(target_cid)
        if not w or event.id <= w.get("after", 0):
            return

        msg = event.message
        raw = (event.raw_text or "").strip()
        text = raw.lower()
        if not msg.sticker:
            if text in ("imtina", "cancel", "çıx", "yox"):
                _WAITING_STICKER.pop(target_cid, None)
                try:
                    await event.delete()
                except Exception as _e:
                    logger.debug("udulan xəta [userbot/plugins/filter.py:1814]: %r", _e)
                await w["back"](w["o"], "❌ ləğv edildi")
            elif w["kind"] == "answer" and raw and not raw.startswith("."):
                _WAITING_STICKER.pop(target_cid, None)
                kw, existed = _put(target_cid, w["target"], raw, w.get("opts"))
                try:
                    await event.delete()
                except Exception as _e:
                    logger.debug("udulan xəta [userbot/plugins/filter.py:1822]: %r", _e)
                await w["back"](w["o"], f"✓ {'yeniləndi' if existed else 'əlavə olundu'}: {kw} → mətn")
            return

        _WAITING_STICKER.pop(target_cid, None)
        ref = await capture_sticker(event.client, msg)
        if w["kind"] == "answer":
            try:
                await event.delete()
            except Exception as _e:
                logger.debug("udulan xəta [userbot/plugins/filter.py:1832]: %r", _e)
            kw, existed = _put(target_cid, w["target"], "", w.get("opts"), st=ref)
            await w["back"](
                w["o"], f"✓ {'yeniləndi' if existed else 'əlavə olundu'}: {kw} → 🎴 {ref.get('emoji', '')}"
            )
            return

        try:
            await event.delete()
        except Exception as _e:
            logger.debug("udulan xəta [userbot/plugins/filter.py:1842]: %r", _e)

        if w["kind"] == "mention":
            target = w["target"]
            cur = (
                mention_cfg(None)
                if target == "global"
                else (mention_cfg(target, effective=False) or mention_cfg(None))
            )
            cur["st"] = ref
            mention_save(target, cur)
            await w["back"](w["o"], f"🎴 stiker seçildi {ref.get('emoji', '')}")
        else:
            kw = w["target"]
            f = _FILTERS.get(target_cid, {}).get(kw)
            if f is not None:
                f["st"] = ref
                _save()
            await w["back"](w["o"], f"🎴 cavab stikeri təyin olundu {ref.get('emoji', '')}")

    # ── .stopfilter əmri ──
    @ub.command(
        "stopfilter",
        pattern=r"^\.stopfilter(?:\s+([\s\S]+))?$",
        help=("filtri sil", "Filtrlənmiş sözü silir. Qlobal filtri silmək üçün: .stopfilter -g <söz>"),
    )
    async def on_stop_filter(event):
        raw = (event.pattern_match.group(1) or "").strip()
        raw, opts = _take_flags(raw)
        kw = _norm(re.sub(r'''^["'“](.*?)["'”]$''', r"\1", raw)).strip()
        chat_id = event.chat_id
        is_glob = opts.get("g") == "global"
        target_cid = GLOBAL_CHAT if is_glob else chat_id

        if not kw:
            await ub.out(event, card([("İstifadə", ".stopfilter [-g] <söz/cümlə>")]))
            return

        fl = _FILTERS.get(target_cid, {})
        if kw not in fl and not is_glob and kw in _FILTERS.get(GLOBAL_CHAT, {}):
            target_cid = GLOBAL_CHAT
            fl = _FILTERS[GLOBAL_CHAT]
            is_glob = True

        if kw in fl:
            del fl[kw]
            _RX.pop(kw, None)
            if not fl:
                _FILTERS.pop(target_cid, None)
            _save()
            status = f"✓ {'[🌐 Qlobal] ' if is_glob else ''}silindi"
        else:
            status = "✗ belə filtr yoxdur"

        out = await ub.out(event, card([("Söz/Cümlə", kw), ("Status", status)]))
        await out.close(DONE_VISIBLE)

    # ── .clearfilters əmri ──
    @ub.command(
        "clearfilters",
        pattern=r"^\.(?:clearfilters|stopall)$",
        help=("bütün filtrləri sil", "Bu çatdakı bütün lokal filtrləri silir."),
    )
    async def on_clear_filters(event):
        chat_id = event.chat_id
        n = len(_FILTERS.get(chat_id, {}))
        if not n:
            out = await ub.out(event, card([("Status", "bu çatda filtr yoxdur")]))
            await out.close(DONE_VISIBLE)
            return
        out = Out(ub, event)

        async def ok(o, cb):
            k = _clear_chat(chat_id)
            await o.update(card([("Status", f"🧹 {k} filtr silindi")]), rows=[])
            await o.close(DONE_VISIBLE)

        async def no(o, cb):
            await o.update(card([("Status", "↩️ ləğv edildi — filtrlər qaldı")]), rows=[])
            await o.close(DONE_VISIBLE)

        out.on("clr_ok")(ok)
        out.on("clr_no")(no)
        await out.open(
            card(
                [
                    ("Diqqət", f"🧹 bu çatdakı {n} filtrin hamısı silinsin?"),
                    ("Qeyd", "geri qaytarmaq olmur"),
                ],
                footer="təsdiqlə və ya ləğv et",
            ),
            [[out.btn(f"✅ Bəli, {n} filtri sil", "clr_ok"), out.btn("✖️ İmtina", "clr_no")]],
        )

    # ── Gələn mesajlara avtomatik cavab (Lokal + Qlobal) ──
    @ub.raw(events.NewMessage(incoming=True))
    async def on_incoming_message_filter(event):
        chat_id = event.chat_id
        msg = event.message
        if _NOWRITE.get(chat_id, 0) > time.time():        # bu çatda yaza bilmirik — boş yerə cavab cəhdi etmə
            return

        fl_local = _FILTERS.get(chat_id, {})
        fl_global = _FILTERS.get(GLOBAL_CHAT, {})
        fl = {**fl_global, **fl_local}

        answered = False
        if fl and (event.raw_text or msg.sticker):
            is_reply = bool(event.is_reply)

            def pick(to_me: bool):
                return (
                    _pick_sticker(fl, msg.document.id, is_reply, to_me)
                    if msg.sticker
                    else _pick(fl, event.raw_text, is_reply, to_me)
                )

            hit = pick(False)
            if is_reply:
                # Reply-in kimə olduğu yalnız nəticəni dəyişəndə soruşulur: əvvəl qlobal filtr varsa HƏR
                # çatdakı HƏR reply üçün GetMessages çağırılırdı (FloodWait riski)
                hit_me = pick(True)
                if hit_me != hit:
                    try:
                        rep = await event.get_reply_message()
                        if rep and rep.out:
                            hit = hit_me
                    except Exception as _e:
                        logger.debug("udulan xəta [userbot/plugins/filter.py:1968]: %r", _e)
            gate = _gate(chat_id, event.sender_id, hit[0]) if hit else "ok"
            if hit and gate != "ok":
                answered = True                       # tağ cavabına da keçmə
                if gate == "fell":
                    name = None
                    with contextlib.suppress(Exception):
                        s_ent = await event.get_sender()
                        name = getattr(s_ent, "first_name", None) or getattr(s_ent, "title", None)
                    until = (_SLEEP.get((chat_id, event.sender_id)) or (time.time() + _SL["dur"], None))[0]
                    _SLEEP[(chat_id, event.sender_id)] = (until, name)
                    logger.info(f"😴 filtr yuxusu: {name or event.sender_id} ({chat_id}) — {_span(_SL['dur'])}")
                    if _SL["note"]:
                        with contextlib.suppress(Exception):
                            await event.reply(
                                f"😴 Filtrlər səndən {_span(_SL['dur'])} yatır — çox tez-tez işlədirsən."
                            )
                hit = None
            if hit:
                try:
                    kw, f = hit
                    if f.get("st"):
                        ok, changed = await send_sticker(
                            event.client, chat_id, f["st"], reply_to=event.id
                        )
                        if changed:
                            _save()
                        if ok:
                            answered = True
                    if not answered and f.get("r"):
                        await event.reply(f["r"])
                        answered = True
                except Exception as e:
                    if _mark_nowrite(chat_id, e):
                        return
                    logger.info(f"filter cavab xətası: {e}")

        if not answered and not _sleep_left(chat_id, event.sender_id):
            try:
                await _mention_reply(event, chat_id)
            except Exception as e:
                if not _mark_nowrite(chat_id, e):
                    logger.info(f"tağ cavabı xətası: {e}")

    # ── Tağ cavabı funksiyaları ──
    async def _me(client):
        me = ub.state.get("me")
        if me is None or getattr(me, "_client_id", None) != id(client):
            me = await client.get_me()
            with contextlib.suppress(Exception):
                me._client_id = id(client)
            ub.state["me"] = me
        return me

    async def _mention_reply(event, chat_id):
        cfg = mention_cfg(chat_id)
        if not cfg["on"]:
            return
        me = await _me(event.client)
        if not _is_tag(event.message, me, cfg["rep"]):
            return
        uid = event.sender_id
        key = (chat_id, uid)
        now = time.time()
        if cfg["cd"] and now - _MENTION_LAST.get(key, 0) < cfg["cd"]:
            return
        _MENTION_LAST[key] = now
        if len(_MENTION_LAST) > 5000:
            for k in [k for k, t in _MENTION_LAST.items() if now - t > 3600]:
                _MENTION_LAST.pop(k, None)
        sent = False
        if cfg.get("st"):
            sent, changed = await send_sticker(event.client, chat_id, cfg["st"], reply_to=event.id)
            if changed:
                target = chat_id if mention_cfg(chat_id, effective=False) is not None else "global"
                mention_save(target, cfg)
        if not sent:
            await send_reaction(event.client, chat_id, event.id, cfg["react"])