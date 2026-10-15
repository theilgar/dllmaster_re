"""
🛡 Vahid İcazələr — common/permissions.py

Həm 🤖 Bot (aiogram), həm də 📱 Userbot (Telethon) üçün:
  - is_creator(user_id)                      — bot sahibi (CREATOR_ID)
  - is_premium(user_id)                      — aktiv premium (sahib həmişə premium)
  - is_admin / is_group_admin(bc, chat, uid) — qrupda admin və ya sahib (sahib həmişə True)
  - is_group_owner(bc, chat, uid)            — qrupun yaradıcısı (bot sahibi həmişə True)

Bütün qrup yoxlamaları bir köməkçidən keçir və 60 san. keşlənir: bir panel açılışında eyni
istifadəçi üçün bir neçə get_chat_member sorğusu getmir.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional, Union

from common.database import is_creator as db_is_creator, is_premium as db_is_premium

logger = logging.getLogger(__name__)

_CACHE_TTL = 60.0
_cache: dict = {}            # (id(bc), chat_id, user_id) -> (vaxt, status, hüquqlar dict)

ADMIN_RIGHTS = [
    ("change_info", "Qrup məlumatını dəyişmək"),
    ("post_messages", "Kanalda paylaşım"),
    ("edit_messages", "Başqasının mesajını redaktə"),
    ("delete_messages", "Mesaj silmək"),
    ("ban_users", "Ban / məhdudlaşdırmaq"),
    ("invite_users", "Dəvət etmək / link"),
    ("pin_messages", "Mesaj sabitləmək"),
    ("manage_topics", "Mövzuları idarə"),
    ("manage_call", "Səsli söhbəti idarə"),
    ("post_stories", "Hekayə paylaşmaq"),
    ("edit_stories", "Hekayə redaktə"),
    ("delete_stories", "Hekayə silmək"),
    ("add_admins", "Admin təyin etmək"),
    ("anonymous", "Anonim qalmaq"),
]

BANNED_RIGHTS = [
    ("send_messages", "Mesaj"), ("send_photos", "Şəkil"), ("send_videos", "Video"),
    ("send_roundvideos", "Dairəvi video"), ("send_audios", "Musiqi"), ("send_voices", "Səs"),
    ("send_docs", "Fayl"), ("send_stickers", "Stiker"), ("send_gifs", "GIF"), ("send_inline", "Inline"),
    ("send_polls", "Sorğu"), ("embed_links", "Link önizləmə"), ("invite_users", "Dəvət"),
    ("pin_messages", "Sabitləmə"), ("change_info", "Məlumat dəyişmə"),
]


def _int(v) -> Optional[int]:
    try:
        return int(str(v).strip())
    except (ValueError, TypeError):
        return None


def is_creator(user_id: Union[int, str], ctx: Any = None) -> bool:
    uid = _int(user_id) if user_id else None
    if not uid:
        return False
    if ctx is None:
        try:
            from common.menu import _GLOBAL_CONTEXT
            ctx = _GLOBAL_CONTEXT
        except Exception:
            ctx = None
    if ctx is not None:
        if hasattr(ctx, "is_creator"):
            try:
                return bool(ctx.is_creator(uid))
            except Exception as _e:
                logger.debug("udulan xəta [common/permissions.py:73]: %r", _e)
        cid = getattr(ctx, "creator_id", None)
        if cid:
            try:
                return int(cid) == uid
            except (ValueError, TypeError):
                pass
    return db_is_creator(uid)


def is_premium(user_id: Union[int, str]) -> bool:
    uid = _int(user_id) if user_id else None
    if not uid:
        return False
    return is_creator(uid) or db_is_premium(uid)


async def _member(bc: Any, chat_id, user_id) -> tuple:
    """(status, hüquqlar) — status: 'creator' | 'administrator' | 'member' | ''."""
    cid, uid = _int(chat_id), _int(user_id)
    if cid is None or uid is None:
        return "", {}
    key = (id(bc), cid, uid)
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1], hit[2]
    status, rights = "", {}
    try:
        if hasattr(bc, "get_permissions"):                       # Telethon
            p = await bc.get_permissions(cid, uid)
            status = "creator" if p.is_creator else ("administrator" if p.is_admin else "member")
            rights = {"can_restrict_members": bool(getattr(p, "ban_users", False)),
                      "can_delete_messages": bool(getattr(p, "delete_messages", False))}
        elif hasattr(bc, "get_chat_member"):                     # aiogram
            m = await bc.get_chat_member(cid, uid)
            status = str(getattr(m.status, "value", m.status)).lower()
            rights = {k: bool(getattr(m, k, False)) for k in ("can_restrict_members", "can_delete_messages")}
    except Exception as e:
        logger.debug(f"icazə yoxlanmadı ({cid}/{uid}): {e}")
        return "", {}
    if len(_cache) > 5000:
        _cache.clear()
    _cache[key] = (now, status, rights)
    return status, rights


async def is_admin(bc: Any, chat_id, user_id) -> bool:
    if is_creator(user_id):
        return True
    status, _ = await _member(bc, chat_id, user_id)
    return status in ("administrator", "creator")


is_group_admin = is_admin


async def is_group_owner(bc: Any, chat_id, user_id) -> bool:
    if is_creator(user_id):
        return True
    status, _ = await _member(bc, chat_id, user_id)
    return status == "creator"


