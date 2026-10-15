"""⏰ .amsg — çata avtomatik (planlı) mesaj göndərmə (Lokal və Qlobal dəstəkli).

  .amsg <vaxt> <mətn>           — bu çata planlı mesaj
  .amsg -g <vaxt> <mətn>        — 🌐 Qlobal: bütün qruplara
  .amsg <vaxt>  (mesaja reply)  — reply etdiyin mesajı göndərir (media/stiker/format qorunur)
  .amsg <vaxt>                  — mətn yazmasan, növbəti mesajını gözləyir
  .amsgs                        — tapşırıqlar kartı (💬 Bu çat / 🌐 Qlobal tabları)
  .amstop <id[,id]>             — tapşırığı sil
  .amnow <id>                   — indi göndər (cədvəl dəyişmir)
  .amtz [Region/Şəhər]          — saat qurşağı (standart Asia/Baku)

Vaxt formatları:
  30m · 2h · 1h30m · 1d · 2saat · 45dəq   — hər N dəqiqə/saat/gün (interval)
  09:00 · 09:00,21:30                     — hər gün həmin saat(lar)da

Bayraqlar (istənilən sırada, vaxtdan əvvəl və ya sonra):
  -g qlobal · -l lokal · -once bir dəfəlik · -now ilkini dərhal göndər
  -del əvvəlki mesajı sil · -s səssiz · -d 1-5 həftə günləri (1=B.e … 7=Bazar)
"""
import asyncio
import contextlib
import re
import time
from datetime import datetime, timedelta, timezone

from telethon import events
from telethon.errors import (
    ChannelPrivateError,
    ChatWriteForbiddenError,
    FloodWaitError,
    UserBannedInChannelError,
)
from userbot.core.userbot_api import Out, ScopedList, ff, logger
from userbot.core.userbot_db import get_udb
from common.utilities import truncate_text
from common.utilities import spawn
from common.formatting import fmt_duration_words as _span


try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

NS = "automsg"
GLOBAL_CHAT = 0
DONE_VISIBLE = 4
PAGE_SIZE = 8
TICK = 15                 # planlayıcı neçə saniyədən bir yoxlayır
GRACE = 600               # saatlı tapşırıq bu qədər gecikibsə (bot sönülü idi) — ötürülür
MIN_EVERY = 60            # lokal minimum interval
MIN_EVERY_GLOBAL = 900    # qlobal minimum interval (hesab ban yeməsin)
GLOBAL_GAP = 3            # qlobal göndərmədə çatlar arası fasilə (san.)
GCACHE_TTL = 600          # qrup siyahısı keşi
DEF_TZ = "Asia/Baku"
CANCEL = ("imtina", "cancel", "çıx", "yox")
WD = {1: "B.e", 2: "Ç.a", 3: "Ç", 4: "C.a", 5: "C", 6: "Ş", 7: "B"}
UNIT = {"m": 60, "dəq": 60, "h": 3600, "saat": 3600, "d": 86400, "g": 86400, "gün": 86400}

_TASKS = {}       # cid -> {tid: task}
_WAIT = {}        # chat_id -> gözləmə vəziyyəti
_RUNNING = set()  # (cid, tid) — hazırda göndərilir
_GCACHE = {"ts": 0, "ids": []}
_CFG = {"tz": DEF_TZ, "gskip": [], "seq": 0}


# ───────────────────────── saxlama ─────────────────────────
def _load():
    _TASKS.clear()
    udb = get_udb()
    try:
        for cid, tid, t in udb.rec_list(NS):
            if isinstance(t, dict) and t.get("kind") in ("int", "time"):
                t["id"] = str(tid)
                _TASKS.setdefault(int(cid), {})[str(tid)] = t
    except Exception as e:
        logger.warning(f".amsg yüklənmədi: {e}")
    try:
        c = udb.kv_get(NS, "cfg") or {}
    except Exception:
        c = {}
    _CFG.update({k: v for k, v in c.items() if k in _CFG})
    ids = [int(t) for fl in _TASKS.values() for t in fl if str(t).isdigit()]
    if ids:
        _CFG["seq"] = max(_CFG["seq"], *ids)


def _save_cfg():
    try:
        get_udb().kv_set(NS, "cfg", dict(_CFG))
    except Exception as e:
        logger.warning(f".amsg ayarları saxlanmadı: {e}")


def _put(cid, t):
    try:
        get_udb().rec_put(NS, cid, t["id"], t)
    except Exception as e:
        logger.warning(f".amsg #{t.get('id')} saxlanmadı: {e}")


def _drop(cid, tid):
    t = _TASKS.get(cid, {}).pop(tid, None)
    if cid in _TASKS and not _TASKS[cid]:
        _TASKS.pop(cid, None)
    def _rm():
        with contextlib.suppress(Exception):
            get_udb().rec_del(NS, cid, tid)
    try:
        asyncio.get_running_loop().run_in_executor(None, _rm)   # SQLite commit event loop-u dondurmasın
    except RuntimeError:
        _rm()
    return t


def _find(tid):
    tid = str(tid)
    for cid, fl in _TASKS.items():
        if tid in fl:
            return cid, fl[tid]
    return None, None


def _move(src, dst, t):
    _drop(src, t["id"])
    t["lm"] = {}
    _TASKS.setdefault(dst, {})[t["id"]] = t
    _put(dst, t)


# ───────────────────────── vaxt ─────────────────────────
def _tz():
    if ZoneInfo:
        with contextlib.suppress(Exception):
            return ZoneInfo(_CFG["tz"])
    return timezone(timedelta(hours=4))




def _at(ts) -> str:
    if not ts:
        return "—"
    tz = _tz()
    dt = datetime.fromtimestamp(ts, tz)
    today = datetime.now(tz).date()
    if dt.date() == today:
        day = ""
    elif dt.date() == today + timedelta(days=1):
        day = "sabah "
    elif dt.date() == today - timedelta(days=1):
        day = "dünən "
    else:
        day = dt.strftime("%d.%m ")
    return f"{day}{dt:%H:%M}"


