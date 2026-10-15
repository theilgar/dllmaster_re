"""
🪟 Admin panellərinin inline rejimdə işləməsi — bot/core/inline_panels.py

Userbot `.bmenu` ilə botun /menu-sunu istənilən çata inline kart kimi göndərir. Belə mesajın düyməsi
basılanda Telegram callback-də `message` vermir, yalnız `inline_message_id` verir — halbuki menyu və digər
admin panelləri `cb.message.edit_text(...)`, `cb.message.delete()`, `bot.edit_message_text(chat_id, message_id)`
yazır. Bu modul onları dəyişmədən işlədir:

  1) callback outer-middleware — admin panel prefiksli, creator-un, mesajsız callback-ə saxta Message qoyur:
       chat.id = creator-un bot çatı, message_id = mənfi "psevdo" ID (inline_message_id-yə bağlı)
     → message.edit_text / edit_reply_markup / delete / answer ... hamısı işləyir
  2) bot sessiyasına request-middleware — psevdo ID ilə gələn Edit* sorğuları inline_message_id-li sorğuya
     çevrilir; DeleteMessage isə "✖️ Bağlandı" redaktəsinə (inline mesajı bot silə bilmir)
     → answer / answer_document kimi YENİ mesajlar creator-un bot çatına gedir (CSV, yedək, QR ...)

Musiqi və istifadəçi callback-lərinə (inline axtarış, ⏹ və s.) toxunulmur — yalnız ADMIN_PREFIXES.
"""
import itertools
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ADMIN_PREFIXES = ("menu:", "df:", "dt:", "fx:", "htop:", "lg:", "pm:", "bk:", "ubacc:", "va:", "gh:", "ghf:",
                  "ghs:", "ghu:", "hgs:", "ae:", "botrestart:", "tgl:", "scan:", "dc:", "hc:", "is:", "tr:", "ds:", "ar:", "dp:", "px:", "ln:", "pla:", "tf:", "hg:", "dh:", "kb_btn:", "ui:",
                  "test_mp3:", "mp3_u:", "mp3_g:", "codec:", "gmenu:", "grp:", "umenu:", "vrc:", "ubm:", "ubc:", "meta:", "shz:", "tip:", "tip_prompt:", "pm_grp:", "vj:", "vp:", "mp3_lk:", "vq:", "vr:")
MAX_MAP = 500

# Yalnız bot sahibinin panelləri — yoxlama bir yerdədir (əvvəl hər handler-in əvvəlində eyni 3 sətir idi).
# Qrup adminlərinin / istifadəçilərin düymələri (grp:, gmenu:, umenu:, mp3_u:, vrc: ...) bura DAXİL DEYİL.
CREATOR_ONLY = ("menu:", "df:", "dt:", "htop:", "lg:", "pm:", "bk:", "va:", "hc:", "is:", "tr:", "ds:", "ar:",
                "dp:", "px:", "ln:", "pla:", "hg:", "hgs:", "dh:", "kb_btn:", "ui:", "test_mp3:", "dc:", "tf:",
                "scan:stop", "botrestart:")

_PSEUDO = {}                    # psevdo message_id → inline_message_id
_BY_IID = {}                    # inline_message_id → psevdo message_id
_COUNTER = itertools.count(1)


def pseudo_id(iid: str) -> int:
    mid = _BY_IID.get(iid)
    if mid is None:
        mid = -(1_000_000_000 + next(_COUNTER))        # mənfi — real mesaj ID-ləri ilə toqquşmasın
        if len(_PSEUDO) > MAX_MAP:                     # köhnələri at
            for old in list(_PSEUDO)[:MAX_MAP // 2]:
                _BY_IID.pop(_PSEUDO.pop(old), None)
        _PSEUDO[mid] = iid
        _BY_IID[iid] = mid
    return mid


def is_pseudo(message_id) -> bool:
    return isinstance(message_id, int) and message_id in _PSEUDO


def install_creator_guard(context):
    """CREATOR_ONLY prefiksli düymələr yalnız bot sahibinə işləyir (inline panellərdən asılı deyil)."""
    from aiogram import BaseMiddleware

    class CreatorOnly(BaseMiddleware):
        async def __call__(self, handler, event, data):
            if (event.data or "").startswith(CREATOR_ONLY) and not context.is_creator(event.from_user.id):
                try:
                    await event.answer("⛔ Yalnız bot sahibi üçün", show_alert=True)
                except Exception as _e:
                    logger.debug("udulan xəta [bot/core/inline_panels.py:65]: %r", _e)
                return None
            return await handler(event, data)

    context.dp.callback_query.outer_middleware(CreatorOnly())


def install(context):
    from aiogram import BaseMiddleware
    from aiogram.client.session.middlewares.base import BaseRequestMiddleware
    from aiogram.methods import (DeleteMessage, EditMessageCaption, EditMessageMedia, EditMessageReplyMarkup,
                                 EditMessageText)
    from aiogram.types import Chat, Message

    bot, dp = context.bot, context.dp
    EDITS = (EditMessageText, EditMessageReplyMarkup, EditMessageMedia, EditMessageCaption)

    class InlineCallback(BaseMiddleware):
        async def __call__(self, handler, event, data):
            iid = getattr(event, "inline_message_id", None)
            if (iid and getattr(event, "message", None) is None and context.creator_id
                    and event.from_user and event.from_user.id == context.creator_id
                    and (event.data or "").startswith(ADMIN_PREFIXES)):
                msg = Message(message_id=pseudo_id(iid), date=datetime.now(timezone.utc),
                              chat=Chat(id=context.creator_id, type="private")).as_(bot)
                event = event.model_copy(update={"message": msg})
            return await handler(event, data)

    class PseudoRequests(BaseRequestMiddleware):
        async def __call__(self, make_request, b, method):
            mid = getattr(method, "message_id", None)
            if is_pseudo(mid):
                iid = _PSEUDO[mid]
                if isinstance(method, EDITS):
                    method = method.model_copy(update={"inline_message_id": iid, "chat_id": None, "message_id": None})
                elif isinstance(method, DeleteMessage):
                    await make_request(b, EditMessageText(inline_message_id=iid, text="✖️ <i>Bağlandı</i>",
                                                          parse_mode="HTML"))
                    return True
            return await make_request(b, method)

    dp.callback_query.outer_middleware(InlineCallback())
    bot.session.middleware(PseudoRequests())
    logger.info("🪟 Admin panelləri inline rejimdə də işləyir (.bmenu)")
