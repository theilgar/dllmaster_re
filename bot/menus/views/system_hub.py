"""
bot.menus.views.system_hub
Sistem və kommunikasiya xülasələri, bölmə pəncərələri, canlı fastfetch və istifadəçi izləmə middleware-i.
"""

import asyncio
import contextlib
import logging
import time
from datetime import datetime
from html import escape
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.core import panel
from bot.menus.navigation import nav_row
from bot.menus.state import get_context
from bot.menus.views.base import (
    LINE,
    SECTIONS,
    TRACK_THROTTLE,
    crumb,
)
from bot.menus.views.dev import dev_button_label
from bot.menus.views.performance import perf_button_label, perf_hub_view
from bot.menus.views.server import (
    srv_button_label,
    userbots_button_label,
    va_button_label,
)
from common.database import get_db, is_maintenance_mode
from common.diagnostics import run_fastfetch
from common.formatting import fmt_duration_words
from common.menu import (
    _btn,
    _counts,
    _safe,
    _short_num,
    aud_overview_text,
    biz_overview_text,
    bots_overview_text,
    com_overview_text,
    depo_overview_text,
    live_overview_text,
    sys_overview_text,
    ui_button_label,
)
from common.utilities import spawn

logger = logging.getLogger(__name__)

FETCH_INTERVAL = 3  # fastfetch yenilənməsi (san.)
FETCH_MAX_RUNTIME = 180  # avtomatik dayanma (san.)
fetch_sessions: dict[tuple[int, int], dict] = {}

HELP_TEXT = (
    crumb("❓ Bələdçi") + "\n" + LINE + "\n"
    "📖 <b>DLLMaster İdarəetmə Bələdçisi</b>\n"
    "<i>Sistemdəki bütün bölmələrin təyinatı və idarə qaydaları:</i>\n\n"
    "🏠 <b>İdarə Paneli (Əsas):</b>\n"
    "• Günlük yükləmələr, aktiv istifadəçi və qrup statistikası.\n"
    "• Depo, Userbot və Server vəziyyətinin canlı icmalı və xəbərdarlıqlar.\n\n"
    "👥 <b>İstifadəçilər və Qruplar:</b>\n"
    "• Auditoriya analitikası, qrafiklər, aktiv istifadəçilər və çat axtarışı.\n"
    "• Qrupların fərdi və ya kütləvi ayarları (flood, reklam, link filtri).\n\n"
    "💎 <b>Premium və Gəlir:</b>\n"
    "• Premium abunəçilər, Telegram Stars qiymətləri və ödəniş jurnalı.\n"
    "• Səlahiyyət pillələri (👑 Sahibi, 💎 Premium, 🆓 Pulsuz istifadəçi limitləri).\n\n"
    "📣 <b>Kommunikasiya:</b>\n"
    "• Qruplara və istifadəçilərə canlı bildiriş yayımı (Broadcast).\n"
    "• Şablon mesajlar (Start salamı, qrup bildirişləri, bələdçilər).\n\n"
    "🎵 <b>Musiqi & Audio:</b>\n"
    "• YouTube (hesablar/cookies), Spotify inteqrasiyası və inline axtarış.\n"
    "• Səs formatları, MP3 bitreyt ayarları və audio emal qaydaları.\n\n"
    "📦 <b>Depo Sistemi:</b>\n"
    "• Musiqi anbarı və avtomatik arxiv doldurucusu (Canlı monitorinq).\n"
    "• Depo kanalı, köməkçi botlar və kənar mənbələrin idarəsi.\n\n"
    "🎙 <b>Canlı Yayım & Səs Çatı:</b>\n"
    "• Qrupların səsli söhbətində canlı musiqi oxutma asistanı (/play).\n"
    "• Yayımın yüksək keyfiyyətli qeydiyyatı (vcrec) və hədəf çata ötürülməsi.\n\n"
    "📱 <b>Userbot & Qrup Auditi:</b>\n"
    "• Şəxsi Telegram hesabları (Userbot 1 və Səs Asistanı).\n"
    "• Qrup hadisələri auditi (silinən/redaktə olunan mesajlar, banlar, üzvlər).\n"
    "• Hər gecə 23:59-da şəxsi çata göndərilən avtomatik audit hesabatı.\n\n"
    "🖥 <b>Sistem & Diaqnostika:</b>\n"
    "• 📡 Server yükü (CPU, RAM, Disk) və canlı /htop monitoru.\n"
    "• 🗂 <b>Layihə Yaddaşı:</b> Bütün layihənin, pluginlərin və bazaların ölçüsü + keş təmizliyi.\n"
    "• 🗄 Ehtiyat nüsxələr (Backup), kod arxivi və xəta loqları.\n"
    "• 🌐 Proxy / Tor bağlantısı və texniki iş rejimi (Maintenance).\n"
    + LINE + "\n"
    "💡 <b>Naviqasiya İşarələri:</b>\n"
    "• 🔄 Yenilə · ⬅️ Bir addım geri · 🏠 Əsas menyu · ✖️ Bağla\n"
    "• 🟢 Aktiv · 🔴 Deaktiv · 🤖 Avtomatik · ✋ Əl ilə\n"
)


