"""
bot.menus.views.audit
Qrup auditi, loqlar, topic seçimi və bildiriş hədəfləri pəncərəsi.
"""

import contextlib
import importlib
import logging
from html import escape
from types import SimpleNamespace

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.core import panel
from bot.menus.navigation import nav_row
from bot.menus.state import get_context
from bot.menus.views.base import LINE, SECTIONS, crumb

logger = logging.getLogger(__name__)

_AU_TOPICS: dict = {}


def _lg():
    """userbot/plugins/logger.py modulunu dinamik yükləyir."""
    return importlib.import_module("userbot.plugins.logger")


def _lgub():
    """userbot obyektini və ya DB shim obyektini qaytarır."""
    ctx = get_context()
    ub = getattr(ctx, "userbot", None)
    if ub is not None and getattr(ub, "db", None) is not None:
        return ub
    from userbot.core.userbot_db import get_udb

    return SimpleNamespace(db=get_udb())


def tg_client():
    """Hədəfləri yoxlamaq üçün Telethon client: əsas userbot, yoxdursa (bot-only rejim) işləyən asistan hesabı."""
    ctx = get_context()
    c = getattr(ctx, "telethon_client", None)
    with contextlib.suppress(Exception):
        if c is not None and c.is_connected():
            return c
    va = getattr(ctx, "voice_assistant", None)
    for sl in getattr(va, "slots", []):
        cl = getattr(sl, "client", None)
        with contextlib.suppress(Exception):
            if getattr(sl, "running", False) and cl is not None and cl.is_connected():
                return cl
    return c


def _au_cb(*parts) -> str:
    return "menu:au:" + ":".join(str(p) for p in parts)


def _au_btn(text: str, *parts) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=_au_cb(*parts))


def _au_dest(lg, ub, s: str, gcid=None) -> str:
    tc, tt, label = lg.get_stream_target(ub, s, gcid)
    if tc:
        name = next((x[2] for x in lg.recent_targets(ub) if x[0] == str(tc) and x[2] != str(tc)), "")
        label = (f"{name} ({tc})" if name else str(tc)) + (f" · topic {tt}" if tt else "")
        if "[miras]" in lg.get_stream_target(ub, s, gcid)[2]:
            label += " ↪"
        elif "[qrup]" in lg.get_stream_target(ub, s, gcid)[2]:
            label += " ★"
    else:
        label = "💾 Saved" + (" ↪" if "[miras]" in label or s != "au" else "")
    return escape(label[:48])


def audit_settings_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    lg, ub = _lg(), _lgub()
    audit_on = ub.db.kv_get("logger", "audit_enabled", "1") == "1"
    groups = lg.listed_groups(ub)
    n_on = sum(1 for c in groups if lg.is_audit_active_for(ub, c))
    lines = [
        crumb(SECTIONS["bots"], "🛡 Qrup auditi və loqlar"),
        LINE,
        f"🛡 Audit: <b>{'🟢 aktiv' if audit_on else '🔴 söndürülüb'}</b> · 🏘 izlənən qrup: <b>{n_on}</b>",
        "<i>Defolt olaraq heç bir qrup izlənmir. Qrupu 🏘 Qrup üzrə ayarlar bölməsində əlavə et — "
        "hər qrup üçün izləyən hesab (🎙 asistan / 📱 userbot), hadisələr və hədəf ayrıca seçilir.</i>",
        "",
    ]
    if note:
        lines.append(f"\nℹ️ {note}")
    rows = [
        [panel.toggle("🛡 Audit", audit_on, _au_cb("tg", "au"), compact=True), _au_btn("🏘 Qrup üzrə ayarlar", "gl")],
    ]
    rows.append(nav_row("bots", refresh="au:hub"))
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def _au_candidates(lg, ub) -> list:
    out = [(c, t, n) for c, t, n in lg.recent_targets(ub)]
    with contextlib.suppress(Exception):
        from bot.core import va_record

        seen = {c for c, _, _ in out}
        for cid, title in list(va_record.known_groups().items())[-6:]:
            if str(cid) not in seen and len(out) < 10:
                out.append((str(cid), None, title))
    return out[:10]