def _when(ts) -> str:
    if not ts:
        return "—"
    left = ts - time.time()
    return f"{_at(ts)} ({_span(left)} sonra)" if left > 60 else f"{_at(ts)} (indi)"


def _next(t, after):
    if t["kind"] == "int":
        return after + t["every"]
    tz = _tz()
    base = datetime.fromtimestamp(after, tz)
    days = set(t.get("days") or [])
    for add in range(0, 9):
        d = (base + timedelta(days=add)).date()
        if days and d.isoweekday() not in days:
            continue
        for hm in t["times"]:
            h, m = map(int, hm.split(":"))
            ts = datetime(d.year, d.month, d.day, h, m, tzinfo=tz).timestamp()
            if ts > after:
                return ts
    return after + 86400


# ───────────────────────── arqumentlər ─────────────────────────
def _parse_days(spec):
    out = set()
    for p in spec.split(","):
        if "-" in p:
            a, _, b = p.partition("-")
            if a.isdigit() and b.isdigit():
                out.update(range(int(a), int(b) + 1))
        elif p.isdigit():
            out.add(int(p))
    return sorted(d for d in out if 1 <= d <= 7)


def _take_flags(text: str):
    opts = {}
    while True:
        m = re.match(r"^-(g|l|once|1|now|del|s|d)(?:\s+|$)", text)
        if not m:
            return text, opts
        f = m.group(1)
        text = text[m.end():]
        if f == "g":
            opts["g"] = True
        elif f == "l":
            opts["g"] = False
        elif f in ("once", "1"):
            opts["once"] = True
        elif f == "now":
            opts["now"] = True
        elif f == "del":
            opts["del"] = True
        elif f == "s":
            opts["sil"] = True
        elif f == "d":
            m2 = re.match(r"^([\d,\-]+)(?:\s+|$)", text)
            if m2:
                opts["days"] = _parse_days(m2.group(1))
                text = text[m2.end():]


def _parse_sched(tok: str):
    tok = tok.lower()
    if re.fullmatch(r"(?:\d+(?:dəq|saat|gün|m|h|d|g))+", tok):
        sec = sum(int(n) * UNIT[u] for n, u in re.findall(r"(\d+)(dəq|saat|gün|m|h|d|g)", tok))
        return {"kind": "int", "every": sec} if sec > 0 else None
    if re.fullmatch(r"\d{1,2}[:.]\d{2}(?:,\d{1,2}[:.]\d{2})*", tok):
        times = []
        for p in tok.split(","):
            h, m = map(int, re.split(r"[:.]", p))
            if h > 23 or m > 59:
                return None
            times.append(f"{h:02d}:{m:02d}")
        return {"kind": "time", "times": sorted(set(times))}
    return None


def _parse_args(raw: str):
    """→ (sched | None | False, opts, body). None = boşdur, False = vaxt tanınmadı."""
    text, opts = _take_flags((raw or "").strip())
    parts = text.split(None, 1)
    if not parts:
        return None, opts, ""
    sched = _parse_sched(parts[0])
    if not sched:
        return False, opts, text
    body, more = _take_flags(parts[1] if len(parts) > 1 else "")
    opts.update(more)
    return sched, opts, body.strip()


def _check(sched, cid):
    if sched["kind"] != "int":
        return None
    if sched["every"] < MIN_EVERY:
        return f"minimum interval {_span(MIN_EVERY)}"
    if cid == GLOBAL_CHAT and sched["every"] < MIN_EVERY_GLOBAL:
        return f"🌐 qlobal üçün minimum interval {_span(MIN_EVERY_GLOBAL)} (hesab spam-a düşməsin)"
    return None


# ───────────────────────── mətn / etiketlər ─────────────────────────
def _preview(text, n=60):
    return truncate_text(text, n, normalize_spaces=True)



def _days_label(days):
    days = sorted(days or [])
    if not days or days == list(range(1, 8)):
        return ""
    if days == [1, 2, 3, 4, 5]:
        return "iş günləri"
    if days == [6, 7]:
        return "həftəsonu"
    return ", ".join(WD[d] for d in days)


def _sched_label(t):
    if t["kind"] == "int":
        s = f"hər {_span(t['every'])}"
        s = ("1️⃣ " + f"{_span(t['every'])} sonra") if t.get("once") else "🔁 " + s
    else:
        s = ("1️⃣ " if t.get("once") else "🕘 ") + ", ".join(t["times"])
    dl = _days_label(t.get("days"))
    return s + (f" · {dl}" if dl else "")


def _content(t, n=40):
    p = _preview(t.get("t"), n)
    if t.get("src"):
        return f"📎 {p}" if p else "📎 media/stiker"
    return p or "—"


def _icons(t, cid):
    return ("🟢" if t.get("on") else "⏸") + ("🌐" if cid == GLOBAL_CHAT else "")


def _opts_label(t):
    o = []
    if t.get("del"):
        o.append("🧹 köhnəni silir")
    if t.get("sil"):
        o.append("🔕 səssiz")
    if t.get("once"):
        o.append("1️⃣ bir dəfəlik")
    return " · ".join(o) or "—"


def _create(cid, sched, opts, text="", src=None):
    _CFG["seq"] += 1
    _save_cfg()
    now = time.time()
    t = {
        "id": str(_CFG["seq"]),
        "t": text or "",
        "src": src,
        "kind": sched["kind"],
        "every": sched.get("every"),
        "times": sched.get("times"),
        "days": opts.get("days") or [],
        "once": bool(opts.get("once")),
        "del": bool(opts.get("del")),
        "sil": bool(opts.get("sil")),
        "on": True,
        "cnt": 0,
        "last": 0,
        "lm": {},
        "skip": [],
        "made": int(now),
    }
    t["next"] = now if opts.get("now") else _next(t, now)
    _TASKS.setdefault(cid, {})[t["id"]] = t
    _put(cid, t)
    return t


