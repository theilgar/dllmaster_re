"""
📦 Depo kanalları menyusu: /depo → 🤖 Köməkçi botlar → 📦 Kanallar  (callback-lər: df:sh*)

  df:sh            hub: kanalların siyahısı, ➕ əlavə et, 🩺 yoxla, 🧹 flood-u ləğv et
  df:sh:<i>        əlavə kanalın səhifəsi: köməkçilər, flood, 🤖 admin et, 🗑 sil
  df:sha           ➕ kanal əlavə et: @kanal / id yazılır və ya kanaldan istənilən post yönləndirilir
  df:shc[:<i>]     🩺 köməkçilərin admin vəziyyətini yoxla
  df:shx           🧹 flood dayanmalarını ləğv et
  df:shh:<i>:<n>   🤖 userbot köməkçiləri admin edir (n = 0 hamısı)
  df:shd:<i> / df:shdy:<i>   🗑 sil (təsdiq ekranı)
  df:shp           siyasət: 🤖 avto ↔ ✋ manual
  df:shw:<m|i>     ✋ manual: kanalın payı (m = əsas depo)
  df:shs:<m|i>:<d> payı dəyiş (+10 / -10 / =100)

Mətn əmrləri eyni işi görür: bot/depo/shard_cmd.py. handlers.setup bir dəfə qurur (make_shard_menu).
"""
import contextlib
import re
import time
from html import escape
from types import SimpleNamespace

from aiogram import F, types
from aiogram.types import InlineKeyboardMarkup

from bot.core import depo_access, depo_shards, panel as ui
from bot.core.panel import btn
from bot.depo.shard_cmd import add_shard
from common.utilities import spawn

WAIT_SECONDS = 300
HELPER_BATCHES = (24, 12)


def channel_line(r) -> str:
    th = f" › 🧵 #{r['thread']}" if r["thread"] else ""
    state = (f"🌊 flood, ~{int(r['left'] // 60) + 1} dəq." if r["left"] > 0
             else "⚠️ əsas bot admin deyil" if not r.get("main_ok", True)
             else "⚠️ xəta verdi" if r.get("bad") else "🟢")
    mark = "📦" if r["primary"] else "➕"
    return (f"{mark} <b>{escape(str(r['ref']))}</b>{th} · {state}\n"
            f"   🤖 {r['helpers']} köməkçi · ⬆️ {r['sent']}/10 dəq."
            + ("" if r["id"] else " · ⚠️ kanal tapılmadı"))


def _label(k) -> str:
    """"m" → əsas depo, rəqəm → əlavə kanalın siyahıdakı sırası."""
    if k == "m":
        return "əsas depo"
    ex = depo_shards.extras()
    return str(ex[int(k)]["ref"]) if str(k).isdigit() and int(k) < len(ex) else "?"


def _weight_key(k):
    ex = depo_shards.extras()
    return depo_shards.MAIN_KEY if k == "m" else (ex[int(k)]["ref"] if str(k).isdigit() and int(k) < len(ex) else None)


def _share(key) -> str:
    """Kanalın yeni mahnılardan düşən payı (%), ✋ manual siyahıdakı çəkilərə görə."""
    ws = [depo_shards.weight(depo_shards.MAIN_KEY)] + [depo_shards.weight(e["ref"]) for e in depo_shards.extras()]
    total = sum(ws)
    return f"{depo_shards.weight(key) * 100 / total:.0f}%" if total else "0%"


def policy_text() -> str:
    if depo_shards.policy() == "manual":
        ws = ", ".join(f"{escape(str(_label(k)))} {_share(_weight_key(k))}" for k in
                       ["m"] + [str(i) for i in range(len(depo_shards.extras()))])
        return f"🎚 Siyasət: ✋ manual · {ws}"
    return "🎚 Siyasət: 🤖 avto"


