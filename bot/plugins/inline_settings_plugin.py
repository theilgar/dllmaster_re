"""
⚡ Inline axtarış paneli — bot/plugins/inline_settings_plugin.py (yalnız creator)

/menu → 🎵 Musiqi → ⚡ Inline axtarış
  ⏳ Gözləmə · 📦 Depo varsa YT gözləmə · ⏱ Axtarış limiti · 🔢 Nəticə sayı · 🗂 Keş müddəti · 📦 Depo nəticəsi
  — hər biri ➖ / ➕ ilə, dəyişiklik DƏRHAL tətbiq olunur (restart lazım deyil)
  📊 Statistika — sorğu sayı, keşdən cavab faizi, orta / son cavab müddəti
  🗑 Keşi təmizlə · ♻️ Defolt · 🔍 axtarış mənbəyi (🎵 YouTube ayarlarına keçid)
Dəyərlər bot/core/inline_settings.py-da, settings cədvəlində saxlanır.
"""
import contextlib
import logging
from html import escape

from aiogram import F, types
from aiogram.types import InlineKeyboardMarkup

from bot.core import inline_settings as IS
from bot.core import panel as ui
from bot.core.panel import btn

logger = logging.getLogger(__name__)

SOURCE_LABEL = {"yt": "▶️ YouTube", "ytm": "🎵 YT Music", "both": "🔀 Hər ikisi"}




