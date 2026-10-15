"""
📱 bot/depo/scanner.py - Userbot (Telethon) Telegram kanal və qrup skaneri.
"""
from __future__ import annotations

import asyncio
import logging

from bot.depo.cleaner import tl_message_item
from common.database import get_db
from userbot.core import tg_session

try:
    from telethon import utils as tl_utils
    from telethon.errors import InviteHashExpiredError, InviteHashInvalidError, UserAlreadyParticipantError
    from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest
    from telethon.tl.types import ChatInviteAlready, ChatInvitePeek, InputMessagesFilterMusic
    try:
        from telethon.errors import InviteRequestSentError
    except ImportError:
        InviteRequestSentError = None
    HAS_TELETHON = True
except ImportError:
    HAS_TELETHON = False
    tl_utils = None
    InviteHashExpiredError = None
    InviteHashInvalidError = None
    UserAlreadyParticipantError = None
    InviteRequestSentError = None
    CheckChatInviteRequest = None
    ImportChatInviteRequest = None
    ChatInviteAlready = None
    ChatInvitePeek = None
    InputMessagesFilterMusic = None

logger = logging.getLogger(__name__)
TG_CHECKPOINT = 500


class TgUser:
    """Userbot əməliyyatları (Telethon)."""

    def __init__(self, context):
        self.ctx = context
        self._own = None
        self.task = None
        self.me = None
        if not HAS_TELETHON:
            logger.warning("⚠️ telethon quraşdırılmayıb — Telegram mənbələri yalnız veb (public) rejimdə işləyəcək")
            return

        ub1_on = getattr(context, "userbot1_enabled", None)
        if ub1_on is None:
            try:
                ub1_on = get_db().get_setting("userbot1:enabled", "1") == "1"
                context.userbot1_enabled = ub1_on
            except Exception:
                ub1_on = True

        if not getattr(context, "userbot_enabled", True) or not ub1_on:
            return

        if getattr(context, "telethon_client", None) is None:
            self._own = tg_session.make_client(context)

    @property
    def client(self):
        return getattr(self.ctx, "telethon_client", None)

    @property
    def own_client(self) -> bool:
        return self._own is not None and self._own is self.client

    @property
    def available(self) -> bool:
        return self.client is not None

    def start(self):
        if not self.own_client:
            return
        try:
            self.task = asyncio.create_task(self._connect())
        except RuntimeError:
            self.task = None
        self.ctx.telethon_task = self.task

    async def _connect(self):
        self.me = await tg_session.connect(self.ctx)

    async def ok(self) -> bool:
        if not getattr(self.ctx, "userbot1_enabled", True):
            return False
        c = self.client
        if not c:
            return False
        try:
            if not c.is_connected():
                task = getattr(self.ctx, "telethon_task", None)
                if not task or task.done():
                    if getattr(self.ctx, "userbot1_enabled", True):
                        self.me = await tg_session.connect(self.ctx) or self.me
                if not c.is_connected():
                    return False
            if self.me is None:
                self.me = getattr(self.ctx, "telethon_me", None)
            return await c.is_user_authorized()
        except Exception:
            return False

    async def entity(self, target: dict):
        c = self.client
        if target["type"] == "user":
            return await c.get_entity(target["name"])
        if target["type"] == "id":
            try:
                return await c.get_entity(target["id"])
            except (ValueError, TypeError):
                await c.get_dialogs()
                return await c.get_entity(target["id"])
        if target["type"] == "invite":
            h = target["hash"]
            try:
                info = await c(CheckChatInviteRequest(h))
            except (InviteHashExpiredError, InviteHashInvalidError):
                raise RuntimeError("Dəvət linki etibarsızdır və ya vaxtı keçib")
            if isinstance(info, (ChatInviteAlready, ChatInvitePeek)):
                return info.chat
            try:
                upd = await c(ImportChatInviteRequest(h))
            except UserAlreadyParticipantError:
                info = await c(CheckChatInviteRequest(h))
                return info.chat
            except Exception as e:
                if InviteRequestSentError and isinstance(e, InviteRequestSentError):
                    raise RuntimeError("Qoşulma sorğusu göndərildi — admin təsdiqləyəndən sonra yenidən əlavə et")
                raise
            logger.info("📱 Userbot dəvət linki ilə çata qoşuldu")
            return upd.chats[0]
        raise RuntimeError("Naməlum hədəf")

    async def entity_for_source(self, src: dict):
        if src.get("username"):
            try:
                return await self.client.get_entity(src["username"])
            except Exception as _e:
                logger.debug("entity_for_source xətası: %r", _e)
        return await self.entity({"type": "id", "id": int(src["id"])})

    @staticmethod
    def describe(ent):
        title = getattr(ent, "title", None) or " ".join(
            x for x in (getattr(ent, "first_name", None), getattr(ent, "last_name", None)) if x) or "Çat"
        return tl_utils.get_peer_id(ent) if tl_utils else 0, title, getattr(ent, "username", None)

    async def scan(self, ent, limit, on_item, should_stop, progress=None, state=None, checkpoint=None) -> dict:
        _, title, _ = self.describe(ent)
        st = {"messages": 0, "items": 0, "added": 0, "unreadable": 0, "new": 0, "resumed": False}

        def handle(msg):
            st["messages"] += 1
            it = tl_message_item(msg, title)
            if not it:
                st["unreadable"] += 1
            else:
                st["items"] += 1
                if on_item(it):
                    st["added"] += 1

        if state is None:
            async for msg in self.client.iter_messages(ent, limit=limit, filter=InputMessagesFilterMusic):
                if should_stop():
                    break
                handle(msg)
                if progress and st["messages"] % 100 == 0:
                    await progress(st)
            return st

        top = int(state.get("top") or 0)
        newest = top
        stopped = False
        if top:
            async for msg in self.client.iter_messages(ent, min_id=top, filter=InputMessagesFilterMusic):
                if should_stop():
                    stopped = True
                    break
                newest = max(newest, msg.id)
                handle(msg)
                st["new"] += 1
                if progress and st["messages"] % 100 == 0:
                    await progress(st)
        if not stopped:
            state["top"] = newest

        if not stopped and not state.get("complete"):
            count = int(state.get("count") or 0)
            remaining = (limit - count) if limit else None
            st["resumed"] = bool(state.get("bottom"))
            if remaining is None or remaining > 0:
                async for msg in self.client.iter_messages(ent, offset_id=int(state.get("bottom") or 0),
                                                           limit=remaining, filter=InputMessagesFilterMusic):
                    if should_stop():
                        stopped = True
                        break
                    handle(msg)
                    if not state.get("top") or msg.id > state["top"]:
                        state["top"] = msg.id
                    state["bottom"] = msg.id
                    state["count"] = int(state.get("count") or 0) + 1
                    if checkpoint and state["count"] % TG_CHECKPOINT == 0:
                        await checkpoint(dict(state))
                    if progress and st["messages"] % 100 == 0:
                        await progress(st)
            if not stopped:
                state["complete"] = True
        if checkpoint:
            await checkpoint(dict(state))
        return st
