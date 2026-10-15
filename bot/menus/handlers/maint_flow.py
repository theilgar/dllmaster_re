"""
bot.menus.handlers.maint_flow
🛠 Texniki iş rejimi paneli: düymələr və istifadəçi mətninin daxil edilməsi.
"""
import asyncio
import contextlib
import logging
from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from bot.menus.state import get_context, is_creator, mt as mt_state, panel_editor, reset_inputs, safe_edit_cb
from bot.menus.views.maintenance import ask_text_view, maint_view, tests_view
from common import maintenance

logger = logging.getLogger(__name__)

TEXT_LIMIT = 1500


async def apply_toggle(context) -> str:
    """Aç / söndür — plugin varsa o (yayım, depo, xəbər), yoxdursa yalnız bayraq. → istifadəçiyə qeyd."""
    api = getattr(context, "maintenance", None)
    if not maintenance.is_active():
        if api:
            res = await api.start()
        else:
            maintenance.enable()
            res = {"voice": 0, "depo": False}
        bits = []
        if res.get("announced"):
            bits.append(f"📣 {res['announced']} yayım qrupuna bildiriş getdi")
        if res["voice"]:
            bits.append(f"🎙 {res['voice']} yayım dayandırıldı")
        if res["depo"]:
            bits.append("📦 depo pauza edildi")
        left = maintenance.remaining()
        return ("🛠 <b>Texniki iş rejimi AÇILDI</b>" + (" · ⚠️ xəbərdarlıq (hamı istifadə edir)" if maintenance.mode() == "notice" else "")
                + (f" · {left // 60} dəq. sonra avtomatik bitəcək" if left else " · müddətsiz (əl ilə söndürün)")
                + (f"\n{' · '.join(bits)}" if bits else ""))
    if api:
        res = await api.finish()
    else:
        res = {"waiters": len(maintenance.disable()), "notified": False, "depo": False}
    return ("🔴 <b>Texniki iş rejimi söndürüldü</b>"
            + (f"\n🔔 {res['waiters']} istifadəçiyə xəbər verilir" if res.get("notified") else "")
            + (f"\n📣 {res['streams']} yayım qrupuna «bitdi» göndərilir" if res.get("streams") else "")
            + (f"\n🗑 {res['deleted']} bildiriş silindi" if res.get("deleted") else "")
            + ("\n📦 depo doldurucu davam edir" if res.get("depo") else ""))


