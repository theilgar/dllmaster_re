"""
🤖 Köməkçi botlar: qruplarda göndərmə + depoya avtomatik əlavə — bot/plugins/helper_groups_plugin.py

👥 /menu → 📦 Depo → 👥 Köməkçi qrupları: depo vəziyyəti, yükləmə rejimi (📦 / 🌐),
   köməkçilərin olduğu bütün qruplar (🔍 skan) və hər qrupun idarəsi.
👥 /menu → 👥 → 💬 Qruplar → qrup → 🤖 Köməkçilər:
   hər köməkçi bot üçün vəziyyət (qrupdadır / deyil / səsi kəsilib) və seçim ✅ / ⚪.
   Seçilmiş köməkçilər o qrupda mahnı göndərməni əsas botla bölüşür (flood-a qarşı, ~(N+1)× sürət) —
   məntiq bot/core/depo_helpers.py → HelperPool.group_send.
   Qrupda olmayan köməkçi: ➕ (Telegram-ın qrup seçimi linki) və ya 🤖 userbot ilə avtomatik əlavə.
🔔 Depoda olmayan köməkçi haqqında creator-a gələn mesajın düymələri (dh:...) də buradadır.
🐢 Qrupda flood olanda həmin qrupa gələn "köməkçi yükləyici bot lazımdır?" sualının düymələri (hgs:...).
"""
import asyncio
import logging
from html import escape

from aiogram import F, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from bot.core import panel
from bot.core.panel import btn

logger = logging.getLogger(__name__)

PRIORITY = 60
STATE = {"ok": "✅ qrupdadır", "out": "⚠️ qrupda deyil",
         "mute": "🔇 yaza bilmir (qrupda üzvlərə musiqi / mesaj bağlıdır) — 🛡 yetki lazımdır",
         "slow": "🐌 slow mode — 🛡 admin olmalıdır", "err": "❓ yoxlanılmadı"}