def help_view() -> tuple[str, InlineKeyboardMarkup]:
    return HELP_TEXT, InlineKeyboardMarkup(
        inline_keyboard=[[_btn("⌨️ Userbot moderasiya əmrləri", "mod_cmds")], nav_row("main")]
    )


def sub_kb(refresh: str = "") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[nav_row("srv", refresh=refresh or "")])


def _ctx_label(attr: str, default: str) -> str:
    ctx = get_context()
    fn = getattr(ctx, attr, None)
    if fn is None:
        return f"{default} · ❌"
    try:
        return fn()
    except Exception:
        return default


def backup_button_label() -> str:
    return _ctx_label("backup_label", "💾 Yedəkləmə")


def _maint_active() -> bool:
    from common import maintenance
    return maintenance.is_active()


def _maint_on() -> bool:
    return bool(_safe(_maint_active, False))


def _ui_label() -> str:
    return ui_button_label()


def sys_text() -> str:
    return sys_overview_text(get_context())


def sys_kb() -> InlineKeyboardMarkup:
    ctx = get_context()
    lbl = lambda name, default: (getattr(ctx, name, None) or (lambda: default))()
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn(srv_button_label(), "srv")],
            [_btn(perf_button_label(), "perf")],
            [_btn(dev_button_label(), "dev")],
            [
                _btn("🗂 Layihə Yaddaşı", "storage"),
                InlineKeyboardButton(
                    text=lbl("proxy_label", "🌐 Proxy / Tor"), callback_data="px:panel"
                ),
            ],
            nav_row("main"),
        ]
    )


def settings_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    text = sys_text()
    if note:
        text += f"\n\nℹ️ {note}"
    return text, sys_kb()


def accel_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    return perf_hub_view(note=note)


def _logs_cfg() -> dict:
    """logs_plugin-in ayarları: hədəf 📍 Hədəflərdən (dest:logs:*), interval / səviyyə logs:* açarlarından."""
    from common import destinations as dest
    db = get_db()
    target = dest.get("logs") or {}
    iv = db.get_setting("logs:interval")
    return {
        "chat": target.get("chat_id"),
        "title": target.get("title"),
        "iv": int(iv) if iv and str(iv).isdigit() else 0,
        "warn": db.get_setting("logs:level") == "warn",
    }


def _iv_label(minutes: int) -> str:
    return fmt_duration_words(minutes * 60) if minutes else "avto-göndəriş söndürülüb"


def arch_button_label() -> str:
    parts = []
    with contextlib.suppress(Exception):
        parts.append("💾" + ("❌" if "❌" in backup_button_label() else "✅"))
    with contextlib.suppress(Exception):
        lc = _logs_cfg()
        parts.append("📜" + ("✅" if lc["chat"] else "⚠️"))
    return "🗄 Arxiv · yedək · loglar" + (" · " + " ".join(parts) if parts else "")


