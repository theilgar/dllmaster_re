"""
🎛 İmkanlar və İdarəetmə Paneli — bot/plugins/features_plugin.py
İdarə paneli: /menu → 💎 Premium → 🎛 İmkanlar

Tablar üzrə təşkil olunub:
  • 💎 Premium İmkanları (bütün imkanlar adları ilə, bir toxunuşla aç/bağla)
  • 🆓 Pulsuz (Free) İmkanları
  • 👑 Bot Sahibi (Creator)
  • ⚙️ Digər İdarəetmələr:
      - 🛠 İş Rejimi (Texniki Baxış / Maintenance): İstifadəçilərə iş getdiyi bildirilir
      - ☕ Bəxşiş Təklifi (Pulsuz istifadəçilərə): 1 dəqiqə sonra avtomatik silinən dəstək mesajı
      - 👁 Bağlı imkanların görünməsi (💎 görünür / 🙈 gizli)
      - 🔢 Playlist və mahnı limitləri
      - 👁 /premium mətni
      - ♻️ İlkin vəziyyətə qaytar (Reset)
"""
import asyncio
import contextlib
import logging
from html import escape

from aiogram import F, types
from aiogram.types import InlineKeyboardMarkup

from common import tiers
from common.database import (
    is_maintenance_mode,
    set_maintenance_mode,
    is_tip_prompt_enabled,
    set_tip_prompt_enabled,
    is_audio_button_enabled,
    set_audio_button_enabled,
    get_auto_hide_duration,
    set_auto_hide_duration,
)
from bot.core import panel as ui
from bot.core.panel import btn
from common.kv import next_of

logger = logging.getLogger(__name__)

PRIORITY = 55
LINE = "──────────────────────"

ROLE_MAP = {
    "prem": "premium",
    "free": "free",
    "creator": "creator",
}

ROLE_TITLES = {
    "prem": "💎 Premium İmkanları",
    "free": "🆓 Pulsuz (Free) İmkanları",
    "creator": "👑 Bot Sahibi (Creator)",
    "more": "⚙️ Limitlər və digər",
}




FEAT_INPUT_TITLES = {
    "playlists:free": ("🎶 Playlist sayı (Pulsuz)", "Pulsuz istifadəçilərin yarada biləcəyi maksimum playlist sayı"),
    "playlist_songs:free": ("🎵 Mahnı sayı (Pulsuz)", "Pulsuz istifadəçinin bir playlistə yığa biləcəyi mahnı sayı"),
    "playlists:premium": ("💎 Playlist sayı (Premium)", "Premium istifadəçilərin yarada biləcəyi maksimum playlist sayı"),
    "playlist_songs:premium": ("💎 Mahnı sayı (Premium)", "Premium istifadəçinin bir playlistə yığa biləcəyi mahnı sayı"),
    "sec": ("⏱ Düymə gizlənmə taymeri", "Mahnı göndərilərkən altındakı düymələrin avtomatik silinmə vaxtı (saniyə)"),
}