# ───────────────────────── göndərmə ─────────────────────────
async def _capture(client, msg):
    """Mesajı saxla: sadə mətn → mətn; media/format → Saxlanılanlara surət."""
    text = msg.raw_text or ""
    if not (msg.media or msg.entities):
        return text, None
    try:
        saved = await client.send_message("me", msg, silent=True)
        return text, ["me", saved.id]
    except Exception as e:
        logger.debug(f".amsg surət Saxlanılanlara getmədi: {e}")
        return text, [msg.chat_id, msg.id]


async def _global_chats(client, t):
    now = time.time()
    if now - _GCACHE["ts"] > GCACHE_TTL:
        ids = []
        async for d in client.iter_dialogs():
            if not d.is_group:
                continue
            ent = d.entity
            if getattr(ent, "left", False) or getattr(ent, "deactivated", False):
                continue
            br = getattr(ent, "default_banned_rights", None)
            if (
                br
                and getattr(br, "send_messages", False)
                and not (getattr(ent, "creator", False) or getattr(ent, "admin_rights", None))
            ):
                continue
            ids.append(d.id)
        _GCACHE.update(ts=now, ids=ids)
    skip = set(_CFG["gskip"]) | set(t.get("skip") or [])
    return [c for c in _GCACHE["ids"] if c not in skip]


async def _send_one(client, chat, t, src_msg):
    key = str(chat)
    lm = t.setdefault("lm", {})
    if t.get("del") and lm.get(key):
        with contextlib.suppress(Exception):
            await client.delete_messages(chat, [lm[key]])
    if src_msg is not None:
        sent = await client.send_message(chat, src_msg, silent=t.get("sil", False))
    else:
        sent = await client.send_message(chat, t["t"], silent=t.get("sil", False))
    if t.get("del"):
        lm[key] = sent.id


async def _deliver(client, chats, t):
    ok, errs = 0, []
    src_msg = None
    if t.get("src"):
        with contextlib.suppress(Exception):
            src_msg = await client.get_messages(t["src"][0], ids=t["src"][1])
        if not src_msg and not t.get("t"):
            return 0, ["mənbə mesaj silinib — ✏️ ilə mesajı yenilə"]
    for i, chat in enumerate(chats):
        if i:
            await asyncio.sleep(GLOBAL_GAP)
        for attempt in (1, 2):
            try:
                await _send_one(client, chat, t, src_msg)
                ok += 1
                break
            except FloodWaitError as e:
                # ≤120 san.-lik FloodWait-i Telethon özü gözləyir (flood_sleep_threshold, tg_session.py) —
                # bura yalnız uzunları gəlir; əvvəlki "<= 120" şərti heç vaxt ödənmirdi
                if attempt == 1 and e.seconds <= 300:
                    await asyncio.sleep(e.seconds + 1)
                    continue
                errs.append(f"FloodWait {e.seconds} san — dayandırıldı")
                logger.warning(f".amsg #{t['id']}: FloodWait {e.seconds}s, qalan çatlar ötürüldü")
                return ok, errs
            except (ChatWriteForbiddenError, ChannelPrivateError, UserBannedInChannelError):
                errs.append(f"{chat}: yazmaq olmur")
                break
            except Exception as e:
                errs.append(f"{chat}: {str(e)[:60]}")
                break
    return ok, errs


async def _run(client, cid, tid, t):
    """Çağırmazdan əvvəl (cid, tid) _RUNNING-ə əlavə olunmalıdır."""
    try:
        chats = [cid] if cid != GLOBAL_CHAT else await _global_chats(client, t)
        ok, errs = await _deliver(client, chats, t)
        t["last"] = time.time()
        t["cnt"] = t.get("cnt", 0) + ok
        if errs:
            t["err"] = errs[-1]
            logger.info(f".amsg #{tid}: {ok} göndərildi, {len(errs)} xəta — {errs[-1]}")
        else:
            t.pop("err", None)
        c2, t2 = _find(tid)
        if t2 is t:
            _put(c2, t)
        return ok, errs
    except Exception as e:
        t["err"] = str(e)[:80]
        logger.warning(f".amsg #{tid} göndərilmədi: {e}")
        return 0, [str(e)[:80]]
    finally:
        _RUNNING.discard((cid, tid))