def arch_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    lc = _logs_cfg()
    title = lc["title"] or lc["chat"]
    ctx = get_context()
    lbl = lambda name, default: (getattr(ctx, name, None) or (lambda: default))()
    lines = [
        crumb(SECTIONS["sys"], "🗄 Arxiv · yedək · loglar") + "\n" + LINE,
        f"💾 <b>Yedək (baza):</b> {escape(backup_button_label())}",
        f"📦 <b>Kod arxivi:</b> {escape((getattr(ctx, 'archive_label', None) or (lambda: '—'))())}",
        "📜 <b>Loglar:</b> "
        + (
            f"{escape(str(title))} · {('hər ' if lc['iv'] else '') + _iv_label(lc['iv'])} · "
            f"{'⚠️+❌' if lc['warn'] else 'hamısı'}"
            if lc["chat"]
            else "⚠️ kanal təyin edilməyib"
        ),
        f"📍 <b>Hədəflər:</b> {escape(lbl('destinations_label', 'Kanal / mövzu seçimi'))}",
    ]
    if note:
        lines.append(f"\n{note}")
    rows = [
        [
            InlineKeyboardButton(text="💾 Yedəkləmə", callback_data="bk:panel"),
            InlineKeyboardButton(text="📦 Kod arxivi", callback_data="ar:panel"),
        ],
        [
            InlineKeyboardButton(text="📜 Loglar", callback_data="lg:panel"),
            InlineKeyboardButton(
                text=lbl("destinations_label", "📍 Hədəflər"), callback_data="ds:panel"
            ),
        ],
    ]
    if lc["chat"]:
        rows.append([InlineKeyboardButton(text="🧪 Log kanalını test et (indi göndər)", callback_data="lg:send")])
    rows.append(nav_row("dev"))
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def depo_row() -> list:
    ctx = get_context()
    filler = getattr(ctx, "depo_filler", None)
    if filler is None:
        return [InlineKeyboardButton(text="📦 Depo", callback_data="df:panel")]
    try:
        on = filler.running
        parts = ["🟢" if on else "🔴"]
        if on:
            parts.append(f"🧵{filler.workers}")
        parts.append(f"📋{_short_num(len(filler.queue))}")
        hp = getattr(filler, "helpers", None)
        if hp and hp.helpers:
            parts.append(f"🤖{len(hp.active())}/{len(hp.helpers)}")
        try:
            from bot.core.youtube_handler import cooldown_left

            if cooldown_left():
                parts.append("⏸403")
        except Exception as _e:
            logger.debug("depo_row cooldown yoxlanışı xətası: %r", _e)
        stall = filler.stall_reason() if hasattr(filler, "stall_reason") else ""
        if stall:
            parts.append("⚠️")
        label = "📦 Depo · " + " ".join(parts)
    except Exception:
        on, stall, label = False, "", "📦 Depo"
    quick = "▶️" if not on else ("⚡" if stall else "⏸")
    return [InlineKeyboardButton(text=label, callback_data="df:panel"), _btn(quick, "dq")]