async def _mcb_maint(cb: CallbackQuery, parts: list, action: str):
    """menu:maint[:tog | :mode | :styled | :del | :delnow | :dur | :voice | :depo | :notify | :stopv | :text | :textdef | :prev]"""
    sub = parts[2] if len(parts) > 2 else ""
    ctx = get_context()
    note = ""
    if sub == "":
        mt_state["stage"] = None
    elif sub == "tog":
        note = await apply_toggle(ctx)
    elif sub == "mode":
        m = maintenance.toggle_mode()
        note = ("🔒 Bağlı rejim: creator-dan başqa heç kim botdan istifadə edə bilmir" if m == "block"
                else "⚠️ Xəbərdarlıq rejimi: hamı botdan istifadə edir, yalnız xəbərdarlıq göstərilir")
    elif sub == "dur":
        maintenance.cycle_duration()
        note = f"⏱ Müddət: {maintenance.duration_label(maintenance.settings()['duration'])} " \
               "<i>(növbəti açılışdan tətbiq olunur)</i>"
    elif sub == "voice":
        on = maintenance.toggle_option(maintenance.K_VOICE)
        note = "🎙 Rejim açılanda aktiv yayımlar dayandırılacaq" if on else "🎙 Aktiv yayımlar davam edəcək (yeniləri bağlıdır)"
    elif sub == "depo":
        on = maintenance.toggle_option(maintenance.K_DEPO)
        note = "📦 Rejim açılanda depo doldurucu pauza olacaq, bitəndə davam edəcək" if on else "📦 Depo doldurucu işləməyə davam edəcək"
    elif sub == "notify":
        on = maintenance.toggle_option(maintenance.K_NOTIFY, default_on=True)
        note = "🔔 Rejim bitəndə cəhd edənlərə xəbər veriləcək" if on else "🔕 Xəbər verilməyəcək"
    elif sub == "streams":
        on = maintenance.toggle_option(maintenance.K_STREAM_NOTICE, default_on=True)
        note = ("📣 Aktiv yayımı olan qruplara eyni bildiriş göndəriləcək" if on
                else "📣 Yayım qruplarına bildiriş göndərilməyəcək")
    elif sub == "styled":
        on = maintenance.toggle_option(maintenance.K_STYLED, default_on=True)
        note = ("📋 Mesajlar Görünüş üslubunda göndəriləcək (kod bloku / sitat)" if on
                else "📋 Mesajlar adi mətn kimi göndəriləcək (öz formatınız saxlanır)")
    elif sub == "del":
        m = maintenance.cycle_delete()
        note = (f"🗑 Bildirişlər {maintenance.DELETE_LABELS[m]} silinəcək" if m != "0"
                else "🗑 Bildirişlər silinməyəcək")
    elif sub == "delnow":
        api = getattr(ctx, "maintenance", None)
        n = await api.cleanup() if api else 0
        note = f"🗑 {n} bildiriş silindi"
    elif sub == "stopv":
        api = getattr(ctx, "maintenance", None)
        n = await api.stop_voice() if api else 0
        note = f"⏹ {n} yayım dayandırıldı"
    elif sub == "tests":
        await cb.answer()
        await safe_edit_cb(cb, *tests_view())
        return
    elif sub == "tr":                                      # menu:maint:tr:<test|all|sim>
        which = parts[3] if len(parts) > 3 else "all"
        from bot.core import selftest
        if which == "sim":                                 # «Adminə bildir» sınağı: saxta istifadəçi xətası
            from bot.plugins.report_plugin import with_report
            await cb.answer("🚨 Saxta xəta göndərildi")
            with contextlib.suppress(Exception):
                await ctx.bot.send_message(
                    cb.from_user.id,
                    "❌ <b>Xəta:</b> <code>TEST — bu saxta xətadır (texniki iş rejimi → Testlər)</code>\n"
                    "<i>Aşağıdakı düyməyə basın: bot sahibinə (sizə) bildiriş gəlməlidir.</i>",
                    parse_mode="HTML", reply_markup=with_report(None))
            return
        keys = selftest.ORDER if which == "all" else ([which] if which in selftest.TESTS else [])
        if not keys:
            await cb.answer()
            return
        await cb.answer("🧪 Test gedir...")

        async def progress(done, current):
            with contextlib.suppress(Exception):
                await safe_edit_cb(cb, *tests_view(done, running=current))

        results = await selftest.run(ctx, keys, progress)
        bad = [r for r in results if not r["ok"]]
        await safe_edit_cb(cb, *tests_view(results, note=("❌ Problem tapıldı — loqa baxın" if bad else "")))
        return
    elif sub == "textdef":
        maintenance.set_option(maintenance.K_TEXT, None)
        note = "♻️ Standart mətn bərpa olundu"
    elif sub == "text":
        await cb.answer()
        reset_inputs()
        mt_state.update(stage="await_text", chat_id=cb.message.chat.id, panel_id=cb.message.message_id)
        await safe_edit_cb(cb, *ask_text_view())
        return
    elif sub == "prev":                                    # istifadəçinin görəcəyi mətn (creator-a)
        await cb.answer()
        with contextlib.suppress(Exception):
            await ctx.bot.send_message(cb.from_user.id, maintenance.message(), parse_mode="HTML",
                                       disable_web_page_preview=True)
        return
    await cb.answer(note.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")[:190] if note else None)
    await safe_edit_cb(cb, *maint_view(note))


async def waiting_maint_text(message: Message) -> bool:
    return mt_state.get("stage") == "await_text" and bool(message.from_user) and is_creator(message.from_user.id)


async def maint_text_input(message: Message):
    ctx = get_context()
    html = message.html_text or ""
    with contextlib.suppress(Exception):
        await message.delete()
    edit = panel_editor(ctx, mt_state, "Texniki iş")
    if not html.strip() or len(html) > TEXT_LIMIT:
        await edit(*ask_text_view(f"❌ Mətn boş və ya çox uzundur (maks. {TEXT_LIMIT} simvol). Yenidən yaz:"))
        return
    try:                                                    # Telegram bu HTML-i qəbul edirmi?
        await ctx.bot.send_message(message.from_user.id, maintenance.message(custom=html), parse_mode="HTML",
                                   disable_web_page_preview=True)
    except Exception as e:
        await edit(*ask_text_view(f"❌ Mətn göndərilə bilmir: <code>{escape(str(e))[:200]}</code>"))
        return
    maintenance.set_option(maintenance.K_TEXT, html)
    mt_state["stage"] = None
    await edit(*maint_view("✅ Mətn saxlanıldı — yuxarıdakı mesaj istifadəçilərin görəcəyi kimidir"))


async def maint_text_not_text(message: Message):
    await message.reply("✍️ Mətn kimi yaz.")


def register_maint_handlers(router: Router):
    router.message.register(maint_text_input, F.chat.type == "private", F.text, ~F.text.startswith("/"),
                            waiting_maint_text)
    router.message.register(maint_text_not_text, F.chat.type == "private", ~F.text, waiting_maint_text)


__all__ = ["apply_toggle", "register_maint_handlers", "_mcb_maint", "asyncio"]