def setup(context):
    dp = context.dp

    is_creator = context.is_creator


    def pool():
        return getattr(context, "depo_helpers", None)

    async def group_view(chat_id: int, back: str, fresh: bool = False, note: str = ""):
        p = pool()
        try:
            from common.database import get_db
            row = await asyncio.to_thread(get_db().get_chat, chat_id)
            title = (row or {}).get("title") or str(chat_id)
        except Exception:
            title = str(chat_id)
        lines = [f"🤖 <b>Köməkçi botlar</b> — {escape(title)}",
                 "Seçilmiş köməkçilər bu qrupda mahnı göndərməni əsas botla növbə ilə bölüşür — "
                 "hər bot ~18 mahnı/dəq., flood gözləmələri azalır.", ""]
        rows = []
        helpers = [h for h in (p.helpers if p else []) if h.me and h.bot]
        if not helpers:
            lines.append("<i>Köməkçi bot yoxdur — /depo → 🤖 Köməkçi botlar-dan token əlavə et.</i>")
        sel = set(p.group_selected(chat_id)) if p else set()
        n_on = 0
        n_mute = 0
        sem = asyncio.Semaphore(8)                        # 29 köməkçi ardıcıl yox — paralel (panel tez açılsın)

        async def _st(h):
            async with sem:
                return await p.member_state(h, chat_id, fresh=fresh)
        states = await asyncio.gather(*(_st(h) for h in helpers)) if helpers else []
        errs = {}                                         # 🩺 xəta mətni → köməkçilər
        for h, st in zip(helpers, states):
            n_mute += st in ("mute", "slow")
            on = h.me.id in sel
            if on and st == "ok":
                n_on += 1
            lines.append(f"{'✅' if on else '▫️'} @{escape(h.me.username)} — {STATE.get(st, st)}"
                         + (f" · göndərdi {h.g_sent}" if h.g_sent else ""))
            if st == "err" and getattr(h, "gerr", {}).get(chat_id):
                errs.setdefault(h.gerr[chat_id], []).append(h.me.username)
            if st in ("ok", "mute", "slow"):
                rows.append([panel.check(f"@{h.me.username}", on, f"hg:t:{chat_id}:{h.me.id}:{back}")])
            else:
                r = [InlineKeyboardButton(text=f"➕ @{h.me.username}",
                                          url=f"https://t.me/{h.me.username}?startgroup=helper")]
                if getattr(context, "telethon_client", None):
                    r.append(btn("🤖 Userbot ilə", f"hg:ua:{chat_id}:{h.me.id}:{back}"))
                rows.append(r)
        if helpers:
            lines += ["", f"⚡ Sürət: ~{(1 + n_on) * 18} mahnı/dəq. (əsas bot + {n_on} köməkçi)"]
            if p and (p.gstats["helper"] or p.gstats["fallback"]):
                g = p.gstats
                lines.append(f"<i>Cəmi qruplarda: köməkçi {g['helper']} · əsas {g['main']} · "
                             f"geri düşən {g['fallback']}</i>")
        if errs:                                          # ❓ niyə yoxlanmadı — səbəb birbaşa görünsün
            lines.append("\n🩺 <b>Yoxlanmadı — səbəb:</b>")
            for e, names in list(errs.items())[:3]:
                lines.append(f"• <code>{escape(e)}</code> ({len(names)} bot)")
        if note:
            lines.append(f"\n{note}")
        if helpers:
            rows.append([btn("🛡 Köməkçilərə yetki ver" + (f" · {n_mute} yaza bilmir" if n_mute else ""),
                             f"hg:gr:{chat_id}:-:{back}")])
        rows.append(panel.nav(*(("hg:hub:0", "Köməkçi qrupları") if back == "hub" else
                                (f"menu:chat:{chat_id}:{back}", "Qrup")),
                              refresh_data=f"hg:f:{chat_id}:-:{back}"))
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    async def grant_note(chat_id) -> str:
        p = pool()
        res = await p.grant_rights(chat_id)
        if not res:
            return "Qrupda köməkçi yoxdur"
        good = sum(1 for _, r in res if r.startswith("✅"))
        lines = [f"🛡 <b>Yetki: {good}/{len(res)} köməkçi admin oldu</b>"]
        bad = [(h, r) for h, r in res if not r.startswith("✅")]
        for h, r in bad[:5]:
            lines.append(f"• @{escape(h.me.username)} — {escape(r)}")
        if bad:
            lines.append("<i>Əsas botun qrupda \"Add new admins\" hüququ olmalıdır, və ya userbot (sənin hesabın) "
                         "qrupda admin əlavə edə bilməlidir.</i>")
        return "\n".join(lines)

    # ═════════════ 👥 Köməkçi qrupları (/menu → 📦 Depo → 👥 Köməkçi qrupları) ═════════════
    KNOWN = "depo_helpers:known_groups"              # skanla tapılan, köməkçilərin olduğu qruplar

    def known_groups() -> list:
        try:
            import json
            from common.database import get_db
            return [int(x) for x in json.loads(get_db().get_setting(KNOWN) or "[]")]
        except Exception:
            return []

    def save_known(ids):
        import json
        from common.database import get_db
        get_db().set_setting(KNOWN, json.dumps(sorted(set(int(x) for x in ids))))

    ext_inp = {"stage": None, "chat_id": None, "panel_id": None}

    async def ext_groups_view(note: str = ""):
        p = pool()
        on = p.ext_enabled if p else True
        lim = p.ext_limit if p else 2
        auto = p.ext_auto if p else True
        total_h = len([h for h in (p.helpers if p else []) if h.me and h.bot])

        lines = [
            "🌐 <b>Kənar qruplar</b>\n" + "─" * 28,
            "Kənar istifadəçilərin botu əlavə etdiyi qruplarda köməkçi botların idarəsi:\n",
            f"📌 <b>Kənar qruplarda köməkçilər:</b> {'🟢 aktiv' if on else '🔴 deaktiv (yalnız əsas bot)'}",
            f"🔢 <b>Qrup başına köməkçi limiti:</b> <b>{lim} bot</b> (maksimum)",
            f"⚡ <b>Qoşulduqda avto-əlavə:</b> {'✅ Bəli (userbot ilə)' if auto else '⚪ Xeyr (yalnız flood zamanı)'}",
            f"🤖 <b>Cəmi köməkçi botlar:</b> {total_h} bot",
            "",
            "<i>ℹ️ Bu ayarlar depoya təsir etmir — yalnız kənar qruplara aiddir. "
            "Söndürdükdə kənar qruplarda heç bir köməkçi işləmir, yalnız əsas bot fəaliyyət göstərir.</i>"
        ]
        if note:
            lines.append(f"\n{note}")

        rows = [
            [panel.toggle("🌐 Kənar qruplarda köməkçilər", on, "hg:ext_tog")],
            [btn(f"🔢 Qrup başına limit: {lim} bot", "hg:ext_pick")],
            [panel.toggle("⚡ Qoşulanda avto-əlavə", auto, "hg:ext_auto")],
            panel.nav("menu:depo", "Depo"),
        ]
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    async def ext_picker_view(note: str = ""):
        p = pool()
        lim = p.ext_limit if p else 2
        total_h = max(1, len([h for h in (p.helpers if p else []) if h.me and h.bot]))
        lines = [
            "🌐 <b>Kənar qruplar › 🔢 Qrup başına limit</b>\n" + "─" * 28,
            f"Hazırkı limit: <b>{lim} bot hər qrup</b>\n",
            "Kənar istifadəçilər botu qrupa əlavə etdikdə və ya flood zamanı "
            "həmin qrup üçün istifadə olunacaq maksimum köməkçi bot sayı.\n",
            "• Minimum: <b>1 bot</b>\n",
            f"• Maksimum: <b>{total_h} bot</b> (mövcud köməkçi sayı)\n",
            "<i>Düymələrlə dəyişin və ya ✏️ ilə rəqəmi yazın:</i>"
        ]
        if note:
            lines.append(f"\n{note}")

        rows = panel.stepper("hg:ext_step:", "hg:ext_inp") + [panel.nav("hg:ext", "Kənar qruplar")]
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    async def hub_view(note: str = ""):
        p = pool()
        from common.database import get_db
        try:
            from bot.core.depo_helpers import SCOPES
        except Exception:
            SCOPES = {}
        helpers = [h for h in (p.helpers if p else []) if h.me]
        ok = sum(1 for h in helpers if h.ok)
        ext_st = f"🟢 {p.ext_limit} bot/qrup" if p and p.ext_enabled else "🔴 Söndürülüb"
        lines = ["👥 <b>Köməkçi qrupları</b>",
                 f"📦 Depo: 🟢 {ok}/{len(helpers)} hazır" + (f" · ⚠️ {len(helpers) - ok} problemli" if ok < len(helpers) else ""),
                 f"📌 Depoya yükləmə rejimi: <b>{escape(SCOPES.get(p.scope, p.scope)) if p else '—'}</b>",
                 f"👥 Kənar qruplarda köməkçilər: <b>{ext_st}</b>",
                 "", "👥 <b>Qruplarda göndərmə</b> <i>(flood-a qarşı — mahnıları köməkçilər də göndərir)</i>"]
        cfg = p.groups_cfg() if p else {}
        ids = list(dict.fromkeys([int(k) for k in cfg] + known_groups()))
        rows = []
        if not ids:
            lines.append("<i>Hələ qrup yoxdur — 🔍 Skan et: köməkçilərin olduğu qrupları tapır.</i>")
        for chat_id in ids[:20]:
            try:
                row = await asyncio.to_thread(get_db().get_chat, chat_id)
                title = (row or {}).get("title") or str(chat_id)
            except Exception:
                title = str(chat_id)
            n_sel = len(cfg.get(str(chat_id), []))
            lines.append(f"• {escape(title[:40])} — " + (f"🟢 {n_sel} köməkçi işləyir" if n_sel else "🔴 deaktiv"))
            rows.append([btn(f"💬 {title[:28]} · " + (f"🟢 {n_sel}" if n_sel else "🔴"), f"hg:v:{chat_id}:hub")])
        if note:
            lines.append(f"\n{note}")
        rows.insert(0, [btn("🤖 Köməkçi botlar", "df:hp"), btn(f"🌐 Kənar qruplar · {ext_st.split()[0]}", "hg:ext")])
        rows.append([btn("🔍 Qrupları skan et", "hg:scan:0"), btn("📦 Depoda yoxla", "hg:dchk:0")])
        rows.append(panel.nav("menu:depo", "Depo"))
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    async def scan_groups() -> str:
        """Botun olduğu qruplarda köməkçi varmı (hər qrupda 2 köməkçi yoxlanılır — sürətli)."""
        p = pool()
        from common.database import get_db
        try:
            rows = await asyncio.to_thread(get_db().sql_all,
                                           "SELECT chat_id FROM chats WHERE active = 1 AND type IN ('group','supergroup') "
                                           "ORDER BY last_seen DESC LIMIT 60")
        except Exception:
            rows = []
        helpers = [h for h in p.helpers if h.me and h.bot]
        found = set(known_groups())
        for (chat_id,) in rows:
            for h in helpers[:2]:
                if await p.member_state(h, chat_id, fresh=True) != "out":
                    found.add(int(chat_id))
                    break
            await asyncio.sleep(0.2)
        await asyncio.to_thread(save_known, found)
        return f"🔍 {len(rows)} qrup yoxlandı — köməkçilərin olduğu qruplar: <b>{len(found)}</b>"

    show = panel.show

    @dp.callback_query(F.data.startswith("hg:"))
    async def hg_cb(cb: types.CallbackQuery):
        p = pool()
        parts = cb.data.split(":", 4)                     # hg:<act>:<chat>:<bot>:<back>  (v: hg:v:<chat>:<back>)
        act = parts[1]
        if act == "ext":
            ext_inp["stage"] = None
            await cb.answer()
            await show(cb, *await ext_groups_view())
            return
        if act == "ext_tog":
            if p:
                on = p.toggle_ext_enabled()
                await cb.answer("Kənar qruplar: " + ("✅ Açıq" if on else "❌ Söndürüldü"))
            else:
                await cb.answer()
            await show(cb, *await ext_groups_view())
            return
        if act == "ext_auto":
            if p:
                on = p.toggle_ext_auto()
                await cb.answer("Qoşulduqda avto-əlavə: " + ("✅ Açıq" if on else "⚪ Bağlı"))
            else:
                await cb.answer()
            await show(cb, *await ext_groups_view())
            return
        if act == "ext_pick":
            ext_inp["stage"] = None
            await cb.answer()
            await show(cb, *await ext_picker_view())
            return
        if act == "ext_step":
            delta = int(parts[2]) if len(parts) > 2 else 1
            v = p.step_ext_limit(delta) if p else 2
            await cb.answer(f"Limit: {v} bot")
            await show(cb, *await ext_picker_view(f"✓ Limit: {v} bot"))
            return
        if act == "ext_inp":
            await cb.answer()
            total_h = max(1, len([h for h in (p.helpers if p else []) if h.me and h.bot]))
            cur = p.ext_limit if p else 2
            ext_inp.update(stage="await_num", chat_id=cb.message.chat.id, panel_id=cb.message.message_id)
            text = (
                "🤖 <b>Köməkçi botlar › 👥 Kənar qrup limiti</b>\n" + "─" * 28 + "\n"
                f"Hazırkı limit: <b>{cur} bot</b>\n\n"
                "✍️ <b>Qrup başına köməkçi bot sayını daxil edin:</b>\n"
                f"• <b>Rəqəm</b> yazın (min: <code>1</code>, maks: <code>{total_h}</code>)\n"
                "• <b>+</b> yazın — maksimum olsun\n"
                "• <b>-</b> yazın — minimum (1) olsun\n"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[[btn("✖️ Ləğv et", "hg:ext_pick")]])
            await show(cb, text, kb)
            return
        if act in ("hub", "scan", "sc", "dchk"):
            note = ""
            if act == "scan":
                await cb.answer("🔍 Skan olunur...")
                note = await scan_groups()
            elif act == "sc" and p:
                p.set_scope(parts[3])
                await cb.answer("📌 Rejim dəyişdi")
            elif act == "dchk" and p:
                await cb.answer("🔄 Yoxlanılır...")
                await p.recheck()
                note = "🔄 Köməkçilər depoda yenidən yoxlandı"
            else:
                await cb.answer()
            await show(cb, *await hub_view(note))
            return
        try:
            chat_id = int(parts[2])
        except (IndexError, ValueError):
            await cb.answer()
            return
        if act == "v":
            back = cb.data.split(":", 3)[3] if cb.data.count(":") >= 3 else "grp:groups:0"
            await cb.answer()
            await show(cb, *await group_view(chat_id, back))
            return
        back = parts[4] if len(parts) > 4 else "grp:groups:0"
        note = ""
        if act == "f":
            await cb.answer("🔄 Yoxlanılır...")
            await show(cb, *await group_view(chat_id, back, fresh=True))
            return
        if act == "gr":
            await cb.answer("🛡 Yetki verilir...")
            note = await grant_note(chat_id)
            await show(cb, *await group_view(chat_id, back, fresh=True, note=note))
            return
        h = p.by_id(int(parts[3])) if p else None
        if h is None:
            await cb.answer("Köməkçi tapılmadı", show_alert=True)
            return
        if act == "t":
            on = await asyncio.to_thread(p.toggle_group, chat_id, h.me.id)
            note = f"@{escape(h.me.username)}: {'✅ bu qrupda göndərəcək' if on else '⚪ söndürüldü'}"
            await cb.answer()
        elif act == "ua":
            await cb.answer("🤖 Userbot əlavə edir...")
            res = await p.userbot_add(h, chat_id, admin=False)
            if res.startswith("✅") and h.me.id not in p.group_selected(chat_id):
                await asyncio.to_thread(p.toggle_group, chat_id, h.me.id)
            note = f"@{escape(h.me.username)}: {escape(res)}"
            await asyncio.sleep(1)
            await show(cb, *await group_view(chat_id, back, fresh=True, note=note))
            return
        await show(cb, *await group_view(chat_id, back, note=note))

    # ── 🔔 depoya əlavə et (creator-a gələn xatırlatma mesajı) ──
    @dp.callback_query(F.data.startswith("dh:"))
    async def dh_cb(cb: types.CallbackQuery):
        p = pool()
        _, act, bot_id = (cb.data.split(":") + ["", ""])[:3]
        h = p.by_id(int(bot_id)) if p and bot_id.isdigit() else None
        if h is None:
            await cb.answer("Köməkçi tapılmadı (silinib?)", show_alert=True)
            return
        res = ""
        if act == "ad":
            await cb.answer("🤖 Userbot depoya admin kimi əlavə edir...")
            target = None
            try:
                from bot.core import audio_cache
                ref = (audio_cache.depo_status() or {}).get("ref") or ""
                target = ref if ref and not str(ref).lstrip("-").isdigit() else await p.depo_id(context.bot)
            except Exception:
                target = await p.depo_id(context.bot)
            res = await p.userbot_add(h, target, admin=True)
            await asyncio.sleep(1)
        else:
            await cb.answer("🔄 Yoxlanılır...")
        await p._check(h)
        text = (f"🤖 @{escape(h.me.username)} — {escape(h.status)}"
                + (f"\n{escape(res)}" if res else ""))
        if h.ok:
            text = f"✅ <b>@{escape(h.me.username)} depoda hazırdır</b> — yükləmə ona da paylanacaq." \
                   + (f"\n<i>{escape(res)}</i>" if res else "")
            kb = None
        else:
            kb = p.add_kb(h)
        try:
            await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as _e:
            logger.debug("udulan xəta [bot/plugins/helper_groups_plugin.py:411]: %r", _e)

    # ── 🐢 qrupda flood → "köməkçi lazımdır?" sualının düymələri ──
    @dp.callback_query(F.data.startswith("hgs:"))
    async def hgs_cb(cb: types.CallbackQuery):
        p = pool()
        _, act, chat = (cb.data.split(":") + ["", "", "0"])[:3]
        if act == "x":
            await cb.answer()
            try:
                await cb.message.delete()
            except Exception as _e:
                logger.debug("udulan xəta [bot/plugins/helper_groups_plugin.py:426]: %r", _e)
            return
        chat_id = int(chat)
        if p is None:
            await cb.answer("Köməkçi botlar modulu yoxdur", show_alert=True)
            return
        if act == "mute":
            await asyncio.to_thread(p.mute_flood, chat_id)
            await cb.answer("🔕 Bu qrupda bir daha soruşulmayacaq")
            try:
                await cb.message.delete()
            except Exception as _e:
                logger.debug("udulan xəta [bot/plugins/helper_groups_plugin.py:438]: %r", _e)
            return
        note = ""
        if act == "gr":
            await cb.answer("🛡 Yetki verilir...")
            note = await grant_note(chat_id)
        elif act == "on":
            await cb.answer("✅ İşə salınır...")
            sel = set(p.group_selected(chat_id))
            owner_chat = await p.is_creator_chat(chat_id)
            lim = None if owner_chat else p.ext_limit
            n = 0
            for h in p.helpers:
                if lim is not None and (len(sel) + n) >= lim:
                    break
                if h.me and h.me.id not in sel and await p.member_state(h, chat_id) == "ok":
                    await asyncio.to_thread(p.toggle_group, chat_id, h.me.id)
                    n += 1
            note = f"✅ {n} köməkçi bu qrupda göndərməyə başladı"
        elif act == "ua":
            await cb.answer("🤖 Userbot əlavə edir...")
            res = []
            owner_chat = await p.is_creator_chat(chat_id)
            lim = None if owner_chat else p.ext_limit
            cur_count = p.group_count(chat_id)
            for h in p.helpers:
                if lim is not None and cur_count >= lim:
                    break
                if h.me and await p.member_state(h, chat_id, fresh=True) in ("out", "err"):
                    r = await p.userbot_add(h, chat_id, admin=False)
                    res.append(f"@{h.me.username}: {r}")
                    if r.startswith("✅"):
                        cur_count += 1
                        if h.me.id not in p.group_selected(chat_id):
                            await asyncio.to_thread(p.toggle_group, chat_id, h.me.id)
                    await asyncio.sleep(1)
            note = "\n".join(escape(x) for x in res) or "Əlavə ediləcək köməkçi yoxdur"
        text, kb, useful = await p.ask_view(chat_id)
        if not useful:
            text = "✅ <b>Bütün köməkçilər bu qrupda işləyir</b>\n" + text.split("\n", 1)[1]
            kb = InlineKeyboardMarkup(inline_keyboard=[[btn("✖️ Bağla", "hgs:x:0")]])
        try:
            await cb.message.edit_text(text + (f"\n\n{note}" if note else ""), parse_mode="HTML",
                                       reply_markup=kb, disable_web_page_preview=True)
        except Exception as _e:
            logger.debug("udulan xəta [bot/plugins/helper_groups_plugin.py:483]: %r", _e)

    from bot.core.input_wait import chain_menu_text
    chain_menu_text(context, lambda uid: is_creator(uid) and ext_inp.get("stage") == "await_num")

    async def waiting_ext_input(message: types.Message) -> bool:
        return ext_inp.get("stage") == "await_num" and is_creator(message.from_user.id)

    @dp.message(F.chat.type == "private", F.text, ~F.text.startswith("/"), waiting_ext_input)
    async def ext_limit_input(message: types.Message):
        raw = message.text.strip().lower()
        try:
            await message.delete()
        except Exception as _e:
            logger.debug("udulan xəta [bot/plugins/helper_groups_plugin.py:499]: %r", _e)
        p = pool()
        if not p:
            ext_inp["stage"] = None
            return

        panel = dict(chat_id=ext_inp["chat_id"], message_id=ext_inp["panel_id"], parse_mode="HTML")
        total_h = max(1, len([h for h in (p.helpers if p else []) if h.me and h.bot]))

        note = ""
        if raw in ("+", "max", "maks", "maksimum"):
            v = p.set_ext_limit(total_h)
            note = f"✅ Limit maksimum ({v} bot) olaraq təyin edildi"
        elif raw in ("-", "min", "minimum"):
            v = p.set_ext_limit(1)
            note = "✅ Limit minimum (1 bot) olaraq təyin edildi"
        elif raw.isdigit():
            val = int(raw)
            if val < 1:
                val = 1
            elif val > total_h:
                val = total_h
            v = p.set_ext_limit(val)
            note = f"✅ Limit {v} bot olaraq təyin edildi"
        else:
            text = (
                "🤖 <b>Köməkçi botlar › 👥 Kənar qrup limiti</b>\n" + "─" * 28 + "\n"
                f"⚠️ <b>\"{escape(message.text[:30])}\" düzgün deyil!</b>\n\n"
                "✍️ <i>Yalnız aşağıdakılardan birini yazın:</i>\n"
                f"• <b>Rəqəm</b> (<code>1</code> ilə <code>{total_h}</code> arası)\n"
                "• <b>+</b> (maksimum köməkçi sayı)\n"
                "• <b>-</b> (minimum — 1 bot)\n"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [btn("✖️ Ləğv et", "hg:ext_pick")]
            ])
            try:
                await context.bot.edit_message_text(text=text, reply_markup=kb, **panel)
            except Exception as _e:
                logger.debug("udulan xəta [bot/plugins/helper_groups_plugin.py:538]: %r", _e)
            return

        ext_inp["stage"] = None
        text, kb = await ext_picker_view(note)
        try:
            await context.bot.edit_message_text(text=text, reply_markup=kb, **panel)
        except Exception as _e:
            logger.debug("udulan xəta [bot/plugins/helper_groups_plugin.py:546]: %r", _e)

    logger.info("🤖 Köməkçi botlar: qruplar + depoya avtomatik əlavə")