def depo_view(ctx_or_note: Any = None, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    if isinstance(ctx_or_note, str):
        note = ctx_or_note
        ctx = get_context()
    elif ctx_or_note is not None:
        ctx = ctx_or_note
    else:
        ctx = get_context()
    text = depo_overview_text(ctx)
    if note:
        text += f"\n\nℹ️ {note}"
    dcl = getattr(ctx, "depo_channel_label", None)
    rows = [
        depo_row(),
        [
            InlineKeyboardButton(
                text=(dcl() if dcl else "📢 Depo kanalı"), callback_data="dc:panel"
            ),
        ],
        nav_row("main"),
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def live_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    from bot.menus.views.audit import _au_btn

    ctx = get_context()
    text = live_overview_text(ctx)
    if note:
        text += f"\n\nℹ️ {note}"
    rows = []
    va = getattr(ctx, "voice_assistant", None)
    if va is not None:
        rows.append([InlineKeyboardButton(text=va_button_label(), callback_data="va:panel")])
    rows += [
        [_btn("⏺ Yayım qeydi", "vcrec"), _au_btn("🎯 Qeyd faylları", "t", "vc")],
        [_btn("📣 Qrup reklamı", "ads")],
        nav_row("main"),
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def bots_view(note: str = "", ctx: Any = None) -> tuple[str, InlineKeyboardMarkup]:
    from bot.menus.views.audit import _au_cb, _lgub

    if ctx is None:
        ctx = get_context()
    text = bots_overview_text(ctx)
    if note:
        text += f"\n\nℹ️ {note}"
    rows = []
    ub_on = getattr(ctx, "userbot_enabled", True)
    if ub_on:
        rows.append([_btn(userbots_button_label(), "ubacc")])
    if ub_on or getattr(ctx, "voice_assistant", None) is not None:      # bot-only rejimdə audit asistanla işləyir
        audit_on = True
        with contextlib.suppress(Exception):
            audit_on = _lgub().db.kv_get("logger", "audit_enabled", "1") == "1"
        rows.append(
            [_btn(f"🛡 Qrup auditi {'🟢' if audit_on else '🔴'} — ayarlar", "audit_hub")]
        )
    if ub_on:
        rows.append(
            [
                _btn("📢 Planlı Mesajlar (.amsg)", "ub_amsg"),
                _btn("🎯 Açar Söz Cavabları (.filter)", "ub_filter"),
            ]
        )
        rows.append([_btn("👤 Profil & Bio (.profile)", "ub_profile")])
    rows.append(nav_row("main"))
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def mod_cmds_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    from bot.menus.views.audit import _au_btn

    lines = [
        crumb(SECTIONS["bots"], "⌨️ Moderasiya Əmrləri"),
        LINE,
        "🛡 <b>Userbot Moderasiya və Audit Əmrləri:</b>\n",
        "🚫 <b>Cəza Əmrləri:</b>",
        "• <code>.ban [vaxt] [səbəb]</code> — istifadəçini qrupdan ban edir",
        "• <code>.unban</code> — istifadəçinin banını qaldırır",
        "• <code>.mute [vaxt]</code> — istifadəçini susdurur (yazdığı silinir)",
        "• <code>.unmute</code> — susdurulmanı ləğv edir",
        "• <code>.kick</code> — istifadəçini qrupdan qovur\n",
        "👮 <b>İdarəetmə & Mesajlar:</b>",
        "• <code>.promote</code> / <code>.demote</code> — admin təyin edir / hüquqları alır",
        "• <code>.pin [loud]</code> / <code>.unpin</code> — mesajı bərkidir / açır",
        "• <code>.del</code> / <code>.purge [say]</code> — mesajları silir\n",
        "📊 <b>Audit Hesabatı (Bot):</b>",
        "• <code>/audit</code> (və ya <code>/hesabat</code>) — günün tam HTML hesabatını şəxsi çata göndərir",
        "• <code>/audit on</code> / <code>/audit off</code> — bütün qrupların 23:59 avtomatik hesabatını açır / söndürür\n",
        "ℹ️ <i>Auditi və 23:59 hesabatını hər qrup üçün ayrıca 📱 Userbot &amp; Audit → 🛡 Qrup auditi → 🏘 Qrup üzrə ayarlar bölməsində idarə edirsən.</i>",
    ]
    if note:
        lines.append(f"\n{note}")
    rows = [nav_row("help", refresh="mod_cmds")]
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def ub_no_inline_list_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    from common.database import get_userbot_inline_disabled_chats

    disabled_chats = get_userbot_inline_disabled_chats()
    now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

    head = f"🚫 <b>inline monitoru</b> · <b>{len(disabled_chats)} çat</b>\n<i>{now_str} · userbot filtri</i>"

    db = None
    with contextlib.suppress(Exception):
        db = get_db()
    rows = []

    lines = [
        f"Sts söndürülmüş çatlar: {len(disabled_chats)} ədəd  rejim mətn/Fastfetch",
        "",
        "# SÖNDÜRÜLMÜŞ ÇATLAR              ID",
    ]
    if not disabled_chats:
        lines.append("  (heç bir çatda söndürülməyib — bütün çatlarda aktivdir)")
    else:
        for i, cid in enumerate(disabled_chats[:20], 1):
            chat_info = db.get_chat(cid) if db else None
            title = (chat_info.get("title") if chat_info else None) or f"Çat {cid}"
            clean_t = str(title).replace("\n", " ").strip()
            short_t = clean_t if len(clean_t) <= 26 else clean_t[:25] + "…"
            lines.append(f"{i:2d} {short_t:28s} {cid}")
            rows.append(
                [
                    _btn(
                        f"🟢 {short_t} (aç)",
                        f"menu:ub_no_inline_toggle:{cid}",
                    )
                ]
            )

    if note:
        lines.append("──────────────────────────────────────")
        lines.append(f"ℹ️ {note}")

    from common.menu import render_card

    card_text = render_card(head, lines)
    rows.append(panel.nav("is:panel", "İnline", refresh_data="menu:ub_no_inline_list"))
    return card_text, InlineKeyboardMarkup(inline_keyboard=rows)


def aud_view(note: str = "", ctx: Any = None) -> tuple[str, InlineKeyboardMarkup]:
    if ctx is None:
        ctx = get_context()
    text = aud_overview_text(ctx)
    if note:
        text += f"\n\nℹ️ {note}"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("📊 Statistika", "stats")],
            [_btn("👤 İstifadəçilər", "users:all:0"), _btn("💬 Qruplar", "grp:groups:0")],
            [_btn("🔍 Axtar", "usearch"), _btn("📄 CSV ixrac", "csv")],
            [InlineKeyboardButton(text="🌐 Kütləvi ayarlar", callback_data="pm_grp:all_panel")],
            nav_row("main", refresh="aud"),
        ]
    )
    return text, kb