def panel_view(tab: str = "prem", note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    """Təmiz, rahat və oxunaqlı imkanlar paneli."""
    if tab not in ("prem", "free", "creator", "more"):
        tab = "prem"

    lines = [
        f"🎛 <b>İmkanlar və limitlər</b> › <b>{ROLE_TITLES[tab]}</b>",
        LINE,
    ]
    rows = []

    if tab in ("prem", "free", "creator"):
        role = ROLE_MAP[tab]
        lines.append(
            "<i>Düyməyə toxunaraq bu rol üçün imkanı aktiv (🟢) və ya deaktiv (🔴) edin:</i>\n"
        )

        for f in tiers.ORDER:
            name = tiers.FEATURES[f][0]
            val = tiers.get(f, role)

            if f in tiers.NEGATIVE:
                if f == "songlink":
                    status = "⚠️ Var (görünür)" if val else "✅ Yoxdur (təmiz)"
                else:  # broadcast
                    status = "⚠️ Gəlir" if val else "✅ Gəlmir"
            else:
                status = "🟢" if val else "🔴"

            btn_label = f"{name} · {status}"
            rows.append([btn(btn_label, f"tf:t:{f}:{role}:{tab}")])

    elif tab == "more":
        sl = tiers.show_locked()
        pl_free_on = tiers.get("playlists", "free")
        pl_free = tiers.fmt_limit(tiers.get_limit("playlists", "free")) if pl_free_on else "🔴"
        songs_free = tiers.fmt_limit(tiers.get_limit("playlist_songs", "free")) if pl_free_on else "🔴"
        pl_prem_on = tiers.get("playlists", "premium")
        pl_prem = tiers.fmt_limit(tiers.get_limit("playlists", "premium")) if pl_prem_on else "🔴"
        songs_prem = tiers.fmt_limit(tiers.get_limit("playlist_songs", "premium")) if pl_prem_on else "🔴"

        lines += [
            "<i>Playlist limitləri və bağlı imkanların görünüşü:</i>\n",
            f"• <b>👁 Bağlı imkanlar:</b> {'💎 görünür (satış üçün)' if sl else '🙈 gizli (ümumiyyətlə görünmür)'}",
            f"• <b>🆓 Pulsuz:</b> {pl_free} playlist · hər birində {songs_free} mahnı",
            f"• <b>💎 Premium:</b> {pl_prem} playlist · hər birində {songs_prem} mahnı",
            "",
            "<i>🛠 Texniki iş rejimi → 🖥 Sistem · ☕ Bəxşiş → 💎 Premium və gəlir · "
            "🔀 / ➕ / 🙈 mahnı düymələri və taymer → 🎵 Musiqi → 🎛 Mahnı düymələri</i>",
            "\n<i>💡 Limitə toxunub rəqəm, sonsuz üçün \"+\", söndürmək üçün \"-\" yaza bilərsiniz.</i>",
        ]
        rows += [
            [btn(f"👁 Bağlı imkanlar: {'💎 Görünür' if sl else '🙈 Gizli'}", "tf:sl")],
            [btn(f"🆓 Playlist: {pl_free}", "tf:inp:playlists:free"),
             btn(f"🆓 Mahnı: {songs_free}", "tf:inp:playlist_songs:free")],
            [btn(f"💎 Playlist: {pl_prem}", "tf:inp:playlists:premium"),
             btn(f"💎 Mahnı: {songs_prem}", "tf:inp:playlist_songs:premium")],
            [btn("🎛 Mahnı düymələri", "kb_btn:panel")],
            [btn("👁 /premium mətni", "tf:perks"), btn("♻️ Standart ayarlar", "tf:reset")],
        ]

    if note:
        lines.append(f"\n{note}")

    # ── Tab Seçim Düymələri ──
    tab_order = ("prem", "free", "creator", "more")           # tək düymə: hər basışda növbəti tab
    tab_names = {"prem": "💎 Premium", "free": "🆓 Pulsuz", "creator": "👑 Sahib", "more": "⚙️ Limitlər"}
    cur_tab = tab if tab in tab_order else "prem"
    next_tab = next_of(tab_order, cur_tab)
    rows.append([btn(f"{tab_names[cur_tab]}  ›  {tab_names[next_tab]}", f"tf:tab:{next_tab}")])
    rows.append(ui.nav("menu:biz", "Premium"))

    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def perks_view() -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "💎 Premium › 🎛 İmkanlar › 👁 <b>Premium mətni</b>\n"
        + LINE + "\n"
        "<i>/premium, satış və aktivləşmə mesajlarında belə görünür:</i>\n\n"
        "✨ <b>Premium ilə əldə edəcəklərin:</b>\n"
        + tiers.perks_html()
    )
    return text, InlineKeyboardMarkup(inline_keyboard=[ui.nav("tf:tab:more", "İmkanlar")])


