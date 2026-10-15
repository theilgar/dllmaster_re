"""
bot.menus.handlers.audit_flow
Qrup auditi, log kanalları və mövzu hədəflərinin tənzimlənməsi axını.
"""

import contextlib
import logging
import re
from html import escape

from aiogram.types import CallbackQuery, InlineKeyboardMarkup

from bot.menus.state import get_context, rec_inp, safe_edit_cb
from bot.menus.views.audit import (
    _AU_TOPICS,
    _au_btn,
    _au_candidates,
    _lg,
    _lgub,
    audit_apply_all_view,
    audit_apply_target,
    audit_clear_view,
    audit_copy_view,
    audit_discover_view,
    audit_groups_view,
    audit_gtarget_view,
    audit_gview,
    audit_open_gtopics,
    audit_open_topics,
    audit_settings_view,
    audit_target_view,
    audit_test,
)
from bot.menus.views.base import LINE, SECTIONS, crumb

logger = logging.getLogger(__name__)


_GROUP_SUBS = {"gcp", "gcpg", "gall", "gdaily", "grep", "gk", "gclr", "gsrc", "gadd", "gtc", "gdel", "gl", "gv", "gat", "gt", "gtg", "gi", "gm", "gp", "gtp", "gr", "gra", "gtest"}


async def _mcb_au_group(cb: CallbackQuery, sub: str, arg: str, extra: str, extra2: str):
    """🏘 Qrup üzrə audit ayarları: menu:au:g*:<chat_id>[:<axın>[:<əlavə>]]."""
    ctx = get_context()
    lgm, ubm = _lg(), _lgub()

    if sub == "gl":                                           # qrup siyahısı (arg = səhifə)
        rec_inp["stage"] = None
        rec_inp.pop("gcid", None)
        await cb.answer()
        await safe_edit_cb(cb, *audit_groups_view(int(arg) if arg.isdigit() else 0))
        return

    if sub == "gk":                                           # 📋 bilinən qruplardan seç
        if arg == "add" and extra.lstrip("-").isdigit():
            c = int(extra)
            lgm.add_group(ubm, c, lgm.known_groups(ubm).get(c, ""))
            await cb.answer("✅ Qrup əlavə olundu")
            await safe_edit_cb(cb, *audit_gview(c, "✅ Qrup auditə əlavə edildi"))
            return
        await cb.answer()
        await safe_edit_cb(cb, *audit_discover_view(int(arg) if arg.isdigit() else 0))
        return

    if sub == "gclr":                                         # 🗑 siyahını təmizlə (arg: off | all)
        if arg in ("off", "all"):
            n = lgm.forget_all(ubm, only_inactive=(arg == "off"))
            await cb.answer(f"🗑 {n} qrup siyahıdan silindi")
            await safe_edit_cb(cb, *audit_groups_view(0, f"🗑 {n} qrup siyahıdan silindi"))
        else:
            await cb.answer()
            await safe_edit_cb(cb, *audit_clear_view())
        return

    if sub == "gadd":                                         # ➕ qrupu yazaraq əlavə et
        await cb.answer()
        rec_inp.update(stage="await_target", stream="au", gcid="add", chat_id=cb.message.chat.id,
                       panel_id=cb.message.message_id)
        await safe_edit_cb(
            cb,
            crumb(SECTIONS["bots"], "🛡 Audit", "➕ Qrup əlavə et")
            + "\n" + LINE + "\n\nİzlənəcək qrupu bu çata yaz və göndər:\n"
            "• <code>@qrup</code> · <code>-1001234567890</code>\n"
            "• <code>https://t.me/qrup</code>\n"
            "<i>İzləyən hesab (asistan / userbot) həmin qrupda üzv olmalıdır.</i>",
            InlineKeyboardMarkup(inline_keyboard=[[_au_btn("✖️ Ləğv et", "gl")]]),
        )
        return

    try:
        cid = int(arg)
    except ValueError:
        await cb.answer("⚠️ Qrup tapılmadı", show_alert=True)
        return
    s = extra if extra in lgm.STREAM_INFO else ""

    async def _t(note: str = ""):
        with contextlib.suppress(Exception):
            await cb.answer(re.sub(r"<[^>]+>", "", note)[:190] if note else None)
        await safe_edit_cb(cb, *audit_gtarget_view(cid, s, note))

    async def _v(note: str = ""):
        with contextlib.suppress(Exception):
            await cb.answer(re.sub(r"<[^>]+>", "", note)[:190] if note else None)
        await safe_edit_cb(cb, *audit_gview(cid, note))

    if sub == "gv":
        rec_inp["stage"] = None
        rec_inp.pop("gcid", None)
        await _v()
    elif sub == "gcpg":                                       # kopyalama siyahısı: səhifə
        await cb.answer()
        await safe_edit_cb(cb, *audit_copy_view(cid, int(extra) if extra.isdigit() else 0))
    elif sub == "gcp":                                        # 📋 başqa qrupdan kopyala
        if not extra.lstrip("-").isdigit():
            await cb.answer()
            await safe_edit_cb(cb, *audit_copy_view(cid))
        else:
            src = int(extra)
            n = lgm.copy_group_settings(ubm, src, cid)
            await _v(f"📋 «{escape(lgm.known_groups(ubm).get(src, str(src))[:24])}» qrupundan {n} ayar kopyalandı")
    elif sub == "gall":                                       # 📤 bu qrupun ayarlarını hamısına tətbiq et
        if extra == "ok":
            n = lgm.copy_to_all(ubm, cid)
            await _v(f"📤 Ayarlar {n} qrupa tətbiq olundu")
        else:
            await cb.answer()
            await safe_edit_cb(cb, *audit_apply_all_view(cid))
    elif sub == "gdaily":                                     # 📅 bu qrup üçün 23:59 hesabat
        on = not lgm.daily_on(ubm, cid)
        lgm.set_daily(ubm, cid, on)
        await _v(f"📅 23:59 hesabat bu qrup üçün {'aktiv edildi 🟢' if on else 'söndürüldü 🔴'}")
    elif sub == "grep":                                       # 📄 bu qrupun bugünkü hesabatı
        await cb.answer("📄 Hesabat hazırlanır...")
        from common.audit import send_daily_audit_report

        try:
            fp, total, _dest = await send_daily_audit_report(
                ctx, force=True, target_chat=cb.from_user.id, chat_id=cid,
                chat_title=lgm.known_groups(ubm).get(cid, str(cid)))
            note = f"📄 Hesabat göndərildi ({total} hadisə)" if fp else "ℹ️ Bu gün üçün hadisə yoxdur"
        except Exception as e:
            logger.warning("audit hesabatı göndərilmədi: %s", e)
            note = f"❌ Hesabat göndərilmədi: {escape(str(e)[:120])}"
        await safe_edit_cb(cb, *audit_gview(cid, note))
    elif sub == "gsrc":                                       # 👁 bu qrupu kim izləyir
        new_src = "userbot" if lgm.group_source(ubm, cid) == "assistant" else "assistant"
        lgm.set_group_source(ubm, cid, new_src)
        note = "👁 İzləyən hesab: " + ("🎙 səsli yayım asistanı" if new_src == "assistant" else "📱 userbot")
        if new_src == "assistant" and not lgm._assistant_slots(ubm):
            note += " — asistan hazırda işləmir, userbot əvəz edir"
        await _v(note)
    elif sub == "gat":                                        # bu qrupda audit aç / bağla
        on = not lgm.is_audit_active_for(ubm, cid)
        ubm.db.kv_set("logger", lgm._gk(cid, "on_au"), "1" if on else "0")
        await _v(f"🛡 Bu qrupda audit {'aktiv edildi 🟢' if on else 'bağlandı 🔴'}")
    elif sub == "gtg" and s in lgm.GEVENTS_SET:               # hadisəni bu qrup üçün aç / bağla
        on = not lgm.stream_on(ubm, s, cid)
        lgm.set_group_event_on(ubm, cid, s, on)
        await _v(f"{lgm.STREAM_INFO[s]['name']}: {'aktiv edildi 🟢' if on else 'söndürüldü 🔴'}")
    elif sub == "gdel":
        lgm.forget_group(ubm, cid)
        await cb.answer("🗑 Qrup siyahıdan silindi (mesaj gələndə yenidən çıxa bilər)")
        await safe_edit_cb(cb, *audit_groups_view(0, "🗑 Qrupun ayarları silindi"))
    elif sub == "gtc" and s:
        lgm.clear_topic(ubm, s, cid)
        await _t("🧵 Topic silindi — hədəf çatın əsas hissəsinə (General) gedir")
    elif sub == "gra":                                        # qrupun bütün ayarlarını sil
        for st in lgm.STREAM_INFO:
            lgm.reset_group_stream(ubm, cid, st)
        ubm.db.kv_set("logger", lgm._gk(cid, "on_au"), "0")        # siyahıda qalır, amma sönülü
        ubm.db.kv_del("logger", lgm._gk(cid, "source"))
        await _v("♻️ Qrupun ayarları sıfırlandı — ümumi ayarlar işləyir")
    elif not s or (s not in ("au", "report") and s not in lgm.GEVENTS_SET):
        await cb.answer("⚠️ Axın tapılmadı", show_alert=True)
    elif sub == "gt":
        rec_inp["stage"] = None
        rec_inp.pop("gcid", None)
        await _t()
    elif sub == "gm":
        lgm.set_group_target(ubm, cid, s, "me")
        await _t("✅ Hədəf: 💾 Saved Messages")
    elif sub == "gr":
        lgm.reset_group_stream(ubm, cid, s)
        await _t("🔗 Qrupun ayarı silindi — ümumi ayar işləyir")
    elif sub == "gtest":
        try:
            note = await audit_test(s, cid)
        except Exception as e:
            note = f"❌ {escape(str(e)[:150])}"
        await _t(note)
    elif sub == "gp":
        cands = _au_candidates(lgm, ubm)
        idx = int(extra2) if extra2.isdigit() else -1
        if not 0 <= idx < len(cands):
            await _t("⚠️ Siyahı köhnəlib — yenidən seç")
            return
        chat, topic, title = cands[idx]
        lgm.set_group_target(ubm, cid, s, chat, topic, title)
        await _t(f"✅ Hədəf: <b>{escape(str(title or chat))}</b>" + (f" · topic {topic}" if topic else ""))
    elif sub == "gi":
        await cb.answer()
        rec_inp.update(stage="await_target", stream=s, gcid=cid, chat_id=cb.message.chat.id,
                       panel_id=cb.message.message_id)
        info = lgm.STREAM_INFO[s]
        await safe_edit_cb(
            cb,
            crumb(SECTIONS["bots"], "🛡 Audit", f"✍️ {info['icon']} {info['name']}")
            + "\n" + LINE + f"\n\n🏘 <b>{escape(lgm.known_groups(ubm).get(cid, str(cid)))}</b> üçün hədəfi bu çata yaz və göndər:\n"
            "• <code>@kanal</code> və ya <code>-1001234567890</code>\n"
            "• topic ilə: <code>-1001234567890 42</code> və ya <code>https://t.me/c/1234567890/42</code>\n"
            "• <code>me</code> — Saved Messages",
            InlineKeyboardMarkup(inline_keyboard=[[_au_btn("✖️ Ləğv et", "gt", cid, s)]]),
        )
    elif sub == "gtp":
        from bot.menus.views.audit import _AU_TOPICS, audit_gtopic_view

        if not extra2:                                        # 🧵 Topic seç → siyahı
            chat = lgm.get_stream_target(ubm, s, cid)[0]
            res = await audit_open_gtopics(cid, s, chat)
            if res is None:
                await cb.answer("Mövzular açılmadı (qrup forum deyil?)", show_alert=True)
                await safe_edit_cb(cb, *audit_gtarget_view(cid, s))
                return
            await cb.answer()
            await safe_edit_cb(cb, *res)
            return
        data = _AU_TOPICS.get(f"g{cid}:{s}")
        if not data:
            await _t("⚠️ Topic siyahısı yoxdur — 🧵 Topic seç düyməsinə yenidən bas")
            return
        if extra2 == "g":
            tid, tname = None, "💬 General"
        elif extra2.isdigit() and int(extra2) < len(data["topics"]):
            tid, name = data["topics"][int(extra2)]
            tname = f"🧵 {name}"
        else:
            await _t("⚠️ Topic tapılmadı")
            return
        lgm.set_group_target(ubm, cid, s, data["chat"], tid, data.get("title", ""))
        await _t(f"✅ Mövzu seçildi: {escape(tname)}")


