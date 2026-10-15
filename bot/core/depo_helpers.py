"""
🤖 Köməkçi botlar — depo kanalına yükləməni bir neçə bota paylayır.

Telegram-ın qrup / kanal limiti (~20 mesaj/dəq.) hər bot üçün ayrıdır: N köməkçi ≈ N dəfə çox yükləmə (say limiti yoxdur).

Necə işləyir (music_plugin-ə toxunmadan):
  1. Əsas botun aiogram sessiyasına request middleware qoşulur.
  2. Depoya gedən SendAudio sorğusu tutulur və növbəti boş köməkçi bota ötürülür
     (hər köməkçi öz tempində — dəqiqədə N mahnı, flood alsa digərinə keçilir).
  3. file_id hər bot üçün fərqlidir — köməkçinin file_id-si ilə əsas bot mahnı göndərə bilməz.
     Çevirmə: əsas bot depo postunu sink çatına (default: creator-in şəxsi çatı) forward edib
     ÖZ file_id-sini alır, forward dərhal silinir. İki rejim:
       🐢 lazy (default) — keşə köməkçinin file_id-si yazılır + "köməkçi file_id → depo postu" xəritəsi.
          Əsas bot həmin file_id ilə nəsə göndərəndə (answer_audio, inline nəticə, edit_message_media)
          middleware sorğunu tutur, BİR DƏFƏ çevirir, keşi yeniləyir. Heç kim istəməyən mahnı üçün
          forward heç olmur → tutum = köməkçilərin tam limiti. Fon çevirici boş vaxtda ən çox
          istənilənləri əvvəlcədən çevirir. İnline-da çevrilə bilməyən nəticə (2.5 san.) o dəfəlik
          göstərilmir, fonda çevrilir.
       ⚡ eager — yükləmə anında çevrilir (tutum sink limitinə bağlıdır, ~60/dəq. hər sink çatı)
  4. music_plugin-ə depo postunun özü (chat_id / message_id) qaytarılır — keş və depo indeksi işləyir.
  Nəsə alınmasa (köməkçi yoxdur / forward olmadı) — əsas bot özü yükləyir, heç nə itmir.

Rejim (/depo → 🤖 Köməkçi botlar):
  filler — yalnız 📦 doldurucunun mahnıları (default) · all — bütün depo yükləmələri · off — söndürülüb

config.env:
  DEPO_HELPER_TOKENS=123:AAA...,456:BBB...   # və ya /depo helper add <token>
  DEPO_SINK_CHATS=<creator_id>,<başqa_id>     # file_id çevirmə çatları (botu /start etmiş şəxslər)
  DEPO_SINK_INTERVAL=1.0                      # bir sink çatına forward-lar arası (san.)

👋 Köməkçi botlara kimsə yazsa (/start və s.) — "bu köməkçi botdur" + əsas botun linki göndərilir.
  Bunun üçün köməkçilər ayrıca, yüngül polling ilə dinlənir (yalnız şəxsi mesajlar).

👥 QRUPLARDA göndərmə (flood-a qarşı): qrupda Telegram hər bota ~20 mesaj/dəq. verir. Qrupa köməkçi
  botlar əlavə edilib seçiləndə (/menu → 👥 → 💬 Qruplar → qrup → 🤖 Köməkçilər) əsas botun o qrupa
  SendAudio (depodakı mahnı, file_id) sorğuları əsas bot + köməkçilər arasında növbə ilə paylanır:
  köməkçi mahnını depo postundan copy_message ilə göndərir (öz file_id-si lazım deyil). N köməkçi ≈ (N+1)×.
  Köməkçinin mesajında yalnız link düymələri qalır (callback düymələri əsas bota aiddir).
  🐢 Bot qrupda mahnı göndərərkən flood-a düşəndə (məs. playlist qrupu) — flood bitən kimi həmin qrupa
  "köməkçi yükləyici bot lazımdır?" sualı gəlir: ➕ köməkçini əlavə et / 🤖 userbot ilə / ✅ qrupdakıları
  işə sal / 🔕 bu qrupda soruşma (30 dəq.-də bir dəfədən çox yox; düymələr yalnız creator üçün).

🔔 Depoya əlavə olunmamış köməkçi (yeni token və ya hüququ alınmış) — creator-a avtomatik mesaj:
  ➕ Depoya əlavə et (Telegram linki — admin hüquqları ilə) · 🤖 Userbot ilə avtomatik · 🔄 Yoxla

Tələb: köməkçi botlar depo kanalında admin olmalıdır ("Post messages"),
depo kanalında "Restrict saving content" (protect content) söndürülmüş olmalıdır — yoxsa forward olmur.
"""
import asyncio
import contextlib
import contextvars
import json
import logging
import os
import re
import time

from aiogram import Bot
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.methods import SendAudio
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Message
from common.database import Settings
from common.utilities import spawn

logger = logging.getLogger(__name__)

# (filler, işçi nömrəsi) — doldurucunun öz yükləməsidir (rejim "filler" üçün) + monitorda faza
UPLOAD_CTX = contextvars.ContextVar("depo_upload_ctx", default=None)

SCOPES = {"off": "🔴 Söndürülüb", "filler": "📦 Yalnız doldurucu", "all": "🌐 Bütün depo yükləmələri"}   # sıra = düymənin dövrü
DEFAULT_SCOPE = "all"        # köməkçilər varsa İSTİFADƏÇİ yükləmələri də onlarla depoya gedir — əsas bot sərbəst
MAIN_FLOOD_ROUTE = True      # "filler" rejimində də: əsas bot depoda flood-dadırsa yükləmə köməkçilərə keçir
DEFAULT_RATE = 18                 # hər köməkçi — dəqiqədə neçə mahnı (Telegram limiti ~20)
RATE_STEPS = [6, 10, 12, 15, 18, 20, 25, 30]
S_TOKENS, S_SCOPE, S_RATE = "depo_helpers:tokens", "depo_helpers:scope", "depo_helpers:rate"
S_CONV = "depo_helpers:conv"
S_GROUPS = "depo_helpers:groups"          # {chat_id: [köməkçi bot id, ...]} — qruplarda göndərmə
GROUP_INTERVAL = 3.2                      # bir bot bir qrupa — mesajlar arası (≈ 18/dəq.)
NOTIFY_EVERY = 6 * 3600                   # eyni köməkçi üçün "depoya əlavə et" xatırlatması
FLOOD_ASK_EVERY = 30 * 60                 # qrup flood-a düşəndə "köməkçi lazımdır?" — ən çox bu qədərdən bir
S_FLOOD_MUTE = "depo_helpers:flood_mute"  # bu qruplarda soruşulmasın
S_EXT_ENABLED = "depo_helpers:ext_enabled"  # kənar qruplarda köməkçilər aktivdirmi ("1" / "0")
S_EXT_LIMIT = "depo_helpers:ext_limit"      # kənar qrup başına maksimum köməkçi sayı (default 2)
S_EXT_AUTO = "depo_helpers:ext_auto"        # kənar qrupa qoşulduqda avtomatik köməkçi əlavə ("1" / "0")
CONV_MODES = {"lazy": "🐢 Lazy — yalnız istənəndə", "eager": "⚡ Dərhal — yükləmə anında"}
INLINE_BUDGET = 2.5               # inline cavab üçün çevirməyə ayrılan vaxt (san.)
SEND_BUDGET = 30.0                # adi göndərmə üçün
# ⚡ eager: sink növbəsi bundan uzundursa (flood / çox işçi) mahnı gözləmədən lazy yolla qeyd olunur
SINK_MAX_WAIT = float(os.getenv("DEPO_SINK_MAX_WAIT") or 20.0)
WARM_INTERVAL = float(os.getenv("DEPO_WARM_INTERVAL") or 3.0)   # fon çevirici: hər N san. bir mahnı
# file_id daşıya bilən sorğular (music_plugin-in istifadə etdikləri + ehtiyat)
FID_METHODS = {"SendAudio", "SendDocument", "SendMediaGroup", "AnswerInlineQuery", "EditMessageMedia",
               "SendVoice", "AnswerWebAppQuery", "SavePreparedInlineMessage",
               "GetFile"}                  # fix_artist: depo indeksindəki yer tutucu file_id də çevrilsin
_TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")
_DEAD_ERRORS = ("chat not found", "not enough rights", "have no rights", "bot was kicked", "not a member",
                "need administrator rights", "chat_write_forbidden", "unauthorized")