def biz_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    ctx = get_context()
    c = _counts()
    text = biz_overview_text(ctx)
    if note:
        text += f"\n\nℹ️ {note}"
    from common.database import is_tip_prompt_enabled

    t_on = is_tip_prompt_enabled()
    tl = (getattr(ctx, "tiers_label", None) or (lambda: "🎛 İmkanlar və limitlər"))()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn(f"💎 Premiumlar ({c['prem']})", "prm:0"), _btn("➕ Premium ver", "prm_add")],
            [_btn("⭐ Qiymətlər", "st"), _btn("🧾 Ödənişlər", "st_pay:0")],
            [
                InlineKeyboardButton(text=tl, callback_data="tf:panel"),
                panel.toggle("☕ Bəxşiş", t_on, "menu:toggle_tip", compact=True),
            ],
            nav_row("main"),
        ]
    )
    return text, kb


def com_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    ctx = get_context()
    text = com_overview_text(ctx)
    if note:
        text += f"\n\nℹ️ {note}"
    rows = [
        [_btn("📢 Broadcast", "bc"), _btn("🎨 Mesaj şablonları", "ms")],
        [_btn("🔘 Start düymələri", "sbtn"), _btn("☕ Bəxşiş mesajı", "tipmsg")],
        nav_row("main"),
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def start_buttons_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    from common.database import START_BUTTON_META, is_start_button_enabled

    now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

    priv_btns = {k: v for k, v in START_BUTTON_META.items() if v.get("scope") == "private"}
    grp_btns = {k: v for k, v in START_BUTTON_META.items() if v.get("scope") == "group"}

    act_priv = sum(1 for k in priv_btns if is_start_button_enabled(k))
    act_grp = sum(1 for k in grp_btns if is_start_button_enabled(k))

    lines = [
        f"🔘 <b>start düymələri</b> · 🟢 aktiv\n<i>{now_str} · şəxsi: {act_priv}/{len(priv_btns)} · qrup: {act_grp}/{len(grp_btns)}</i>",
        LINE,
        "<b>Şəxsi çat düymələri:</b>",
    ]
    k_rows = []
    for k, meta in priv_btns.items():
        on = is_start_button_enabled(k)
        icon = "🟢" if on else "🔴"
        lines.append(f"• {icon} {meta['label']} — {meta.get('desc', '')}")
        k_rows.append([panel.toggle(meta["label"], on, f"menu:sbtn_tog:{k}", compact=True)])

    lines.append("\n<b>Qrup çat düymələri:</b>")
    for k, meta in grp_btns.items():
        on = is_start_button_enabled(k)
        icon = "🟢" if on else "🔴"
        lines.append(f"• {icon} {meta['label']} — {meta.get('desc', '')}")
        k_rows.append([panel.toggle(meta["label"], on, f"menu:sbtn_tog:{k}", compact=True)])

    if note:
        lines.append(f"\nℹ️ {note}")

    k_rows.append([_btn("👁 /start Önizləməsi", "sbtn_prev"), _btn("♻️ Hamısını aktiv et", "sbtn_reset")])
    k_rows.append(nav_row("com"))
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=k_rows)