def hub_view(note: str = ""):
    ex = depo_shards.extras()
    lines = ["📦 <b>Depo kanalları</b>\n━━━━━━━━━━━━━━━━━━",
             "<i>Telegram hər bota hər kanal üçün ayrıca saatlıq kvota verir: ikinci kanal tutumu artırır. "
             "Kanalda bütün köməkçilər kvotanı dolduranda yükləmə digərinə keçir. Yeni mahnılar kanallara bölünür, "
             "köhnələr yerində qalır.</i>\n"]
    lines += [channel_line(r) for r in depo_shards.report()]
    if not ex:
        lines.append("\n<i>Əlavə kanal yoxdur. ➕ ilə əlavə edin: əsas bot və köməkçilər orada admin olmalıdır.</i>")
    else:
        lines.append("\n🎚 <b>Siyasət:</b> 🤖 Avto — yük köməkçi sayına və flood-a görə bölünür · "
                     "✋ Manual — kanalların payını özünüz təyin edirsiniz")
    if note:
        lines.append(f"\n{note}")
    rows = [[btn("➕ Kanal əlavə et", "df:sha")]]
    rows += ui.grid([btn(f"⚙️ {e['ref']}"[:30], f"df:sh:{i}") for i, e in enumerate(ex)], 2)
    if ex:
        rows.append([btn("🩺 Yoxla", "df:shc"), btn("🧹 Flood-u ləğv et", "df:shx")])
        manual = depo_shards.policy() == "manual"
        rows.append([ui.mode("Siyasət", not manual, "df:shp")])
        if manual:
            ks = ["m"] + [str(i) for i in range(len(ex))]
            rows += ui.grid([btn(f"⚖️ {_label(k)}: {_share(_weight_key(k))}"[:30], f"df:shw:{k}") for k in ks], 2)
    rows.append(ui.nav("dc:panel", "Depo kanalı", close_data="df:close"))
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def weight_view(k: str, note: str = ""):
    key = _weight_key(k)
    if key is None:
        return hub_view(note or "⚠️ Kanal tapılmadı")
    text = (f"⚖️ <b>Depo kanalları › {escape(_label(k))}</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"Pay: <b>{depo_shards.weight(key)}</b> → yeni mahnıların <b>{_share(key)}</b>-i\n\n"
            "<i>Yeni mahnılar kanallara payların nisbətində bölünür (məs. 100 və 100 — yarı-yarı, 300 və 100 — 3:1). "
            "<b>0</b> — bu kanala yeni mahnı getmir (köhnələr yerində qalır). Flood-da olan kanal "
            "hər halda atlanır.</i>")
    if note:
        text += f"\n\n{note}"
    rows = [[btn(f"{d:+d}", f"df:shs:{k}:{d:+d}") for d in (-50, -10, 10, 50)],
            [btn("↩️ Bərabər (100)", f"df:shs:{k}:=100"), btn("⛔ 0", f"df:shs:{k}:=0")],
            ui.nav("df:sh", "Kanallar", close_data="df:close")]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def channel_view(helpers, i: int, note: str = ""):
    ex = depo_shards.extras()
    if not 0 <= i < len(ex):
        return hub_view(note or "⚠️ Kanal tapılmadı")
    e = ex[i]
    r = next((x for x in depo_shards.report() if not x["primary"] and x["ref"] == e["ref"]), None) or \
        {"ref": e["ref"], "id": None, "thread": e["thread"], "left": 0, "sent": 0, "helpers": 0, "primary": False}
    total = len([h for h in helpers.helpers if h.me and h.bot])
    lines = [f"📦 <b>Depo kanalları › {escape(str(e['ref']))}</b>\n━━━━━━━━━━━━━━━━━━", channel_line(r),
             f"🆔 <code>{r['id'] or '—'}</code> · admin köməkçi: <b>{r['helpers']}/{total}</b>",
             "<i>Admin olmayan köməkçi bu kanala yazmır. Telegram bir kanalda admin sayını məhdudlaşdırır "
             "(təxminən 50), ona görə hamısını əlavə etmək alınmaya bilər.</i>"]
    if note:
        lines.append(f"\n{note}")
    batches = [btn("🤖 Hamısını admin et", f"df:shh:{i}:0")]
    batches += [btn(f"🤖 {n} köməkçi", f"df:shh:{i}:{n}") for n in HELPER_BATCHES if n < total]
    rows = ui.grid(batches, 2) + [[btn("🩺 Yoxla", f"df:shc:{i}"), btn("🗑 Sil", f"df:shd:{i}")],
                                  ui.nav("df:sh", "Kanallar", close_data="df:close")]
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def add_prompt_view(note: str = ""):
    text = ("📦 <b>Depo kanalları › Kanal əlavə et</b>\n━━━━━━━━━━━━━━━━━━\n"
            "✍️ <b>Kanalı göstərin:</b>\n"
            "• kanaldan <b>istənilən postu bura yönləndirin</b> (ən asan), və ya\n"
            "• <code>@kanal</code> / <code>-100…</code> id / <code>t.me/kanal</code> yazın\n"
            "• forum qrupu üçün mövzu linki: <code>t.me/qrup/12</code>\n\n"
            "<i>Əvvəlcə əsas botu kanala admin edin, \"Restrict saving content\" söndürülməlidir.</i>")
    if note:
        text += f"\n\n{note}"
    return text, InlineKeyboardMarkup(inline_keyboard=[[ui.cancel("df:sh")]])


def confirm_rm_view(i: int):
    ex = depo_shards.extras()
    name = escape(str(ex[i]["ref"])) if 0 <= i < len(ex) else "?"
    text = (f"🗑 <b>{name}</b> siyahıdan silinsin?\n\n"
            "<i>Yeni mahnılar bu kanala getməyəcək. Kanalın özü və orada olan mahnılar toxunulmaz qalır.</i>")
    return text, InlineKeyboardMarkup(inline_keyboard=[ui.confirm(f"df:shdy:{i}", f"df:sh:{i}", "sil"),
                                                       ui.nav("df:sh", "Kanallar", close_data="df:close")])