def _db():
    from common.database import get_db
    return get_db()


class Helper:
    def __init__(self, token: str, from_env: bool):
        self.token = token
        self.from_env = from_env
        self.bot = None
        self.me = None
        self.ok = False
        self.status = "yoxlanılır..."
        self.next_at = 0.0                # tempə görə növbəti boş an (monotonic)
        self.cool_until = 0.0             # flood (RetryAfter) bitənə qədər
        self.sent = 0
        self.floods = 0
        self.fails = 0
        self.groups = {}                  # chat_id → (vəziyyət, yoxlama vaxtı): ok / out / mute / err
        self.g_next = {}                  # chat_id → növbəti boş an (qrup tempi)
        self.g_cool = {}                  # chat_id → flood bitənə qədər
        self.g_sent = 0

    @property
    def name(self) -> str:
        if self.me:
            return f"@{self.me.username}"
        return f"bot{self.token.split(':', 1)[0]}"


class HelperPool:
    def __init__(self, context):
        self.ctx = context
        self.helpers = []
        self.lock = asyncio.Lock()
        self.stats = {"helper": 0, "fallback": 0, "converted": 0, "conv_fail": 0}
        self._depo = {"id": None, "ts": 0.0}
        self._sinks = {}                  # chat_id → növbəti boş an
        self._sink_lock = asyncio.Lock()
        self.last_error = ""
        # 👋 köməkçilərə yazanlara cavab (ayrıca Dispatcher)
        self._poll_dp = None
        self._poll_task = None
        self._main_username = None
        self._replied = {}                # (bot_id, user_id) → son cavab vaxtı (spam qarşısı)
        self.redirects = 0
        # 🐢 lazy çevirmə
        self.foreign = {}                 # köməkçi file_id → (chat_id, message_id, unique_id)
        self.converted = {}               # köməkçi file_id → əsas botun file_id-si (yaddaşda, son çevrilənlər)
        self._conv_locks = {}
        self._dead = set()                # depo postu silinib — çevrilə bilmir
        self._warm_task = None
        self._warm_fails = {}             # fid → uğursuz cəhd sayı (fon çevirici ilişib qalmasın)
        self.cstats = {"lazy": 0, "warm": 0, "inline_skip": 0, "fail": 0}
        self._main_g = {}                 # chat_id → əsas botun qrupdakı növbəti boş anı
        self._g_lock = asyncio.Lock()
        self._notified = {}               # bot id → son "depoya əlavə et" mesajı
        self.gstats = {"helper": 0, "main": 0, "fallback": 0}
        self._init_table()

    # ── ayarlar ──
    _cfg = Settings(empty_is_default=True)
    _get = staticmethod(_cfg.get)
    _set = staticmethod(_cfg.put)

    @property
    def scope(self) -> str:
        v = self._get(S_SCOPE, DEFAULT_SCOPE)
        return v if v in SCOPES else DEFAULT_SCOPE

    def set_scope(self, v: str):
        if v in SCOPES:
            self._set(S_SCOPE, v)

    @property
    def rate(self) -> int:
        try:
            return max(1, min(60, int(self._get(S_RATE, DEFAULT_RATE))))
        except (TypeError, ValueError):
            return DEFAULT_RATE

    def step_rate(self, direction: int) -> int:
        cur = self.rate
        if direction > 0:
            nxt = next((x for x in RATE_STEPS if x > cur), RATE_STEPS[-1])
        else:
            nxt = next((x for x in reversed(RATE_STEPS) if x < cur), RATE_STEPS[0])
        self._set(S_RATE, str(nxt))
        return nxt

    def set_rate(self, val: int) -> int:
        v = max(1, min(60, int(val)))
        self._set(S_RATE, str(v))
        return v

    def step_rate_delta(self, delta: int) -> int:
        return self.set_rate(self.rate + delta)

    @property
    def conv_mode(self) -> str:
        v = self._get(S_CONV, "lazy")
        return v if v in CONV_MODES else "lazy"

    def toggle_conv(self) -> str:
        nxt = "eager" if self.conv_mode == "lazy" else "lazy"
        self._set(S_CONV, nxt)
        return nxt

    # ── 👥 kənar qruplar üçün köməkçi bot tənzimləmələri ──
    @property
    def ext_enabled(self) -> bool:
        """Kənar qruplar üçün köməkçi botlar aktivdirmi?"""
        return (self._get(S_EXT_ENABLED) or "1") == "1"

    def set_ext_enabled(self, val: bool):
        self._set(S_EXT_ENABLED, "1" if val else "0")

    def toggle_ext_enabled(self) -> bool:
        nxt = not self.ext_enabled
        self.set_ext_enabled(nxt)
        return nxt

    @property
    def ext_limit(self) -> int:
        """Kənar qrup başına maksimum neçə köməkçi bot qoşula bilər (1..len(helpers))."""
        try:
            val = int(self._get(S_EXT_LIMIT) or "2")
            return max(1, val)
        except (ValueError, TypeError):
            return 2

    def set_ext_limit(self, val: int) -> int:
        max_h = max(1, len([h for h in self.helpers if h.me and h.bot]))
        v = max(1, min(max_h, int(val)))
        self._set(S_EXT_LIMIT, str(v))
        return v

    def step_ext_limit(self, delta: int) -> int:
        return self.set_ext_limit(self.ext_limit + delta)

    @property
    def ext_auto(self) -> bool:
        """Kənar qrupa qoşulduqda avtomatik köməkçi əlavə edilsinmi?"""
        return (self._get(S_EXT_AUTO) or "1") == "1"

    def set_ext_auto(self, val: bool):
        self._set(S_EXT_AUTO, "1" if val else "0")

    def toggle_ext_auto(self) -> bool:
        nxt = not self.ext_auto
        self.set_ext_auto(nxt)
        return nxt

    async def is_creator_chat(self, chat_id: int) -> bool:
        """Çat bot sahibinin öz çatı / depodurmu, yoxsa kənar istifadəçinin qrupudur?"""
        creator_id = getattr(self.ctx, "creator_id", None)
        if not creator_id:
            return False
        if chat_id > 0:
            return chat_id == creator_id
        if await self.is_depo(chat_id, getattr(self.ctx, "bot", None)):
            return True
        try:
            bot = getattr(self.ctx, "bot", None)
            if bot:
                m = await bot.get_chat_member(chat_id, creator_id)
                if m and m.status in ("creator", "administrator"):
                    return True
        except Exception as _e:
            logger.debug("udulan xəta [bot/core/depo_helpers.py:270]: %r", _e)
        return False

    async def auto_add_to_group(self, chat_id: int):
        """Kənar qrupa əsas bot qoşulduqda avtomatik olaraq ext_limit sayda köməkçi əlavə edir."""
        if not self.ext_enabled or not self.ext_auto:
            return
        try:
            if await self.is_creator_chat(chat_id):
                return
            client = getattr(self.ctx, "telethon_client", None)
            if client is None:
                return
            helpers = [h for h in self.helpers if h.me and h.bot]
            sel = set(self.group_selected(chat_id))
            limit = self.ext_limit
            added_count = 0
            for h in helpers:
                if added_count >= limit:
                    break
                st = await self.member_state(h, chat_id, fresh=True)
                if st == "ok":
                    if h.me.id not in sel:
                        await asyncio.to_thread(self.toggle_group, chat_id, h.me.id)
                    added_count += 1
                elif st in ("out", "err"):
                    res = await self.userbot_add(h, chat_id, admin=False)
                    if res.startswith("✅"):
                        if h.me.id not in self.group_selected(chat_id):
                            await asyncio.to_thread(self.toggle_group, chat_id, h.me.id)
                        added_count += 1
                    await asyncio.sleep(1.5)
            logger.info(f"🤖 Kənar qrup {chat_id}: {added_count}/{limit} köməkçi avtomatik qoşuldu")
        except Exception as e:
            logger.debug(f"🤖 auto_add_to_group xətası ({chat_id}): {e}")

    def _db_tokens(self) -> list:
        try:
            data = json.loads(self._get(S_TOKENS) or "[]")
        except ValueError:
            data = []
        return [t for t in data if isinstance(t, str)]

    def tokens(self) -> list:
        """[(token, env-dəndir?)] — təkrarsız, sıra qorunur."""
        out, seen = [], set()
        main = getattr(self.ctx.bot, "token", None)
        env = [t.strip() for t in (os.getenv("DEPO_HELPER_TOKENS") or "").replace(";", ",").split(",")]
        for t, env_flag in [(t, True) for t in env] + [(t, False) for t in self._db_tokens()]:
            if t and t not in seen and t != main:
                seen.add(t)
                out.append((t, env_flag))
        return out

    def add_token(self, token: str) -> str:
        token = token.strip()
        if not _TOKEN_RE.match(token):
            return "format"
        if token == getattr(self.ctx.bot, "token", None):
            return "main"
        if any(t == token for t, _ in self.tokens()):
            return "dup"
        self._set(S_TOKENS, json.dumps(self._db_tokens() + [token]))
        return "ok"

    def remove(self, index: int) -> str:
        if not 0 <= index < len(self.helpers):
            return "range"
        h = self.helpers[index]
        if h.from_env:
            return "env"
        self._set(S_TOKENS, json.dumps([t for t in self._db_tokens() if t != h.token]))
        return "ok"

    # ── sink (file_id çevirmə) çatları ──
    def sinks(self) -> list:
        raw = os.getenv("DEPO_SINK_CHATS") or ""
        ids = [int(x) for x in re.findall(r"-?\d+", raw)]
        if not ids and getattr(self.ctx, "creator_id", 0):
            ids = [int(self.ctx.creator_id)]
        return ids

    @staticmethod
    def sink_interval() -> float:
        try:
            return max(0.2, float(os.getenv("DEPO_SINK_INTERVAL") or 1.0))
        except ValueError:
            return 1.0

    # ── vəziyyət ──
    def active(self) -> list:
        return [h for h in self.helpers if h.ok]

    def flooded(self) -> int:
        now = time.monotonic()
        return sum(1 for h in self.active() if h.cool_until > now)

    def capacity(self) -> int:
        """Nəzəri sürət (mahnı/dəq.) — köməkçilər və sink çevirmə limitindən kiçiyi."""
        up = len(self.active()) * self.rate
        if self.conv_mode == "lazy":
            return up                     # forward yükləmə anında olmur — sink limiti təsir etmir
        conv = int(len(self.sinks()) * 60 / self.sink_interval())
        return min(up, conv) if up else 0

    def applies(self) -> bool:
        sc = self.scope
        if sc == "off" or not self.active() or not self.sinks():
            return False
        if sc == "all" or UPLOAD_CTX.get() is not None:
            return True
        # 📦 filler rejimi, amma əsas bot depoda flood-dadır → bu yükləmə də köməkçilərə (gözləmək əvəzinə)
        return MAIN_FLOOD_ROUTE and time.monotonic() < getattr(self, "main_depo_cool", 0)

    def note_main_flood(self, wait: float):
        self.main_depo_cool = time.monotonic() + wait + 1
        self.stats["main_flood"] = self.stats.get("main_flood", 0) + 1

    async def depo_id(self, bot):
        # tapılmayanda (restartdan dərhal sonra depo hələ təyin olunmayıb) None KEŞLƏNMİR — növbəti dəfə yenə soruşulur
        if self._depo["id"] is None or time.monotonic() - self._depo["ts"] > 600:
            try:
                from bot.core import audio_cache
                self._depo["id"] = await audio_cache.depo_chat_id(bot)
            except Exception:
                self._depo["id"] = None
            if self._depo["id"] is not None:
                self._depo["ts"] = time.monotonic()
        return self._depo["id"]

    async def _await_depo(self):
        """⏳ Startda depo hələ məlum deyilsə — tapılana qədər (2 dəq.) gözlə, sonra köməkçiləri yenidən yoxla."""
        for _ in range(40):
            await asyncio.sleep(3)
            if await self.depo_id(self.ctx.bot) is not None:
                pending = [h for h in self.helpers if h.status.startswith("⏳")]
                await asyncio.gather(*(self._check(h) for h in pending), return_exceptions=True)
                logger.info(f"🤖 Depo tapıldı — {len(pending)} köməkçi yenidən yoxlandı: "
                            f"{sum(1 for h in pending if h.ok)} hazır")
                self._waiting_depo = None
                if any(not h.ok for h in pending):
                    await self.notify_missing()
                return
        for h in self.helpers:
            if h.status.startswith("⏳"):
                h.status = "⚠️ depo kanalı tapılmadı (📍 Hədəflər → depo təyin olunub?)"
        self._waiting_depo = None

    async def is_depo(self, chat_id, bot) -> bool:
        depo = await self.depo_id(bot)
        if depo is None:
            return False
        if str(chat_id) == str(depo):
            return True
        try:
            from bot.core import audio_cache
            ref = (audio_cache.depo_status() or {}).get("ref") or ""
            return bool(ref) and str(chat_id).lstrip("@").lower() == str(ref).lstrip("@").lower()
        except Exception:
            return False

    # ── başlatma / yoxlama ──
    async def reload(self):
        await self.stop_polling()
        async with self.lock:
            await self._close_all()
            self.helpers = [Helper(t, env) for t, env in self.tokens()]
        if self.helpers:
            await asyncio.gather(*(self._check(h) for h in self.helpers), return_exceptions=True)
            logger.info(f"🤖 Köməkçi botlar: {len(self.active())}/{len(self.helpers)} hazır "
                        f"({', '.join(h.name + ('✅' if h.ok else '❌') for h in self.helpers)})")
            await self.start_polling()
            spawn(self.notify_missing())
            spawn(self.warm_groups())
        self.start_warm()

    # ── 👋 köməkçiyə yazana əsas botun linki ──
    async def main_username(self):
        if not self._main_username:
            try:
                self._main_username = (await self.ctx.bot.get_me()).username
            except Exception:
                return None
        return self._main_username

    @property
    def polling(self) -> bool:
        return bool(self._poll_task and not self._poll_task.done())

    # ── 👋 köməkçilərə yazanlar → əsas bota yönləndirmə ──
    # Əvvəl: 24 köməkçinin HƏR BİRİ üçün ayrıca long-polling (aiogram Dispatcher) — 24 daimi bağlantı;
    # Telegram-da qısa nasazlıq olanda (502 Bad Gateway) hamısı eyni anda təkrar yoxlayıb GetUpdates
    # flood-una düşür, loga yüzlərlə ERROR tökülürdü. İndi: TƏK yüngül dövrə — köməkçilər növbə ilə,
    # POLL_EVERY saniyədən bir qısa sorğu (timeout=0); xətalar səssiz, nasazlıqda fasilə uzanır.
    POLL_EVERY = 45

    async def start_polling(self):
        if not [h for h in self.helpers if h.bot and h.me]:
            return
        for h in self.helpers:
            if h.bot and h.me:
                with contextlib.suppress(Exception):     # köhnə yığılmış mesajlara cavab verilməsin
                    await h.bot.delete_webhook(drop_pending_updates=True)
        self._poll_dp = None
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info(f"👋 Köməkçi botlar dinlənilir ({len(self.helpers)}, hər {self.POLL_EVERY} san.) — "
                    "yazanlara əsas botun linki göndərilir")

    async def _poll_loop(self):
        offsets, fails = {}, 0
        while True:
            bad = 0
            for h in list(self.helpers):
                if not (h.bot and h.me):
                    continue
                try:
                    ups = await h.bot.get_updates(offset=offsets.get(h.me.id), timeout=0, limit=50,
                                                  allowed_updates=["message"], request_timeout=15)
                except TelegramRetryAfter as e:
                    await asyncio.sleep(min(e.retry_after, 30))
                    continue
                except Exception:
                    bad += 1                              # 502 / timeout — səssiz, növbəti dövrədə yenə
                    continue
                for u in ups:
                    offsets[h.me.id] = u.update_id + 1
                    m = u.message
                    if m and m.chat.type == "private":
                        await self._redirect(h.bot, m)
                await asyncio.sleep(0.3)                  # köməkçilər arasında
            fails = fails + 1 if bad > len(self.helpers) // 2 else 0
            if fails == 3:
                logger.info("👋 Telegram köməkçi sorğularına cavab vermir (502 / timeout) — fasilə uzadıldı")
            await asyncio.sleep(self.POLL_EVERY * (4 if fails >= 3 else 1))

    async def _redirect(self, bot, message):
        key = (bot.id, message.from_user.id if message.from_user else 0)
        now = time.monotonic()
        if now - self._replied.get(key, 0) < 10:              # eyni adama 10 san.-də bir cavab
            return
        self._replied[key] = now
        if len(self._replied) > 5000:
            self._replied.clear()
        user = await self.main_username()
        if not user:
            return
        self.redirects += 1
        try:
            await bot.send_message(
                message.chat.id,
                "🤖 <b>Bu köməkçi botdur</b> — yalnız mahnı deposuna xidmət edir.\n\n"
                f"🎵 Musiqi axtarmaq və yükləmək üçün əsas bota keç: @{user}",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="🎵 Əsas bota keç", url=f"https://t.me/{user}?start=helper")]]),
            )
        except Exception as e:
            logger.debug(f"Köməkçi cavab vermədi: {e}")

    async def stop_polling(self):
        task = self._poll_task
        self._poll_dp = self._poll_task = None
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(BaseException):
                await task

    async def _check(self, h: Helper):
        h.ok = False
        try:
            if h.bot is None:
                from common.context import create_bot_session
                sess = create_bot_session()
                h.bot = Bot(token=h.token, session=sess) if sess else Bot(token=h.token)
            h.me = await h.bot.get_me()
            depo = await self.depo_id(self.ctx.bot)
            if depo is None:
                h.status = "⏳ depo gözlənilir — avtomatik yoxlanılacaq"
                if not getattr(self, "_waiting_depo", None):
                    with contextlib.suppress(RuntimeError):
                        self._waiting_depo = asyncio.get_running_loop().create_task(self._await_depo())
                return
            cm = await h.bot.get_chat_member(depo, h.me.id)
            chat = await h.bot.get_chat(depo)
            st = getattr(cm, "status", "")
            st = getattr(st, "value", st)
            if st == "creator":
                h.ok = True
            elif st == "administrator":
                h.ok = chat.type != "channel" or bool(getattr(cm, "can_post_messages", False))
                if not h.ok:
                    h.status = "⚠️ admindir, amma \"Post messages\" icazəsi yoxdur"
            elif st == "member" and chat.type != "channel":
                h.ok = True
            else:
                h.status = "⚠️ depoda admin deyil"
            if h.ok:
                h.status = "🟢 hazır"
                if getattr(chat, "has_protected_content", False):
                    h.status = "🟢 hazır · ⚠️ depoda protect content açıqdır — forward olmayacaq"
        except TelegramForbiddenError:
            h.status = "⚠️ depoya əlavə olunmayıb"
        except TelegramBadRequest as e:
            h.status = "⚠️ " + ("depoya əlavə olunmayıb" if "not found" in str(e).lower() else str(e)[:80])
        except Exception as e:
            h.status = f"❌ {type(e).__name__}: {str(e)[:80]}"

    async def recheck(self):
        self._depo["ts"] = 0
        await asyncio.gather(*(self._check(h) for h in self.helpers), return_exceptions=True)
        for h in self.helpers:
            h.groups.clear()

    # ── 🔔 depoya əlavə olunmayan köməkçi → creator-a mesaj ──
    def missing(self) -> list:
        # ⏳ depo hələ tapılmayıbsa — bu "əlavə olunmayıb" demək deyil, xatırlatma göndərilmir
        return [h for h in self.helpers if h.me and not h.ok and not h.status.startswith("⏳")]

    def add_kb(self, h, chat_kind: str = "channel", chat_id=None) -> InlineKeyboardMarkup:
        """➕ Telegram linki (hüquqlarla) · 🤖 userbot ilə · 🔄 yoxla"""
        u = h.me.username
        if chat_kind == "channel":
            link = f"https://t.me/{u}?startchannel&admin=post_messages+edit_messages+delete_messages"
            auto, again = f"dh:ad:{h.me.id}", f"dh:chk:{h.me.id}"
        else:
            link = f"https://t.me/{u}?startgroup=helper"
            auto, again = f"hg:ua:{chat_id}:{h.me.id}", f"hg:v:{chat_id}"
        rows = [[InlineKeyboardButton(text="➕ Əlavə et (Telegram)", url=link)]]
        if getattr(self.ctx, "telethon_client", None):
            rows.append([InlineKeyboardButton(text="🤖 Userbot ilə avtomatik", callback_data=auto)])
        rows.append([InlineKeyboardButton(text="🔄 Yoxla", callback_data=again)])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    async def notify_missing(self, force: bool = False):
        creator = getattr(self.ctx, "creator_id", None)
        if not creator:
            return
        now = time.time()
        if not self._notified:                     # restartdan sonra da təkrar spam olmasın
            try:
                self._notified = {int(k): v for k, v in json.loads(self._get("depo_helpers:notified") or "{}").items()}
            except (ValueError, TypeError):
                self._notified = {}
        changed = False
        for h in self.missing():
            if not force and now - self._notified.get(h.me.id, 0) < NOTIFY_EVERY:
                continue
            self._notified[h.me.id] = now
            changed = True
            if changed:
                self._set("depo_helpers:notified", json.dumps({str(k): v for k, v in self._notified.items()}))
            try:
                await self.ctx.bot.send_message(
                    creator,
                    f"🤖 <b>Köməkçi bot depoda deyil:</b> @{h.me.username}\n{h.status}\n\n"
                    "Depo kanalına <b>admin</b> kimi (✅ Post messages) əlavə olunmalıdır — yoxsa yükləmə "
                    "ona paylanmır.\n<i>➕ düyməsi kanal seçimini hazır hüquqlarla açır; 🤖 userbot "
                    "(sənin hesabın) bunu özü edir.</i>",
                    parse_mode="HTML", reply_markup=self.add_kb(h))
            except Exception as e:
                logger.debug(f"🤖 xatırlatma göndərilmədi: {e}")

    def by_id(self, bot_id: int):
        return next((h for h in self.helpers if h.me and h.me.id == int(bot_id)), None)

    async def userbot_add(self, h, chat, admin: bool) -> str:
        """🤖 Userbot (creator-in hesabı) köməkçini kanala admin / qrupa üzv kimi əlavə edir."""
        client = getattr(self.ctx, "telethon_client", None)
        if client is None:
            return "❌ userbot qoşulmayıb"
        try:
            if not client.is_connected():
                await client.connect()
            entity = await client.get_entity(chat)
            bot_ent = await client.get_entity(h.me.username)
            if admin:
                await client.edit_admin(entity, bot_ent, post_messages=True, edit_messages=True,
                                        delete_messages=True, is_admin=True)
            else:
                from telethon.tl.functions.channels import InviteToChannelRequest
                from telethon.tl.functions.messages import AddChatUserRequest
                from telethon.tl.types import Channel
                if isinstance(entity, Channel):
                    await client(InviteToChannelRequest(entity, [bot_ent]))
                else:
                    await client(AddChatUserRequest(entity.id, bot_ent, fwd_limit=0))
            return "✅ əlavə olundu"
        except Exception as e:
            return f"❌ {type(e).__name__}: {str(e)[:120]}"

    # ── 🐢 qrup flood-a düşdü → "köməkçi lazımdır?" ──
    def flood_muted(self, chat_id) -> bool:
        try:
            return str(chat_id) in json.loads(self._get(S_FLOOD_MUTE) or "[]")
        except (ValueError, TypeError):
            return False

    def mute_flood(self, chat_id):
        try:
            cur = set(json.loads(self._get(S_FLOOD_MUTE) or "[]"))
        except (ValueError, TypeError):
            cur = set()
        cur.add(str(chat_id))
        self._set(S_FLOOD_MUTE, json.dumps(sorted(cur)))

    def on_group_flood(self, chat_id, wait: float):
        """Middleware çağırır (sinxron) — sual flood bitəndən sonra göndərilir."""
        if not self.ext_enabled:
            return
        if not hasattr(self, "_flood_ask"):
            self._flood_ask = {}
        now = time.time()
        if now - self._flood_ask.get(chat_id, 0) < FLOOD_ASK_EVERY or self.flood_muted(chat_id):
            return
        self._flood_ask[chat_id] = now
        spawn(self._ask_after(chat_id, wait))

    async def ask_view(self, chat_id) -> tuple:
        if not self.ext_enabled and not await self.is_creator_chat(chat_id):
            return "", None, False
        helpers = [h for h in self.helpers if h.me and h.bot]
        sel = set(self.group_selected(chat_id))
        states = {h.me.id: await self.member_state(h, chat_id, fresh=True) for h in helpers}
        idle = [h for h in helpers if states[h.me.id] == "ok" and h.me.id not in sel]
        out = [h for h in helpers if states[h.me.id] in ("out", "err")]
        muted = [h for h in helpers if states[h.me.id] in ("mute", "slow")]
        working = [h for h in helpers if states[h.me.id] == "ok" and h.me.id in sel]

        is_creator = await self.is_creator_chat(chat_id)
        limit = len(helpers) if is_creator else self.ext_limit

        def names(lst):
            shown = ", ".join(f"@{h.me.username}" for h in lst[:3])
            return shown + (f" və daha {len(lst) - 3}" if len(lst) > 3 else "")
        lines = ["🐢 <b>Bu qrupda Telegram limitinə (flood) düşdüm</b>",
                 "Telegram qrupda hər bota ~20 mesaj/dəq. verir. Köməkçi yükləyici bot əlavə etsən, "
                 "mahnıları növbə ilə göndərərik — gözləmə azalar.", ""]
        rows = []
        if not helpers:
            lines.append("🤖 Köməkçi bot hələ yoxdur — /depo → 🤖 Köməkçi botlar-da token əlavə et.")
        if working:
            lines.append(f"✅ İşləyir ({len(working)}): " + names(working))
        if muted:
            lines.append(f"🔇 Qrupdadır, amma yaza bilmir ({len(muted)}): " + names(muted)
                         + "\n   <i>qrupda üzvlərə musiqi / mesaj bağlıdır və ya slow mode — admin olmalıdırlar</i>")
            rows.append([InlineKeyboardButton(text=f"🛡 Yetki ver — admin et ({len(muted)})",
                                              callback_data=f"hgs:gr:{chat_id}")])
        needed = max(0, limit - len(working))
        can_idle = idle[:needed]
        if can_idle:
            lines.append(f"⚪ Qrupdadır, amma istifadə olunmur ({len(can_idle)}): " + names(can_idle))
            rows.append([InlineKeyboardButton(text=f"✅ Qrupdakıları işə sal ({len(can_idle)})",
                                              callback_data=f"hgs:on:{chat_id}")])
        needed_after_idle = max(0, needed - len(can_idle))
        can_out = out[:needed_after_idle]
        if can_out:
            lines.append(f"➕ Əlavə oluna bilər ({len(can_out)}): " + names(can_out))
            for i in range(0, min(len(can_out), 6), 2):
                rows.append([InlineKeyboardButton(text=f"➕ @{h.me.username}",
                                                  url=f"https://t.me/{h.me.username}?startgroup=helper")
                             for h in can_out[i:i + 2]])
            if getattr(self.ctx, "telethon_client", None):
                rows.append([InlineKeyboardButton(text=f"🤖 Userbot əlavə etsin ({len(can_out)})",
                                                  callback_data=f"hgs:ua:{chat_id}")])
        rows.append([InlineKeyboardButton(text="🔕 Bu qrupda soruşma", callback_data=f"hgs:mute:{chat_id}"),
                     InlineKeyboardButton(text="✖️ Bağla", callback_data="hgs:x:0")])
        lines.append("\n<i>Düymələr yalnız bot sahibi üçündür.</i>")
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows), bool(can_idle or can_out or muted or not helpers)

    async def _ask_after(self, chat_id, wait: float):
        if not self.ext_enabled and not await self.is_creator_chat(chat_id):
            return
        await asyncio.sleep(max(1.0, wait) + 2)
        try:
            text, kb, useful = await self.ask_view(chat_id)
            if not useful:                         # hamısı artıq işləyir — soruşmağın mənası yoxdur
                return
            await self.ctx.bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb,
                                            disable_web_page_preview=True)
        except Exception as e:
            logger.debug(f"🐢 flood sualı göndərilmədi ({chat_id}): {e}")

    # ── 🛡 köməkçilərə qrupda yetki (admin) — məhdudiyyət / slow mode / "yalnız adminlər" keçilsin ──
    async def grant_rights(self, chat_id, helpers=None) -> list:
        """Əvvəl əsas bot (admin, "Add new admins" hüququ ilə), alınmasa userbot (creator hesabı)."""
        if helpers is None:
            helpers = [h for h in self.helpers if h.me and h.bot]
        out = []
        client = getattr(self.ctx, "telethon_client", None)
        for h in helpers:
            if await self.member_state(h, chat_id) == "out":
                continue
            res = None
            try:
                await self.ctx.bot.promote_chat_member(chat_id, h.me.id, can_invite_users=True)
                res = "✅ admin (əsas bot)"
            except Exception as e:
                res = f"❌ əsas bot: {str(e)[:60]}"
                if client is not None:
                    try:
                        if not client.is_connected():
                            await client.connect()
                        ent = await client.get_entity(chat_id)
                        bot_ent = await client.get_entity(h.me.username)
                        await client.edit_admin(ent, bot_ent, invite_users=True, is_admin=True)
                        res = "✅ admin (userbot)"
                    except Exception as e2:
                        res += f" · userbot: {str(e2)[:60]}"
            h.groups.pop(chat_id, None)
            out.append((h, res))
            await asyncio.sleep(0.4)
        return out

    # ── 👥 qruplarda göndərmə ──
    def groups_cfg(self) -> dict:
        try:
            v = json.loads(self._get(S_GROUPS) or "{}")
            return {str(k): [int(x) for x in b] for k, b in v.items()} if isinstance(v, dict) else {}
        except (ValueError, TypeError):
            return {}

    def _save_groups(self, cfg: dict):
        self._set(S_GROUPS, json.dumps({k: v for k, v in cfg.items() if v}))

    def group_selected(self, chat_id) -> list:
        return self.groups_cfg().get(str(chat_id), [])

    def toggle_group(self, chat_id, bot_id: int) -> bool:
        cfg = self.groups_cfg()
        cur = cfg.setdefault(str(chat_id), [])
        on = int(bot_id) not in cur
        if on:
            cur.append(int(bot_id))
        else:
            cur.remove(int(bot_id))
        self._save_groups(cfg)
        return on

    async def member_state(self, h, chat_id, fresh: bool = False) -> str:
        """ok — audio göndərə bilir · out — qrupda deyil · mute — yaza bilmir · slow — slow mode · err
        Yalnız QƏTİ nəticələr 10 dəq. keşlənir; "err" (restartdan dərhal sonra şəbəkə / sessiya hazır deyil,
        müvəqqəti 5xx, flood) keşlənmir — əvvəl o, 10 dəq. "❓ yoxlanılmadı" kimi qalırdı."""
        hit = h.groups.get(chat_id)
        if hit and not fresh and time.monotonic() - hit[1] < 600:
            return hit[0]
        state, err = "err", None
        for attempt in range(2):
            try:
                state = await self._member_state_once(h, chat_id)
                break
            except TelegramRetryAfter as e:
                err = e
                await asyncio.sleep(min(e.retry_after, 5) + 0.5)
            except Exception as e:
                err = e
                await asyncio.sleep(1.0)
        if state == "err":
            msg = f"{type(err).__name__}: {str(err)[:120]}"
            logger.warning(f"🤖 {getattr(h, 'name', '?')}: qrup {chat_id} yoxlanmadı — {msg}")
            if not hasattr(h, "gerr"):
                h.gerr = {}
            h.gerr[chat_id] = msg                           # 🩺 paneldə səbəb göstərilsin
            h.groups.pop(chat_id, None)                     # keşləmə — növbəti açılışda yenə yoxlanılsın
            return state
        getattr(h, "gerr", {}).pop(chat_id, None)
        h.groups[chat_id] = (state, time.monotonic())
        return state

    async def _member_state_once(self, h, chat_id) -> str:
        try:
            cm = await h.bot.get_chat_member(chat_id, h.me.id)
        except TelegramForbiddenError:
            return "out"
        except TelegramBadRequest as e:
            low = str(e).lower()
            if any(k in low for k in ("not found", "not a member", "user not found", "participant")):
                return "out"
            raise
        st = getattr(getattr(cm, "status", ""), "value", getattr(cm, "status", ""))
        if st in ("left", "kicked"):
            return "out"
        if st in ("administrator", "creator"):
            return "ok"
        if st == "restricted" and not getattr(cm, "can_send_audios", True):
            return "mute"
        # adi üzv: qrupun ümumi icazələri (yalnız adminlər yazır / musiqi bağlı / slow mode)
        chat = await h.bot.get_chat(chat_id)
        perms = getattr(chat, "permissions", None)
        if perms is not None and (perms.can_send_audios is False or perms.can_send_messages is False):
            return "mute"
        if getattr(chat, "slow_mode_delay", None):
            return "slow"
        return "ok"

    async def warm_groups(self, delay: float = 10):
        """♻️ Restartdan sonra: köməkçi seçilmiş qruplar arxa fonda yoxlanılır — panel açılanda hazır olsun,
        ilk mahnılar da dərhal köməkçilərlə paylansın."""
        await asyncio.sleep(delay)
        for chat in list(self.groups_cfg()):
            sel = set(self.group_selected(chat))
            hs = [h for h in self.helpers if h.me and h.bot and h.me.id in sel]
            sem = asyncio.Semaphore(8)

            async def one(h, c=int(chat), sem=sem):          # sem döngüdə hər qrup üçün yenidən yaranır — default ilə bağla
                async with sem:
                    await self.member_state(h, c)
            await asyncio.gather(*(one(h) for h in hs), return_exceptions=True)
            ok = sum(1 for h in hs if (h.groups.get(int(chat)) or ("",))[0] == "ok")
            logger.info(f"🤖 Qrup {chat}: {ok}/{len(hs)} köməkçi hazır")

    async def group_senders(self, chat_id) -> list:
        if not self.ext_enabled and not await self.is_creator_chat(chat_id):
            return []
        sel = set(self.group_selected(chat_id))
        out = []
        for h in self.helpers:
            if h.me and h.bot and h.me.id in sel and await self.member_state(h, chat_id) == "ok":
                out.append(h)
        if not await self.is_creator_chat(chat_id):
            out = out[:self.ext_limit]
        return out

    def group_count(self, chat_id) -> int:
        """Sinxron təxmin (toplu yükləmədə fasilə üçün): seçilmiş və qrupda olan köməkçilər."""
        creator_id = getattr(self.ctx, "creator_id", None)
        is_creator = (chat_id > 0 and chat_id == creator_id)
        if not is_creator and not self.ext_enabled:
            return 0
        sel = set(self.group_selected(chat_id))
        count = sum(1 for h in self.helpers if h.me and h.me.id in sel
                    and (h.groups.get(chat_id) or ("ok",))[0] == "ok")
        if not is_creator:
            count = min(count, self.ext_limit)
        return count

    @staticmethod
    def _url_only(markup):
        if not isinstance(markup, InlineKeyboardMarkup):
            return None
        rows = [[b for b in row if b.url] for row in markup.inline_keyboard]
        rows = [r for r in rows if r]
        return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None

    def _depo_post(self, file_id: str):
        hit = self.foreign.get(file_id)
        if hit:
            return hit[0], hit[1]
        try:
            row = _db().sql_one("SELECT chat_id, message_id FROM audio_cache WHERE file_id = %s "
                                "AND message_id IS NOT NULL LIMIT 1", (file_id,))
            return (row[0], row[1]) if row else None
        except Exception:
            return None

    async def group_send(self, method, bot, make_request):
        """Qrupa SendAudio (file_id): əsas bot + köməkçilər — kimin növbəsi tezdirsə o göndərir."""
        chat = method.chat_id
        helpers = await self.group_senders(chat)
        post = await asyncio.to_thread(self._depo_post, method.audio) if helpers else None
        if not helpers or not post:
            return await make_request(bot, method)
        for _ in range(len(helpers) + 2):
            async with self._g_lock:
                now = time.monotonic()
                cands = [("main", max(self._main_g.get(chat, 0), 0))] + \
                        [(h, max(h.g_next.get(chat, 0), h.g_cool.get(chat, 0))) for h in helpers]
                who, at = min(cands, key=lambda c: c[1])
                start = max(now, at)
                if who == "main":
                    self._main_g[chat] = start + GROUP_INTERVAL
                else:
                    who.g_next[chat] = start + GROUP_INTERVAL
            if start > now:
                await asyncio.sleep(start - now)
            if who == "main":
                self.gstats["main"] += 1
                try:
                    return await make_request(bot, method)
                except TelegramRetryAfter as e:
                    self._main_g[chat] = time.monotonic() + e.retry_after + 1
                    if not helpers:
                        raise
                    continue
            try:
                kw = dict(chat_id=chat, from_chat_id=post[0], message_id=post[1],
                          caption=method.caption, parse_mode=method.parse_mode,
                          reply_markup=self._url_only(method.reply_markup))
                if getattr(method, "message_thread_id", None):
                    kw["message_thread_id"] = method.message_thread_id
                if getattr(method, "reply_parameters", None):
                    kw["reply_parameters"] = method.reply_parameters
                mid = await who.bot.copy_message(**kw)
                who.g_sent += 1
                self.gstats["helper"] += 1
                return Message.model_validate({
                    "message_id": mid.message_id, "date": int(time.time()),
                    "chat": {"id": chat, "type": "supergroup"},
                    "from": {"id": who.me.id, "is_bot": True, "first_name": who.me.first_name or "bot"},
                }).as_(bot)
            except TelegramRetryAfter as e:
                who.g_cool[chat] = time.monotonic() + e.retry_after + 1
                logger.info(f"🤖 {who.name} qrupda flood {e.retry_after} san. — digərinə keçilir")
                continue
            except (TelegramForbiddenError, TelegramBadRequest) as e:
                low = str(e).lower()
                if any(k in low for k in ("rights", "restricted", "slow", "forbidden: not enough", "admin")):
                    st = "mute"                     # qrupdadır, amma yazmağa icazəsi yoxdur → 🛡 yetki lazımdır
                elif isinstance(e, TelegramForbiddenError) or "not a member" in low or "chat not found" in low:
                    st = "out"
                else:
                    st = "err"
                who.groups[chat] = (st, time.monotonic())
                logger.info(f"🤖 {who.name} qrupa göndərə bilmədi: {e}")
                helpers = [h for h in helpers if h is not who]
                continue
            except Exception as e:
                logger.debug(f"🤖 qrup göndərmə: {e}")
                helpers = [h for h in helpers if h is not who]
                continue
        self.gstats["fallback"] += 1
        return await make_request(bot, method)

    async def _close_all(self):
        for h in self.helpers:
            if h.bot:
                try:
                    await h.bot.session.close()
                except Exception as _e:
                    logger.debug("udulan xəta [bot/core/depo_helpers.py:996]: %r", _e)

    async def close(self):
        if self._warm_task:
            self._warm_task.cancel()
        await self.stop_polling()
        await self._close_all()
        self.helpers = []

    # ── yükləmə ──
    async def _reserve(self):
        """Ən tez boşalan köməkçini seçir (flood alanlar cooldown-larına görə avtomatik arxaya düşür)
        və ona yer ayırır → (helper, gözləmə san.)"""
        interval = 60.0 / self.rate
        async with self.lock:
            now = time.monotonic()
            cands = self.active()
            if not cands:
                return None, 0.0
            h = min(cands, key=lambda x: max(x.next_at, x.cool_until))
            start = max(now, h.next_at, h.cool_until)
            h.next_at = start + interval
            return h, start - now

    @staticmethod
    def _phase(name: str):
        ctx = UPLOAD_CTX.get()
        if ctx:
            filler, idx = ctx
            try:
                filler._phase(idx, name)
            except Exception as _e:
                logger.debug("udulan xəta [bot/core/depo_helpers.py:1028]: %r", _e)

    async def upload(self, method, bot, make_request):
        attempts = max(2, len(self.active()) * 2)
        for _ in range(attempts):
            h, wait = await self._reserve()
            if h is None:
                break
            if wait > 0:
                self._phase("SLOT")
                await asyncio.sleep(wait)
            self._phase("UP")
            try:
                msg = await h.bot(method)
            except TelegramRetryAfter as e:
                h.cool_until = time.monotonic() + e.retry_after + 1
                h.floods += 1
                logger.info(f"🤖 {h.name}: flood {e.retry_after} san. — digər köməkçiyə keçilir")
                continue
            except (TelegramForbiddenError, TelegramBadRequest) as e:
                err = str(e).lower()
                if isinstance(e, TelegramForbiddenError) or any(x in err for x in _DEAD_ERRORS):
                    h.ok = False
                    h.status = f"⚠️ {str(e)[:80]}"
                    logger.warning(f"🤖 {h.name} söndürüldü: {e}")
                    continue
                raise                                  # faylın özü ilə bağlı xəta — əsas bot da alacaqdı
            except Exception as e:
                h.fails += 1
                self.last_error = f"{h.name}: {type(e).__name__}: {e}"[:150]
                h.cool_until = time.monotonic() + 15        # şəbəkə xətası — qısa fasilə, digərləri işləsin
                continue
            h.sent += 1
            sink_wait = self.sink_backlog()
            if self.conv_mode == "eager" and getattr(msg, "audio", None) and sink_wait > SINK_MAX_WAIT:
                # sink çatı flood-dadır və ya növbə uzundur — işçini bloklamırıq, lazy kimi qeyd edirik
                self.stats["sink_skip"] = self.stats.get("sink_skip", 0) + 1
                if self.stats["sink_skip"] % 50 == 1:
                    logger.info(f"🤖 sink növbəsi {sink_wait:.0f} san. — eager çevirmə lazy-yə ötürülür")
                await self.register_foreign(msg)
                self.stats["helper"] += 1
                return msg.model_copy().as_(bot)
            if self.conv_mode == "lazy" and getattr(msg, "audio", None):
                await self.register_foreign(msg)
                self.stats["helper"] += 1
                return msg.model_copy().as_(bot)       # köməkçinin file_id-si — göndəriləndə çevriləcək
            try:
                res = await self._as_main(msg, bot)
                self.stats["helper"] += 1
                return res
            except Exception as e:
                # çevirmə alınmadı — köməkçinin postunu silib əsas botla yükləyirik (depoda dublikat qalmasın)
                self.stats["conv_fail"] += 1
                self.last_error = f"file_id çevirmə: {type(e).__name__}: {e}"[:150]
                logger.warning(f"🤖 file_id çevrilmədi, əsas bot yükləyir: {e}")
                try:
                    await h.bot.delete_message(msg.chat.id, msg.message_id)
                except Exception as _e:
                    logger.debug("udulan xəta [bot/core/depo_helpers.py:1086]: %r", _e)
                break
        self.stats["fallback"] += 1
        self._phase("UP")
        return await make_request(bot, method)

    def sink_backlog(self) -> float:
        """Ən tez boşalan sink çatına qədər gözləmə (san.) — flood + növbə."""
        sinks = self.sinks()
        if not sinks:
            return 0.0
        now = time.monotonic()
        return max(0.0, min(self._sinks.get(c, 0.0) for c in sinks) - now)

    async def _sink_slot(self):
        sinks = self.sinks()
        interval = self.sink_interval()
        async with self._sink_lock:
            now = time.monotonic()
            chat = min(sinks, key=lambda c: self._sinks.get(c, 0.0))
            start = max(now, self._sinks.get(chat, 0.0))
            self._sinks[chat] = start + interval
            return chat, start - now

    async def _forward_audio(self, bot, chat_id, message_id, unique_id=None):
        """Depo postunu sink çatına forward edir → əsas botun Audio obyekti; forward silinir."""
        fwd = None
        for _ in range(4):
            chat, wait = await self._sink_slot()
            if wait > SINK_MAX_WAIT:
                raise RuntimeError(f"sink çatı flood-dadır ({wait:.0f} san. gözləmə)")
            if wait > 0:
                self._phase("FWD")                     # monitorda "UP" yox, sink gözləməsi görünsün
                await asyncio.sleep(wait)
            try:
                fwd = await bot.forward_message(chat_id=chat, from_chat_id=chat_id,
                                                message_id=message_id, disable_notification=True)
                break
            except TelegramRetryAfter as e:
                logger.warning(f"🤖 sink çatı {chat}: flood {e.retry_after} san.")
                async with self._sink_lock:
                    self._sinks[chat] = time.monotonic() + e.retry_after + 1
                if e.retry_after > SINK_MAX_WAIT:
                    raise RuntimeError(f"sink çatı flood-dadır ({e.retry_after} san.)")
        if fwd is None:
            raise RuntimeError("sink çatına forward alınmadı (flood)")
        spawn(self._delete(bot, fwd.chat.id, fwd.message_id))
        if not fwd.audio or (unique_id and fwd.audio.file_unique_id != unique_id):
            raise RuntimeError("forward-da eyni audio tapılmadı")
        self.stats["converted"] += 1
        return fwd.audio

    async def _as_main(self, msg, bot):
        """⚡ eager: depo postunun özü (chat / message_id köməkçinindir), audio isə əsas botun file_id-si ilə."""
        if not getattr(msg, "audio", None):
            raise RuntimeError("köməkçinin cavabında audio yoxdur")
        audio = await self._forward_audio(bot, msg.chat.id, msg.message_id, msg.audio.file_unique_id)
        return msg.model_copy(update={"audio": audio}).as_(bot)

    # ───────────── 🐢 lazy çevirmə ─────────────
    def _init_table(self):
        try:
            db = _db()
            with db.sql() as cur:                  # cədvəl common/database.py sxemində yaradılır
                # restartdan əvvəl çevrilib, amma keşə yazılmamış qalan sətirləri düzəlt
                cur.execute("""UPDATE audio_cache SET file_id = (
                        SELECT main_file_id FROM depo_foreign f WHERE f.file_id = audio_cache.file_id)
                    WHERE file_id IN (SELECT file_id FROM depo_foreign WHERE main_file_id IS NOT NULL)""")
                rows = cur.execute("""SELECT file_id, unique_id, chat_id, message_id, main_file_id
                    FROM depo_foreign WHERE main_file_id IS NULL OR converted >= %s""",
                                   (int(time.time()) - 2 * 86400,)).fetchall()
            for r in rows:
                if r[4]:
                    self.converted[r[0]] = r[4]
                else:
                    self.foreign[r[0]] = (r[2], r[3], r[1])
            if self.foreign:
                logger.info(f"🐢 Çevrilməmiş köməkçi file_id-ləri: {len(self.foreign)}")
        except Exception as e:
            logger.warning(f"depo_foreign cədvəli hazırlanmadı: {e}")

    def pending(self) -> int:
        return len(self.foreign)

    async def register_foreign(self, msg):
        a = msg.audio
        self.foreign[a.file_id] = (msg.chat.id, msg.message_id, a.file_unique_id)

        def put():
            db = _db()
            with db.sql() as cur:
                cur.execute("""INSERT INTO depo_foreign
                    (file_id, unique_id, chat_id, message_id, main_file_id, created) VALUES (%s,%s,%s,%s,NULL,%s)
                    ON CONFLICT (file_id) DO UPDATE SET unique_id=EXCLUDED.unique_id, chat_id=EXCLUDED.chat_id,
                        message_id=EXCLUDED.message_id, main_file_id=NULL, created=EXCLUDED.created,
                        converted=NULL""",
                            (a.file_id, a.file_unique_id, msg.chat.id, msg.message_id, int(time.time())))
        try:
            await asyncio.to_thread(put)
        except Exception as e:
            logger.warning(f"depo_foreign yazılmadı: {e}")

    def _save_converted(self, fid: str, main_fid: str):
        db = _db()
        with db.sql() as cur:
            cur.execute("UPDATE depo_foreign SET main_file_id=%s, converted=%s WHERE file_id=%s",
                        (main_fid, int(time.time()), fid))
            cur.execute("UPDATE audio_cache SET file_id=%s WHERE file_id=%s", (main_fid, fid))
        try:
            from bot.core import audio_cache
            audio_cache.mark_dirty()              # depo axtarış indeksi köhnə file_id saxlamasın
        except Exception as _e:
            logger.debug("udulan xəta [bot/core/depo_helpers.py:1194]: %r", _e)

    def _drop_dead(self, fid: str):
        db = _db()
        with db.sql() as cur:
            cur.execute("DELETE FROM depo_foreign WHERE file_id=%s", (fid,))

    async def resolve(self, fid: str, bot, source: str = "lazy"):
        """Köməkçinin file_id-si → əsas botunku (lazım olsa forward ilə). Alınmasa None."""
        if fid in self.converted:
            return self.converted[fid]
        if fid not in self.foreign or fid in self._dead:
            return None
        lock = self._conv_locks.setdefault(fid, asyncio.Lock())
        async with lock:
            if fid in self.converted:                 # paralel sorğu artıq çevirib
                return self.converted[fid]
            entry = self.foreign.get(fid)
            if not entry:
                return None
            chat_id, message_id, uid = entry
            try:
                audio = await self._forward_audio(bot, chat_id, message_id, uid)
            except TelegramBadRequest as e:
                err = str(e).lower()
                if "not found" in err or "can't be forwarded" in err or "protected" in err:
                    # depo postu silinib / forward qadağandır — music_plugin köhnə file_id ilə xəta alıb
                    # keşi özü təmizləyəcək və mahnını yenidən yükləyəcək
                    self._dead.add(fid)
                    self.foreign.pop(fid, None)
                    await asyncio.to_thread(self._drop_dead, fid)
                self.cstats["fail"] += 1
                self.last_error = f"çevirmə: {str(e)[:120]}"
                return None
            except Exception as e:
                self.cstats["fail"] += 1
                self.last_error = f"çevirmə: {type(e).__name__}: {str(e)[:110]}"
                return None
            finally:
                self._conv_locks.pop(fid, None)
            main_fid = audio.file_id
            self.converted[fid] = main_fid
            self.foreign.pop(fid, None)
            self.cstats[source] += 1
            if len(self.converted) > 50_000:
                for k in list(self.converted)[:20_000]:
                    self.converted.pop(k, None)
            try:
                await asyncio.to_thread(self._save_converted, fid, main_fid)
            except Exception as e:
                logger.warning(f"Çevrilmiş file_id yazılmadı: {e}")
            return main_fid

    # ── sorğudakı köməkçi file_id-lərini tapıb əvəz etmək ──
    def _collect(self, obj, out: set, depth: int = 0):
        if depth > 5 or obj is None:
            return
        if isinstance(obj, str):
            if obj in self.foreign or obj in self.converted:
                out.add(obj)
        elif isinstance(obj, (list, tuple)):
            for x in obj:
                self._collect(x, out, depth + 1)
        elif hasattr(type(obj), "model_fields"):
            for name in type(obj).model_fields:
                self._collect(getattr(obj, name, None), out, depth + 1)

    def _replace(self, obj, mp: dict, depth: int = 0):
        if depth > 5 or obj is None:
            return obj
        if isinstance(obj, str):
            return mp.get(obj, obj)
        if isinstance(obj, (list, tuple)):
            new = [self._replace(x, mp, depth + 1) for x in obj]
            return type(obj)(new) if any(a is not b for a, b in zip(new, obj)) else obj
        if hasattr(type(obj), "model_fields"):
            upd = {}
            for name in type(obj).model_fields:
                v = getattr(obj, name, None)
                nv = self._replace(v, mp, depth + 1)
                if nv is not v:
                    upd[name] = nv
            return obj.model_copy(update=upd) if upd else obj
        return obj

    async def substitute(self, method, bot):
        """Əsas botun sorğusunda köməkçi file_id-si varsa — əsas botunku ilə əvəz edir."""
        if type(method).__name__ not in FID_METHODS or not (self.foreign or self.converted):
            return method
        found = set()
        self._collect(method, found)
        if not found:
            return method
        inline = type(method).__name__ == "AnswerInlineQuery"
        tasks = {fid: asyncio.ensure_future(self.resolve(fid, bot)) for fid in found}
        done, _ = await asyncio.wait(tasks.values(), timeout=INLINE_BUDGET if inline else SEND_BUDGET)
        mp = {fid: t.result() for fid, t in tasks.items()
              if t in done and not t.cancelled() and t.exception() is None and t.result()}
        # vaxtında bitməyənlər fonda davam edir (tapşırıq ləğv olunmur) — növbəti dəfə hazır olacaq
        if inline:
            left = found - set(mp)
            if left:
                self.cstats["inline_skip"] += len(left)
                kept = [r for r in method.results if getattr(r, "audio_file_id", None) not in left]
                method = method.model_copy(update={"results": kept})
        return self._replace(method, mp) if mp else method

    # ── 🔥 fon çevirici: boş vaxtda ən çox istənilən mahnıları əvvəlcədən çevirir ──
    def _next_warm(self):
        db = _db()
        with db.sql() as cur:
            rows = cur.execute("""SELECT f.file_id FROM depo_foreign f
                    LEFT JOIN audio_cache a ON a.file_id = f.file_id
                    WHERE f.main_file_id IS NULL
                    ORDER BY COALESCE(a.hits, 0) DESC, f.created DESC LIMIT 50""").fetchall()
        for r in rows:
            if self._warm_fails.get(r[0], 0) < 3:          # 3 dəfə alınmayanı ötür (istənəndə yenə sınanır)
                return r[0]
        return None

    async def warm_loop(self):
        while True:
            await asyncio.sleep(WARM_INTERVAL)
            if self.conv_mode != "lazy" or not self.foreign or WARM_INTERVAL <= 0:
                continue
            try:
                fid = await asyncio.to_thread(self._next_warm)
                if fid and fid not in self.foreign:            # DB-də var, yaddaşda yox (köhnə restart)
                    self._dead.add(fid)
                    await asyncio.to_thread(self._drop_dead, fid)
                    continue
                if fid and not await self.resolve(fid, self.ctx.bot, source="warm"):
                    self._warm_fails[fid] = self._warm_fails.get(fid, 0) + 1
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug(f"Fon çevirici: {e}")

    def start_warm(self):
        if WARM_INTERVAL > 0 and not (self._warm_task and not self._warm_task.done()):
            self._warm_task = asyncio.create_task(self.warm_loop())

    @staticmethod
    async def _delete(bot, chat_id, message_id):
        for _ in range(3):
            try:
                await bot.delete_message(chat_id, message_id)
                return
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except Exception:
                return