async def _mcb_au(cb: CallbackQuery, parts: list, action: str):
    """menu:au:<sub>:<arg>[:<extra>] — bot/menus/views/audit.py-dəki düymələrlə eyni protokol."""
    sub = parts[2] if len(parts) > 2 else ""
    arg = parts[3] if len(parts) > 3 else ""
    extra = parts[4] if len(parts) > 4 else ""
    lgm, ubm = _lg(), _lgub()
    if not ubm:
        await cb.answer("❌ Userbot yüklənməyib", show_alert=True)
        return

    async def _target(stream: str, note: str = ""):
        with contextlib.suppress(Exception):
            await cb.answer(re.sub(r"<[^>]+>", "", note)[:190] if note else None)
        await safe_edit_cb(cb, *audit_target_view(stream, note))

    if sub in _GROUP_SUBS:
        await _mcb_au_group(cb, sub, arg, extra, parts[5] if len(parts) > 5 else "")
        return

    note = ""
    if sub == "hubtog":                       # 🤖 Botlar pəncərəsindəki 🛡 açarı
        cur_on = ubm.db.kv_get("logger", "audit_enabled", "1") == "1"
        ubm.db.kv_set("logger", "audit_enabled", "0" if cur_on else "1")
        await cb.answer(f"🛡 Qrup auditi {'bağlandı 🔴' if cur_on else 'aktiv edildi 🟢'}")
        from bot.menus.views.system_hub import bots_view

        await safe_edit_cb(cb, *bots_view())
        return
    if sub == "hub":                          # ⬅️ Geri / 🔄 — yalnız göstər, heç nəyi dəyişmə
        pass
    elif sub == "tg":
        if arg == "au":
            cur_on = ubm.db.kv_get("logger", "audit_enabled", "1") == "1"
            ubm.db.kv_set("logger", "audit_enabled", "0" if cur_on else "1")
            note = f"🛡 Qrup auditi {'bağlandı 🔴' if cur_on else 'aktiv edildi 🟢'}"
        elif arg in lgm.STREAM_INFO:
            on = lgm.toggle_stream(ubm, arg)
            info = lgm.STREAM_INFO[arg]
            await cb.answer(f"{info['name']}: {'aktiv edildi 🟢' if on else 'söndürüldü 🔴'}")
            await safe_edit_cb(cb, *audit_settings_view())
            return
    elif sub == "t" and arg in lgm.STREAM_INFO:
        rec_inp["stage"] = None
        await _target(arg)
        return
    elif sub == "me" and arg in lgm.STREAM_INFO:
        lgm.set_target(ubm, arg, "me")
        await _target(arg, "✅ Hədəf: 💾 Saved Messages")
        return
    elif sub == "test" and arg in lgm.STREAM_INFO:
        try:
            note = await audit_test(arg)
        except Exception as e:
            note = f"❌ {escape(str(e)[:150])}"
        await _target(arg, note)
        return
    elif sub == "tc" and arg in lgm.STREAM_INFO:
        lgm.clear_topic(ubm, arg)
        await _target(arg, "🧵 Topic silindi — hədəf çatın əsas hissəsinə (General) gedir")
        return
    elif sub == "rs" and arg in lgm.STREAM_INFO:
        lgm.reset_stream_target(ubm, arg)
        await _target(arg, "🔗 Öz hədəfi silindi — miras hədəfə gedir")
        return
    elif sub == "pk" and arg in lgm.STREAM_INFO:
        cands = _au_candidates(lgm, ubm)
        idx = int(extra) if extra.isdigit() else -1
        if not 0 <= idx < len(cands):
            await _target(arg, "⚠️ Siyahı köhnəlib — yenidən seç")
            return
        chat, topic, title = cands[idx]
        lgm.set_target(ubm, arg, chat, topic, title)
        await _target(arg, f"✅ Hədəf: <b>{escape(str(title or chat))}</b>" + (f" · topic {topic}" if topic else ""))
        return
    elif sub == "tp" and arg in lgm.STREAM_INFO:
        if not extra:                         # 🧵 Topic seç → siyahını aç
            chat = lgm.get_stream_target(ubm, arg)[0]
            res = await audit_open_topics(arg, chat)
            if res is None:
                await cb.answer("Mövzular açılmadı (qrup forum deyil?)", show_alert=True)
                await safe_edit_cb(cb, *audit_target_view(arg))
                return
            await cb.answer()
            await safe_edit_cb(cb, *res)
            return
        data = _AU_TOPICS.get(arg)
        if not data:
            await _target(arg, "⚠️ Topic siyahısı yoxdur — 🧵 Topic seç düyməsinə yenidən bas")
            return
        if extra == "g":
            tid, tname = None, "💬 General"
        elif extra.isdigit() and int(extra) < len(data["topics"]):
            tid, name = data["topics"][int(extra)]
            tname = f"🧵 {name}"
        else:
            await _target(arg, "⚠️ Topic tapılmadı")
            return
        lgm.set_target(ubm, arg, data["chat"], tid, data.get("title", ""))
        await _target(arg, f"✅ Mövzu seçildi: {escape(tname)}")
        return
    elif sub == "in" and arg in lgm.STREAM_INFO:
        await cb.answer()
        rec_inp.pop("gcid", None)
        rec_inp.update(
            stage="await_target",
            stream=arg,
            chat_id=cb.message.chat.id,
            panel_id=cb.message.message_id,
        )
        info = lgm.STREAM_INFO[arg]
        await safe_edit_cb(
            cb,
            crumb(SECTIONS["bots"], "🛡 Audit", f"✍️ {info['icon']} {info['name']}")
            + "\n"
            + LINE
            + "\n\nHədəfi bu çata yaz və göndər:\n"
            "• <code>@kanal</code> və ya <code>-1001234567890</code>\n"
            "• topic ilə: <code>-1001234567890 42</code> və ya <code>https://t.me/c/1234567890/42</code>\n"
            "• <code>me</code> — Saved Messages",
            InlineKeyboardMarkup(inline_keyboard=[[_au_btn("✖️ Ləğv et", "t", arg)]]),
        )
        return
    with contextlib.suppress(Exception):
        await cb.answer(re.sub(r"<[^>]+>", "", note)[:190] if note else None)
    await safe_edit_cb(cb, *audit_settings_view(note))