def audio_buttons_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    from common.database import get_auto_hide_duration, is_audio_button_enabled

    mix_on = is_audio_button_enabled("mix")
    plus_on = is_audio_button_enabled("plus")
    hide_on = is_audio_button_enabled("hide")
    sec = get_auto_hide_duration()
    timer_txt = "Daimi (heç vaxt silinmir)" if sec == 0 else f"{sec} saniyə"

    lines = [
        crumb(SECTIONS["mus"]) + " › 🎛 <b>Mahnı düymələri</b>",
        LINE,
        "Mahnı çatda və ya inline göndərilərkən altındakı düymələrin və taymerin tənzimlənməsi:\n",
        f"• <b>🔀 Mix düyməsi:</b> {'🟢 Aktiv (göstərilir)' if mix_on else '🔴 Deaktiv (gizlədilib)'}",
        f"• <b>➕ Playlist düyməsi:</b> {'🟢 Aktiv (göstərilir)' if plus_on else '🔴 Deaktiv (gizlədilib)'}",
        f"• <b>🙈 Gizlət düyməsi:</b> {'🟢 Aktiv (göstərilir)' if hide_on else '🔴 Deaktiv (gizlədilib)'}",
        f"• <b>⏱ Avtomatik gizlənmə taymeri:</b> <b>{timer_txt}</b>",
        "  <i>(0 olduqda düymələr həmişə qalır, saniyə seçildikdə həmin vaxtdan sonra avtomatik silinir)</i>",
    ]
    if note:
        lines.append(f"\n{note}")

    rows = [
        [
            panel.toggle("🔀 Mix", mix_on, "kb_btn:t:mix"),
            panel.toggle("➕ Playlist", plus_on, "kb_btn:t:plus"),
        ],
        [panel.toggle("🙈 Gizlət", hide_on, "kb_btn:t:hide")],
        [panel.btn(f"⏱ Gizlənmə taymeri: {timer_txt}", "kb_btn:pick")],
        [panel.btn("♻️ Standart ayarlar", "kb_btn:reset")],
        nav_row("mus"),
    ]
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def audio_buttons_picker_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    from common.database import get_auto_hide_duration

    sec = get_auto_hide_duration()
    timer_txt = "Daimi (silinmir)" if sec == 0 else f"{sec} saniyə"
    lines = [
        crumb(SECTIONS["mus"]) + " › 🎛 <b>Mahnı düymələri › Taymer</b>",
        LINE,
        "⏱ <b>Mahnı düymələrinin avtomatik gizlənmə vaxtı:</b>\n",
        f"Hazırkı dəyər: <b>{timer_txt}</b>\n",
        "<i>(0 olduqda düymələr həmişə qalır, saniyə seçildikdə həmin vaxtdan sonra avtomatik silinir)</i>",
    ]
    if note:
        lines.append(f"\n{note}")
    rows = panel.stepper("kb_btn:vstep:", "kb_btn:vinp") + [panel.nav("kb_btn:panel", "Düymələr")]
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def fetch_kb(sess: dict, running: bool = True) -> InlineKeyboardMarkup:
    if not running:
        return InlineKeyboardMarkup(
            inline_keyboard=[[_btn("▶️ Yenidən başlat", "fetch")], nav_row("srv")]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn("▶️ Davam" if sess["paused"] else "⏸ Fasilə", "fetch_pause"),
                _btn("⏹ Dayandır", "fetch_stop"),
            ],
            nav_row("srv"),
        ]
    )