class DepoUploadMiddleware(BaseRequestMiddleware):
    """Əsas botun depoya SendAudio sorğularını köməkçi botlara yönəldir."""

    def __init__(self, context):
        self.ctx = context

    async def __call__(self, make_request, bot, method):
        try:
            return await self._call(make_request, bot, method)
        except TelegramRetryAfter as e:
            # 🐢 qrupa mahnı göndərərkən flood → həmin qrupa "köməkçi lazımdır?" (flood bitəndən sonra)
            pool = getattr(self.ctx, "depo_helpers", None)
            chat = getattr(method, "chat_id", None)
            if pool is not None and isinstance(method, SendAudio) and str(chat).startswith("-"):
                with contextlib.suppress(Exception):
                    if not await pool.is_depo(chat, bot):
                        pool.on_group_flood(chat, e.retry_after)
            raise

    async def _call(self, make_request, bot, method):
        pool = getattr(self.ctx, "depo_helpers", None)
        if pool is not None:
            try:
                method = await pool.substitute(method, bot)      # 🐢 lazy: köməkçi file_id → əsas botunku
            except Exception as e:
                logger.warning(f"file_id əvəzlənmədi: {e}")
        # 👥 qrupa depodakı mahnı (file_id) — seçilmiş köməkçilərlə paylaşılır (flood-a qarşı)
        if (pool is not None and isinstance(method, SendAudio) and isinstance(method.audio, str)
                and str(method.chat_id).startswith("-") and pool.group_selected(method.chat_id)):
            return await pool.group_send(method, bot, make_request)
        # yalnız real fayl yükləməsi: string (əsas botun file_id-si / URL) köməkçi üçün keçərsizdir
        if (pool is not None and isinstance(method, SendAudio) and isinstance(method.audio, InputFile)
                and pool.applies() and await pool.is_depo(method.chat_id, bot)):
            return await pool.upload(method, bot, make_request)
        try:
            return await make_request(bot, method)
        except TelegramRetryAfter as e:
            # əsas bot depoda flood-a düşdü → növbəti yükləmələr müvəqqəti köməkçilərə; bu sorğu da dərhal
            if (pool is not None and isinstance(method, SendAudio) and isinstance(method.audio, InputFile)
                    and pool.active() and await pool.is_depo(method.chat_id, bot)):
                pool.note_main_flood(e.retry_after)
                if pool.applies():
                    logger.info(f"🤖 Əsas bot depoda flood ({e.retry_after} san.) — yükləmə köməkçilərə keçdi")
                    return await pool.upload(method, bot, make_request)
            raise


def install(context) -> HelperPool:
    """Hovuzu yaradır (plugin yenidən yüklənəndə köhnəsini əvəz edir), middleware bir dəfə qoşulur."""
    pool = HelperPool(context)
    context.depo_helpers = pool
    if not getattr(context, "_depo_upload_mw", False):
        context.bot.session.middleware(DepoUploadMiddleware(context))
        context._depo_upload_mw = True
    return pool
