"""📨 join_requests.py — qrupa gələn qoşulma istəkləri (join requests): baxmaq, rədd etmək, qəbul etmək.

Komandalar (qrupda, admin hesabı ilə yazılır):
  • .istek                  — gözləyən istəklərin sayı və ilk 10 nəfər
  • .istek sil [say]        — istəkləri RƏDD edir (say yoxdursa hamısı)
  • .istek qebul [say]      — istəkləri QƏBUL edir (say yoxdursa hamısı)
    sil: red · rədd · decline · reject    qebul: qəbul · ok · approve · accept

Lazım olan admin hüququ: «Üzv əlavə et» (invite_users). Yalnız superqrup / kanalda işləyir (adi qrupda istək olmur).
Say verilməyəndə Telegram-ın «hamısını» sorğusu işləyir (bir cavabla hamısı); say verilsə istəklər ən köhnədən başlayaraq
tək-tək həll olunur, proqres göstərilir. Dayandırmaq: .stop
"""
import asyncio
import contextlib
import time
from datetime import datetime
from html import escape

from telethon.errors import ChatAdminRequiredError, FloodWaitError, RPCError
from telethon.tl.functions.messages import (
    GetChatInviteImportersRequest,
    HideAllChatJoinRequestsRequest,
    HideChatJoinRequestRequest,
)
from telethon.tl.types import Channel, InputUserEmpty

from userbot.core.userbot_api import box_text, logger, plain_name, safe_edit

LIST_LIMIT = 10          # .istek: siyahıda göstərilən nəfər
BATCH = 100              # Telegram bir sorğuda ən çox bu qədər istək qaytarır
PAUSE = 0.3              # tək-tək həll edəndə sorğular arası fasilə (flood-dan qorunmaq üçün)

DECLINE = {"sil", "red", "rədd", "redd", "decline", "reject", "no"}
APPROVE = {"qebul", "qəbul", "ok", "approve", "accept", "yes"}


def parse_action(arg: str):
    """'sil' → False (rədd), 'qebul' → True (qəbul), boş / siyahı sözü → None, naməlum → ... (Ellipsis)."""
    a = (arg or "").strip().lower()
    if a in DECLINE:
        return False
    if a in APPROVE:
        return True
    if a in ("", "list", "siyahi", "siyahı", "bax"):
        return None
    return ...


async def check_rights(client, chat):
    """İcazə yoxlaması; uyğundursa None, yoxsa istifadəçiyə mətn."""
    if not isinstance(chat, Channel):
        return "ℹ️ Qoşulma istəkləri yalnız superqrup / kanalda olur — adi qrupda və ya şəxsi çatda işləmir."
    perms = await client.get_permissions(chat, "me")
    if not (perms.is_creator or getattr(perms, "invite_users", False)):
        return "⛔ Bu çatda <b>üzv əlavə etmə</b> admin icazən yoxdur."
    return None


async def iter_requests(client, chat, limit: int):
    """Gözləyən istəkləri (ən köhnədən) səhifə-səhifə verir: (User | None, ChatInviteImporter). Cəmi ən çox `limit` ədəd."""
    offset_date, offset_user, got = None, InputUserEmpty(), 0
    while got < limit:
        size = min(BATCH, limit - got)
        res = await client(GetChatInviteImportersRequest(
            peer=chat, offset_date=offset_date, offset_user=offset_user, limit=size, requested=True))
        if not res.importers:
            return
        users = {u.id: u for u in res.users}
        for imp in res.importers:
            yield users.get(imp.user_id), imp
            got += 1
        last = res.importers[-1]
        offset_date, offset_user = last.date, users.get(last.user_id) or InputUserEmpty()
        if len(res.importers) < size:
            return


async def pending_count(client, chat) -> int:
    res = await client(GetChatInviteImportersRequest(
        peer=chat, offset_date=None, offset_user=InputUserEmpty(), limit=1, requested=True))
    return int(getattr(res, "count", 0) or 0)


async def list_requests(client, chat, limit: int = LIST_LIMIT) -> tuple[int, list]:
    """→ (cəmi gözləyən, [(User | None, importer), ...] ilk `limit` ədəd)."""
    total = await pending_count(client, chat)
    if not total:
        return 0, []
    return total, [pair async for pair in iter_requests(client, chat, limit)]


def render_list(title: str, total: int, items: list) -> str:
    if not total:
        return f"✅ <b>{escape(title)}</b>: gözləyən istək yoxdur."
    lines = [f"📨 <b>{escape(title)}</b> — gözləyən istək: <b>{total}</b>", ""]
    for i, (user, imp) in enumerate(items, 1):
        name = escape(plain_name(user)) if user is not None else str(imp.user_id)      # blokda link işləmir → linksiz
        uname = f" @{user.username}" if user is not None and getattr(user, "username", None) else ""
        when = datetime.fromtimestamp(imp.date.timestamp()).strftime("%d.%m %H:%M") if imp.date else ""
        about = f" — <i>{escape(imp.about[:40])}</i>" if getattr(imp, "about", None) else ""
        lines.append(f"{i}. {name}{uname} · {when}{about}")
    if total > len(items):
        lines.append(f"<i>... və daha {total - len(items)} nəfər</i>")
    lines += ["", "<i>.istek sil — hamısını rədd et · .istek qebul — hamısını qəbul et\n"
                  ".istek sil 20 / .istek qebul 20 — ən köhnə 20-ni</i>"]
    return "\n".join(lines)