def start_fetch_live(key: tuple[int, int], message: Message):
    for k, old in list(fetch_sessions.items()):
        if k != key:
            old["stop"] = True
    sess = {"paused": False, "stop": False, "until": time.time() + FETCH_MAX_RUNTIME}
    fetch_sessions[key] = sess

    async def _loop():
        last_text = None
        try:
            while time.time() < sess["until"] and not sess["stop"]:
                if not sess["paused"]:
                    out = await run_fastfetch()
                    text = f"🖥 <b>Fastfetch</b> (canlı)\n\n<code>{escape(out)}</code>"
                    if text != last_text:
                        last_text = text
                        try:
                            await message.edit_text(
                                text, parse_mode="HTML", reply_markup=fetch_kb(sess, True)
                            )
                        except Exception as e:
                            if "not modified" not in str(e).lower():
                                break
                await asyncio.sleep(FETCH_INTERVAL)
        finally:
            fetch_sessions.pop(key, None)
            with contextlib.suppress(Exception):
                await message.edit_reply_markup(reply_markup=fetch_kb(sess, False))

    spawn(_loop())


def stop_fetch_on(message: Message):
    if not message:
        return
    sess = fetch_sessions.get((message.chat.id, message.message_id))
    if sess:
        sess["stop"] = True


class UserTrackerMiddleware(BaseMiddleware):
    def __init__(self):
        self._last = {}

    async def __call__(self, handler, event, data):
        try:
            await self._track(event)
        except Exception as e:
            logger.warning("İstifadəçi izləmə xətası: %s", e)
        return await handler(event, data)

    async def _track(self, event):
        db = get_db()

        if isinstance(event, ChatMemberUpdated):
            status = event.new_chat_member.status
            if event.chat.type in ("group", "supergroup", "channel"):
                active = status in ("member", "administrator", "creator", "restricted")
                await self._save_chat(event.chat, active=active, force=True)
                return
            if event.chat.type != "private" or not event.from_user or event.from_user.is_bot:
                return
            if status == "kicked":
                await asyncio.to_thread(db.set_blocked, event.from_user.id, True)
            elif status == "member":
                await self._save(event.from_user, force=True)
            return

        if isinstance(event, Message):
            chat = event.chat
        elif isinstance(event, CallbackQuery) and event.message:
            chat = event.message.chat
        else:
            return
        if chat.type in ("group", "supergroup", "channel"):
            await self._save_chat(chat)
            return
        user = event.from_user
        if chat.type != "private" or not user or user.is_bot:
            return
        await self._save(user)

    async def _save(self, user, force=False):
        now = time.time()
        if not force and now - self._last.get(user.id, 0) < TRACK_THROTTLE:
            return
        self._last[user.id] = now
        await asyncio.to_thread(
            get_db().upsert_user, user.id, user.username, user.first_name, user.last_name
        )

    async def _save_chat(self, chat, active=True, force=False):
        key = f"chat:{chat.id}"
        now = time.time()
        if not force and now - self._last.get(key, 0) < TRACK_THROTTLE:
            return
        self._last[key] = now
        await asyncio.to_thread(
            get_db().upsert_chat,
            chat.id,
            chat.type,
            getattr(chat, "title", None),
            getattr(chat, "username", None),
            active,
        )


__all__ = [
    "HELP_TEXT",
    "UserTrackerMiddleware",
    "accel_view",
    "arch_button_label",
    "arch_view",
    "aud_view",
    "audio_buttons_picker_view",
    "audio_buttons_view",
    "biz_view",
    "bots_view",
    "com_view",
    "depo_row",
    "depo_view",
    "fetch_kb",
    "fetch_sessions",
    "help_view",
    "live_view",
    "mod_cmds_view",
    "settings_view",
    "start_buttons_view",
    "start_fetch_live",
    "stop_fetch_on",
    "sub_kb",
    "sys_kb",
    "sys_text",
    "ub_no_inline_list_view",
]