def setup(context):
    dp = context.dp

    is_creator = context.is_creator


    def label() -> str:
        st = getattr(context, "inline_stats", None) or {}
        avg = st.get("total", 0) / st["answered"] if st.get("answered") else 0
        return f"⚡ Axtarış · {avg:.1f} san" if avg else "⚡ Axtarış ayarları"

    context.inline_settings_label = label

    def stats_lines() -> list:
        st = getattr(context, "inline_stats", None)
        if not st or not st.get("queries"):
            return ["📊 <i>Hələ sorğu yoxdur (restartdan sonra sayılır)</i>"]
        q = st["queries"]
        hit = st["cache_hits"] * 100 / q if q else 0
        avg = st["total"] / st["answered"] if st["answered"] else 0
        size = context.inline_cache_size() if hasattr(context, "inline_cache_size") else 0
        out = [f"📊 Sorğu: <b>{q}</b> · keşdən: <b>{hit:.0f}%</b> · YouTube axtarışı: {st['searches']}",
               f"⏱ Orta cavab: <b>{avg:.2f} san</b> · son: {st['last']:.2f} san · keşdə: {size} sorğu"]
        extra = []
        if st["depo_first"]:
            extra.append(f"📦 depo əvvəl: {st['depo_first']}")
        if st["timeouts"]:
            extra.append(f"⌛ vaxt bitdi: {st['timeouts']}")
        if st["errors"]:
            extra.append(f"❌ xəta: {st['errors']}")
        if extra:
            out.append(" · ".join(extra))
        return out

    def source() -> str:
        try:
            from bot.core import youtube_handler
            return youtube_handler.search_source()
        except Exception:
            return "yt"

    def chat_stats_line() -> str:
        st = getattr(context, "chat_search_stats", None)
        if not st or not st.get("queries"):
            return "📊 /music: <i>hələ sorğu yoxdur</i>"
        avg = st["total"] / st["answered"] if st["answered"] else 0
        hit = st["cache_hits"] * 100 / st["queries"]
        return (f"📊 /music: sorğu <b>{st['queries']}</b> · keşdən <b>{hit:.0f}%</b> · "
                f"orta <b>{avg:.2f} san</b> · son {st['last']:.2f} san")

    def mix_stats_line() -> str:
        st = getattr(context, "mix_stats", None)
        if not st or not st.get("queries"):
            return "📊 Mix: <i>hələ sorğu yoxdur</i>"
        avg = st["total"] / st["answered"] if st["answered"] else 0
        hit = st["cache_hits"] * 100 / st["queries"]
        return (f"📊 Mix: sorğu <b>{st['queries']}</b> · keşdən <b>{hit:.0f}%</b> · "
                f"orta <b>{avg:.2f} san</b> · son {st['last']:.2f} san"
                + (f" · ❌ {st['errors']}" if st["errors"] else ""))

    def section(title: str, names: list) -> list:
        out = [f"\n<b>{title}</b>"]
        for name in names:
            mark = "" if IS.is_default(name) else " ✏️"
            out.append(f"{IS.TITLES[name]}: <b>{escape(IS.fmt(name))}</b>{mark}\n   <i>{escape(IS.SPEC[name][4])}</i>")
        return out

    from bot.core.input_wait import InputWait, chain_menu_text
    inline_input = InputWait(300, target=None, chat_id=None, panel_id=None)  # 5 dəq. sonra özü bitir
    chain_menu_text(context, lambda uid: inline_input.waiting() and is_creator(uid))

    def rows_for(names: list) -> list:
        rows = []
        for name in names:
            cb_data = f"is:t:{name}" if name in IS.TOGGLES else f"is:pick:{name}"
            rows.append([btn(f"{IS.TITLES[name]}: {IS.fmt(name)}", cb_data)])
        return rows

    def inline_picker_view(name: str, note: str = ""):
        title = IS.TITLES.get(name, name)
        desc = IS.SPEC[name][4] if name in IS.SPEC else ""
        cur = IS.fmt(name)
        lines = [
            "<i>🏠 › 🎵 Musiqi › ⚡ Inline axtarış</i>",
            f"⚡ <b>Axtarış ayarları › {title}</b>",
            "━━━━━━━━━━━━━━━━━━",
            f"<i>{desc}</i>\n",
            f"🔢 <b>Cari dəyər:</b> <b>{cur}</b>\n",
            "<i>Dəyəri dəyişmək üçün aşağıdakı düymələrdən istifadə edin və ya çata istədiyiniz rəqəmi yazın:</i>",
            "• <b>+</b> — maksimum",
            "• <b>-</b> — söndür / 0",
        ]
        if note:
            lines.append(f"\n{note}")
        rows = [
            *ui.stepper(f"is:vstep:{name}:", f"is:vinp:{name}"),
            ui.nav("is:panel", "Inline", close_data="is:close"),
        ]
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    def panel(note: str = ""):
        lines = ["<i>🏠 › 🎵 Musiqi</i>", "⚡ <b>Axtarış ayarları</b>", "━━━━━━━━━━━━━━━━━━"]
        lines += section("🔎 Inline (@bot ...)", IS.INLINE_ORDER)
        lines += section("🎵 /music (çatda)", IS.CHAT_ORDER)
        lines += section("🔀 Mix (inline 🔀 düyməsi və /mix)", IS.MIX_ORDER)
        lines += section("🧩 Ümumi", IS.SHARED_ORDER)
        src = source()
        lines += ["━━━━━━━━━━━━━━━━━━",
                  f"🔍 Mənbə: <b>{SOURCE_LABEL.get(src, src)}</b>"
                  + (" <i>— hər ikisini gözləyir, ən yavaşı budur</i>" if src == "both" else ""),
                  *stats_lines(),
                  chat_stats_line(),
                  mix_stats_line(),
                  "━━━━━━━━━━━━━━━━━━",
                  "<i>Dəyişiklik dərhal tətbiq olunur. ✏️ — defoltdan fərqli. Inline və /music eyni keşi işlədir.</i>"]
        if note:
            lines.append(f"\n{note}")
        rows = [[btn("— 🔎 Inline —", "is:noop")], *rows_for(IS.INLINE_ORDER),
                [btn("— 🎵 /music —", "is:noop")], *rows_for(IS.CHAT_ORDER),
                [btn("— 🔀 Mix —", "is:noop")], *rows_for(IS.MIX_ORDER),
                [btn("— 🧩 Ümumi —", "is:noop")], *rows_for(IS.SHARED_ORDER)]
        size = context.inline_cache_size() if hasattr(context, "inline_cache_size") else 0
        rows.append([btn(f"🗑 Keşi təmizlə ({size})", "is:clear"), btn("♻️ Standart ayarlar", "is:reset")])
        rows.append([btn("🚫 Userbot inline: söndürülmüş çatlar", "menu:ub_no_inline_list")])
        rows.append(ui.nav("menu:mus", "Musiqi", close_data="is:close", refresh_data="is:panel"))
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    @dp.callback_query(F.data.startswith("is:"))
    async def is_cb(cb: types.CallbackQuery):
        parts = cb.data.split(":")
        act = parts[1]
        note = ""
        if act == "noop":
            await cb.answer()
            return
        if act == "close":
            inline_input.clear()
            await cb.answer()
            with contextlib.suppress(Exception):
                await cb.message.delete()
            return

        if act == "panel":
            inline_input.clear()
            await cb.answer()
            with contextlib.suppress(Exception):
                text, kb = panel()
                await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
            return

        if act == "pick" and len(parts) > 2 and parts[2] in IS.SPEC:
            name = parts[2]
            inline_input.start("val")
            inline_input.update(target=name, chat_id=cb.message.chat.id, panel_id=cb.message.message_id)
            await cb.answer()
            with contextlib.suppress(Exception):
                text, kb = inline_picker_view(name)
                await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
            return

        if act == "vinp" and len(parts) > 2 and parts[2] in IS.SPEC:
            name = parts[2]
            inline_input.start("val")
            inline_input.update(target=name, chat_id=cb.message.chat.id, panel_id=cb.message.message_id)
            await cb.answer("✍️ Çata istədiyiniz rəqəmi, '+' və ya '-' yazın")
            return

        if act == "vstep" and len(parts) > 3 and parts[2] in IS.SPEC:
            name = parts[2]
            delta = int(parts[3])
            if name in ("debounce", "ytwait"):
                cur = IS.get(name)
                step_amt = 0.1 if abs(delta) == 1 else 0.5
                new_val = max(0.0, round(cur + (step_amt if delta > 0 else -step_amt), 2))
                IS.put(name, new_val)
            else:
                cur = IS.get(name)
                new_val = max(0, int(cur + delta))
                IS.put(name, new_val)

            note = f"✅ {IS.TITLES[name]}: {IS.fmt(name)}"
            await cb.answer(f"{IS.TITLES[name]}: {IS.fmt(name)}")
            with contextlib.suppress(Exception):
                text, kb = inline_picker_view(name, note)
                await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
            return

        inline_input.clear()

        if act in ("+", "-") and len(parts) > 2 and parts[2] in IS.SPEC:
            name = parts[2]
            IS.step(name, 1 if act == "+" else -1)
            await cb.answer(f"{IS.TITLES[name]}: {IS.fmt(name)}")
        elif act == "t" and len(parts) > 2 and parts[2] in IS.TOGGLES:
            IS.toggle(parts[2])
            await cb.answer(f"{IS.TITLES[parts[2]]}: {IS.fmt(parts[2])}")
        elif act == "clear":
            n = context.inline_cache_clear() if hasattr(context, "inline_cache_clear") else 0
            await cb.answer(f"🗑 {n} sorğu keşdən silindi")
        elif act == "reset":
            IS.reset()
            await cb.answer("♻️ Defolt dəyərlər")
            note = "♻️ Bütün dəyərlər defolta qaytarıldı (config.env-də INLINE_* varsa o işləyir)"
        else:
            await cb.answer()
        with contextlib.suppress(Exception):
            text, kb = panel(note)
            await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)

    async def waiting_inline_input(message: types.Message) -> bool:
        return inline_input.waiting() and is_creator(message.from_user.id)

    @dp.message(F.chat.type == "private", F.text, ~F.text.startswith("/"), waiting_inline_input)
    async def inline_value_message(message: types.Message):
        target = inline_input.get("target")
        if not target or target not in IS.SPEC:
            inline_input.clear()
            return
        raw = message.text.strip().lower()
        with contextlib.suppress(Exception):
            await message.delete()

        if raw in ("-", "off", "0", "bagli", "bağlı", "sondur"):
            IS.put(target, 0)                                    # put() minimuma sıxır (timeout 0 olmur)
            note = f"🔴 {IS.TITLES[target]}: {IS.fmt(target)}"
        elif raw in ("+", "max", "sonsuz", "inf", "unlimited"):
            steps = IS.SPEC[target][2]
            max_v = steps[-1] if steps else 100
            IS.put(target, max_v)
            note = f"✅ {IS.TITLES[target]} maksimuma qoyuldu: {IS.fmt(target)}"
        else:
            try:
                num = float(raw)
                val = int(num) if num.is_integer() and isinstance(IS.SPEC[target][1], int) else num
                IS.put(target, max(0, val))
                note = f"✅ {IS.TITLES[target]}: {IS.fmt(target)}"
            except ValueError:
                with contextlib.suppress(Exception):
                    err_text, err_kb = inline_picker_view(target, f"⚠️ \"{message.text[:20]}\" düzgün rəqəm deyil! Rəqəm, '+' və ya '-' yazın.")
                    await context.bot.edit_message_text(
                        chat_id=inline_input["chat_id"],
                        message_id=inline_input["panel_id"],
                        text=err_text,
                        parse_mode="HTML",
                        reply_markup=err_kb,
                        disable_web_page_preview=True,
                    )
                return

        inline_input.clear()                                # dəyər qəbul olundu
        text, kb = inline_picker_view(target, note)
        with contextlib.suppress(Exception):
            await context.bot.edit_message_text(
                chat_id=inline_input["chat_id"],
                message_id=inline_input["panel_id"],
                text=text,
                parse_mode="HTML",
                reply_markup=kb,
                disable_web_page_preview=True,
            )

    logger.info("✅ Inline ayarları paneli yükləndi")