def make_shard_menu(dp, bot, helpers, is_creator):
    wait = {"until": 0, "chat_id": None, "msg_id": None}
    running = set()

    def waiting(uid) -> bool:
        return is_creator(uid) and time.time() < wait["until"]

    async def _waiting_filter(message: types.Message) -> bool:
        return (bool(message.from_user) and waiting(message.from_user.id)
                and not (message.text or "").startswith("/"))

    @dp.message(F.chat.type == "private", _waiting_filter)
    async def shard_input(message: types.Message):
        # kanalı tapmaq və botun hüququnu yoxlamaq: bot/core/depo_access.py ("Depo kanalı" səhifəsi ilə ortaq)
        found, thread, err = await depo_access.from_message(bot, message)
        with contextlib.suppress(Exception):
            await message.delete()
        coords = (wait["chat_id"], wait["msg_id"])
        if err:
            text, kb = add_prompt_view(err)                              # gözləmə davam edir
        else:
            wait["until"] = 0
            text, kb = hub_view(await add_shard(bot, helpers, found.ref, thread, found))
        if coords[1]:
            with contextlib.suppress(Exception):
                await bot.edit_message_text(chat_id=coords[0], message_id=coords[1], text=text,
                                            parse_mode="HTML", reply_markup=kb)
                return
        await message.answer(text, parse_mode="HTML", reply_markup=kb)

    async def _add_helpers(chat_id, ref, n):
        try:
            res = await helpers.shard_add_helpers(ref, n)
            errs = "\n".join(f"• {escape(e)}" for e in res["errors"][:5])
            with contextlib.suppress(Exception):
                await bot.send_message(
                    chat_id, f"🤖 <b>Köməkçilər {escape(str(ref))} kanalına əlavə olundu</b>\n"
                             f"✅ yeni: {res['added']} · əvvəldən: {res['already']} · ❌ alınmadı: {res['failed']}"
                             + (f"\n{errs}" if errs else ""), parse_mode="HTML")
        finally:
            running.discard(ref)

    async def handle(cb: types.CallbackQuery, parts: list, show):
        act = parts[1]
        arg = int(parts[2]) if len(parts) > 2 and parts[2].lstrip("-").isdigit() else None
        ex = depo_shards.extras()
        await cb.answer()
        if act == "sh":
            await show(*(channel_view(helpers, arg) if arg is not None else hub_view()))
        elif act == "sha":
            wait.update(until=time.time() + WAIT_SECONDS, chat_id=cb.message.chat.id, msg_id=cb.message.message_id)
            await show(*add_prompt_view())
        elif act == "shc":
            await helpers.verify_all_shards()
            await show(*(channel_view(helpers, arg, "🩺 Yoxlandı") if arg is not None else hub_view("🩺 Yoxlandı")))
        elif act == "shp":
            depo_shards.set_policy("auto" if depo_shards.policy() == "manual" else "manual")
            await show(*hub_view("✋ Manual: payları özünüz təyin edirsiniz" if depo_shards.policy() == "manual"
                                 else "🤖 Avto: yük köməkçi sayına və flood-a görə bölünür"))
        elif act == "shw":
            await show(*weight_view(parts[2] if len(parts) > 2 else ""))
        elif act == "shs" and len(parts) > 3:
            key = _weight_key(parts[2])
            raw = parts[3]
            if key is not None and re.fullmatch(r"(=\d{1,4}|[+-]\d{1,4})", raw):
                cur = depo_shards.weight(key)
                depo_shards.set_weight(key, int(raw[1:]) if raw.startswith("=") else cur + int(raw))
            await show(*weight_view(parts[2]))
        elif act == "shx":
            helpers.clear_channel_cool()
            await show(*hub_view("✅ Flood dayanmaları ləğv olundu"))
        elif act == "shh" and arg is not None and 0 <= arg < len(ex):
            ref = ex[arg]["ref"]
            n = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
            if ref in running:
                await show(*channel_view(helpers, arg, "⏳ Artıq gedir — bitəndə yazacağam"))
                return
            running.add(ref)
            spawn(_add_helpers(cb.message.chat.id, ref, n))
            await show(*channel_view(helpers, arg, "⏳ Userbot köməkçiləri əlavə edir (1.5 san. fasilə ilə) — bitəndə yazacağam"))
        elif act == "shd" and arg is not None:
            await show(*confirm_rm_view(arg))
        elif act == "shdy" and arg is not None and 0 <= arg < len(ex):
            depo_shards.remove(ex[arg]["ref"])
            await show(*hub_view("🗑 Kanal siyahıdan silindi"))
        else:
            await show(*hub_view())

    return SimpleNamespace(handle=handle, waiting=waiting, wait=wait, running=running)