def reset_view() -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "💎 Premium › 🎛 İmkanlar › ♻️ <b>Standart ayarlar</b>\n"
        + LINE + "\n"
        "Bütün imkanlar və limitlər defolt vəziyyətə qayıtsın?\n\n"
        "• <b>💎 / 🆓</b> — yalnız Premium və Pulsuz rolları\n"
        "• <b>👑 + 💎 + 🆓</b> — Bot sahibi də daxil bütün rollar"
    )
    return text, InlineKeyboardMarkup(inline_keyboard=[
        [btn("♻️ 💎 + 🆓 · tövsiyə", "tf:resetok"), btn("♻️ 👑 + 💎 + 🆓", "tf:resetall")],
        [ui.cancel("tf:tab:more")],
    ])


def valpicker_view(target_key: str, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    title, desc = FEAT_INPUT_TITLES.get(target_key, (target_key, ""))
    if target_key == "sec":
        sec = get_auto_hide_duration()
        cur_str = "Daimi (silinmir)" if sec == 0 else f"{sec} san"
    else:
        lim_name, role = target_key.split(":")
        val = tiers.get_limit(lim_name, role)
        is_on = tiers.get("playlists", role)
        cur_str = "🔴 Söndürülüb (Off)" if not is_on else (f"{tiers.fmt_limit(val)} {'playlist' if lim_name == 'playlists' else 'mahnı'}")

    lines = [
        "🎛 <b>İmkanlar və limitlər › Dəyər</b>",
        LINE,
        f"⚙️ <b>{title}</b>",
        f"<i>{desc}</i>\n",
        f"🔢 <b>Cari dəyər:</b> <b>{cur_str}</b>\n",
        "<i>Dəyəri tənzimləmək üçün düymələrdən istifadə edin və ya çata birbaşa rəqəm yazın:</i>",
        "• <b>+</b> — <b>sonsuz (∞)</b> / daimi",
        "• <b>-</b> — <b>off (bağlı)</b> olsun",
    ]
    if note:
        lines.append(f"\n{note}")

    rows = ui.stepper(f"tf:vstep:{target_key}:", f"tf:vinp:{target_key}") + [ui.nav("tf:tab:more", "İmkanlar")]
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def setup(context):
    dp = context.dp
    is_creator = context.is_creator

    from bot.core.input_wait import InputWait, chain_menu_text
    feat_input = InputWait(300, target=None, chat_id=None, panel_id=None)   # 5 dəq. sonra gözləmə özü bitir
    chain_menu_text(context, lambda uid: feat_input.waiting() and is_creator(uid))

    def label() -> str:
        try:
            from common import maintenance
            maint = " 🛠" if maintenance.is_active() else ""
            return f"🎛 İmkanlar və İdarəetmə{maint}"
        except Exception:
            return "🎛 İmkanlar və İdarəetmə"

    context.tiers_label = label

    show = ui.show

    @dp.callback_query(F.data.startswith("tf:"))
    async def features_cb(cb: types.CallbackQuery):

        parts = cb.data.split(":")
        act = parts[1]
        note = ""
        current_tab = "prem"

        if act == "noop":
            await cb.answer()
            return

        if act == "inp":
            target_key = ":".join(parts[2:])
            if target_key not in FEAT_INPUT_TITLES:
                await cb.answer()
                return
            await cb.answer()
            feat_input.start("val")
            feat_input.update(target=target_key, chat_id=cb.message.chat.id, panel_id=cb.message.message_id)
            text, kb = await asyncio.to_thread(valpicker_view, target_key)
            await show(cb, text, kb)
            return

        elif act == "vinp":
            target_key = ":".join(parts[2:])
            feat_input.start("val")
            feat_input.update(target=target_key, chat_id=cb.message.chat.id, panel_id=cb.message.message_id)
            await cb.answer("✍️ Çata istədiyiniz rəqəmi, '+' (sonsuz) və ya '-' (off) yazın")
            return

        elif act == "vstep":
            if len(parts) >= 5:
                target_key = f"{parts[2]}:{parts[3]}"
                delta = int(parts[4])
            else:
                target_key = parts[2]
                delta = int(parts[3])

            if target_key == "sec":
                cur = get_auto_hide_duration()
                new_sec = max(0, min(3600, cur + delta))
                await asyncio.to_thread(set_auto_hide_duration, new_sec)
                t_txt = "Daimi (silinmir)" if new_sec == 0 else f"{new_sec} san"
                await cb.answer(f"⏱ {t_txt}")
                note = f"✅ Taymer: {t_txt}"
            else:
                lim_name, role = target_key.split(":")
                cur = tiers.get_limit(lim_name, role)
                is_on = tiers.get("playlists", role)
                if not is_on:
                    if delta > 0:
                        await asyncio.to_thread(tiers.put, "playlists", role, True)
                        await asyncio.to_thread(tiers.set_limit, lim_name, role, max(1, delta))
                else:
                    if cur == 0:
                        if delta < 0:
                            new_v = 50 if lim_name == "playlists" else 500
                            await asyncio.to_thread(tiers.set_limit, lim_name, role, new_v)
                    else:
                        new_v = max(1, cur + delta)
                        await asyncio.to_thread(tiers.set_limit, lim_name, role, new_v)
                v = tiers.get_limit(lim_name, role)
                lbl = tiers.LIMITS.get(lim_name, (lim_name,))[0]
                await cb.answer(f"{lbl}: {tiers.fmt_limit(v)}")
                note = f"✅ {lbl}: {tiers.fmt_limit(v)}"

            text, kb = await asyncio.to_thread(valpicker_view, target_key, note)
            await show(cb, text, kb)
            return

        elif act == "cancel_inp":
            feat_input.clear()
            await cb.answer("Ləğv edildi")
            await show(cb, *await asyncio.to_thread(panel_view, "more"))
            return

        feat_input.clear()

        if act == "panel":
            current_tab = "prem"

        elif act == "tab":
            current_tab = parts[2] if len(parts) > 2 else "prem"

        elif act == "t":
            # tf:t:<feature>:<role>:<tab>
            f = parts[2]
            r = parts[3]
            current_tab = parts[4] if len(parts) > 4 else "prem"
            new = await asyncio.to_thread(tiers.toggle, f, r)
            state_label = "✅ Açıq" if new else "❌ Bağlı"
            feat_name = tiers.FEATURES.get(f, (f,))[0]
            note = f"✅ {tiers.ROLE_LABEL.get(r, r)}: {feat_name} → {state_label}"
            await cb.answer(f"{feat_name}: {state_label}")

        elif act == "maint":
            current_tab = "more"
            from bot.menus.handlers.maint_flow import apply_toggle     # müddət / yayım / depo siyasəti də tətbiq olunur
            await apply_toggle(context)
            from common import maintenance
            new_val = maintenance.is_active()
            if new_val:
                await cb.answer("🛠 İş Rejimi AKTİV EDİLDİ!\nİstifadəçilərə xəbərdarlıq ediləcək.", show_alert=True)
                note = "🛠 <b>İş Rejimi aktiv edildi</b> (istifadəçilərə qəfil dayanmalar ola biləcəyi bildirilir)"
            else:
                await cb.answer("🔴 İş Rejimi DEAKTİV EDİLDİ!\nBot normal rejimdə işləyir.", show_alert=True)
                note = "🔴 <b>İş Rejimi söndürüldü</b> (bot normal rejimdədir)"

        elif act == "tip":
            current_tab = "more"
            new_val = not is_tip_prompt_enabled()
            await asyncio.to_thread(set_tip_prompt_enabled, new_val)
            if new_val:
                await cb.answer("☕ Bəxşiş təklifi AKTİV EDİLDİ!\nPulsuz istifadəçilərə yükləmədən sonra 1 dəq. təklif çıxacaq.", show_alert=True)
                note = "☕ <b>Bəxşiş təklifi aktiv edildi</b>"
            else:
                await cb.answer("🔴 Bəxşiş təklifi DEAKTİV EDİLDİ!", show_alert=True)
                note = "🔴 <b>Bəxşiş təklifi söndürüldü</b>"

        elif act == "btn":
            current_tab = "more"
            sub = parts[2] if len(parts) > 2 else ""
            if sub in ("mix", "plus", "hide"):
                cur = is_audio_button_enabled(sub)
                new_state = not cur
                await asyncio.to_thread(set_audio_button_enabled, sub, new_state)
                lbl = {"mix": "🔀 Mix", "plus": "➕ Playlist", "hide": "🙈 Gizlət"}.get(sub, sub)
                st = "AÇILDI" if new_state else "BAĞLANDI"
                note = f"{lbl} düyməsi: {'🟢 Açıq' if new_state else '🔴 Bağlı'}"
                await cb.answer(f"{lbl} düyməsi {st}")

        elif act == "sec":
            delta = int(parts[2]) if len(parts) > 2 else 5
            current_tab = "more"
            from common.database import step_auto_hide_duration
            new_sec = await asyncio.to_thread(step_auto_hide_duration, delta)
            t_txt = "Daimi (silinmir)" if new_sec == 0 else f"{new_sec} san"
            note = f"⏱ Düymə gizlənmə vaxtı: {t_txt}"
            await cb.answer(f"⏱ Müddət: {t_txt}")

        elif act == "sl":
            current_tab = "more"
            new = not tiers.show_locked()
            await asyncio.to_thread(tiers.set_show_locked, new)
            status_text = "💎 görünür" if new else "🙈 gizlidir"
            note = f"👁 Bağlı imkanlar istifadəçiyə: {status_text}"
            await cb.answer(note)

        elif act == "l":
            # tf:l:<name>:<role>:<dir>
            name = parts[2]
            r = parts[3]
            direction = int(parts[4]) if len(parts) > 4 else 1
            current_tab = "more"
            if not tiers.get("playlists", r) and direction > 0:
                await asyncio.to_thread(tiers.put, "playlists", r, True)
                v = await asyncio.to_thread(tiers.get_limit, name, r)
                note = f"✅ {tiers.ROLE_LABEL.get(r, r)}: Playlistlər aktiv edildi ({tiers.fmt_limit(v)})"
            else:
                v = await asyncio.to_thread(tiers.step_limit, name, r, direction)
                lim_name = tiers.LIMITS.get(name, (name,))[0]
                note = f"🔢 {lim_name} ({tiers.ROLE_LABEL.get(r, r)}): {tiers.fmt_limit(v)}"
            await cb.answer(note)

        elif act == "perks":
            await cb.answer()
            await show(cb, *await asyncio.to_thread(perks_view))
            return

        elif act == "reset":
            await cb.answer()
            await show(cb, *reset_view())
            return

        elif act in ("resetok", "resetall"):
            current_tab = "more"
            await asyncio.to_thread(tiers.reset, act == "resetall")
            note = "♻️ Bütün imkanlar ilkin vəziyyətə qaytarıldı"
            await cb.answer("♻️ İlkin vəziyyətə qaytarıldı")

        else:
            await cb.answer()

        await show(cb, *await asyncio.to_thread(panel_view, current_tab, note))

    async def waiting_feat_input(message: types.Message) -> bool:
        return feat_input.waiting() and is_creator(message.from_user.id)

    @dp.message(F.chat.type == "private", F.text, ~F.text.startswith("/"), waiting_feat_input)
    async def feat_value_message(message: types.Message):
        target_key = feat_input.get("target", "")
        raw = message.text.strip().lower()
        try:
            await message.delete()
        except Exception as _e:
            logger.debug("udulan xəta [bot/plugins/features_plugin.py:482]: %r", _e)

        title, desc = FEAT_INPUT_TITLES.get(target_key, (target_key, ""))
        note = ""

        if raw in ("-", "off", "bagli", "bağlı"):
            if target_key == "sec":
                await asyncio.to_thread(set_audio_button_enabled, "mix", False)
                await asyncio.to_thread(set_audio_button_enabled, "plus", False)
                await asyncio.to_thread(set_audio_button_enabled, "hide", False)
                note = "🔴 Mahnı düymələri söndürüldü (Off)"
            else:
                lim_name, role = target_key.split(":")
                await asyncio.to_thread(tiers.put, "playlists", role, False)
                if lim_name == "playlist_songs":
                    await asyncio.to_thread(tiers.set_limit, "playlist_songs", role, 0)
                note = f"🔴 {tiers.ROLE_LABEL.get(role, role)}: Playlistlər söndürüldü (Off)"

        elif raw in ("+", "sonsuz", "inf", "unlimited", "daimi"):
            if target_key == "sec":
                await asyncio.to_thread(set_auto_hide_duration, 0)
                await asyncio.to_thread(set_audio_button_enabled, "mix", True)
                await asyncio.to_thread(set_audio_button_enabled, "plus", True)
                await asyncio.to_thread(set_audio_button_enabled, "hide", True)
                note = "✅ Taymer: Daimi (sonsuz — heç vaxt silinmir)"
            else:
                lim_name, role = target_key.split(":")
                await asyncio.to_thread(tiers.set_limit, lim_name, role, 0)
                await asyncio.to_thread(tiers.put, "playlists", role, True)
                lbl = tiers.LIMITS.get(lim_name, (lim_name,))[0]
                note = f"✅ {tiers.ROLE_LABEL.get(role, role)}: {lbl} → Sonsuz (∞)"

        elif raw.isdigit():
            val = int(raw)
            if target_key == "sec":
                val = max(0, min(3600, val))
                await asyncio.to_thread(set_auto_hide_duration, val)
                t_txt = "Daimi (silinmir)" if val == 0 else f"{val} san"
                note = f"✅ Taymer: {t_txt}"
            else:
                lim_name, role = target_key.split(":")
                val = max(0, val)
                await asyncio.to_thread(tiers.set_limit, lim_name, role, val)
                await asyncio.to_thread(tiers.put, "playlists", role, True)
                lbl = tiers.LIMITS.get(lim_name, (lim_name,))[0]
                note = f"✅ {tiers.ROLE_LABEL.get(role, role)}: {lbl} → {tiers.fmt_limit(val)}"

        else:
            err_text = (
                f"🎛 <b>İmkanlar və İdarəetmə › Dəyər daxil et</b>\n"
                f"{LINE}\n"
                f"⚙️ <b>{title}</b>\n\n"
                f"⚠️ <b>\"{escape(message.text[:30])}\" düzgün deyil!</b>\n\n"
                f"✍️ <i>Yalnız aşağıdakılardan birini yazın:</i>\n"
                f"• <b>Rəqəm</b> (məs: <code>25</code>)\n"
                f"• <b>+</b> (sonsuz)\n"
                f"• <b>-</b> (off / bağlı)\n"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[[btn("✖️ Ləğv et", "tf:cancel_inp")]])
            with contextlib.suppress(Exception):
                await context.bot.edit_message_text(
                    chat_id=feat_input["chat_id"],
                    message_id=feat_input["panel_id"],
                    text=err_text,
                    parse_mode="HTML",
                    reply_markup=kb,
                )
            return

        feat_input.clear()                                  # dəyər qəbul olundu — növbəti mətn axtarışa getsin
        text, kb = await asyncio.to_thread(valpicker_view, target_key, note)
        with contextlib.suppress(Exception):
            await context.bot.edit_message_text(
                chat_id=feat_input["chat_id"],
                message_id=feat_input["panel_id"],
                text=text,
                parse_mode="HTML",
                reply_markup=kb,
                disable_web_page_preview=True,
            )

    logger.info("🎛 İmkanlar və İdarəetmə paneli yükləndi")