async def _flood_safe(call, report=None):
    """Sorğunu FloodWait-ə görə gözləyib təkrarlayır."""
    while True:
        try:
            return await call()
        except FloodWaitError as e:
            if report:
                await report(f"⏳ Telegram limiti — {e.seconds} san. gözlənilir...", False)
            await asyncio.sleep(e.seconds + 1)


async def resolve_all(client, chat, approved: bool, report=None) -> None:
    """Hamısını bir sorğu ilə həll edir (Telegram tərəfində)."""
    await _flood_safe(lambda: client(HideAllChatJoinRequestsRequest(chat, approved=approved)), report)


async def resolve_some(client, chat, approved: bool, n: int, report, is_stopped=lambda: False) -> tuple[int, int]:
    """Ən köhnə n istəyi tək-tək həll edir → (uğurlu, alınmayan). report(mətn, final) — proqres."""
    done = failed = 0
    last = 0.0
    async for user, imp in iter_requests(client, chat, n):
        if is_stopped():
            raise asyncio.CancelledError()
        target = user if user is not None else imp.user_id
        try:
            await _flood_safe(lambda: client(HideChatJoinRequestRequest(chat, target, approved=approved)), report)
            done += 1
        except ChatAdminRequiredError:
            raise
        except RPCError as e:                      # məs. HIDE_REQUESTER_MISSING: istək artıq geri çəkilib
            failed += 1
            logger.debug(f"istək {imp.user_id}: {e}")
        if time.monotonic() - last > 3:
            last = time.monotonic()
            await report(f"⏳ {done + failed}/{n} · ✅ {done} · ❌ {failed}", False)
        await asyncio.sleep(PAUSE)
    return done, failed


def verb(approved: bool) -> str:
    return "qəbul edildi" if approved else "rədd edildi"


def register(ub):
    @ub.command(
        "istek",
        pattern=r"^\.ist[eə]k(?:\s+(\S+))?(?:\s+(\d+))?$",
        help=(
            "qrupa gələn qoşulma istəkləri",
            "Qrupun gözləyən qoşulma istəklərinə baxır, hamısını (və ya ilk N-ni) rədd və ya qəbul edir.\n\n"
            "<b>İstifadə (qrupda, admin kimi):</b>\n"
            "• <code>.istek</code> — say və ilk 10 nəfər\n"
            "• <code>.istek sil</code> — hamısını rədd et\n"
            "• <code>.istek qebul</code> — hamısını qəbul et\n"
            "• <code>.istek sil 20</code> / <code>.istek qebul 20</code> — ən köhnə 20-ni\n\n"
            "Hüquq: «Üzv əlavə et». Dayandırmaq: <code>.stop</code>",
        ),
    )
    async def on_requests(event):
        client = event.client
        chat = await event.get_chat()
        err = await check_rights(client, chat)
        if err:
            await safe_edit(event, err)
            return
        title = getattr(chat, "title", "") or "qrup"
        arg = (event.pattern_match.group(1) or "").strip()
        limit = int(event.pattern_match.group(2)) if event.pattern_match.group(2) else None
        if arg.isdigit():                                  # .istek 25 — siyahıda 25 nəfər
            arg, limit = "", int(arg)
        action = parse_action(arg)

        if action is ...:
            await safe_edit(event, "ℹ️ İstifadə: <code>.istek</code> · <code>.istek sil [say]</code> · "
                                   "<code>.istek qebul [say]</code>")
            return
        if action is None:                                 # siyahı
            try:
                total, items = await list_requests(client, chat, limit or LIST_LIMIT)
            except Exception as e:
                logger.info(f".istek xətası: {e}")
                await safe_edit(event, f"❌ <b>.istek:</b> <code>{escape(str(e))[:200]}</code>")
                return
            await safe_edit(event, box_text(render_list(title, total, items)))     # 📋 /ui ayarına görə kod bloku
            return

        async def report(text, final):
            await safe_edit(event, text + ("" if final else "\n<i>Dayandırmaq: .stop</i>"))

        async def runner():
            try:
                before = await pending_count(client, chat)
                if not before:
                    await report(f"✅ <b>{escape(title)}</b>: gözləyən istək yoxdur.", True)
                    return
                await report(f"⏳ <b>{escape(title)}</b>: {min(before, limit) if limit else before} istək "
                             f"{'qəbul edilir' if action else 'rədd edilir'}...", False)
                if limit:
                    done, failed = await resolve_some(client, chat, action, limit, report, lambda: False)
                else:
                    await resolve_all(client, chat, action, report)
                    done, failed = before, 0
                left = await pending_count(client, chat)
                done = done if limit else max(before - left, 0)
                await safe_edit(event, box_text(
                    f"✅ <b>{escape(title)}</b>\n📨 {done} istək {verb(action)}"
                    + (f" · ❌ alınmadı: {failed}" if failed else "")
                    + f"\n<i>Qalan gözləyən: {left}</i>"))
            except asyncio.CancelledError:
                await safe_edit(event, "⏹ <b>.istek</b> dayandırıldı.")
                raise
            except ChatAdminRequiredError:
                await safe_edit(event, "⛔ Bu çatda <b>üzv əlavə etmə</b> admin icazən yoxdur.")
            except Exception as e:
                logger.info(f".istek xətası: {e}")
                with contextlib.suppress(Exception):
                    await safe_edit(event, f"❌ <b>.istek:</b> <code>{escape(str(e))[:200]}</code>")

        ub.track(event.chat_id, "istek", asyncio.create_task(runner()))