async def _tick(client):
    now = time.time()
    for cid, fl in list(_TASKS.items()):
        for tid, t in list(fl.items()):
            if not t.get("on") or (cid, tid) in _RUNNING:
                continue
            nx = t.get("next")
            if not nx:
                t["next"] = _next(t, now)
                _put(cid, t)
                continue
            if now < nx:
                continue
            late = now - nx
            send = True
            if t["kind"] == "int":
                if t.get("days") and datetime.now(_tz()).isoweekday() not in t["days"]:
                    send = False
                t["next"] = nx + (int(late // t["every"]) + 1) * t["every"]   # catch-up: yalnız 1 dəfə
            else:
                if late > GRACE:
                    send = False
                t["next"] = _next(t, now)
            if t.get("once") and (send or t["kind"] == "time"):
                t["on"], t["next"] = False, None
            _put(cid, t)
            if send:
                _RUNNING.add((cid, tid))
                spawn(_run(client, cid, tid, t))
            else:
                logger.info(f".amsg #{tid}: vaxt ötüb/gün uyğun deyil — bu dəfə göndərilmədi")


async def _loop(ub):
    logger.info("⏰ .amsg planlayıcısı başladı")
    while True:
        client = getattr(ub, "client", None) or ub.state.get("am_client")   # hesab dəyişəndə ub.client yenilənir
        try:
            if client is not None and client.is_connected():
                await _tick(client)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f".amsg planlayıcı xətası: {e}")
        await asyncio.sleep(TICK)


def _ensure(ub, client=None):
    if client is not None:
        ub.state["am_client"] = client
    if (getattr(ub, "client", None) or ub.state.get("am_client")) is None:
        return
    task = ub.state.get("am_loop")
    if task and not task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    ub.state["am_loop"] = loop.create_task(_loop(ub))


def _loop_alive(ub):
    task = ub.state.get("am_loop")
    return bool(task and not task.done())


# ───────────────────────── register ─────────────────────────
def register(ub):
    _load()
    old = ub.state.pop("am_loop", None)      # plugin reload olanda köhnə dövrü dayandır
    if old and not old.done():
        old.cancel()

    def card(rows, footer=None):
        return ff(ub.title("automsg"), rows, footer=footer)

    def scope_label(cid, chat_id=None):
        if cid == GLOBAL_CHAT:
            return "🌐 Qlobal — bütün qruplara"
        return "💬 Bu çat" if chat_id is None or cid == chat_id else f"💬 Çat {cid}"

    def content_prompt(cid, sched, opts):
        tmp = dict(sched, days=opts.get("days"), once=opts.get("once"))
        return card(
            [
                ("Əhatə", scope_label(cid)),
                ("Cədvəl", _sched_label(tmp)),
                ("# Mesaj nə olsun?", ""),
                ("✍️ Mətn", "mesajı bu çata yaz"),
                ("📎 Media", "və ya foto / video / stiker / fayl göndər"),
            ],
            footer="ləğv: 'imtina' yaz və ya düyməyə bas",
        )

    # ── 🃏 əsas panel (siyahı + tapşırıq kartı) — .amsgs və .amsg hər ikisi bunu açır ──
    async def panel(event, view="chat", start=None):
        """start: None → siyahı · ("detail", tid, note) · ("content", cid, sched, opts)"""
        client = event.client
        chat_id = event.chat_id
        out = Out(ub, event)
        out.cur = None
        sl = ScopedList(out, chat_id, GLOBAL_CHAT, lambda c: len(_TASKS.get(c, {})), lambda o: show_list(o),
                        page_size=PAGE_SIZE, view=view)
        vcid = sl.cid

        def tids(o):
            return list(_TASKS.get(vcid(o), {}))

        # ── siyahı ──
        def list_body(o):
            cid = vcid(o)
            fl = _TASKS.get(cid, {})
            n = sl.pages(o)
            body = [
                ("Görünüş", scope_label(cid) if cid == GLOBAL_CHAT else f"💬 Bu çat ({chat_id})"),
                ("Tapşırıqlar", f"💬 Bu çat: {len(_TASKS.get(chat_id, {}))} · 🌐 Qlobal: {len(_TASKS.get(GLOBAL_CHAT, {}))}"),
                ("Saat", f"{_CFG['tz']} · indi {datetime.now(_tz()):%H:%M}"),
                ("Planlayıcı", "🟢 işləyir" if _loop_alive(ub) else "🔴 işləmir"),
            ]
            if cid == GLOBAL_CHAT:
                body.append(("Bu çat", "🚫 qlobaldan istisnadır" if chat_id in _CFG["gskip"] else "✅ qlobal mesajları alır"))
            if fl:
                items = sl.window(o, fl.values())
                body.append("# Siyahı" + (f" — {o.page + 1}/{n}" if n > 1 else ""))
                for t in items:
                    nx = f" · ⏭ {_at(t['next'])}" if t.get("on") and t.get("next") else ""
                    body.append((f"#{t['id']} {_icons(t, cid)}", f"{_sched_label(t)}{nx} → {_content(t, 28)}"))
                body.append("# İşarələr")
                body.append(("🟢 / ⏸ / 🌐", "aktiv / dayandırılıb / qlobal"))
            return body

        def foot(o, note=None):
            if note:
                return note
            return (
                "tapşırığa bas → idarə et | ➕ yeni"
                if tids(o)
                else f"Burada tapşırıq yoxdur. ➕ bas və ya: .amsg {'-g ' if vcid(o) == GLOBAL_CHAT else ''}<vaxt> <mətn>"
            )

        def list_rows(o):
            cid = vcid(o)
            btns = []
            for tid in sl.window(o, tids(o)):
                sl.ensure(tid, register_task)
                t = _TASKS[cid][tid]
                btns.append(o.btn(f"#{tid} {_icons(t, cid)} {_preview(t.get('t') or '📎', 12)}", f"o{tid}"))
            grid = sl.grid(btns)
            pager = sl.pager_row(o)
            if pager:
                grid.append(pager)
            grid.append(sl.tab_row(o))
            row = [o.btn(f"➕ Yeni {'qlobal' if cid == GLOBAL_CHAT else 'lokal'}", "add")]
            if cid == GLOBAL_CHAT:
                row.append(o.btn("✅ Bu çatı daxil et" if chat_id in _CFG["gskip"] else "🚫 Bu çatı istisna et", "gsk"))
            grid.append(row)
            return grid

        def list_view(o, note=None):
            o.cur = None
            sl.clamp(o)
            return card(list_body(o), footer=foot(o, note)), list_rows(o)

        # ── tapşırıq kartı ──
        def detail_body(cid, t):
            b = [
                ("Tapşırıq", f"#{t['id']}"),
                ("Əhatə", scope_label(cid, chat_id)),
                ("Mesaj", _content(t, 80)),
                ("Cədvəl", _sched_label(t)),
                ("Vəziyyət", "🟢 aktiv" if t.get("on") else "⏸ dayandırılıb"),
                ("Növbəti", _when(t.get("next")) if t.get("on") else "—"),
                ("Son göndərmə", _at(t.get("last")) if t.get("last") else "hələ yox"),
                ("Göndərilib", f"{t.get('cnt', 0)} dəfə"),
                ("Seçimlər", _opts_label(t)),
            ]
            if cid == GLOBAL_CHAT:
                b.append(("Bu çat", "🚫 istisna" if chat_id in (t.get("skip") or []) else "✅ daxil"))
            if t.get("err"):
                b.append(("⚠️ Son xəta", _preview(t["err"], 60)))
            if not _loop_alive(ub):
                b.append(("⚠️ Planlayıcı", "işləmir — logları yoxla"))
            return b

        def detail_rows(o, cid, t):
            tid = t["id"]
            r = [[o.btn(o.tr("⏸ Dayandır") if t.get("on") else o.tr("▶️ Başlat"), f"p{tid}"), o.btn(o.tr("📤 İndi göndər"), f"n{tid}")]]
            if t["kind"] == "int":
                r.append([o.btn(f"⏱ Interval: {_span(t['every'])}", f"i{tid}")])
            r.append(
                [
                    o.btn(o.tr("🧹 Köhnəni sil: {status}", status='🟢' if t.get('del') else '🔴'), f"x{tid}"),
                    o.btn(o.tr("🔕 Səssiz: {status}", status='🟢' if t.get('sil') else '🔴'), f"s{tid}"),
                ]
            )
            r.append([o.btn(o.tr("1️⃣ Bir dəfəlik: {status}", status='🟢' if t.get('once') else '🔴'), f"q{tid}")])
            if cid == GLOBAL_CHAT:
                r.append(
                    [
                        o.btn(o.tr("💬 Yalnız bu çata keçir"), f"l{tid}"),
                        o.btn(o.tr("✅ Bu çatı daxil et") if chat_id in (t.get("skip") or []) else o.tr("🚫 Bu çatı istisna et"), f"k{tid}"),
                    ]
                )
            else:
                r.append([o.btn(o.tr("🌐 Qlobal et (bütün qruplar)"), f"g{tid}")])
            r.append([o.btn(o.tr("✏️ Mesajı dəyiş"), f"e{tid}"), o.btn(o.tr("🗑 Sil"), f"d{tid}")])
            r.append([o.btn(o.tr("⬅️ Siyahı"), "back")])
            return r

        def detail_view(o, tid, note=None):
            cid, t = _find(tid)
            if t is None:
                return list_view(o, "tapşırıq artıq yoxdur")
            sl.ensure(t["id"], register_task)
            o.cur = t["id"]
            o.view = "global" if cid == GLOBAL_CHAT else "chat"
            return (
                card(detail_body(cid, t), footer=note or "idarə et: dayandır / göndər / interval / sil"),
                detail_rows(o, cid, t),
            )

        def interval_picker_view(o, cid, t, note=None):
            tid = t["id"]
            min_e = MIN_EVERY_GLOBAL if cid == GLOBAL_CHAT else MIN_EVERY
            body = [
                ("Tapşırıq", f"#{tid}"),
                ("Cari interval", f"hər {_span(t['every'])}"),
                ("Həddlər", f"Min: {_span(min_e)} · Maks: 24 saat"),
                ("# İzah", "Mesajın bu interval ilə təkrar göndərilməsi"),
            ]
            rows = [
                [
                    o.btn("-5", f"istep_{tid}_-5"),
                    o.btn("-1", f"istep_{tid}_-1"),
                    o.btn("+1", f"istep_{tid}_1"),
                    o.btn("+5", f"istep_{tid}_5"),
                ],
                [o.btn("✏️ Öz rəqəmini yaz", f"iinp_{tid}")],
                [o.btn("⬅️ Geri", f"o{tid}")],
            ]
            return card(body, footer=note or "+ / - düymələri ilə dəqiqəni dəyiş"), rows

        async def show_interval_picker(o, cid, t, note=None):
            await o.update(*interval_picker_view(o, cid, t, note))

        async def show_list(o, note=None):
            _WAIT.pop(chat_id, None)
            await o.update(*list_view(o, note))

        async def show_detail(o, tid, note=None):
            await o.update(*detail_view(o, tid, note))

        async def back_new(o, note=None, t=None):
            if t is not None:
                await show_detail(o, t["id"], note)
            else:
                await show_list(o, note)

        # ── tapşırıq düymələri (id-yə görə, tənbəl qeydiyyat) ──
        def register_task(tid):
            def act(fn):
                async def h(o, cb):
                    cid, t = _find(tid)
                    if t is None:
                        return await show_list(o, "tapşırıq artıq yoxdur")
                    note = await fn(o, cid, t)
                    if note is not False:
                        await show_detail(o, tid, note)
                return h

            async def a_open(o, cid, t):
                _WAIT.pop(chat_id, None)
                return None

            async def a_pause(o, cid, t):
                t["on"] = not t.get("on")
                t["next"] = _next(t, time.time()) if t["on"] else None
                _put(cid, t)
                return "▶️ başladıldı" if t["on"] else "⏸ dayandırıldı"

            async def a_now(o, cid, t):
                if (cid, t["id"]) in _RUNNING:
                    return "⏳ artıq göndərilir"
                await o.update(card([("Tapşırıq", f"#{t['id']}"), ("Status", "📤 göndərilir…")]), rows=[])
                _RUNNING.add((cid, t["id"]))
                ok, errs = await _run(client, cid, t["id"], t)
                return f"📤 {ok} çata göndərildi" + (f" · ⚠️ {len(errs)} xəta" if errs else "")

            async def a_int_open(o, cid, t):
                _WAIT.pop(chat_id, None)
                await show_interval_picker(o, cid, t)
                return False

            out.on(f"i{tid}")(act(a_int_open))
            out.on(f"ipick_{tid}")(act(a_int_open))

            for d in (-5, -1, 1, 5):
                def _make_istep(delta):
                    async def _h(o, cb):
                        cid, t = _find(tid)
                        if not t:
                            return await show_list(o, "tapşırıq artıq yoxdur")
                        min_e = MIN_EVERY_GLOBAL if cid == GLOBAL_CHAT else MIN_EVERY
                        step = delta * 60
                        t["every"] = max(min_e, min(86400, t["every"] + step))
                        if t.get("on"):
                            t["next"] = time.time() + t["every"]
                        _put(cid, t)
                        await show_interval_picker(o, cid, t, f"✓ hər {_span(t['every'])}")
                    return _h
                out.on(f"istep_{tid}_{d}")(_make_istep(d))

            async def a_int_inp(o, cb):
                cid, t = _find(tid)
                if not t:
                    return await show_list(o, "tapşırıq artıq yoxdur")
                _WAIT[chat_id] = {
                    "o": o, "stage": "interval", "cid": cid, "tid": tid, "ts": time.time(),
                    "back": lambda o, note=None, cur_t=t: (
                        show_interval_picker(o, cid, cur_t, note) if cur_t else show_list(o, note)
                    ),
                }
                await o.update(
                    card(
                        [
                            ("Tapşırıq", f"#{tid}"),
                            ("Cari interval", _span(t["every"])),
                            ("Daxiletmə", "Vaxt (məs: 30m, 2h, 45dəq), '+' və ya '-' yazın"),
                        ],
                        footer="bu çata yaz və ya 'imtina'",
                    ),
                    rows=[[o.btn("✖️ Ləğv et", f"ipick_{tid}")]],
                )
            out.on(f"iinp_{tid}")(a_int_inp)

            def toggle(field, on_txt, off_txt):
                async def f(o, cid, t):
                    t[field] = not t.get(field)
                    if field == "del" and not t[field]:
                        t["lm"] = {}
                    _put(cid, t)
                    return on_txt if t[field] else off_txt
                return f

            async def a_glob(o, cid, t):
                if cid == GLOBAL_CHAT:
                    return None
                note = ""
                if t["kind"] == "int" and t["every"] < MIN_EVERY_GLOBAL:
                    t["every"] = MIN_EVERY_GLOBAL
                    if t.get("on"):
                        t["next"] = time.time() + t["every"]
                    note = f" (interval {_span(MIN_EVERY_GLOBAL)}-yə qaldırıldı)"
                _move(cid, GLOBAL_CHAT, t)
                return "🌐 qlobal edildi — bütün qruplara gedəcək" + note

            async def a_loc(o, cid, t):
                if cid != GLOBAL_CHAT:
                    return None
                _move(cid, chat_id, t)
                return "💬 yalnız bu çata keçirildi"

            async def a_skip(o, cid, t):
                sk = t.setdefault("skip", [])
                if chat_id in sk:
                    sk.remove(chat_id)
                    note = "✅ bu çat daxil edildi"
                else:
                    sk.append(chat_id)
                    note = "🚫 bu çat istisna edildi"
                _put(cid, t)
                return note

            async def a_edit(o, cid, t):
                await o.update(
                    card(
                        [
                            ("Tapşırıq", f"#{t['id']}"),
                            ("İndiki", _content(t, 60)),
                            ("# Yeni mesaj", ""),
                            ("✍️ / 📎", "yeni mətni və ya medianı bu çata göndər"),
                        ],
                        footer="ləğv: 'imtina' yaz və ya düyməyə bas",
                    ),
                    rows=[[o.btn("✖️ Ləğv et", f"o{t['id']}")]],
                )
                _WAIT[chat_id] = {"o": o, "stage": "content", "tid": t["id"], "ts": time.time(), "back": back_new}
                return False

            async def a_delete(o, cid, t):
                _drop(cid, t["id"])
                await show_list(o, f"🗑 #{t['id']} silindi")
                return False

            for prefix, fn in (
                ("o", a_open),
                ("p", a_pause),
                ("n", a_now),
                ("x", toggle("del", "🧹 köhnə mesaj silinəcək", "köhnə mesaj qalacaq")),
                ("s", toggle("sil", "🔕 səssiz", "🔔 səsli")),
                ("q", toggle("once", "1️⃣ bir dəfəlik", "🔁 təkrarlanan")),
                ("g", a_glob),
                ("l", a_loc),
                ("k", a_skip),
                ("e", a_edit),
                ("d", a_delete),
            ):
                out.on(f"{prefix}{tid}")(act(fn))

        # ── siyahı düymələri (tablar və səhifələr — ScopedList) ──
        async def toggle_gskip(o, cb):
            if chat_id in _CFG["gskip"]:
                _CFG["gskip"].remove(chat_id)
                note = "✅ bu çat qlobal mesajları alacaq"
            else:
                _CFG["gskip"].append(chat_id)
                note = "🚫 bu çat bütün qlobal mesajlardan istisna edildi"
            _save_cfg()
            await show_list(o, note)

        out.on("gsk")(toggle_gskip)

        async def on_click_add(o, cb):
            cid = vcid(o)
            await o.update(
                card(
                    [
                        ("Rejim", f"➕ Yeni {'🌐 qlobal' if cid == GLOBAL_CHAT else '💬 lokal'} avto-mesaj"),
                        ("Format", "<vaxt> <mətn>"),
                        ("Nümunə 1", "2h Salam hamıya 👋"),
                        ("Nümunə 2", "09:00,21:00 Sabahınız xeyir"),
                        ("Nümunə 3", "-d 1-5 08:30 İş günü başladı"),
                        ("Qeyd", "yalnız vaxt yazsan, sonra mesajın özünü (media da olar) göndərərsən"),
                    ],
                    footer="bu çata yaz və ya 'imtina'",
                ),
                rows=[[o.btn("✖️ Ləğv et", "back")]],
            )
            _WAIT[chat_id] = {"o": o, "stage": "sched", "cid": cid, "ts": time.time(), "back": back_new}

        out.on("add")(on_click_add)
        out.on("back")(lambda o, cb: show_list(o))

        async def refresh(o):                       # sistem 🔄 düyməsi
            if o.cur:
                await show_detail(o, o.cur)
            else:
                await show_list(o)

        out.refresh = refresh

        # ── açılış ──
        if start and start[0] == "detail":
            text, rows = detail_view(out, start[1], start[2])
        elif start and start[0] == "content":
            _, cid, sched, opts = start
            text, rows = content_prompt(cid, sched, opts), [[out.btn("✖️ Ləğv et", "back")]]
        else:
            text, rows = list_view(out)
        await out.open(text, rows)
        if start and start[0] == "content":       # kart göndəriləndən SONRA gözləməyə başla
            _WAIT[chat_id] = {
                "o": out, "stage": "content", "cid": start[1], "sched": start[2],
                "opts": start[3], "ts": time.time(), "back": back_new,
            }
        return out

    # ── .amsg ──
    @ub.command(
        "amsg",
        pattern=r"^\.amsg(?:\s+([\s\S]+))?$",
        help=(
            "çata avtomatik (planlı) mesaj",
            "<b>İstifadə:</b>\n"
            "• <code>.amsg 2h Salam hamıya</code> — hər 2 saatdan bir\n"
            "• <code>.amsg 09:00,21:00 Sabahınız xeyir</code> — hər gün bu saatlarda\n"
            "• <code>.amsg -d 1-5 08:30 İş günü</code> — yalnız iş günləri\n"
            "• <code>.amsg -once 30m Xatırlatma</code> — 30 dəq sonra bir dəfə\n"
            "• <code>.amsg -g 6h Elan</code> — 🌐 bütün qruplara\n"
            "• mesaja reply → <code>.amsg 1d</code> — həmin mesajı (media ilə) göndərir\n\n"
            "<b>Bayraqlar:</b> <code>-g</code> qlobal · <code>-once</code> bir dəfə · "
            "<code>-now</code> ilkini dərhal · <code>-del</code> köhnəni sil · <code>-s</code> səssiz · "
            "<code>-d 1-5</code> günlər",
        ),
    )
    async def on_add(event):
        _ensure(ub, event.client)
        sched, opts, body = _parse_args(event.pattern_match.group(1) or "")
        cid = GLOBAL_CHAT if opts.get("g") else event.chat_id

        if not sched:
            await ub.out(
                event,
                card(
                    [
                        ("Interval", ".amsg 2h <mətn> · 30m · 1h30m · 1d"),
                        ("Saatla", ".amsg 09:00,21:00 <mətn>"),
                        ("Bir dəfə", ".amsg -once 45m <mətn>"),
                        ("Qlobal", ".amsg -g 6h <mətn>"),
                        ("Media", "mesaja reply → .amsg 2h"),
                        ("Siyahı", ".amsgs"),
                    ],
                    footer="vaxt formatı tanınmadı" if sched is False else "vaxt və mesaj daxil edin",
                ),
            )
            return

        err = _check(sched, cid)
        if err:
            await ub.out(event, card([("Xəta", err)]))
            return

        text, src = body, None
        if not body and event.is_reply:
            rep = await event.get_reply_message()
            if rep and (rep.raw_text or rep.media):
                text, src = await _capture(event.client, rep)

        view = "global" if cid == GLOBAL_CHAT else "chat"
        if not (text or src):
            await panel(event, view, start=("content", cid, sched, opts))
            return

        t = _create(cid, sched, opts, text, src)
        note = f"✓ #{t['id']} əlavə olundu"
        if cid == GLOBAL_CHAT:
            with contextlib.suppress(Exception):
                note += f" · hər dəfə ~{len(await _global_chats(event.client, t))} qrupa"
        await panel(event, view, start=("detail", t["id"], note))

    # ── .amsgs ──
    @ub.command(
        "amsgs",
        pattern=r"^\.amsgs(?:\s+(-g|global))?$",
        help=("avto-mesajlar kartı", "Planlı mesajları göstərir: dayandır, indi göndər, interval, qlobal et, sil."),
    )
    async def on_list(event):
        _ensure(ub, event.client)
        await panel(event, "global" if (event.pattern_match.group(1) or "") else "chat")

    # ── .amstop ──
    @ub.command(
        "amstop",
        pattern=r"^\.amstop(?:\s+([\d,\s#]+))?$",
        help=("avto-mesajı sil", "<code>.amstop 3</code> və ya <code>.amstop 3,5</code> — id-lər .amsgs kartında görünür"),
    )
    async def on_stop(event):
        ids = [x for x in re.split(r"[,\s#]+", event.pattern_match.group(1) or "") if x]
        if not ids:
            await ub.out(event, card([("İstifadə", ".amstop <id[,id]>"), ("Siyahı", ".amsgs")]))
            return
        rows = []
        for tid in ids:
            cid, t = _find(tid)
            if t is None:
                rows.append((f"#{tid}", "✗ tapılmadı"))
            else:
                _drop(cid, t["id"])
                rows.append((f"#{tid}", f"✓ silindi{' [🌐]' if cid == GLOBAL_CHAT else ''}"))
        out = await ub.out(event, card(rows))
        await out.close(DONE_VISIBLE)

    # ── .amnow ──
    @ub.command(
        "amnow",
        pattern=r"^\.amnow(?:\s+#?(\d+))?$",
        help=("avto-mesajı indi göndər", "<code>.amnow 3</code> — cədvələ toxunmadan dərhal göndərir"),
    )
    async def on_now(event):
        _ensure(ub, event.client)
        tid = event.pattern_match.group(1)
        cid, t = _find(tid) if tid else (None, None)
        if t is None:
            await ub.out(event, card([("İstifadə", ".amnow <id>"), ("Status", "✗ tapşırıq tapılmadı" if tid else "id lazımdır")]))
            return
        if (cid, t["id"]) in _RUNNING:
            await ub.out(event, card([("Tapşırıq", f"#{tid}"), ("Status", "⏳ artıq göndərilir")]))
            return
        _RUNNING.add((cid, t["id"]))
        ok, errs = await _run(event.client, cid, t["id"], t)
        rows = [("Tapşırıq", f"#{tid}"), ("Göndərildi", f"📤 {ok} çata")]
        if errs:
            rows.append(("⚠️ Xəta", f"{len(errs)} · {_preview(errs[-1], 50)}"))
        out = await ub.out(event, card(rows))
        await out.close(DONE_VISIBLE)

    # ── .amtz ──
    @ub.command(
        "amtz",
        pattern=r"^\.amtz(?:\s+(\S+))?$",
        help=("avto-mesaj saat qurşağı", "<code>.amtz Asia/Baku</code> — saatlı tapşırıqlar bu qurşaqla işləyir"),
    )
    async def on_tz(event):
        arg = (event.pattern_match.group(1) or "").strip()
        if not arg:
            await ub.out(event, card([("Saat qurşağı", _CFG["tz"]), ("İndi", f"{datetime.now(_tz()):%d.%m %H:%M}"), ("Dəyiş", ".amtz Region/Şəhər")]))
            return
        try:
            if ZoneInfo is None:
                raise ValueError("zoneinfo yoxdur")
            ZoneInfo(arg)
        except Exception as e:
            await ub.out(event, card([("Xəta", f"tanınmadı: {arg}"), ("Səbəb", str(e)[:60])]))
            return
        _CFG["tz"] = arg
        _save_cfg()
        now = time.time()
        for cid, fl in _TASKS.items():
            for t in fl.values():
                if t["kind"] == "time" and t.get("on"):
                    t["next"] = _next(t, now)
                    _put(cid, t)
        out = await ub.out(event, card([("Saat qurşağı", arg), ("İndi", f"{datetime.now(_tz()):%H:%M}"), ("Status", "✓ yadda saxlandı")]))
        await out.close(DONE_VISIBLE)

    # ── gözləmə: vaxt / mesaj məzmunu ──
    @ub.raw(events.NewMessage(outgoing=True))
    async def on_outgoing_wait(event):
        _ensure(ub, event.client)
        chat_id = event.chat_id
        w = _WAIT.get(chat_id)
        if not w:
            return
        msg = event.message
        o = w["o"]
        raw = (event.raw_text or "").strip()
        if (
            msg.date.timestamp() < w["ts"] - 2
            or getattr(msg, "via_bot_id", None)
            or msg.reply_markup is not None                       # inline kartın özü
            or (o.sent is not None and msg.id == o.sent.id)
            or (getattr(o.msg, "id", None) == msg.id)
            or "automsg@" in raw[:120]                            # mətn rejiminə düşən kart
            or raw.startswith(".")
        ):
            return

        if not msg.media and raw.lower() in CANCEL:
            _WAIT.pop(chat_id, None)
            with contextlib.suppress(Exception):
                await event.delete()
            await w["back"](o, "❌ ləğv edildi")
            return

        if w.get("stage") == "interval":
            cid = w["cid"]
            tid = w["tid"]
            cid, t = _find(tid)
            with contextlib.suppress(Exception):
                await event.delete()
            if not t:
                _WAIT.pop(chat_id, None)
                await w["back"](o, "tapşırıq artıq yoxdur", None)
                return
            _WAIT.pop(chat_id, None)
            min_e = MIN_EVERY_GLOBAL if cid == GLOBAL_CHAT else MIN_EVERY
            low = raw.lower()
            note = ""
            if low in ("-", "min", "0"):
                t["every"] = min_e
                note = f"✅ interval: {_span(min_e)} (minimum)"
            elif low in ("+", "max"):
                t["every"] = 86400
                note = "✅ interval: 1 gün (maksimum)"
            else:
                s = _parse_sched(raw)
                if s and s.get("kind") == "int":
                    t["every"] = max(min_e, min(86400, s["every"]))
                    note = f"✅ interval: {_span(t['every'])}"
                elif raw.isdigit():
                    t["every"] = max(min_e, min(86400, int(raw) * 60))
                    note = f"✅ interval: {_span(t['every'])}"
                else:
                    note = "⚠️ düzgün vaxt deyil (məs: 30m, 2h)"
            if t.get("on"):
                t["next"] = time.time() + t["every"]
            _put(cid, t)
            await w["back"](o, note, t)
            return

        if w["stage"] == "sched":
            sched, opts, body = _parse_args(raw)
            with contextlib.suppress(Exception):
                await event.delete()
            cid = w["cid"]
            if "g" in opts:
                cid = GLOBAL_CHAT if opts["g"] else chat_id
            err = "vaxt tanınmadı" if not sched else _check(sched, cid)
            if err:
                await o.update(
                    card([("Xəta", err), ("Nümunə", "2h Salam · 09:00 Sabahınız xeyir")], footer="yenidən yaz və ya 'imtina'"),
                    rows=[[o.btn("✖️ Ləğv et", "back")]],
                )
                return
            if body:
                _WAIT.pop(chat_id, None)
                t = _create(cid, sched, opts, body)
                await w["back"](o, f"✓ #{t['id']} əlavə olundu", t)
                return
            w.update(stage="content", cid=cid, sched=sched, opts=opts, ts=time.time())
            await o.update(content_prompt(cid, sched, opts), rows=[[o.btn("✖️ Ləğv et", "back")]])
            return

        # stage == "content"
        _WAIT.pop(chat_id, None)
        text, src = await _capture(event.client, msg)
        if src is None or src[0] == "me":           # surət alınmayıbsa orijinalı silmə
            with contextlib.suppress(Exception):
                await event.delete()
        if w.get("tid"):
            cid, t = _find(w["tid"])
            if t is None:
                await w["back"](o, "tapşırıq artıq yoxdur")
                return
            t["t"], t["src"] = text, src
            t.pop("err", None)
            _put(cid, t)
            await w["back"](o, "✏️ mesaj yeniləndi", t)
            return
        t = _create(w["cid"], w["sched"], w["opts"], text, src)
        await w["back"](o, f"✓ #{t['id']} əlavə olundu", t)

    # planlayıcı restartdan sonra da başlasın (register vaxtı ub.client hələ None ola bilər)
    @ub.raw(events.NewMessage())
    async def on_any_message(event):
        _ensure(ub, event.client)

    _ensure(ub)
