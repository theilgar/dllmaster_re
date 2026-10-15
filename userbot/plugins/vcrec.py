"""⏺ .vcrec — canlı yayım qeydinin idarə paneli (asistan hesabı ilə), userbot-dan.

  .vcrec                       — paneli aç (botun /vrec paneli ilə eynidir)
  .vcrec start | stop | mark   — qeydə başla / saxla və göndər / 📍 nişan
  .vcrec auto | manual         — ⚡ avtomatik (yayım başlayan kimi asistan qoşulub qeyd edir) / ✋ manual
  .vcrec timer [0|15|30|60|120]— ⏱ avto-dayanma taymeri (dəqiqə)
  .vcrec silent [on|off]       — 🔕 səssiz qeyd rejimi
  .vcrec dest target|dm|here   — 📤 nəticənin göndəriləcəyi yer
  .vcrec music 30|50|70|100    — qeyddə musiqinin səviyyəsi
  .vcrec status                — gedən qeydlər

Komanda çatdan dərhal silinir; panel bot tərəfindən göndərilir — default olaraq botun şəxsi çatına
(paneldə 👁 ilə qrupa dəyişmək olar). Bütün məntiq bot/core/va_record.py-da və
bot/plugins/voice_assistant_plugin.py-dadır (/vrec) — burada yalnız körpü.
Yayımı yalnız 🎙 asistan qeyd edir — .rec kartının ⏺ bölməsi də eyni qeydi idarə edir.
"""
import contextlib

from userbot.core.userbot_api import safe_edit


def register(ub):
    @ub.command(
        "vcrec",
        pattern=r"^\.vcrec(?:\s+(.+))?$",
        help=(
            "asistan ilə yayım qeydi (panel)",
            "Canlı yayımı 🎙 asistan hesabı qeyd edir — danışanlar + asistanın oxutduğu mahnılar bir mp3-də.\n\n"
            "• <code>.vcrec</code> — idarə paneli (⏺ / ⏹ · 📍 · ✋ Manual / ⚡ Avtomatik · ayarlar)\n"
            "• <code>.vcrec start|stop|mark|auto|manual|status</code>\n"
            "• <code>.vcrec timer 0|15|30|60|120</code> — avto-dayanma taymeri\n"
            "• <code>.vcrec silent on|off</code> — səssiz qeyd\n"
            "• <code>.vcrec dest target|dm|here</code> — göndəriləcək yer\n"
            "• <code>.vcrec music 30|50|70|100</code> — musiqinin səviyyəsi\n"
            "Botda eyni panel: qrupda <code>/vrec</code>. Qrup auditi — <code>.rec</code>."
        ),
    )
    async def _(event):
        fn = getattr(ub.context, "vrec_command", None)
        if fn is None:
            va = getattr(ub.context, "voice_assistant", None)
            rec = getattr(va, "rec", None) if va else None
            if rec:
                cid = event.chat_id
                chat = await event.get_chat()
                title = getattr(chat, "title", None) or str(cid)
                me = await event.client.get_me()
                by = " ".join(x for x in (me.first_name, me.last_name) if x) or "userbot"
                sub = (event.pattern_match.group(1) or "").strip().lower()
                if sub in ("start", "stop", "mark", "auto", "manual"):
                    note = await rec.action(cid, title, sub, by=by)
                    await safe_edit(event, f"🎙 <b>.vcrec</b>: {note}")
                    return
                elif sub in ("status", "?", "vəziyyət"):
                    await safe_edit(event, f"🎙 <b>.vcrec status:</b>\n{rec.status_text()}")
                    return
            await safe_edit(event, "❌ 🎙 Canlı yayım asistanı yüklənməyib (bot rejimi söndürülüb?) — "
                                   "<code>python init.py</code> ilə hər ikisini işə sal.")
            return
        cid = event.chat_id
        if cid is None or cid > 0:
            with contextlib.suppress(Exception):
                await event.delete()
            target = ub.creator_id
            bot_inst = getattr(ub.context, "bot", None)
            rec_inst = getattr(getattr(ub.context, "voice_assistant", None), "rec", None)
            if bot_inst and rec_inst and target:
                text, kb = rec_inst.groups_view()
                with contextlib.suppress(Exception):
                    await bot_inst.send_message(target, text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
                return
            await safe_edit(event, "⏺ <code>.vcrec</code> qrupda yazılır — həmin qrupun səsli yayımı qeyd olunur.")
            return
        chat = await event.get_chat()
        title = getattr(chat, "title", None) or str(cid)
        with contextlib.suppress(Exception):
            await event.delete()                     # çatda iz qalmasın
        me = await event.client.get_me()
        by = " ".join(x for x in (me.first_name, me.last_name) if x) or "userbot"
        try:
            await fn(cid, title, event.pattern_match.group(1) or "", by=by)
        except Exception as e:
            with contextlib.suppress(Exception):
                await event.client.send_message("me", f"❌ .vcrec ({title}): {e}")