def audit_target_view(s: str, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    lg, ub = _lg(), _lgub()
    info = lg.STREAM_INFO[s]
    parent = info.get("parent")
    explicit = lg.has_explicit_target(ub, s)
    path = [SECTIONS["live"]] if s in ("vc", "tr", "spl") else [SECTIONS["bots"], "🛡 Qrup auditi"]
    if parent in lg.AUDIT_GROUPS:
        path.append(f"{lg.STREAM_INFO[parent]['icon']} {lg.STREAM_INFO[parent]['name']}")
    lines = [
        crumb(*path, f"🎯 {info['icon']} {info['name']}"),
        LINE,
        f"Hazırkı hədəf: <b>{_au_dest(lg, ub, s)}</b>",
        (
            "🎯 Bu hadisənin öz hədəfi var"
            if explicit
            else (
                f"↪ Öz hədəfi yoxdur — «{lg.STREAM_INFO[parent]['name']}» hədəfinə gedir"
                if parent
                else "↪ Öz hədəfi yoxdur — Saved Messages"
            )
        ),
        "",
        "✍️ <b>Yaz</b> düyməsinə bas və göndər:",
        "• <code>@kanal</code> · <code>-1001234567890</code>",
        "• topic: <code>-1001234567890 42</code> · <code>https://t.me/c/1234567890/42</code>",
        "• <code>me</code> — Saved Messages",
        "Və ya aşağıdan 📌 əvvəlki hədəflərdən birini seç.",
        "<i>Userbot hesabı həmin kanal / qrupda yaza bilməlidir (topic-li qrupda — topic ID).</i>",
    ]
    if note:
        lines.append(f"\nℹ️ {note}")
    back = ("hub",)
    rows = [
        [_au_btn("✍️ Kanal / qrup / topic yaz", "in", s)],
        [_au_btn("💾 Saved Messages", "me", s), _au_btn("🧪 Test göndər", "test", s)],
    ]
    cur_chat = lg.get_stream_target(ub, s)[0]
    if cur_chat and str(cur_chat).startswith("-100"):
        rows.insert(1, [_au_btn("🧵 Topic seç", "tp", s)])
    cands = _au_candidates(lg, ub)
    for i in range(0, len(cands), 2):
        rows.append(
            [
                _au_btn(f"📌 {n[:22]}" + (f" #{t}" if t else ""), "pk", s, i + j)
                for j, (c, t, n) in enumerate(cands[i : i + 2])
            ]
        )
    if explicit and lg.ub_topic(ub, s):
        rows.append([_au_btn("🧵 Topic-i sil (çat qalır)", "tc", s)])
    if explicit:
        rows.append([_au_btn("🔗 Öz hədəfini sil", "rs", s)])
    if s in ("vc", "tr", "spl"):
        rows.append(nav_row("live"))
    else:
        rows.append(panel.nav(_au_cb(*back), "Bölmə" if back[0] == "g" else "Audit"))
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def audit_apply_target(s: str, raw: str, gcid=None) -> str:
    lg, ub = _lg(), _lgub()
    info = lg.STREAM_INFO[s]
    label = f"{info['icon']} {info['name']}"
    client = tg_client()
    title = ""
    try:
        chat, topic, err = await lg.resolve_target(client, raw, 0)
    except Exception as e:
        logger.warning("hədəf yoxlanışı xətası: %s", e)
        return f"❌ Hədəf tapılmadı: {escape(str(e)[:120])}"
    if err or not chat:
        return f"❌ Hədəf tapılmadı: {escape(err or 'boş dəyər')}"
    if client is not None and chat != "me":
        with contextlib.suppress(Exception):
            ent = await client.get_entity(int(chat) if str(chat).lstrip("-").isdigit() else chat)
            title = getattr(ent, "title", None) or getattr(ent, "first_name", None) or ""
    if gcid is not None:
        lg.set_group_target(ub, gcid, s, chat, topic, title)
    else:
        lg.set_target(ub, s, chat, topic, title)
    if chat == "me":
        chat = "Saved"
    dest_str = (f"{title} ({chat})" if title and title != str(chat) else str(chat or "Saved")) + (
        f" · topic {topic}" if topic else ""
    )
    return f"✅ «{label}» hədəfi təyin olundu: <b>{escape(dest_str)}</b>"


async def audit_fetch_topics(chat) -> tuple[str, list] | None:
    client = tg_client()
    if not chat or str(chat) == "me" or client is None or not client.is_connected():
        return None
    from telethon.tl.functions.messages import GetForumTopicsRequest
    from telethon.tl.types import ForumTopic

    ent = await client.get_entity(int(chat) if str(chat).lstrip("-").isdigit() else chat)
    if not getattr(ent, "forum", False):
        return None
    res = await client(GetForumTopicsRequest(peer=ent, offset_date=None, offset_id=0, offset_topic=0, limit=60))
    topics = [
        (t.id, t.title)
        for t in res.topics
        if isinstance(t, ForumTopic) and t.id != 1 and not getattr(t, "hidden", False)
    ]
    return getattr(ent, "title", None) or str(chat), topics


def audit_topic_view(s: str, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    lg, ub = _lg(), _lgub()
    info, data = lg.STREAM_INFO[s], _AU_TOPICS.get(s)
    if not data:
        return audit_target_view(s, note or "⚠️ Topic siyahısı yoxdur — 🧵 Topic seç düyməsinə yenidən bas")
    _, cur_topic, _ = lg.get_stream_target(ub, s)
    lines = [
        crumb(SECTIONS["bots"], "🛡 Qrup auditi", f"🧵 {info['icon']} {info['name']}"),
        LINE,
        f"Qrup: <b>{escape(data['title'][:50])}</b> · {len(data['topics'])} topic",
        "Bu hadisənin loqları hansı topic-ə getsin?",
    ]
    if note:
        lines.append(f"\nℹ️ {note}")
    sel = lambda tid: str(cur_topic or "") == str(tid or "") and lg.has_explicit_target(ub, s)
    rows = [[panel.choice("💬 General (topicsiz)", sel(None), _au_cb("tp", s, "g"))]]
    rows += panel.grid(
        [panel.choice(f"🧵 {name[:26]}", sel(tid), _au_cb("tp", s, i)) for i, (tid, name) in enumerate(data["topics"])]
    )
    rows.append(panel.nav(_au_cb("t", s), "Hədəf", refresh_data=_au_cb("tp", s)))
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def audit_open_topics(s: str, chat, title: str = "") -> tuple[str, InlineKeyboardMarkup] | None:
    try:
        got = await audit_fetch_topics(chat)
    except Exception as e:
        logger.warning("🧵 topic-lər alınmadı (%s): %s", chat, e)
        return None
    if not got:
        return None
    _AU_TOPICS[s] = {"chat": str(chat), "title": title or got[0], "topics": got[1]}
    return audit_topic_view(s)


async def audit_test(s: str, gcid=None) -> str:
    lg, ub = _lg(), _lgub()
    client = tg_client()
    if client is None or not client.is_connected():
        return "⚠️ Əsas userbot qoşulmayıb — test göndərilmədi"
    info = lg.STREAM_INFO[s]
    if not lg.stream_on(ub, s, gcid):
        return "⚠️ Bu hadisə söndürülüb — əvvəlcə ✅ et"
    where = f" ({escape(_gname(lg, ub, gcid))})" if gcid is not None else ""
    msg = await lg.send_to_target(
        ub,
        client,
        stream=s,
        text=f"🧪 <b>Test</b> — {info['icon']} {escape(info['name'])}{where} loqları bura gələcək.",
        chat_id=gcid,
    )
    return (
        f"🧪 Test göndərildi → {_au_dest(lg, ub, s, gcid)}"
        if msg is not None
        else "❌ Göndərilmədi — userbot həmin çatda yaza bilmir? (loqa bax)"
    )


# ───────────────────────── 🏘 Qrup üzrə ayarlar ─────────────────────────
# Hər izlənən qrup üçün: auditi aç / bağla, hər hadisəni ayrıca aç / bağla və ayrıca hədəfə (kanal / qrup / topic)
# yönləndir. Qrupun öz ayarı yoxdursa ümumi ayarlar (yuxarıdakı bölmələr) işləyir.
GEVENTS = ("del1", "purge", "ed", "kick", "mute", "unban", "join", "left", "promo", "pin", "title", "photo")
GROUPS_PER_PAGE = 8


def _gname(lg, ub, cid) -> str:
    return lg.known_groups(ub).get(int(cid)) or str(cid)


def audit_groups_view(page: int = 0, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    lg, ub = _lg(), _lgub()
    groups = sorted(lg.listed_groups(ub).items(),
                    key=lambda x: (not lg.is_audit_active_for(ub, x[0]), (x[1] or "").lower()))
    chunk, page, pages = panel.paginate(groups, page, GROUPS_PER_PAGE)
    lines = [
        crumb(SECTIONS["bots"], "🛡 Qrup auditi", "🏘 Qrup üzrə ayarlar"),
        LINE,
        "🟢 — izlənir · ⚪ — söndürülüb. Siyahıda yalnız sənin əlavə etdiyin qruplar olur.",
        "<i>Yeni qrup: ➕ ilə @username / -100… / link yaz, ya da 📋 ilə hesabların gördüyü qruplardan seç.</i>",
    ]
    if not groups:
        lines.append("\n<i>Hələ qrup yoxdur.</i>")
    if note:
        lines.append(f"\nℹ️ {note}")
    rows = []
    for cid, name in chunk:
        dot = "🟢" if lg.is_audit_active_for(ub, cid) else "⚪"
        rows.append([_au_btn(f"{dot} {(name or str(cid))[:34]}", "gv", cid)])
    if (nav := panel.page_row(page, pages, lambda p: _au_cb("gl", p))):
        rows.append(nav)
    rows.append([_au_btn("➕ Qrup əlavə et (yaz)", "gadd"), _au_btn("📋 Bilinənlərdən seç", "gk")])
    if groups:
        rows.append([_au_btn("🗑 Siyahını təmizlə", "gclr")])
    rows.append(panel.nav(_au_cb("hub"), "Audit", refresh_data=_au_cb("gl", page)))
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def audit_discover_view(page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    """Hesabların mesaj gördüyü, amma siyahıda olmayan qruplar — toxunuşla əlavə edilir."""
    lg, ub = _lg(), _lgub()
    items = sorted(lg.discovered_groups(ub).items(), key=lambda x: (x[1] or "").lower())
    chunk, page, pages = panel.paginate(items, page, GROUPS_PER_PAGE)
    lines = [
        crumb(SECTIONS["bots"], "🛡 Qrup auditi", "🏘 Qruplar", "📋 Bilinən qruplar"),
        LINE,
        "Toxunduğun qrup siyahıya əlavə olunur və audit orada <b>açılır</b>.",
    ]
    if not items:
        lines.append("\n<i>Əlavə olunmamış qrup yoxdur (qrupda mesaj gəldikcə burada görünür).</i>")
    rows = [[_au_btn(f"➕ {(n or str(c))[:34]}", "gk", "add", c)] for c, n in chunk]
    if (nav := panel.page_row(page, pages, lambda p: _au_cb("gk", p))):
        rows.append(nav)
    rows.append(panel.nav(_au_cb("gl"), "Qruplar", refresh_data=_au_cb("gk", page)))
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def audit_clear_view() -> tuple[str, InlineKeyboardMarkup]:
    lg, ub = _lg(), _lgub()
    groups = lg.listed_groups(ub)
    on = sum(1 for c in groups if lg.is_audit_active_for(ub, c))
    lines = [
        crumb(SECTIONS["bots"], "🛡 Qrup auditi", "🏘 Qruplar", "🗑 Siyahını təmizlə"),
        LINE,
        f"Siyahıda <b>{len(groups)}</b> qrup var: 🟢 {on} izlənir · ⚪ {len(groups) - on} izlənmir.",
        "",
        "⚪ <b>İzlənməyənləri sil</b> — yalnız audit söndürülmüş qruplar siyahıdan çıxır.",
        "🗑 <b>Hamısını sil</b> — izlənənlər də daxil olmaqla bütün qrupların ayarları silinir.",
        "<i>Silinən qrup yenidən yalnız ➕ ilə əlavə olunur.</i>",
    ]
    rows = [
        [_au_btn(f"⚪ İzlənməyənləri sil ({len(groups) - on})", "gclr", "off")],
        [_au_btn(f"🗑 Hamısını sil ({len(groups)})", "gclr", "all")],
        [_au_btn("✖️ Ləğv et", "gl")],
    ]
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def audit_copy_view(dst, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    """Hansı qrupun ayarlarını kopyalayaq — dst qrupuna."""
    lg, ub = _lg(), _lgub()
    dst = int(dst)
    items = sorted(((c, n) for c, n in lg.listed_groups(ub).items() if c != dst), key=lambda x: (x[1] or "").lower())
    chunk, page, pages = panel.paginate(items, page, GROUPS_PER_PAGE)
    lines = [
        crumb(SECTIONS["bots"], "🛡 Qrup auditi", f"🏘 {escape(_gname(lg, ub, dst)[:20])}", "📋 Kopyala"),
        LINE,
        f"<b>{escape(_gname(lg, ub, dst))}</b> qrupuna hansı qrupun ayarları kopyalansın?",
        "Kopyalanır: hadisə açarları, hədəflər (topic ilə), izləyən hesab, 23:59 hesabat.",
        "<i>Bu qrupun əvvəlki həmin ayarları əvəz olunur; auditin açıq / bağlı vəziyyəti dəyişmir.</i>",
    ]
    if not items:
        lines.append("\n<i>Başqa qrup yoxdur — əvvəlcə ➕ ilə qrup əlavə et.</i>")
    rows = []
    for c, n in chunk:
        dot = "🟢" if lg.is_audit_active_for(ub, c) else "⚪"
        rows.append([_au_btn(f"{dot} {(n or str(c))[:30]} ({len(lg.group_settings(ub, c))})", "gcp", dst, c)])
    if (nav := panel.page_row(page, pages, lambda p: _au_cb("gcpg", dst, p))):
        rows.append(nav)
    rows.append(panel.nav(_au_cb("gv", dst), "Qrup"))
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def audit_apply_all_view(src) -> tuple[str, InlineKeyboardMarkup]:
    """Təsdiq: bu qrupun ayarları bütün digər qruplara tətbiq olunsun?"""
    lg, ub = _lg(), _lgub()
    src = int(src)
    others = [c for c in lg.listed_groups(ub) if c != src]
    lines = [
        crumb(SECTIONS["bots"], "🛡 Qrup auditi", f"🏘 {escape(_gname(lg, ub, src)[:20])}", "📤 Hamısına tətbiq et"),
        LINE,
        f"<b>{escape(_gname(lg, ub, src))}</b> qrupunun ayarları (<b>{len(lg.group_settings(ub, src))}</b> ədəd) "
        f"siyahıdakı digər <b>{len(others)}</b> qrupa tətbiq olunacaq.",
        "<i>Onların əvvəlki hadisə açarları, hədəfləri, izləyən hesab və hesabat ayarları əvəz olunur. "
        "Auditin açıq / bağlı vəziyyəti dəyişmir.</i>",
    ]
    rows = []
    if others:
        rows.append([_au_btn(f"✅ Bəli, {len(others)} qrupa tətbiq et", "gall", src, "ok")])
    rows.append([_au_btn("✖️ Ləğv et", "gv", src)])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def audit_gview(cid, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    lg, ub = _lg(), _lgub()
    cid = int(cid)
    name = _gname(lg, ub, cid)
    active = lg.is_audit_active_for(ub, cid)
    src_asst = lg.group_source(ub, cid) == "assistant" and bool(lg._assistant_slots(ub))
    lines = [
        crumb(SECTIONS["bots"], "🛡 Qrup auditi", "🏘 Qruplar", f"🏘 {escape(name[:24])}"),
        LINE,
        f"🏘 <b>{escape(name)}</b> · <code>{cid}</code>",
        f"🛡 Bu qrupda audit: <b>{'🟢 aktiv' if active else '🔴 bağlı'}</b>",
        f"👁 İzləyən hesab: <b>{'🎙 səsli yayım asistanı' if src_asst else '📱 userbot'}</b>"
        + ("" if src_asst or lg.group_source(ub, cid) == "userbot" else " <i>(asistan işləmir — userbot əvəz edir)</i>"),
        f"🎯 Bütün hadisələr: {_au_dest(lg, ub, 'au', cid)}",
        "",
        "<i>✅ / ▫️ — hadisəni bu qrup üçün aç / bağla · 🎯 — bu hadisənin bu qrupdakı ayrıca hədəfi "
        "(★ qrupun öz hədəfi · ↪ ümumi ayardan gəlir)</i>",
    ]
    if note:
        lines.append(f"\nℹ️ {note}")
    rows = [
        [
            panel.toggle("🛡 Bu qrupda audit", active, _au_cb("gat", cid), compact=True),
            _au_btn("🎯 Hamısı üçün hədəf", "gt", cid, "au"),
        ],
        [_au_btn(f"👁 İzləyən: {'🎙 Asistan' if lg.group_source(ub, cid) == 'assistant' else '📱 Userbot'}", "gsrc", cid)],
        [
            panel.toggle("📅 23:59 hesabat", lg.daily_on(ub, cid), _au_cb("gdaily", cid), compact=True),
            _au_btn("📄 İndi hesabat", "grep", cid),
        ],
        [_au_btn("📤 Hesabat hədəfi: " + (_au_dest(lg, ub, "report", cid)[:22]
                                           if lg.has_group_target(ub, cid, "report") or lg.has_group_target(ub, cid, "au")
                                           else "şəxsi çat"),
                 "gt", cid, "report")],
    ]
    for e in GEVENTS:
        ei = lg.STREAM_INFO[e]
        rows.append([
            panel.check(f"{ei['icon']} {ei['name']}", lg.stream_on(ub, e, cid), _au_cb("gtg", cid, e)),
            _au_btn(f"🎯 {_au_dest(lg, ub, e, cid)[:18]}", "gt", cid, e),
        ])
    rows.append([_au_btn("📋 Başqa qrupdan kopyala", "gcp", cid), _au_btn("📤 Hamısına tətbiq et", "gall", cid)])
    rows.append([_au_btn("♻️ Ayarları sıfırla", "gra", cid), _au_btn("🗑 Qrupu siyahıdan sil", "gdel", cid)])
    rows.append(panel.nav(_au_cb("gl"), "Qruplar", refresh_data=_au_cb("gv", cid)))
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def audit_gtarget_view(cid, s: str, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    lg, ub = _lg(), _lgub()
    cid = int(cid)
    info = lg.STREAM_INFO[s]
    explicit = lg.has_group_target(ub, cid, s)
    lines = [
        crumb(SECTIONS["bots"], "🛡 Qrup auditi", "🏘 Qruplar", f"🏘 {escape(_gname(lg, ub, cid)[:20])}", f"🎯 {info['icon']} {info['name']}"),
        LINE,
        f"Hazırkı hədəf: <b>{_au_dest(lg, ub, s, cid)}</b>",
        "★ Bu qrup üçün ayrıca hədəf var" if explicit else "↪ Bu qrup üçün ayrıca hədəf yoxdur — ümumi ayar işləyir",
        "",
        "✍️ <b>Yaz</b> düyməsinə bas və göndər:",
        "• <code>@kanal</code> · <code>-1001234567890</code>",
        "• topic: <code>-1001234567890 42</code> · <code>https://t.me/c/1234567890/42</code>",
        "• <code>me</code> — Saved Messages",
        "Və ya aşağıdan 📌 əvvəlki hədəflərdən birini seç.",
        "<i>Userbot hesabı həmin kanal / qrupda yaza bilməlidir.</i>",
    ]
    if note:
        lines.append(f"\nℹ️ {note}")
    rows = [
        [_au_btn("✍️ Kanal / qrup / topic yaz", "gi", cid, s)],
        [_au_btn("💾 Saved Messages", "gm", cid, s), _au_btn("🧪 Test göndər", "gtest", cid, s)],
    ]
    cur_chat = lg.get_stream_target(ub, s, cid)[0]
    if cur_chat and str(cur_chat).startswith("-100"):
        rows.insert(1, [_au_btn("🧵 Topic seç", "gtp", cid, s)])
    cands = _au_candidates(lg, ub)
    for i in range(0, len(cands), 2):
        rows.append([
            _au_btn(f"📌 {n[:22]}" + (f" #{t}" if t else ""), "gp", cid, s, i + j)
            for j, (c, t, n) in enumerate(cands[i:i + 2])
        ])
    if explicit and lg.ub_topic(ub, s, cid):
        rows.append([_au_btn("🧵 Topic-i sil (çat qalır)", "gtc", cid, s)])
    if explicit or lg.ub_group_flag(ub, cid, s):
        rows.append([_au_btn("🔗 Qrupun ayarını sil (ümumiyə qayıt)", "gr", cid, s)])
    rows.append(panel.nav(_au_cb("gv", cid), "Qrup"))
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def audit_gtopic_view(cid, s: str, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    lg, ub = _lg(), _lgub()
    cid = int(cid)
    info, data = lg.STREAM_INFO[s], _AU_TOPICS.get(f"g{cid}:{s}")
    if not data:
        return audit_gtarget_view(cid, s, note or "⚠️ Topic siyahısı yoxdur — 🧵 Topic seç düyməsinə yenidən bas")
    _, cur_topic, _ = lg.get_stream_target(ub, s, cid)
    lines = [
        crumb(SECTIONS["bots"], "🛡 Qrup auditi", f"🧵 {info['icon']} {info['name']}"),
        LINE,
        f"Hədəf qrup: <b>{escape(data['title'][:50])}</b> · {len(data['topics'])} topic",
        "Bu hadisənin loqları hansı topic-ə getsin?",
    ]
    if note:
        lines.append(f"\nℹ️ {note}")
    own = lg.has_group_target(ub, cid, s)
    sel = lambda tid: own and str(cur_topic or "") == str(tid or "")
    rows = [[panel.choice("💬 General (topicsiz)", sel(None), _au_cb("gtp", cid, s, "g"))]]
    rows += panel.grid([
        panel.choice(f"🧵 {name[:26]}", sel(tid), _au_cb("gtp", cid, s, i)) for i, (tid, name) in enumerate(data["topics"])
    ])
    rows.append(panel.nav(_au_cb("gt", cid, s), "Hədəf", refresh_data=_au_cb("gtp", cid, s)))
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def audit_open_gtopics(cid, s: str, chat) -> tuple[str, InlineKeyboardMarkup] | None:
    try:
        got = await audit_fetch_topics(chat)
    except Exception as e:
        logger.warning("🧵 topic-lər alınmadı (%s): %s", chat, e)
        return None
    if not got:
        return None
    _AU_TOPICS[f"g{int(cid)}:{s}"] = {"chat": str(chat), "title": got[0], "topics": got[1]}
    return audit_gtopic_view(cid, s)


__all__ = [
    "_AU_TOPICS",
    "_au_btn",
    "_au_candidates",
    "_au_cb",
    "_au_dest",
    "_lg",
    "_lgub",
    "GEVENTS",
    "_gname",
    "audit_apply_target",
    "audit_gtarget_view",
    "audit_gtopic_view",
    "audit_apply_all_view",
    "audit_clear_view",
    "audit_copy_view",
    "audit_discover_view",
    "audit_gview",
    "audit_groups_view",
    "audit_open_gtopics",
    "audit_fetch_topics",
    "audit_open_topics",
    "audit_settings_view",
    "audit_target_view",
    "audit_test",
    "audit_topic_view",
]
