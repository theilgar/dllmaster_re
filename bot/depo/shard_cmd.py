"""
🧩 /depo shard ... — əlavə depo kanalları (bot/core/depo_shards.py) üçün əmrlər: siyahı, əlavə et, sil, yoxla,
köməkçiləri userbot ilə admin et, flood dayanmalarını ləğv et. handlers.setup bir dəfə qurur (make_shard_cmd).
"""
import contextlib
from html import escape

from aiogram import types

from bot.core import audio_cache, depo_access, depo_shards
from common.utilities import spawn


def shard_text(note: str = "") -> str:
    lines = ["🧩 <b>Depo kanalları (shard)</b>\n━━━━━━━━━━━━━━━━━━",
             f"🎚 Siyasət: {'✋ manual' if depo_shards.policy() == 'manual' else '🤖 avto'}"]
    if not depo_shards.extras():
        lines.append("<i>Əlavə kanal yoxdur — bütün mahnılar əsas depoya gedir.</i>")
    for r in depo_shards.report():
        th = f" › 🧵 #{r['thread']}" if r["thread"] else ""
        state = (f"🌊 flood-da, ~{int(r['left'] // 60) + 1} dəq." if r["left"] > 0
                 else "⚠️ əsas bot admin deyil" if not r.get("main_ok", True)
                 else "⚠️ xəta verdi, bir az gözləyir" if r.get("bad") else "🟢 işləyir")
        lines.append(f"{'📦' if r['primary'] else '➕'} <b>{escape(str(r['ref']))}</b>{th} · {state}\n"
                     f"   🤖 köməkçi: {r['helpers']} · ⬆️ son 10 dəq.: {r['sent']}"
                     + (f" · ⚖️ pay: {r['weight']}" if depo_shards.policy() == "manual" else "")
                     + ("" if r["id"] else " · ⚠️ kanal tapılmadı"))
    lines.append("\n<code>/depo shard add &lt;@kanal|id&gt; [mövzu]</code> · <code>rm</code> · <code>check</code>\n"
                 "<code>/depo shard helpers &lt;@kanal|id&gt; [say]</code> — userbot köməkçiləri admin edir\n"
                 "<code>/depo shard clear</code> — flood dayanmalarını ləğv edir\n"
                 "<code>/depo shard policy auto|manual</code> · <code>/depo shard weight &lt;main|@kanal&gt; &lt;pay&gt;</code>")
    if note:
        lines.append(f"\n{note}")
    return "\n".join(lines)


async def add_shard(bot, helpers, ref, thread=None, found=None) -> str:
    """Kanalı siyahıya əlavə edir (əmr də, menyu da bunu çağırır) → istifadəçiyə qeyd (HTML).
    Kanal tapılmalı və bot orada yazmaq hüququ olan admin olmalıdır (bot/core/depo_access.py) — yoxsa əlavə olunmur."""
    if found is None:
        found, err = await depo_access.inspect(bot, ref)
        if err:
            return err
    cid = await depo_shards.resolve(bot, found.ref)
    if cid is None:
        return "❌ Əsas bot bu kanala çıxış tapmadı — onu kanala admin kimi əlavə edin."
    if cid == await audio_cache.depo_chat_id(bot):
        return "⚠️ Bu artıq əsas depodur."
    r = depo_shards.add(found.ref, thread)
    main_ok = await helpers.verify_main(cid)
    ok, total = await helpers.verify_shard(cid)
    prot = ""
    with contextlib.suppress(Exception):
        if found.chat.has_protected_content:
            prot = "\n⚠️ Kanalda <b>Restrict saving content</b> açıqdır — forward olmayacaq, söndürün."
    return ({"ok": f"✅ Kanal əlavə olundu: <b>{escape(found.title)}</b>",
             "dup": "⚠️ Bu kanal artıq siyahıdadır"}[r]
            + ("" if main_ok else "\n⚠️ Əsas bot bu kanalda yoxlanmadı — 🩺 Yoxla ilə təkrarlayın.")
            + f"\n🤖 {ok}/{total} köməkçi admindir"
            + ("" if ok else "\nKöməkçilər admin olmayana qədər yük bu kanala getmir: kanalın səhifəsindəki "
                             "🤖 düyməsi və ya <code>/depo shard helpers " + escape(str(found.ref)) + "</code>") + prot)


def make_shard_cmd(bot, helpers):
    async def shard_cmd(message: types.Message, args: str):
        parts = args.split()[1:]                                   # "shard" sözündən sonra
        sub = parts[0].lower() if parts else ""
        ref = parts[1] if len(parts) > 1 else ""
        note = ""
        if sub == "add" and ref:
            thread = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
            note = await add_shard(bot, helpers, ref, thread)
        elif sub in ("rm", "del") and ref:
            note = "🗑 Kanal siyahıdan silindi" if depo_shards.remove(ref) else "⚠️ Belə kanal yoxdur"
        elif sub == "check":
            await helpers.verify_all_shards()
            note = "🩺 Köməkçilərin admin vəziyyəti yoxlandı"
        elif sub == "clear":
            helpers.clear_channel_cool()
            note = "✅ Flood dayanmaları ləğv olundu"
        elif sub == "policy":
            if ref in ("auto", "manual"):
                depo_shards.set_policy(ref)
            note = f"🎚 Siyasət: {'✋ manual' if depo_shards.policy() == 'manual' else '🤖 avto'}"
        elif sub == "weight" and ref and len(parts) > 2 and parts[2].isdigit():
            key = depo_shards.MAIN_KEY if ref.lower() in ("main", "əsas", "esas") else ref
            if key != depo_shards.MAIN_KEY and not any(e["ref"].lower() == ref.lower() for e in depo_shards.extras()):
                note = "⚠️ Belə kanal yoxdur"
            else:
                note = f"⚖️ {escape(ref)}: pay {depo_shards.set_weight(key, int(parts[2]))}"
        elif sub == "helpers" and ref:
            limit = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0

            async def run():
                res = await helpers.shard_add_helpers(ref, limit)
                errs = "\n".join(f"• {escape(e)}" for e in res["errors"][:5])
                with contextlib.suppress(Exception):
                    await message.answer(
                        f"🤖 <b>Köməkçilər {escape(ref)} kanalına əlavə olundu</b>\n"
                        f"✅ yeni: {res['added']} · əvvəldən: {res['already']} · ❌ alınmadı: {res['failed']}"
                        + (f"\n{errs}" if errs else ""), parse_mode="HTML")
            spawn(run())
            note = "⏳ Userbot köməkçiləri əlavə edir (1.5 san. fasilə ilə) — bitəndə yazacağam."
        elif sub:
            note = "❓ Naməlum əmr."
        await message.answer(shard_text(note), parse_mode="HTML")

    return shard_cmd
