"""
🎙 bot/voice/engine.py - Canlı yayım mühərriki (Assistant).
"""
from __future__ import annotations

import asyncio
from collections import deque
import contextlib
import json
import logging
import os
import re
import time

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from html import escape

from bot.core import audio_cache, cpu_pool, depo_shards, song_names as names
from bot.core.links import _YOUTUBE_VID_RE as _VID_RE
from bot.voice.calls_adapter import Calls, _LibLog, pytgcalls_version
from bot.voice.models import (
    DIALOG_SCAN,
    DM_CD_STEPS,
    DM_TEXT,
    IDLE_LEAVE,
    LOUDNORM,
    MAX_CACHE_BYTES,
    MAX_DURATION,
    MAX_SLOTS,
    MAXPER_STEPS,
    MIX_REFILL_AT,
    MIX_STEPS,
    PLAY_DIR,
    QUALITY,
    S_DEPO,
    S_DM_CD,
    S_DM_ON,
    S_MAXPER,
    S_MIXAUTO,
    S_MIXN,
    S_NORM,
    S_ON,
    S_QUAL,
    S_SRC,
    S_THROTTLE,
    S_VOL,
    S_WHO,
    SESSION_PATH,
    SESSIONS_FILE,
    SOURCES,
    T_BEST,
    T_BEST_DEPO,
    T_FETCH,
    T_JOIN,
    T_MEMBER,
    T_NORM,
    T_ROUTE,
    T_YT,
    VOICE_CACHE_DIR,
    VOL_STEPS,
    WHO,
    ChatState,
    NotMember,
    Slot,
    Track,
    _Deadline,
    _deadline,
    _flood_seconds,
    _get,
    _set,
    extra_session_path,
    progress_bar,
)
from common.i18n import _ as tr
from common.utilities import format_duration as fmt_dur, spawn, ui_styled

PLANNED_FLAG = os.path.join("data", "voice_planned_restart")      # planlı restart: açılışda yayım avtomatik bərpa olunur

API_ID = os.getenv("TELETHON_API_ID")
API_HASH = os.getenv("TELETHON_API_HASH")

def _get_api_creds():
    return os.getenv("TELETHON_API_ID") or API_ID, os.getenv("TELETHON_API_HASH") or API_HASH


try:
    from telethon import events, utils as tl_utils
    from telethon.tl.functions.channels import GetFullChannelRequest, GetParticipantRequest
    from telethon.tl.functions.messages import GetFullChatRequest
    from telethon.tl.functions.phone import GetGroupCallRequest
    from telethon.tl.types import ChannelParticipantAdmin, ChannelParticipantCreator, ChatAdminRights, InputGroupCall
    HAS_TELETHON = True
except ImportError:
    HAS_TELETHON = False
    events = None
    tl_utils = None
    GetFullChannelRequest = None
    GetParticipantRequest = None
    GetFullChatRequest = None
    GetGroupCallRequest = None
    ChannelParticipantAdmin = None
    ChannelParticipantCreator = None
    ChatAdminRights = None
    InputGroupCall = None

logger = logging.getLogger(__name__)

class SessionRevoked(RuntimeError):
    """Asistan sessiyası Telegram tərəfindən ləğv olunub — təkrar cəhd mənasızdır."""


class Assistant:
    def __init__(self, context):
        self.ctx = context
        # 🎙 asistan hesabları: slot 0 — əsas (data/assistant.session), digərləri data/assistant_N.session
        self.slots = [Slot(0, SESSION_PATH)]
        for i in range(1, MAX_SLOTS):
            path = extra_session_path(i)
            if os.path.isfile(path) and os.path.getsize(path) > 10:
                self.slots.append(Slot(i, path))
        self.bind = {}                       # chat_id → Slot (qrupu hansı hesab aparır)
        self.status = "söndürülüb"
        self.error = ""
        self.chats = {}                      # chat_id → ChatState
        self._lock = asyncio.Lock()
        global REC
        from bot.core.va_record import Recorder
        self.rec = REC = Recorder(self)      # ⏺ asistan qeydi

    # ── ayarlar ──
    @property
    def enabled(self) -> bool:
        return _get(S_ON, "0") == "1"

    @property
    def source(self) -> str:
        v = _get(S_SRC, "")
        if v in SOURCES:
            return v
        try:
            from bot.core.youtube_handler import search_source
            src = search_source()
            return src if src in SOURCES else "ytm"
        except Exception:
            return "ytm"

    @property
    def depo_first(self) -> bool:
        return _get(S_DEPO, "1") == "1"

    @property
    def who(self) -> str:
        v = _get(S_WHO, "all")
        return v if v in WHO else "all"

    @property
    def mix_n(self) -> int:
        try:
            v = int(_get(S_MIXN, 15))
        except (TypeError, ValueError):
            v = 15
        return v if v in MIX_STEPS else 15

    @property
    def quality(self) -> str:
        v = _get(S_QUAL, "best")
        return v if v in QUALITY else "best"

    @property
    def normalize(self) -> bool:
        return _get(S_NORM, "1") == "1"

    @property
    def volume(self) -> int:
        try:
            v = int(_get(S_VOL, 100))
        except (TypeError, ValueError):
            v = 100
        return v if v in VOL_STEPS else 100

    @property
    def throttle(self) -> bool:
        return _get(S_THROTTLE, "1") == "1"

    def apply_throttle(self):
        """Yayım gedərkən 📦 depo yükləmə proseslərini yarıya endir — CPU boş qalsın, səs kəsilməsin."""
        try:
            from bot.core import cpu_pool
        except Exception:
            return
        live = any(s.current for s in self.chats.values())
        full = cpu_pool.depo_procs()
        want = max(1, full // 2) if (live and self.throttle) else full
        lane = cpu_pool.LANES["depo"]
        if lane.size != want:
            lane.size = want
            logger.info(f"🎙 📦 Depo prosesləri: {want}/{full}" + (" (yayım gedir)" if want < full else ""))

    @property
    def mix_auto(self) -> bool:
        return _get(S_MIXAUTO, "1") == "1"

    # ── 🔀 Mix ──
    async def mix_tracks(self, vid: str, limit: int, seen: set) -> list:
        """Verilən mahnının YouTube Mix-i → Track siyahısı (depoda olanlar 📦 file_id ilə, təkrarlar süzülür)."""
        ym = getattr(self.ctx, "youtube_manager", None)
        if ym is None or not vid:
            return []
        res = await ym.youtube_mix(vid, limit=limit + 10)
        vids = [m.group(1) for r in res or [] if (m := _VID_RE.search(r.get("url") or ""))]
        cached = await asyncio.to_thread(audio_cache.cached_ids, vids) if vids else set()
        out = []
        for r in res or []:
            m = _VID_RE.search(r.get("url") or "")
            if not m:
                continue
            v, dur = m.group(1), int(r.get("raw_duration") or 0)
            if v in seen or dur > MAX_DURATION:
                continue
            seen.add(v)
            c = await asyncio.to_thread(audio_cache.get_cached, v) if v in cached else None
            title = f"{c['performer']} - {c['title']}" if c and c.get("performer") and c["performer"] != "YouTube" \
                else r.get("title") or v
            out.append(Track(title, v, f"https://www.youtube.com/watch?v={v}", dur or (c or {}).get("duration") or 0,
                             (c or {}).get("file_id"), "", depo=bool(c), src="mix"))
            if len(out) >= limit:
                break
        return out

    async def start_mix(self, chat_id: int, title: str, seed: Track, by: str) -> tuple:
        """Seed mahnını (oxunmursa) oxudur, ardınca Mix-i növbəyə qoyur, ♾ rejimi açır. → (oxunur?, əlavə sayı)"""
        st = self.state(chat_id, title)
        seen = {t.vid for t in [st.current, *st.queue] if t and t.vid}
        if seed.vid:
            seen.add(seed.vid)
        tracks = await self.mix_tracks(seed.vid, self.mix_n, seen)
        if not tracks:
            raise RuntimeError("bu mahnı üçün Mix tapılmadı")
        st.radio = {"seed": seed.vid, "seen": seen}
        started = False
        if st.current is None or (st.current.vid != seed.vid):
            kind, _ = await self.enqueue(chat_id, title, seed)
            started = kind == "playing"
        st.queue.extend(tracks)
        if st.queue:
            self.prefetch(st.queue[0])
        await self.refresh_np(st)
        return started, len(tracks)

    async def refill(self, st: ChatState, force: bool = False):
        """♾ Mix: növbə azalanda son mahnının Mix-indən yeni partiya."""
        if not st.radio or getattr(st, "stopped", False) or st.refilling or not (self.mix_auto or force):
            return 0
        try:
            from bot.plugins.restart_plugin import is_restart_pending
            if is_restart_pending():
                return 0
        except Exception as _e:
            logger.debug("udulan xəta [bot/voice/engine.py:256]: %r", _e)
        st.refilling = True
        try:
            base = (st.queue[-1].vid if st.queue and st.queue[-1].vid else
                    st.current.vid if st.current and st.current.vid else st.radio["seed"])
            tracks = await self.mix_tracks(base, self.mix_n, st.radio["seen"])
            if not tracks and base != st.radio["seed"]:
                tracks = await self.mix_tracks(st.radio["seed"], self.mix_n, st.radio["seen"])
            if getattr(st, "stopped", False) or st.chat_id not in self.chats:
                return 0
            st.queue.extend(tracks)
            if tracks:
                self.prefetch(st.queue[0])
                logger.info(f"🎙 ♾ Mix ({st.chat_id}): +{len(tracks)} mahnı")
            return len(tracks)
        except Exception as e:
            logger.warning(f"🎙 Mix davamı alınmadı: {e}")
            return 0
        finally:
            st.refilling = False

    # əsas hesab (slot 0) — köhnə kod üçün eyni adlar
    client = property(lambda self: self.slots[0].client, lambda self, v: setattr(self.slots[0], "client", v))
    calls = property(lambda self: self.slots[0].calls, lambda self, v: setattr(self.slots[0], "calls", v))
    me = property(lambda self: self.slots[0].me, lambda self, v: setattr(self.slots[0], "me", v))

    @property
    def running(self) -> bool:
        return any(sl.running for sl in self.slots)

    def has_session(self) -> bool:
        return any(sl.has_session() for sl in self.slots)

    def _read_session(self) -> str:
        return self.slots[0].read_session()

    def _write_session(self, s: str):
        self.slots[0].write_session(s)

    async def logout(self):
        """Əsas asistan hesabından çıxış: Telegram-da sessiya bağlanır, fayl silinir, asistan söndürülür."""
        client = self.client
        await self.stop()
        try:
            if client is None and self.has_session():
                from telethon import TelegramClient
                from telethon.sessions import StringSession
                api_id, api_hash = _get_api_creds()
                client = TelegramClient(StringSession(self._read_session()), int(api_id), api_hash)
            if client is not None:
                if not client.is_connected():
                    await client.connect()
                await client.log_out()
        except Exception as e:
            logger.warning(f"🎙 log_out: {e}")
        with contextlib.suppress(OSError):
            os.remove(SESSION_PATH)
        self.me = None
        _set(S_ON, "0")

    # ── 🎙🎙 çoxlu hesab: yük bölgüsü ──
    @property
    def max_per(self) -> int:
        try:
            v = int(_get(S_MAXPER, "0"))
        except (TypeError, ValueError):
            v = 0
        return v if v in MAXPER_STEPS else 0

    def my_ids(self) -> set:
        return {sl.me.id for sl in self.slots if sl.me}

    def load(self, sl: Slot) -> int:
        return sum(1 for b in self.bind.values() if b is sl)

    def calls_for(self, chat_id):
        sl = self.bind.get(chat_id)
        return sl.calls if sl and sl.calls else self.calls

    def any_client(self):
        sl = next((x for x in self.slots if x.running and x.voice_enabled), None)
        return sl.client if sl else None

    def slot_for(self, chat_id) -> Slot:
        """Sinxron: artıq bağlıdırsa o, yoxdursa ən az yüklü işləyən hesab (mesaj mətnləri üçün)."""
        sl = self.bind.get(chat_id)
        if sl and sl.running and sl.voice_enabled:
            return sl
        live = [x for x in self.slots if x.running and x.voice_enabled] or [x for x in self.slots if x.voice_enabled] or self.slots
        return min(live, key=self.load)

    async def assign(self, chat_id) -> Slot:
        """Qrupa hesab təyin edir: 1) artıq bağlı  2) qrupda üzv olan  3) ən az yüklü (limit nəzərə alınır)."""
        sl = self.bind.get(chat_id)
        if sl and sl.running and sl.voice_enabled:
            return sl
        live = [x for x in self.slots if x.running and x.voice_enabled]
        if not live:
            raise RuntimeError("səsli yayım üçün aktiv asistan yoxdur (bütün hesablar yalnız Depo rejiminə alınıb)")
        cap = self.max_per
        free = [x for x in live if not cap or self.load(x) < cap]
        if not free:
            raise RuntimeError(f"Bütün asistanlar doludur ({len(live)} hesab × {cap} yayım). "
                               "Bir yayım bitəndə yenidən cəhd edin.")
        members = []
        for x in sorted(free, key=self.load):
            if await self.member_status(chat_id, x) == "member":
                members.append(x)
                break
        sl = members[0] if members else min(free, key=self.load)
        self.bind[chat_id] = sl
        return sl

    # ── başlat / dayandır ──
    async def start(self) -> str:
        """Bütün qoşulmuş asistan hesablarını başladır. Qaytarır: əsas hesabın xətası (və ya "")."""
        async with self._lock:
            api_id, api_hash = _get_api_creds()
            if not api_id or not api_hash:
                self.status, self.error = "xəta", "TELETHON_API_ID / TELETHON_API_HASH yoxdur"
                return self.error
            if not pytgcalls_version():
                self.status, self.error = "xəta", "py-tgcalls quraşdırılmayıb: pip install py-tgcalls"
                return self.error
            if not any(sl.has_session() for sl in self.slots):
                self.status, self.error = "hesab yoxdur", "🔑 Hesab qoşulmayıb"
                return self.error
            for sl in self.slots:
                if sl.has_session() and not sl.running:
                    await self._start_slot(sl)
            ok = [sl for sl in self.slots if sl.running]
            self.status = "işləyir" if ok else "xəta"
            self.error = "" if ok else (self.slots[0].error or "heç bir asistan başlamadı")
            return self.slots[0].error if not self.slots[0].running else ""

    async def _start_slot(self, sl: Slot) -> str:
        try:
            from telethon import TelegramClient
            from telethon.sessions import StringSession
            px = None
            try:
                from common.proxy import telethon_proxy
                px = telethon_proxy()
            except Exception as _px_err:
                logger.debug("Proxy oxunmadı: %r", _px_err)
            api_id, api_hash = _get_api_creds()
            sl.client = TelegramClient(StringSession(sl.read_session()), int(api_id), api_hash, proxy=px)
            sl.client.flood_sleep_threshold = 60          # köhnə (işləyən) dəyər: qısa FloodWait-i Telethon özü gözləsin
            await sl.client.connect()
            if not await sl.client.is_user_authorized():
                raise SessionRevoked("sessiya etibarsızdır — hesabı yenidən qoş")
            sl.me = await sl.client.get_me()
            if any(x is not sl and x.me and x.me.id == sl.me.id for x in self.slots):
                raise RuntimeError("bu hesab artıq başqa slotda qoşulub")
            if sl.voice_enabled:
                sl.calls = Calls(sl.client, self._on_end, self._on_closed)
                await sl.calls.start()
                self._register_dm(sl)                         # 💬 şəxsi mesajlara avto-cavab
                self.rec.attach(sl)                           # ⏺ söhbət jurnalı + ⚡ avtomatik qeyd hadisələri
                with contextlib.suppress(Exception):          # 🛡 qrup auditi: silinən / redaktə / üzv / ban hadisələri
                    from userbot.plugins import logger as _audit
                    _audit.attach_audit(sl.client, self.ctx)
                spawn(self._warm(sl))         # qrupların entity keşi (qoşulma sürətli olsun)
                sl.status, sl.error = "işləyir", ""
                logger.info(f"🎙 Canlı yayım asistanı {sl.icon}: {sl.me.first_name} (@{sl.me.username or '—'}) · "
                            f"py-tgcalls {pytgcalls_version()}")
            else:
                sl.calls = None
                sl.status, sl.error = "işləyir (yalnız depo/işçi)", ""
                logger.info(f"📦 Arxa plan userbotu {sl.icon}: {sl.me.first_name} (@{sl.me.username or '—'}) · "
                            f"səsli yayımlardan və qruplardan kənarlaşdırılıb")
            return ""
        except SessionRevoked as e:
            # UB1-dəki kimi (userbot/core/tg_session.py): faylı .dead-ə köçür → has_session() False olur
            # və hər /play-da ləğv olunmuş açarla yenidən qoşulma (FloodWait / AuthKey xətaları) dayanır.
            dead = ""
            with contextlib.suppress(OSError):
                os.replace(sl.path, sl.path + ".dead")
                dead = sl.path + ".dead"
            logger.warning(f"🎙 Asistan {sl.icon}: {e} — {sl.path} → {dead or 'köçürülmədi'}")
            sl.status, sl.error = "xəta", str(e)[:200]
            await self._close_slot(sl)
            if dead and getattr(self.ctx, "creator_id", None):
                with contextlib.suppress(Exception):
                    await self.ctx.bot.send_message(
                        self.ctx.creator_id,
                        f"🎙 {sl.icon} asistan sessiyası ləğv olunub (çıxış edilib / açar silinib).\n"
                        "🔑 Canlı yayım asistanı panelindən hesabı yenidən qoş.")
            return sl.error
        except Exception as e:
            logger.error(f"🎙 Asistan {sl.icon} başlamadı: {e}", exc_info=True)
            sl.status, sl.error = "xəta", str(e)[:200]
            await self._close_slot(sl)
            return sl.error

    # ── 💬 şəxsi mesajlara avto-cavab ──
    @property
    def dm_on(self) -> bool:
        return _get(S_DM_ON, "1") == "1"

    @property
    def dm_cd(self) -> int:
        try:
            v = int(_get(S_DM_CD, "12"))
        except (TypeError, ValueError):
            v = 12
        return v if v in DM_CD_STEPS else 12

    async def _bot_info(self):
        if not getattr(self, "_bot_me", None):
            self._bot_me = await self.ctx.bot.get_me()
        return self._bot_me

    def _register_dm(self, sl: Slot = None):
        from telethon import events
        sl = sl or self.slots[0]
        if not hasattr(self, "_dm_seen"):
            self._dm_seen = {}
            self.dm_stats = {"replied": 0, "skipped": 0}

        @sl.client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
        async def _on_dm(event):
            try:
                await self._reply_dm(event, sl)
            except Exception as e:
                logger.info(f"🎙 Şəxsi mesaja cavab verilmədi: {e}")

    async def _reply_dm(self, event, sl: Slot = None):
        sl = sl or self.slots[0]
        if not self.dm_on:
            return
        sender = await event.get_sender()
        uid = getattr(sender, "id", None)
        if not uid or getattr(sender, "bot", False) or uid == 777000 or uid in self.my_ids():
            return
        now = time.time()
        last = self._dm_seen.get(uid, 0)
        if self.dm_cd and now - last < self.dm_cd * 3600:
            self.dm_stats["skipped"] += 1
            return
        self._dm_seen[uid] = now
        if len(self._dm_seen) > 5000:
            self._dm_seen.pop(next(iter(self._dm_seen)))
        me = await self._bot_info()
        # 1) botun inline kartı — üzərində "▶️ Botu başlat" düyməsi (basan kimi bot /start ilə açılır)
        try:
            res = await sl.client.inline_query(me.username, "va:start", entity=await event.get_input_chat())
            if res:
                await res[0].click(event.chat_id, reply_to=event.id, hide_via=True)
                self.dm_stats["replied"] += 1
                return
        except Exception as e:
            logger.debug(f"🎙 inline kart alınmadı, link ilə cavab: {e}")
        # 2) ehtiyat: mətn + link (istifadəçi hesabı düymə göndərə bilmir)
        link = f"https://t.me/{me.username}?start=va"
        text = DM_TEXT.format(bot_name=escape(me.first_name or me.username)) + f'\n\n▶️ <a href="{link}">Botu başlat</a>'
        await event.reply(text, parse_mode="html", link_preview=False)
        self.dm_stats["replied"] += 1

    async def _warm(self, sl: Slot = None):
        """Başlanğıcda son aktiv çatlar keşə (hamısı yox — FloodWait olmasın, /play ilə yarışmasın)."""
        sl = sl or self.slots[0]
        known = sl.__dict__.setdefault("known", set())
        with contextlib.suppress(Exception):
            async for d in sl.client.iter_dialogs(limit=200):
                if getattr(d, "is_group", False) or getattr(d, "is_channel", False):
                    known.add(d.id)

    async def _close_slot(self, sl: Slot):
        sl.calls = None
        if sl.client is not None:
            with contextlib.suppress(Exception):
                from userbot.plugins import logger as _audit
                _audit.detach_audit(sl.client)
            with contextlib.suppress(Exception):
                await sl.client.disconnect()
        sl.client = None

    async def _close_client(self):
        for sl in self.slots:
            await self._close_slot(sl)

    async def stop(self, kb=None, for_restart=False):
        async with self._lock:
            if for_restart:
                self.rec.save_all()                       # ⏺ növbəti başlanğıcda recover() bitirəcək
                self.save_active_sessions(now=True)
                if any(st.current or st.queue for st in self.chats.values()):
                    with contextlib.suppress(OSError):    # ♻️ planlı restart: açılışda yayım düyməsiz avtomatik bərpa olunur
                        with open(PLANNED_FLAG, "w", encoding="utf-8") as f:
                            f.write(str(time.time()))
                for sl in self.slots:
                    if sl.calls:
                        with contextlib.suppress(Exception):
                            await sl.calls.stop()
                if self.calls:
                    with contextlib.suppress(Exception):
                        await self.calls.stop()
            else:
                await self.rec.stop_all("asistan söndürüldü")
                if self.rec.tasks:                        # qeyd hazırlanıb asistan ilə göndərilsin, sonra bağla
                    await asyncio.wait(set(self.rec.tasks), timeout=180)
                for chat_id in list(self.chats):
                    await self._end_chat(chat_id, announce=True, reason="asistan söndürüldü", kb=kb)
            await self._close_client()
            self.status = "söndürülüb"

    # ── qrupa qoşulma ──
    async def member_status(self, chat_id: int, sl: Slot = None) -> str:
        """Bot API ilə: 'member' | 'left' | 'kicked' | 'unknown'."""
        sl = sl or self.slot_for(chat_id)
        if not sl.me:
            return "unknown"
        try:
            m = await self.ctx.bot.get_chat_member(chat_id, sl.me.id)
        except Exception as e:
            logger.debug(f"🎙 get_chat_member: {e}")
            return "unknown"
        st = str(getattr(m.status, "value", m.status)).lower()
        if st in ("member", "administrator", "creator") or (st == "restricted" and getattr(m, "is_member", True)):
            return "member"
        return "kicked" if st == "kicked" else "left"

    async def _cache_entity(self, chat_id: int, sl: Slot = None) -> bool:
        """Asistan hesabının qrupu "tanıması" (Telethon entity keşi). StringSession keşi boş başlayır.
        Sıra (ən ucuzdan): 1) yaddaş  2) Telethon keşi  3) qrupun @username-i — bir sorğu
        4) dialoqları TAPANA QƏDƏR oxu və dayan (əvvəl BÜTÜN dialoqlar yüklənirdi — çox çatlı
        hesabda dəqiqələr + FloodWait, /play "Qrup yoxlanılır"-da ilişirdi)."""
        sl = sl or self.slot_for(chat_id)
        known = sl.__dict__.setdefault("known", set())
        if chat_id in known:
            return True
        try:
            await sl.client.get_input_entity(chat_id)
            known.add(chat_id)
            return True
        except Exception as _e:
            logger.debug("udulan xəta [bot/plugins/voice_assistant_plugin.py:820]: %r", _e)
        with contextlib.suppress(Exception):            # açıq qrup: @username ilə bir sorğu
            chat = await self.ctx.bot.get_chat(chat_id)
            if getattr(chat, "username", None):
                await sl.client.get_input_entity(chat.username)
                await sl.client.get_input_entity(chat_id)
                known.add(chat_id)
                return True
        t0, n = time.monotonic(), 0
        try:
            async for d in sl.client.iter_dialogs(limit=DIALOG_SCAN):
                n += 1
                if d.id == chat_id:
                    known.add(chat_id)
                    logger.info(f"🎙 {sl.icon} qrup tapıldı: {n} dialoq, {time.monotonic() - t0:.1f} san.")
                    return True
        except Exception as e:
            logger.info(f"🎙 {sl.icon} dialoqlar oxunmadı: {e}")
        logger.info(f"🎙 {sl.icon} qrup {chat_id} {n} dialoqda tapılmadı")
        return False

    async def ensure_member(self, chat_id: int, sl: Slot = None):
        """Qrupa hesab təyin edilir (üzv olan / ən az yüklü); o qrupdadırsa entity hazırlanır,
        deyilsə NotMember (özü icazəsiz qoşulmur)."""
        sl = sl or await self.assign(chat_id)
        if not getattr(sl, "voice_enabled", True):
            raise RuntimeError(f"{sl.icon} yalnız Depo üçündür — səsli yayımlara və qruplara qoşula bilməz")
        status = await self.member_status(chat_id, sl)
        if status in ("left", "kicked"):
            raise NotMember(status)
        if not await self._cache_entity(chat_id, sl):
            raise NotMember("unknown")

    async def ensure_call(self, chat_id: int, sl: Slot):
        """Qrupda səsli söhbət (video chat) açıqdırmı? Bağlıdırsa — asistanın icazəsi varsa özü açır,
        yoxdursa aydın xəta (əvvəl py-tgcalls bağlı söhbətə qoşulmağa çalışıb ilişirdi)."""
        from telethon.tl.functions.channels import GetFullChannelRequest
        from telethon.tl.functions.messages import GetFullChatRequest
        from telethon.tl.functions.phone import CreateGroupCallRequest
        from telethon.tl.types import InputPeerChannel
        ent = await sl.client.get_input_entity(chat_id)
        if isinstance(ent, InputPeerChannel):
            full = await sl.client(GetFullChannelRequest(ent))
        else:
            full = await sl.client(GetFullChatRequest(abs(chat_id)))
        call = getattr(full.full_chat, "call", None)
        if call is not None:
            await self._ensure_can_speak(chat_id, sl, call)
            return True
        try:
            import random
            await sl.client(CreateGroupCallRequest(peer=ent, random_id=random.randint(1, 2 ** 31 - 1)))
            logger.info(f"🎙 {sl.icon} səsli söhbəti özü açdı: {chat_id}")
            await asyncio.sleep(1)
            return True
        except Exception as e:
            logger.info(f"🎙 səsli söhbət açılmadı ({chat_id}): {e}")
            raise RuntimeError("Qrupda səsli söhbət (video chat) açıq deyil. Qrup admini onu başlatsın "
                               "və ya asistanı \"Video chatları idarə et\" icazəli admin edin — özü açacaq.")

    async def _ensure_can_speak(self, chat_id: int, sl: Slot, call):
        """Səsli söhbətdə yeni iştirakçılar susdurulursa (və ya kanalın canlı yayımıdırsa) adi iştirakçı
        səs yayımlaya bilmir — py-tgcalls isə xəta vermədən gözləyir. Asistan admin deyilsə, bot onu
        "Video chatları idarə et" icazəsi ilə admin etməyə çalışır; alınmasa aydın xəta."""
        muted = False
        with contextlib.suppress(Exception):
            from telethon.tl.functions.phone import GetGroupCallRequest
            gc = await sl.client(GetGroupCallRequest(call=call, limit=1))
            muted = bool(getattr(gc.call, "join_muted", False)) or bool(getattr(gc.call, "rtmp_stream", False))
        try:
            chat = await self.ctx.bot.get_chat(chat_id)
            is_channel = str(getattr(chat.type, "value", chat.type)) == "channel"
        except Exception:
            is_channel = False
        if not (muted or is_channel):
            return
        m = await self.ctx.bot.get_chat_member(chat_id, sl.me.id)
        status = str(getattr(m.status, "value", m.status)).lower()
        if status == "creator" or (status == "administrator" and getattr(m, "can_manage_video_chats", False)):
            return
        try:
            await self.ctx.bot.promote_chat_member(chat_id, sl.me.id, can_manage_video_chats=True,
                                                   can_invite_users=True)
            logger.info(f"🎙 {sl.icon} \"Video chatları idarə et\" admini edildi: {chat_id}")
            await asyncio.sleep(1)
        except Exception as e:
            logger.info(f"🎙 asistan admin edilmədi ({chat_id}): {e}")
            raise RuntimeError(
                ("Bu kanalın canlı yayımında" if is_channel else "Bu qrupun səsli söhbətində yeni iştirakçılar susdurulur —")
                + f" asistan ({sl.name()}) danışa bilmir. Onu \"Video chatları idarə et\" icazəli admin edin "
                  "(və ya botu \"Admin təyin etmək\" icazəli admin edin — özü edəcək), sonra yenidən /play.")

    async def diagnose_join(self, chat_id: int, sl: Slot) -> str:
        """Qoşulma ilişəndə: asistan söhbətin iştirakçılarındadırmı? → səbəbi ayırır, loga və istifadəçiyə."""
        in_call = None
        with contextlib.suppress(Exception):
            from telethon.tl.functions.channels import GetFullChannelRequest
            from telethon.tl.functions.phone import GetGroupParticipantsRequest
            ent = await sl.client.get_input_entity(chat_id)
            full = await _deadline(sl.client(GetFullChannelRequest(ent)), 10)
            call = full.full_chat.call
            me = await sl.client.get_input_entity("me")
            res = await _deadline(sl.client(GetGroupParticipantsRequest(
                call=call, ids=[me], sources=[], offset="", limit=10)), 10)
            in_call = any(getattr(getattr(p, "peer", None), "user_id", None) == sl.me.id
                          for p in getattr(res, "participants", []))
        multi = self.load(sl) > 0
        logger.warning(f"🎙 {sl.icon} qoşulma diaqnozu ({chat_id}): iştirakçılarda="
                       f"{'bəli' if in_call else 'xeyr' if in_call is False else 'bilinmir'}, "
                       f"bu hesabda başqa aktiv yayım: {'var' if multi else 'yox'}, py-tgcalls {pytgcalls_version()}")
        if in_call:
            return ("Asistan söhbətə qoşuldu, amma səs bağlantısı (WebRTC/UDP) qurulmadı — server bu söhbətin "
                    "Telegram media serverinə çata bilmir." + (" Bu hesab eyni anda başqa qrupda da yayımdadır — "
                                                               "ikinci asistan hesabı qoşmaq kömək edə bilər."
                                                               if multi else ""))
        if in_call is False:
            return ("Telegram asistanın söhbətə qoşulmasını tamamlamadı." + (
                " Bu hesab artıq başqa qrupda yayımdadır — py-tgcalls bir hesabla eyni anda ikinci söhbətə "
                "qoşula bilmir ola bilər: ➕ ikinci asistan hesabı qoşun." if multi else ""))
        return "Səsli yayıma qoşulmaq alınmadı."

    async def vctest(self, chat_id: int) -> dict:
        """Qoşulmanı TAM loqla sınayır: event loop, versiyalar, IPv6, Telegram-ın verdiyi media serverləri,
        bütün kitabxana logları (DEBUG). Asistanın mövcud hesabı ilə — ikinci sessiya açılmır."""
        import platform
        import socket
        summary, log = ["🧪 <b>Səsli yayım diaqnostikası</b>", "━━━━━━━━━━━━━━━━━━"], []
        loop = asyncio.get_running_loop()
        loop_name = f"{type(loop).__module__}.{type(loop).__name__}"
        uv = "uvloop" in loop_name
        summary.append(f"🔁 Event loop: <code>{loop_name}</code>" + (" ⚠️" if uv else " ✅"))
        summary.append(f"🧩 py-tgcalls {escape(pytgcalls_version() or '—')} · Python {platform.python_version()}")
        with contextlib.suppress(Exception):
            from importlib import metadata
            summary.append(f"📡 Telethon {metadata.version('telethon')}")

        def v6() -> bool:
            try:
                with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as so:
                    so.connect(("2001:4860:4860::8888", 53))
                    return True
            except OSError:
                return False
        has6 = await asyncio.to_thread(v6)
        summary.append(f"🌐 IPv6: {'var' if has6 else 'yoxdur'}")
        sl = self.bind.get(chat_id) or self.slot_for(chat_id)
        if not sl or not sl.running:
            summary.append("❌ Asistan işləmir — əvvəl 🔄 Yenidən qoşul")
            return {"summary": summary, "log": log}
        if chat_id in self.chats:
            summary.append("⚠️ Bu qrupda artıq yayım var — əvvəl /stop yaz")
            return {"summary": summary, "log": log}
        # sınaq tonu (15 san.)
        tone = os.path.abspath(os.path.join(PLAY_DIR, f"vctest_{int(time.time())}.m4a"))
        os.makedirs(PLAY_DIR, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            *cpu_pool.ffmpeg_prefix(),
            "ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=15",
            "-c:a", "aac", "-b:a", "96k", tone, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await proc.wait()
        # bütün logları tut
        cap = _LibLog()
        cap.NAMES = ("pytgcalls", "ntgcalls", "telethon")
        cap.lines = deque(maxlen=400)
        root = logging.getLogger()
        old_root = root.level
        # Telegram-ın raw yeniləmələri (qoşulma parametrləri — media server ünvanları)
        raw_seen, endpoints = [], []
        from telethon import events

        async def on_raw(update):
            name = type(update).__name__
            if "GroupCall" in name or "Phone" in name:
                raw_seen.append(name)
                params = getattr(update, "params", None)
                data = getattr(params, "data", None) if params else None
                if data:
                    with contextlib.suppress(Exception):
                        js = json.loads(data)
                        for c in (js.get("transport") or {}).get("candidates") or []:
                            endpoints.append(f"{c.get('protocol', '?')} {c.get('ip')}:{c.get('port')} ({c.get('type', '')})")
                        for k in ("endpoint", "endpoints", "rtmp", "stream"):
                            if k in js:
                                endpoints.append(f"{k}: {str(js[k])[:120]}")
        sl.client.add_event_handler(on_raw, events.Raw)
        t0 = time.monotonic()
        result, err = "", None
        try:
            with cap:
                root.setLevel(logging.DEBUG)
                with contextlib.suppress(Exception):
                    await self._cache_entity(chat_id, sl)
                    await self.ensure_call(chat_id, sl)
                try:
                    await _deadline(sl.calls.play(chat_id, tone), 45)
                    result = "✅ QOŞULDU — sınaq tonu oxunur (15 san.)"
                    await asyncio.sleep(4)
                except _Deadline:
                    result = "❌ 45 san.-də cavab yoxdur (ilişdi)"
                except Exception as e:
                    err = e
                    result = f"❌ {type(e).__name__}: {str(e)[:200]}"
        finally:
            root.setLevel(old_root)
            sl.client.remove_event_handler(on_raw, events.Raw)
            with contextlib.suppress(Exception):
                await sl.calls.leave(chat_id)
            with contextlib.suppress(OSError):
                os.remove(tone)
        took = time.monotonic() - t0
        summary += ["━━━━━━━━━━━━━━━━━━", f"🎙 Nəticə ({took:.0f} san.): <b>{escape(result)}</b>",
                    f"📨 Telegram yeniləmələri: {escape(', '.join(dict.fromkeys(raw_seen)) or 'heç biri')}",
                    f"🖧 Media serverləri: {len(endpoints)}"]
        summary += [f"   <code>{escape(e)}</code>" for e in endpoints[:8]]
        if uv and not result.startswith("✅"):
            summary.append("\n💡 uvloop aktivdir — py-tgcalls ilə uyğunsuz ola bilər. config.env: "
                           "<code>DLM_UVLOOP=0</code> → restart → yenidən /vctest")
        if not raw_seen and not result.startswith("✅"):
            summary.append("💡 Telegram qoşulma cavabı gəlmədi — sorğu getmədi / rədd edildi (log faylına bax)")
        elif endpoints and not result.startswith("✅"):
            summary.append("💡 Telegram media serverləri verdi, amma səs bağlantısı qurulmadı — server "
                           "bu ünvanlara UDP ilə çata bilmir (firewall / provayder / IPv6)")
        log += [f"loop={loop_name} ipv6={has6} versions={pytgcalls_version()}", f"result={result} took={took:.1f}s",
                f"raw={raw_seen}", f"endpoints={endpoints}", "─" * 40, *cap.lines]
        if err is not None:
            import traceback
            log += ["─" * 40, *traceback.format_exception(err)]
        logger.warning(f"🧪 vctest ({chat_id}): {result} · loop={loop_name} · raw={len(raw_seen)} · "
                       f"endpoints={len(endpoints)}")
        return {"summary": summary, "log": log}

    async def heal(self, sl: Slot, reason: str):
        """Mühərrik ilişib (qoşulma yarıda kəsildi) — slotu yenidən başlat. Onun yayımları bitir:
        ilişmiş mühərrikdə onsuz da işləmirlər, əks halda /stop və yeni /play da donur."""
        if getattr(sl, "healing", False):
            return
        sl.healing = True
        try:
            logger.warning(f"🎙 {sl.icon} mühərrik bərpa olunur: {reason}")
            chats = [c for c, b in list(self.bind.items()) if b is sl]
            old = sl.calls
            for cid in chats:
                with contextlib.suppress(Exception):
                    await self._end_chat(cid, announce=True, reason="asistan bərpa olunur — bir azdan /play",
                                         leave=False)
                if old is not None:                       # köhnə mühərrik söhbətdən çıxsın (yeni qoşula bilsin)
                    with contextlib.suppress(Exception):
                        await old.leave(cid)
            await self._close_slot(sl)
            err = ""
            for attempt in range(3):
                await asyncio.sleep(2 + attempt * 3)
                err = await self._start_slot(sl)
                if not err:
                    break
            logger.info(f"🎙 {sl.icon} bərpa: " + ("✅" if not err else f"❌ {err}"))
            if err and self.ctx.creator_id:
                with contextlib.suppress(Exception):
                    await self.ctx.bot.send_message(
                        self.ctx.creator_id, f"🎙 {sl.icon} asistan bərpa olunmadı: <code>{escape(err[:200])}</code>\n"
                                             "/menu → 🎙 Canlı yayım → 🔄 Yenidən qoşul", parse_mode="HTML")
        finally:
            sl.healing = False

    async def join(self, chat_id: int):
        """Admin icazəsindən sonra: asistan qrupa qoşulur (açıq qrup — username, qapalı — botun dəvət linki)."""
        from telethon.errors import UserAlreadyParticipantError
        from telethon.tl.functions.channels import JoinChannelRequest
        from telethon.tl.functions.messages import ImportChatInviteRequest
        bot = self.ctx.bot
        sl = await self.assign(chat_id)
        status = await self.member_status(chat_id, sl)
        if status == "member" and await self._cache_entity(chat_id, sl):
            return
        if status == "kicked":
            try:
                await bot.unban_chat_member(chat_id, sl.me.id, only_if_banned=True)
            except Exception:
                raise RuntimeError("Asistan bu qrupdan qadağan olunub (ban). Qadağanı götürün və ya botu "
                                   "\"İstifadəçiləri bloklamaq\" icazəli admin edin, sonra yenidən ✅ basın.")
        chat = await bot.get_chat(chat_id)
        try:
            if chat.username:
                await sl.client(JoinChannelRequest(chat.username))
            else:
                try:
                    link = await bot.create_chat_invite_link(chat_id, member_limit=1, name="🎙 asistan")
                except Exception:
                    raise RuntimeError("Qapalı qrupdur — botu <b>admin</b> edin (\"Dəvət linki ilə istifadəçi əlavə "
                                       "etmək\" icazəsi), sonra yenidən ✅ basın.")
                await sl.client(ImportChatInviteRequest(link.invite_link.rsplit("/", 1)[-1].lstrip("+")))
        except UserAlreadyParticipantError:
            pass
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Asistan qoşula bilmədi: {escape(str(e)[:120])}")
        await asyncio.sleep(1)
        if not await self._cache_entity(chat_id, sl):
            raise RuntimeError("Asistan qoşuldu, amma qrup hələ görünmür — bir az sonra yenidən sınayın.")
        logger.info(f"🎙 Asistan {sl.icon} qrupa qoşuldu: {chat.title} ({chat_id})")

    @staticmethod
    def _clean_search_query(q: str) -> str:
        """Axtarış sorğusunu dırnaqlardan və artıq simvollardan təmizləyir."""
        if not q:
            return ""
        cleaned = re.sub(r'["\'«»“”„‟`]', ' ', q)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        return cleaned.strip(" -:;,.")

    @staticmethod
    def _rank_search_results(query: str, results: list, max_duration: int = MAX_DURATION) -> list:
        """
        YouTube / YTM axtarış nəticələrini istifadəçinin sorğusuna ən dəqiq uyğun gələnə görə sıralayır.
        İstənməyən versiyaları (1 saatlıq loop, remix, cover, reaction, tutorial və s.) arxa plana atır.
        """
        if not results:
            return []
        q_lower = query.lower()
        q_tokens = names.tokens(query)
        scored = []
        for idx, r in enumerate(results):
            url = r.get("url") or ""
            m = _VID_RE.search(url)
            if not m:
                continue
            dur = r.get("raw_duration") or 0
            if dur > max_duration:
                continue
            title = r.get("title") or ""
            title_lower = title.lower()
            t_tokens = names.tokens(title)

            # 1. Baza token uyğunluğu
            if q_tokens:
                matched_tokens = sum(1 for w in q_tokens if any(w in tw or (len(w) >= 3 and tw.startswith(w)) for tw in t_tokens))
                base_score = matched_tokens / len(q_tokens)
            else:
                base_score = 0.5

            # 2. names.query_score ilə artist-track uyğunluğu
            artist, track = names.split_title(title)
            qs = names.query_score(query, artist, track)
            score = max(base_score, qs) * 100.0

            # Orijinal YouTube axtarış sırasına kiçik üstünlük (10..0)
            score += max(0, 10 - idx * 2)

            # YouTube Music mənbəsi mahnı üçün adətən daha təmizdir
            if r.get("source") == "ytm":
                score += 15

            # Rəsmi reliz üstünlüyü
            if any(w in title_lower for w in ("official", "rəsmi", "resmi", "audio", "video", "topic", "vevo")):
                score += 8

            # Normal mahnı müddəti (1.5 - 7 dəqiqə)
            if 90 <= dur <= 420:
                score += 10
            elif 45 <= dur < 90 or 420 < dur <= 600:
                score += 2
            elif dur > 900 and not any(w in q_lower for w in ("album", "albom", "mix", "full", "konsert", "concert", "saat", "hour")):
                score -= 50
            elif dur < 45 and not any(w in q_lower for w in ("short", "teaser", "snippet")):
                score -= 40

            # İstənməyən versiyalar üçün cərimələr (əgər sorğuda xüsusi qeyd edilməyibsə)
            penalties = [
                (("remix", "mix"), ("remix", "mix"), 25),
                (("bass boosted", "bassboost", "bass boost"), ("bass",), 35),
                (("slowed", "reverb", "speed up", "sped up", "nightcore"), ("slowed", "reverb", "sped", "speed", "nightcore"), 40),
                (("1 hour", "10 hour", "1 saat", "10 saat", "loop", "sonsuz"), ("hour", "saat", "loop"), 50),
                (("cover", "karaoke", "instrumental", "minus"), ("cover", "karaoke", "instrumental", "minus"), 35),
                (("reaction", "review", "tutorial", "parodiya", "parody"), ("reaction", "review", "tutorial", "parodiya", "parody"), 60),
                (("8d", "8d audio"), ("8d",), 30),
            ]
            for phrases, query_keys, penalty_val in penalties:
                has_in_title = any(p in title_lower for p in phrases)
                has_in_query = any(k in q_lower for k in query_keys)
                if has_in_title and not has_in_query:
                    score -= penalty_val

            scored.append((score, r))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for s, r in scored]

    # ── mahnı tapmaq ──
    async def resolve(self, query: str, by: str) -> Track:
        raw_query = (query or "").strip()
        m = _VID_RE.search(raw_query)
        if m:
            vid = m.group(1)
            c = await asyncio.to_thread(audio_cache.get_cached, vid)
            title = f"{c['performer']} - {c['title']}" if c and c.get("performer") else (c or {}).get("title") or vid
            return Track(title, vid, f"https://www.youtube.com/watch?v={vid}", (c or {}).get("duration") or 0,
                         (c or {}).get("file_id"), by, depo=bool(c), src="link")

        clean_q = self._clean_search_query(raw_query) or raw_query
        if self.depo_first:
            hits = await asyncio.to_thread(audio_cache.search_depo, clean_q, 5)
            best_hit = None
            best_score = -1.0
            for h in hits or []:
                perf = h.get("performer") or ""
                ttl = h.get("title") or ""
                score = names.query_score(clean_q, perf, ttl)
                if score > best_score:
                    best_score = score
                    best_hit = h

            if best_hit and best_score >= 0.60:
                h = best_hit
                title = f"{h.get('performer')} - {h.get('title')}" if h.get("performer") else h.get("title")
                logger.info(f"🎙 Depoda etibarlı uyğunluq tapıldı ({best_score:.2f} >= 0.60): {title}")
                return Track(title, h.get("video_id"), f"https://www.youtube.com/watch?v={h.get('video_id')}",
                             h.get("duration") or 0, h.get("file_id"), by, depo=True, src="depo")
            elif best_hit:
                logger.info(f"🎙 Depo uyğunluğu zəifdir ({best_score:.2f} < 0.60, '{best_hit.get('performer')} - {best_hit.get('title')}') — YouTube-a keçilir: {clean_q!r}")

        ym = getattr(self.ctx, "youtube_manager", None)
        if ym is None:
            raise RuntimeError("youtube_manager yoxdur")
        results = await ym.youtube_search(clean_q, limit=10, source=self.source)
        if not results and self.source != "yt":
            logger.info(f"🎙 {self.source} nəticə vermədi, YouTube (yt) ilə yoxlanılır: {clean_q!r}")
            results = await ym.youtube_search(clean_q, limit=10, source="yt")

        ranked = self._rank_search_results(clean_q, results)
        if not ranked:
            raise RuntimeError("tapılmadı")
        pick = ranked[0]
        vid = _VID_RE.search(pick["url"]).group(1)
        c = await asyncio.to_thread(audio_cache.get_cached, vid)
        title = f"{c['performer']} - {c['title']}" if c and c.get("performer") and c["performer"] != "YouTube" \
            else pick.get("title")
        return Track(title, vid, pick["url"], pick.get("raw_duration") or (c or {}).get("duration") or 0,
                     (c or {}).get("file_id"), by, depo=bool(c), src=pick.get("source") or self.source)

    # ── faylı hazırlamaq: əvvəl daimi səs keşi / depo, yoxdursa yüklə (depoya da düşür) ──
    async def obtain(self, t: Track) -> str:
        if t.path and os.path.exists(t.path):
            return t.path
        if t.vid:
            cached_voice = await asyncio.to_thread(self._get_from_voice_cache, t.vid)
            if cached_voice:
                t.path, t.fmt = cached_voice, "⚡ Keş"
                logger.info(f"🎙 ⚡ Səs keşindən dərhal alındı: {t.title}")
                return cached_voice
        path = None
        if self.quality == "best" and t.vid:
            # depoda varsa Opus-u qısa gözlə (yayım tez başlasın), yoxdursa daha uzun
            lim = T_BEST_DEPO if t.file_id else T_BEST
            try:
                path = await _deadline(self._obtain_best(t), lim)
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                logger.info(f"🎙 💎 Opus {lim} san.-də gəlmədi — depo / m4a ilə davam ({t.title})")
            except Exception as e:
                logger.info(f"🎙 💎 Opus alınmadı ({str(e)[:120]}) — depo / m4a ilə davam")
        if path is None:
            path = await self._obtain_depo(t)                 # öz limitləri var (yollar paralel + YouTube)
        if self.normalize:
            path = await self._normalize(t, path)
        if path and t.vid and os.path.exists(path):
            await asyncio.to_thread(self._save_to_voice_cache, t.vid, path)
        return path

    async def _obtain_best(self, t: Track) -> str:
        """YouTube-un ən yaxşı audio axını (Opus 251, ~160 kbps) — çevirmə yoxdur. Depo fonda dolur."""
        from bot.core import youtube_handler as yh
        os.makedirs(PLAY_DIR, exist_ok=True)
        parallel_on = getattr(yh, "is_parallel_dl_enabled", lambda: True)()
        opts = {
            "format": "bestaudio[acodec=opus]/bestaudio[ext=webm]/bestaudio",
            "outtmpl": os.path.abspath(os.path.join(PLAY_DIR, f"{t.vid}_{id(t)}_best.%(ext)s")),
            "noplaylist": True, "nopart": True, "quiet": True, "retries": 3, "fragment_retries": 5,
            **yh.JS_OPTS, **yh.cookie_opts(), **yh._px().ytdlp_opts("download"),
        }
        if parallel_on:
            opts["concurrent_fragment_downloads"] = 4
            opts["http_chunk_size"] = 10485760
        res = await cpu_pool.ytdl_download(t.url, opts)
        if not os.path.exists(res["path"]):
            raise RuntimeError("fayl yoxdur")
        t.path, t.fmt = res["path"], "💎 Opus"
        if not t.file_id:                                  # depoda yoxdursa — orada da olsun (bir dəfə)
            spawn(self._fill_depo(t))
        return t.path

    async def _fill_depo(self, t: Track):
        fetch = getattr(self.ctx, "music_fetch_audio", None)
        if fetch is None:
            return
        import threading
        try:
            res = await fetch(t.url, t.title, t.duration, threading.Event())
            if res.get("path"):
                with contextlib.suppress(OSError):
                    os.remove(res["path"])
        except Exception as e:
            logger.debug(f"🎙 depoya yazılmadı: {e}")

    async def _normalize(self, t: Track, src: str) -> str:
        """Səs səviyyəsini bərabərləşdirir (loudnorm). Nəticə FLAC — itkisiz, əlavə keyfiyyət itkisi olmur."""
        dst = os.path.splitext(src)[0] + "_norm.flac"
        t0 = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *cpu_pool.ffmpeg_prefix(), "ffmpeg", "-nostdin", "-y", "-hide_banner", "-loglevel", "error", "-i", src,
            "-vn", "-af", LOUDNORM, "-ar", "48000", "-ac", "2", "-c:a", "flac", "-compression_level", "0", dst,
            stdin=asyncio.subprocess.DEVNULL,            # terminal girişi bağlı qalmasın (SIGTTIN → ffmpeg donur)
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        try:
            _, err = await _deadline(proc.communicate(), T_NORM)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(OSError):
                os.remove(dst)
            logger.warning(f"🎙 🔊 Normallaşdırma {T_NORM} san.-də bitmədi — orijinal fayl oxunur ({t.title})")
            return src
        logger.info(f"🎙 🔊 normallaşdırıldı: {t.title} ({time.monotonic() - t0:.1f} san.)")
        if proc.returncode != 0 or not os.path.exists(dst):
            logger.info(f"🎙 Normallaşdırma alınmadı: {err.decode(errors='ignore')[-150:]}")
            with contextlib.suppress(OSError):
                os.remove(dst)
            return src
        with contextlib.suppress(OSError):
            os.remove(src)
        t.path = dst
        return dst

    async def _obtain_depo(self, t: Track) -> str:
        """Faylı hazırla: 1) depoda yoxdursa — yüklə (depoya da düşür)
        2) depodadırsa — Bot API və asistan hesabı PARALEL, hansı tez gəlsə (hər biri ≤ T_ROUTE)
        3) ikisi də alınmasa — YouTube-dan birbaşa (≤ T_YT). Hər yolun nəticəsi loga yazılır."""
        os.makedirs(PLAY_DIR, exist_ok=True)
        base = os.path.abspath(os.path.join(PLAY_DIR, f"{t.vid or int(time.time() * 1000)}_{id(t)}"))
        dst = base + ".m4a"
        errors = []
        if not t.file_id and t.url:
            fetch = getattr(self.ctx, "music_fetch_audio", None)
            if fetch is None:
                raise RuntimeError("music_plugin yüklənməyib")
            import threading
            t0 = time.monotonic()
            try:
                res = await _deadline(fetch(t.url, t.title, t.duration, threading.Event()), T_FETCH)
            except asyncio.TimeoutError:
                res = {}
                errors.append(f"yükləmə {T_FETCH} san.-də bitmədi")
            if res.get("path") and os.path.exists(res["path"]):
                os.replace(res["path"], dst)
                t.path, t.fmt = dst, "AAC"
                logger.info(f"🎙 ⬇️ yükləndi: {t.title} ({time.monotonic() - t0:.1f} san.)")
                return dst
            t.file_id = res.get("file_id") or t.file_id
        if t.file_id:
            # ARDICIL: əvvəl Bot API (adətən 2–4 san.), alınmasa asistan hesabı. Paralel işlədəndə asistanın
            # Telethon endirməsi yarıda ləğv olunurdu — eyni bağlantı ilə gedən yayıma qoşulmanı poza bilər
            path = None
            if not str(t.file_id).startswith("depo:"):         # yer tutucu file_id Bot API-də işləmir
                path = await self._race([("Bot API", self._dl_botapi(t, base + ".bot.m4a"))], T_ROUTE, errors, t.title)
            if not path:
                path = await self._race([("asistan", self._dl_client(t, base + ".cl.m4a"))], T_ROUTE, errors, t.title)
            if path:
                os.replace(path, dst)
                t.path, t.fmt = dst, "📦 AAC"
                return dst
        if t.vid:
            logger.info(f"🎙 depodan alınmadı ({'; '.join(errors) or '—'}) — YouTube-dan birbaşa: {t.title}")
            t0 = time.monotonic()
            try:
                path = await _deadline(self._dl_youtube(t, base), T_YT)
                logger.info(f"🎙 ▶️ YouTube-dan: {t.title} ({time.monotonic() - t0:.1f} san.)")
                return path
            except asyncio.TimeoutError:
                errors.append(f"YouTube {T_YT} san.-də bitmədi")
            except Exception as e:
                errors.append(f"YouTube: {str(e)[:80]}")
        logger.warning(f"🎙 Fayl alınmadı ({t.title}): {'; '.join(errors)}")
        raise RuntimeError("mahnı faylı alınmadı — " + ("; ".join(errors)[:200] or "mənbə yoxdur"))

    async def _race(self, routes: list, limit: float, errors: list, title: str):
        """Yolları paralel işlət, ilk uğurlunu götür, qalanlarını ləğv et. Hər nəticə loga."""
        t0 = time.monotonic()
        tasks = {asyncio.ensure_future(aw): name for name, aw in routes}
        winner = None
        try:
            pending = set(tasks)
            while pending and winner is None:
                left = limit - (time.monotonic() - t0)
                if left <= 0:
                    break
                done, pending = await asyncio.wait(pending, timeout=left, return_when=asyncio.FIRST_COMPLETED)
                for f in done:
                    name = tasks[f]
                    if f.exception() is None and f.result() and os.path.exists(f.result()):
                        winner = f.result()
                        logger.info(f"🎙 📦 {name} ilə alındı: {title} ({time.monotonic() - t0:.1f} san.)")
                        break
                    errors.append(f"{name}: {str(f.exception() or 'fayl yoxdur')[:90]}")
                    logger.info(f"🎙 📦 {name} alınmadı ({time.monotonic() - t0:.1f} san.): {errors[-1]}")
            for f, name in tasks.items():
                if not f.done():
                    errors.append(f"{name}: {limit:.0f} san.-də bitmədi")
        finally:
            for f in tasks:
                if not f.done():
                    f.cancel()
                    f.add_done_callback(lambda x: x.cancelled() or x.exception())
                elif f.exception() is None and f.result() and f.result() != winner:
                    with contextlib.suppress(OSError):
                        os.remove(f.result())
        return winner

    async def _dl_botapi(self, t: Track, path: str) -> str:
        await self.ctx.bot.download(t.file_id, destination=path, timeout=T_ROUTE)   # ≤20 MB
        return path

    async def _dl_client(self, t: Track, path: str) -> str:
        client = self.any_client()
        if client is None:
            raise RuntimeError("asistan işləmir")
        c = await asyncio.to_thread(audio_cache.get_cached, t.vid) if t.vid else None
        if not (c and c.get("chat_id") and c.get("message_id")):
            raise RuntimeError("depoda post yeri bilinmir")
        try:
            ent = await client.get_input_entity(c["chat_id"])
        except Exception:
            ref = audio_cache.depo_yt_setting() if getattr(t, "source", "") == "youtube" else audio_cache.depo_setting()
            ref = depo_shards.ref_of(c["chat_id"]) or ref or audio_cache.depo_setting()      # əlavə kanalın postu — öz kanalı ilə
            ent = await client.get_input_entity(ref if not ref.lstrip("-").isdigit() else int(ref))
        msg = await client.get_messages(ent, ids=c["message_id"])
        if not msg or not msg.media:
            raise RuntimeError("depo postu tapılmadı")
        await client.download_media(msg, file=path)
        return path

    async def _dl_youtube(self, t: Track, base: str) -> str:
        from bot.core import youtube_handler as yh
        opts = {"format": "bestaudio[ext=m4a]/bestaudio", "outtmpl": base + ".yt.%(ext)s",
                "noplaylist": True, "nopart": True, "quiet": True, "retries": 3, "fragment_retries": 5,
                **yh.JS_OPTS, **yh.cookie_opts(), **yh._px().ytdlp_opts("download")}
        res = await cpu_pool.ytdl_download(t.url or f"https://www.youtube.com/watch?v={t.vid}", opts)
        if not os.path.exists(res["path"]):
            raise RuntimeError("fayl yoxdur")
        t.path, t.fmt = res["path"], "▶️ YouTube"
        return t.path

    def prefetch(self, t: Track):
        if t.prep is None:
            t.prep = asyncio.ensure_future(self.obtain(t))
            t.prep.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)

    # ── növbə və oxutma ──
    def state(self, chat_id, title="") -> ChatState:
        st = self.chats.get(chat_id)
        if st is None:
            st = self.chats[chat_id] = ChatState(chat_id, title)
        else:
            st.stopped = False
            if title:
                st.title = title
        return st

    async def enqueue(self, chat_id: int, title: str, t: Track, progress=None, front: bool = False) -> tuple:
        """front=True (🔀 Mixplay açıqkən istifadəçi /play): mahnı Mix mahnılarının ÖNÜNƏ düşür — digər istifadəçi
        mahnılarından sonra (onların sırası qorunur), amma avtomatik Mix tracklərindən əvvəl."""
        st = self.state(chat_id, title)
        if st.idle_task:
            st.idle_task.cancel()
            st.idle_task = None
        if not t.file_id and t.url:
            if progress:
                with contextlib.suppress(Exception):
                    await progress(tr("📦 Depoda yoxdur — depoya yüklənir..."))
            await self.ensure_depo(t)
        async with st.lock:
            if st.current is None and not st.held:
                try:
                    await self._play(st, t, progress)
                except Exception:
                    self._drop_file(t)
                    raise
                return "playing", 0
            if front:
                pos = 0
                for q in st.queue:
                    if q.src == "mix":
                        break
                    pos += 1
                st.queue.insert(pos, t)
            else:
                st.queue.append(t)
                pos = len(st.queue) - 1
            self.prefetch(st.queue[0])
            self.save_active_sessions()
            return "queued", pos + 1

    async def _play(self, st: ChatState, t: Track, progress=None):
        if not self.running or getattr(st, "stopped", False) or st.chat_id not in self.chats:
            self._drop_file(t)
            return
        t0 = time.monotonic()

        async def stage(label, aw, limit, err):
            if progress:
                with contextlib.suppress(Exception):
                    await progress(label)
            s0 = time.monotonic()
            try:
                return await _deadline(aw, limit)
            except _Deadline:
                logger.warning(f"🎙 /play ilişdi: «{label}» {time.monotonic() - s0:.0f} san. "
                                f"({st.chat_id}, {t.title})")
                raise RuntimeError(err)
            except asyncio.TimeoutError as e:                  # kitabxananın öz limiti — ilişmə deyil
                logger.warning(f"🎙 «{label}»: kitabxana vaxt limiti ({time.monotonic() - s0:.0f} san.): {e!r}")
                raise RuntimeError(f"{err} ({type(e).__name__})")
            except asyncio.CancelledError:
                cur = asyncio.current_task()
                if cur is not None and cur.cancelling():       # bizi həqiqətən ləğv ediblər — ötür
                    raise
                # daxildə (py-tgcalls / Telethon) ləğv olunub — əvvəl səssizcə itirdi, indi görünür
                logger.warning(f"🎙 «{label}» daxildən ləğv olundu ({st.chat_id}, {t.title})", exc_info=True)
                raise RuntimeError(f"{err} (daxili ləğv — logda ətraflı)")
            finally:
                logger.debug(f"🎙 {label}: {time.monotonic() - s0:.1f} san.")

        bound = self.bind.get(st.chat_id)
        recording = self.rec.active(st.chat_id)
        # artıq söhbətdəyik (mahnı oxunurdu və ya ⏺ qeyd gedir) — yalnız mahnını dəyiş
        in_call = bool(bound and bound.running and (st.started or recording))
        if in_call:
            sl = bound
        else:
            sl = await stage("🔐 Qrup yoxlanılır...", self.assign(st.chat_id), T_MEMBER,
                             "Asistan hesabı cavab vermir — 🔄 Yenidən qoşul")
            if getattr(st, "stopped", False) or st.chat_id not in self.chats:
                self._drop_file(t)
                return
            await stage("🔐 Qrup yoxlanılır...", self.ensure_member(st.chat_id, sl), T_MEMBER,
                        "Asistan hesabı qrupu tapa bilmədi (Telegram yavaş cavab verir və ya FloodWait). "
                        "Bir dəqiqə sonra yenidən /play — tapılan qrup yadda qalır, növbəti dəfə dərhal olacaq")
        if getattr(st, "stopped", False) or st.chat_id not in self.chats:
            self._drop_file(t)
            return
        src = "📦 depodan götürülür..." if t.file_id else "⬇️ yüklənir..."
        if self.quality == "best" and t.vid:
            src = "💎 ən yaxşı keyfiyyət hazırlanır..." + (" (alınmasa — depodan)" if t.file_id else "")
        try:
            path = await stage(src, asyncio.shield(t.prep) if t.prep is not None else self.obtain(t),
                               T_BEST + T_ROUTE + T_YT + 30, "mahnı faylı vaxtında hazırlanmadı")
        except asyncio.CancelledError:
            self._drop_file(t)
            raise
        if getattr(st, "stopped", False) or st.chat_id not in self.chats:
            self._drop_file(t)
            return
        if not in_call:
            await stage("🎙 Səsli söhbət yoxlanılır...", self.ensure_call(st.chat_id, sl), T_MEMBER,
                        "Səsli söhbətin vəziyyəti alınmadı — bir az sonra yenidən /play")
        if getattr(st, "stopped", False) or st.chat_id not in self.chats:
            self._drop_file(t)
            return
        tried, attempt = [], 0
        while True:
            if getattr(st, "stopped", False) or st.chat_id not in self.chats:
                self._drop_file(t)
                return
            tried.append(sl)
            attempt += 1
            logger.info(f"🎙 {sl.icon} yayıma qoşulur: {st.chat_id} · {os.path.basename(path)}"
                        + (f" · bu hesabda {self.load(sl) - 1} başqa yayım" if self.load(sl) > 1 else "")
                        + (f" · cəhd {attempt}" if attempt > 1 else ""))
            if progress:
                with contextlib.suppress(Exception):
                    await progress("🎙 Səsli yayıma qoşulur...")
            libs, kind, err = _LibLog(), "", None
            st.joining = time.time()
            s0 = time.monotonic()
            try:
                if not in_call:
                    # əvvəlki yarımçıq qoşulmadan qalan vəziyyət (həm py-tgcalls, həm Telegram) — təmizlə
                    with contextlib.suppress(Exception):
                        await sl.calls.leave(st.chat_id)
                if getattr(st, "stopped", False) or st.chat_id not in self.chats:
                    self._drop_file(t)
                    return
                with libs:
                    await _deadline(sl.calls.play(st.chat_id, path), T_JOIN)
                sl.suspect = 0
                break
            except _Deadline:
                kind = "hang"                                 # BİZİM limit — həqiqi ilişmə
            except asyncio.CancelledError as e:
                cur = asyncio.current_task()
                if cur is not None and cur.cancelling():
                    raise
                kind, err = "lib", e
            except Exception as e:                            # py-tgcalls / Telegram xətası (öz timeout-u da)
                kind, err = "lib", e
            finally:
                st.joining = 0.0
            # ── alınmadı ──
            if recording:                                     # ⏺ qeyd gedir — yayımdan çıxıb qeydi qırma
                raise RuntimeError(f"Mahnı başlamadı ({type(err).__name__ if err else 'ilişdi'}) — "
                                   "⏺ qeyd davam edir, yenidən /play")
            self.bind.pop(st.chat_id, None)
            with contextlib.suppress(Exception):
                await sl.calls.leave(st.chat_id)
            took = time.monotonic() - s0
            name = type(err).__name__ if err is not None else "ilişdi"
            logger.warning(f"🎙 {sl.icon} qoşulma alınmadı ({st.chat_id}, {took:.0f} san.): {name}"
                           + (f": {str(err)[:200]}" if err is not None and str(err) else ""))
            if libs.lines:
                logger.warning(f"🎙 py-tgcalls logu (son {len(libs.lines)}):\n" + "\n".join(libs.lines))
            flood = _flood_seconds(err)
            if flood:                                         # təkrar cəhd gözləməni uzadır — dayan
                raise RuntimeError(f"Telegram asistana {flood} san. gözləmə verdi (çoxlu qoşulma cəhdi). "
                                   f"{max(1, flood // 60 + 1)} dəq. sonra yenidən /play.")
            if kind == "lib" and attempt == 1:                # kitabxana xətası — təmizləmədən sonra bir dəfə də
                await asyncio.sleep(3)
                self.bind[st.chat_id] = sl
                continue
            # başqa asistan hesabı bu qrupdadırsa — onunla sına
            alt = None
            for x in self.slots:
                if x not in tried and x.running and x.voice_enabled and (not self.max_per or self.load(x) < self.max_per):
                    with contextlib.suppress(Exception):
                        if await self.member_status(st.chat_id, x) == "member":
                            alt = x
                            break
            if alt is not None:
                logger.info(f"🎙 {alt.icon} ilə yenidən cəhd: {st.chat_id}")
                self.bind[st.chat_id] = alt
                sl, attempt = alt, 0
                await stage("🔐 Qrup yoxlanılır...", self.ensure_member(st.chat_id, sl), T_MEMBER,
                            "Asistan hesabı qrupu tapa bilmədi")
                await stage("🎙 Səsli söhbət yoxlanılır...", self.ensure_call(st.chat_id, sl), T_MEMBER,
                            "Səsli söhbətin vəziyyəti alınmadı")
                continue
            if kind == "hang":
                # bərpa YALNIZ həqiqi ilişmədə, başqa yayımı pozmadan, 10 dəq.-də ən çox bir dəfə
                if not self.load(sl) and time.time() - getattr(sl, "last_heal", 0) > 600:
                    sl.last_heal = time.time()
                    spawn(self.heal(sl, f"qoşulma {T_JOIN} san.-də bitmədi ({st.chat_id})"))
                    raise RuntimeError("Səsli yayım mühərriki ilişdi — asistan bərpa olunur, bir azdan /play.")
                raise RuntimeError("Səsli yayıma qoşulmaq alınmadı (cavab gəlmədi). Bir azdan yenidən /play.")
            diag = await self.diagnose_join(st.chat_id, sl)
            raise RuntimeError(f"{diag} [{name}]")
        if getattr(st, "stopped", False) or st.chat_id not in self.chats:
            self._drop_file(t)
            with contextlib.suppress(Exception):
                await sl.calls.leave(st.chat_id)
            return
        logger.info(f"🎙 ▶️ {t.title} ({st.chat_id}) — {time.monotonic() - t0:.1f} san.-də başladı")
        if self.volume != 100:
            await sl.calls.volume(st.chat_id, self.volume)
        prev = st.current
        st.current, st.started, st.paused = t, time.time(), False
        self.rec.on_track(st.chat_id, t)                     # ⏺ qeyd jurnalı (faylı da saxlayır)
        st.pause_time = 0.0
        st.total_paused = 0.0
        if st.ticker_task and not st.ticker_task.done():
            st.ticker_task.cancel()
        st.ticker_task = asyncio.create_task(self._progress_ticker(st))
        self.save_active_sessions()
        self.apply_throttle()
        if prev and prev is not t:
            self._drop_file(prev)
        if st.queue and not getattr(st, "stopped", False):
            self.prefetch(st.queue[0])
        if st.radio and len(st.queue) <= MIX_REFILL_AT and not getattr(st, "stopped", False):
            spawn(self._refill_and_refresh(st))
        if not getattr(st, "stopped", False) and st.chat_id in self.chats:
            await self.announce(st)

    async def _refill_and_refresh(self, st: ChatState):
        if getattr(st, "stopped", False) or st.chat_id not in self.chats:
            return
        if await self.refill(st):
            if not getattr(st, "stopped", False) and st.chat_id in self.chats:
                await self.refresh_np(st)

    async def next(self, chat_id: int, skipped=False):
        st = self.chats.get(chat_id)
        if not st or getattr(st, "stopped", False) or chat_id not in self.chats:
            return
        async with st.lock:
            if getattr(st, "stopped", False) or chat_id not in self.chats:
                return
            st.play_task = asyncio.current_task()
            try:
                if not skipped and st.queue and self._restart_pending() and not getattr(st, "stopped", False):
                    await self._hold_for_restart(st)          # ⏳ restart gözlənilir: növbə restartdan sonra davam edəcək
                    return
                if not st.queue and st.radio and not getattr(st, "stopped", False):
                    await self.refill(st)                     # ♾ Mix — növbə bitib, yenisini gətir
                while st.queue and not getattr(st, "stopped", False) and chat_id in self.chats:
                    if not self.running or getattr(st, "stopped", False):
                        return
                    t = st.queue.popleft()
                    st.active_track = t
                    try:
                        await self._play(st, t)
                        return
                    except asyncio.CancelledError:
                        self._drop_file(t)
                        return
                    except Exception as e:
                        logger.warning(f"🎙 Oxudulmadı ({t.title}): {e}")
                        if not getattr(st, "stopped", False) and chat_id in self.chats:
                            with contextlib.suppress(Exception):
                                with ui_styled("voice"):
                                    await self.ctx.bot.send_message(chat_id, f"⚠️ <b>Oxudulmadı: {escape(t.title)}</b>\n"
                                                                             f"<code>{escape(str(e)[:150])}</code>",
                                                                    parse_mode="HTML")
                        self._drop_file(t)
                    finally:
                        st.active_track = None
                if skipped:                                   # ⏭ son mahnıda — səsi dərhal kəs, yayımdan çıx
                    await self._end_chat(chat_id, announce=True, reason="növbə bitdi")
                    return
                if st.current:
                    self._drop_file(st.current)
                st.current = None
                self.rec.on_track_end(chat_id)
                if st.ticker_task and not st.ticker_task.done():
                    st.ticker_task.cancel()
                    st.ticker_task = None
                self.save_active_sessions()
                if not getattr(st, "stopped", False) and chat_id in self.chats:
                    await self.announce(st, ended=True)
                    st.idle_task = asyncio.ensure_future(self._idle_leave(chat_id))
            finally:
                if getattr(st, "play_task", None) is asyncio.current_task():
                    st.play_task = None

    @staticmethod
    def _restart_pending() -> bool:
        try:
            from bot.plugins.restart_plugin import is_restart_pending
            return is_restart_pending()
        except Exception as e:
            logger.debug("restart vəziyyəti oxunmadı: %r", e)
            return False

    async def _hold_for_restart(self, st: ChatState):
        """⏳ «Bitdikdə restart»: cari mahnı bitdi. Növbədəkilər başlamır — saxlanılır, restartdan sonra avtomatik davam edir.
        (Əks halda yayım və növbə sonsuz uzanıb restartı gecikdirərdi.) Restart ləğv olunsa — release_holds() davam etdirir."""
        if st.current:
            self._drop_file(st.current)
        st.current = None
        st.held = True
        self.rec.on_track_end(st.chat_id)
        if st.ticker_task and not st.ticker_task.done():
            st.ticker_task.cancel()
            st.ticker_task = None
        self.save_active_sessions(now=True)
        logger.info(f"🎙 ⏳ Restart gözlənilir — {st.chat_id}: {len(st.queue)} mahnı restartdan sonra davam edəcək")
        with contextlib.suppress(Exception):
            with ui_styled("voice"):
                note = await self.ctx.bot.send_message(
                    st.chat_id,
                    tr("⏳ <b>Bot restart olunur.</b>\nNövbədəki <b>{n}</b> mahnı restartdan sonra avtomatik davam edəcək.",
                       n=len(st.queue)), parse_mode="HTML")
            spawn(self._delete_later(note, 20))

    @staticmethod
    async def _delete_later(msg, delay: float):
        await asyncio.sleep(delay)
        with contextlib.suppress(Exception):
            await msg.delete()

    async def release_holds(self):
        """Restart ləğv olundu — saxlanmış yayımlar qaldığı yerdən davam edir."""
        for cid, st in list(self.chats.items()):
            if getattr(st, "held", False):
                st.held = False
                if st.current is None and st.queue:
                    spawn(self.next(cid))

    async def _idle_leave(self, chat_id):
        await asyncio.sleep(IDLE_LEAVE)
        st = self.chats.get(chat_id)
        if st and st.current is None and not st.queue:
            await self._end_chat(chat_id, announce=False)

    async def _on_end(self, chat_id):
        st = self.chats.get(chat_id)
        if st and not getattr(st, "stopped", False) and chat_id in self.chats and st.current and time.time() - st.started > 2:     # təkrar hadisələri süz
            await self.next(chat_id)

    async def _on_closed(self, chat_id, status):
        if self.rec.active(chat_id) and not self.rec.recent_toggle(chat_id):
            await self.rec.stop(chat_id, reason=f"səsli yayım bağlandı ({status})", call_gone=True)
        st = self.chats.get(chat_id)
        if st is not None and getattr(st, "joining", 0.0) and time.time() - st.joining < 90 and "LEFT" in status:   # öz təmizləməmiz / qoşulma ərəfəsi
            logger.debug(f"🎙 qoşulma zamanı hadisə ötürüldü ({status}): {chat_id}")
            return
        if chat_id in self.chats:
            logger.info(f"🎙 Yayım bağlandı ({status}): {chat_id}")
            await self._end_chat(chat_id, announce=True, reason="səsli yayım bağlandı", leave=False)

    async def _end_chat(self, chat_id, announce=True, reason="", leave=True, kb=None):
        st = self.chats.pop(chat_id, None)
        sl = self.bind.pop(chat_id, None)
        recording = self.rec.active(chat_id)
        if recording:
            self.rec.on_track_end(chat_id)
            if sl:
                self.bind[chat_id] = sl                    # ⏺ hesab qrupa bağlı qalır — qeyd onunla gedir
        self.apply_throttle()
        if st:
            st.stopped = True
            st.radio = None
            if st.idle_task:
                st.idle_task.cancel()
                st.idle_task = None
            if st.play_task and not st.play_task.done() and st.play_task is not asyncio.current_task():
                st.play_task.cancel()
                st.play_task = None
            if st.ticker_task and not st.ticker_task.done():
                st.ticker_task.cancel()
                st.ticker_task = None
            tracks_to_drop = [st.current, getattr(st, "active_track", None), *st.queue]
            st.queue.clear()
            for t in tracks_to_drop:
                if t:
                    if t.prep and not t.prep.done():
                        t.prep.cancel()
                    self._drop_file(t)
            st.current = None
            st.active_track = None
            self.save_active_sessions()
        calls_set = set()
        if sl and sl.calls:
            calls_set.add(sl.calls)
        if self.calls:
            calls_set.add(self.calls)
        for s in self.slots:
            if s.calls:
                calls_set.add(s.calls)
        if leave and recording:
            await self.rec.silence(chat_id)                # yayımda qal, musiqini kəs — qeyd davam edir
        elif leave:
            for c in calls_set:
                with contextlib.suppress(Exception):          # /stop heç vaxt ilişməsin / xəta atmasın
                    await c.leave(chat_id)
        if not st:
            return
        if announce and st.np_msg:
            with contextlib.suppress(Exception):
                reason_txt = reason if reason else tr("Səsli yayım dayandırıldı")
                with ui_styled("voice"):
                    await self.ctx.bot.edit_message_text(
                        chat_id=chat_id, message_id=st.np_msg, parse_mode="HTML",
                        text=f"{tr('⏹ <b>Yayım bitdi</b>')}\n{escape(reason_txt)}", reply_markup=kb)

    @staticmethod
    def _drop_file(t: Track):
        if t and t.path:
            with contextlib.suppress(Exception):
                voice_cache_abs = os.path.abspath(VOICE_CACHE_DIR)
                if os.path.abspath(t.path).startswith(voice_cache_abs):
                    t.path = None
                    return
            with contextlib.suppress(OSError):
                os.remove(t.path)
            t.path = None

    def clear_queue(self, chat_id: int) -> int:
        """Növbədə gözləyən mahnıları silir və öncədən yükləmələri dayandırır."""
        st = self.chats.get(chat_id)
        if not st or not st.queue:
            return 0
        n = len(st.queue)
        tracks_to_drop = list(st.queue)
        st.queue.clear()
        st.radio = None
        for t in tracks_to_drop:
            if t:
                if t.prep and not t.prep.done():
                    t.prep.cancel()
                self._drop_file(t)
        self.save_active_sessions()
        return n

    def _save_to_voice_cache(self, vid: str, src_path: str):
        """Mahnı faylını daimi səs keşi qovluğunda saxlayır."""
        # Sinxrondur (çoxmeqabaytlıq kopya) — async koddan asyncio.to_thread ilə çağırılır.
        if not vid or not src_path or not os.path.exists(src_path):
            return
        os.makedirs(VOICE_CACHE_DIR, exist_ok=True)
        ext = os.path.splitext(src_path)[1] or ".m4a"
        target = os.path.join(VOICE_CACHE_DIR, f"{vid}{ext}")
        if not os.path.exists(target):
            try:
                import shutil
                tmp = f"{target}.part"
                shutil.copyfile(src_path, tmp)             # copy2 mənbənin köhnə mtime-ını köçürürdü →
                os.replace(tmp, target)                    # təzə fayl LRU-da "ən köhnə" sayılıb ilk silinirdi
                logger.info(f"🎙 ⚡ Səs keşinə yazıldı: {vid} ({os.path.getsize(target) // 1024} KB)")
            except Exception as e:
                logger.debug(f"🎙 Keşə kopyalanmadı: {e}")
        self._clean_voice_cache()

    def _get_from_voice_cache(self, vid: str) -> str | None:
        """Keşdə bu video_id-yə uyğun fayl varsa onun yolunu qaytarır."""
        if not vid or not os.path.isdir(VOICE_CACHE_DIR):
            return None
        for ext in (".m4a", ".flac", ".webm", ".mp3", ".opus"):
            p = os.path.join(VOICE_CACHE_DIR, f"{vid}{ext}")
            if os.path.exists(p) and os.path.getsize(p) > 100 * 1024:
                with contextlib.suppress(OSError):
                    os.utime(p, None)  # LRU üçün mtime təzələnir
                return p
        return None

    def _clean_voice_cache(self):
        """Keş ölçüsü MAX_CACHE_BYTES (1.5 GB)-dan çoxdursa, ən köhnə faylları silir."""
        if not os.path.exists(VOICE_CACHE_DIR):
            return
        try:
            files = []
            total_size = 0
            for fn in os.listdir(VOICE_CACHE_DIR):
                fp = os.path.join(VOICE_CACHE_DIR, fn)
                if os.path.isfile(fp):
                    sz = os.path.getsize(fp)
                    total_size += sz
                    files.append((fp, os.path.getmtime(fp), sz))
            if total_size > MAX_CACHE_BYTES:
                files.sort(key=lambda x: x[1])
                fresh = time.time() - 3600                  # son 1 saatda yazılan / çalınan (növbədəki) qalsın
                for fp, mt, sz in files:
                    if mt > fresh or fp.endswith(".part"):
                        continue
                    with contextlib.suppress(OSError):
                        os.remove(fp)
                    total_size -= sz
                    if total_size <= MAX_CACHE_BYTES * 0.8:
                        break
        except Exception as e:
            logger.debug(f"🎙 Keş təmizləmə xətası: {e}")

    async def ensure_depo(self, t: Track):
        """Əgər mahnı depoda yoxdursa, əvvəlcə depoya yükləyir və Telegram file_id alır."""
        if t.file_id or not t.url:
            return
        fetch = getattr(self.ctx, "music_fetch_audio", None)
        if fetch is None:
            return
        import threading
        try:
            res = await _deadline(fetch(t.url, t.title, t.duration, threading.Event(), need_file_id=True, upload_depo=True), T_FETCH)
            if res.get("file_id"):
                t.file_id = res["file_id"]
                t.depo = True
                t.src = "depo"
            if res.get("artist") and res.get("track"):
                t.title = f"{res['artist']} - {res['track']}"
            if res.get("path") and os.path.exists(res["path"]):
                await asyncio.to_thread(self._save_to_voice_cache, t.vid, res["path"])
        except Exception as e:
            logger.warning(f"🎙 ensure_depo xətası ({t.title}): {e}")

    SESSIONS_QUEUE_CAP = 500                   # bir qrup üçün diskə yazılan maksimum növbə (böyük mix siyahıları)

    def _snapshot_sessions(self) -> dict:
        data = {}
        for chat_id, st in list(self.chats.items()):
            if st.current or st.queue:
                data[str(chat_id)] = {
                    "chat_id": chat_id,
                    "title": st.title,
                    "saved_at": time.time(),
                    "current": {
                        "title": st.current.title,
                        "vid": st.current.vid,
                        "url": st.current.url,
                        "duration": st.current.duration,
                        "file_id": st.current.file_id,
                        "by": st.current.by,
                        "user_id": getattr(st.current, "user_id", None),
                        "track_id": getattr(st.current, "track_id", None),
                    } if st.current else None,
                    "queue": [
                        {
                            "title": q.title,
                            "vid": q.vid,
                            "url": q.url,
                            "duration": q.duration,
                            "file_id": q.file_id,
                            "by": q.by,
                            "user_id": getattr(q, "user_id", None),
                            "track_id": getattr(q, "track_id", None),
                        } for q in list(st.queue)[:self.SESSIONS_QUEUE_CAP]
                    ],
                }
        return data

    @staticmethod
    def _write_sessions(data: dict):
        """indent olmadan (C-sürətli json) + atomik yazı — indent=2 saf-Python kodlayıcıya düşüb event loop-u dondururdu."""
        try:
            os.makedirs("data", exist_ok=True)
            tmp = SESSIONS_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(json.dumps(data, ensure_ascii=False))
            os.replace(tmp, SESSIONS_FILE)
        except Exception as e:
            logger.debug(f"Sessiyalar yazılmadı: {e}")

    def save_active_sessions(self, now: bool = False):
        """Aktiv yayım və növbələri data/voice_sessions.json faylına yazır.
        Çağırışlar birləşdirilir (≈1 san.-də bir yazı): hər mahnı əlavəsində böyük növbəni təkrar-təkrar
        yığmaq event loop-u dondururdu. now=True — dərhal və sinxron (restart / dayandırma)."""
        if now:
            self._sess_pending = False
            self._write_sessions(self._snapshot_sessions())
            return
        if getattr(self, "_sess_pending", False):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._write_sessions(self._snapshot_sessions())
            return
        self._sess_pending = True

        async def _later():
            await asyncio.sleep(1.0)
            if not self._sess_pending:                  # arada now=True ilə yazılıb
                return
            self._sess_pending = False
            await asyncio.to_thread(self._write_sessions, self._snapshot_sessions())

        self._sess_task = loop.create_task(_later())

    # ── ♻️ restartdan sonra avtomatik bərpa ──
    AUTO_RESUME_MAX_AGE = 600           # planlı restartdan bu qədər saniyə keçməyibsə — avtomatik bərpa

    @staticmethod
    def _planned_restart() -> bool:
        """Planlı restart bayrağı təzədirmi (stop(for_restart) yazır; köhnə qalıq çökmədən sonra səhvən işə düşməsin)."""
        try:
            with open(PLANNED_FLAG, encoding="utf-8") as f:
                return time.time() - float(f.read().strip() or 0) < Assistant.AUTO_RESUME_MAX_AGE
        except (OSError, ValueError):
            return False

    def prepare_recovery(self):
        """Açılışın əvvəlində (asistan başlamamış, mesajlar emal olunmazdan öncə) çağırılır: planlı restartdan sonra
        bərpa olunacaq qrupları qeyd edir. Bu qruplara gələn /skip, /end, /play komandaları bərpa bitənə qədər gözləyir
        (restart zamanı yazılmış komandalar Telegram-da saxlanılır və açılışdan sonra gəlir)."""
        self._recover_events = {}
        if not self._planned_restart():
            return
        try:
            with open(SESSIONS_FILE, encoding="utf-8") as f:
                sessions = json.load(f)
        except (OSError, ValueError):
            return
        now = time.time()
        for k, v in sessions.items():
            if now - v.get("saved_at", 0) < self.AUTO_RESUME_MAX_AGE and (v.get("current") or v.get("queue")):
                self._recover_events[int(k)] = asyncio.Event()

    async def wait_resumed(self, chat_id: int, timeout: float = 45.0):
        """Qrup restartdan sonra bərpa olunur — bitənə qədər gözləyir (komanda boş vəziyyətə düşməsin)."""
        ev = getattr(self, "_recover_events", {}).get(chat_id)
        if ev is not None and not ev.is_set():
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(ev.wait(), timeout)

    @staticmethod
    def _track_from(d: dict) -> Track:
        return Track(d["title"], d.get("vid"), d.get("url"), d.get("duration") or 0, d.get("file_id"),
                     d.get("by") or "Bərpa", depo=bool(d.get("file_id")),
                     src="depo" if d.get("file_id") else "yt", user_id=d.get("user_id"), track_id=d.get("track_id"))

    async def restore_chat(self, chat_id: int, data: dict) -> bool:
        """Saxlanmış yayımı qaytarır: oxunan mahnı yenidən başlayır, növbə olduğu kimi bərpa olunur."""
        cur = data.get("current")
        queue = list(data.get("queue") or [])
        first = cur or (queue.pop(0) if queue else None)
        if not first:
            return False
        await self.enqueue(chat_id, data.get("title") or "", self._track_from(first))
        st = self.chats.get(chat_id)
        if not st:
            return False
        for q in queue:
            st.queue.append(self._track_from(q))
        if st.queue:
            self.prefetch(st.queue[0])
        self.save_active_sessions()
        await self.refresh_np(st)
        return True

    async def _ask_recovery(self, chat_id: int, data: dict):
        cur = data.get("current")
        queue = data.get("queue") or []
        lines = [
            tr("⚠️ <b>Bot yenidən başladıldı</b>"),
            "",
            tr("📻 <i>Restartdan əvvəl səsli yayımda mahnılar var idi:</i>"),
        ]
        if cur:
            lines.append(f"🎙 <b>Son oxunan:</b> <code>{escape(cur['title'])}</code>")
        if queue:
            lines.append(f"\n📋 <b>Növbədə:</b> {len(queue)} mahnı")
            for i, q in enumerate(queue[:4], 1):
                lines.append(f"{i}. {escape(q['title'])}")
            if len(queue) > 4:
                lines.append(tr("<i>... və daha {n} mahnı</i>", n=len(queue) - 4))
        lines.append("")
        lines.append(tr("<i>Səsli yayım təkrar açılsın və növbə bərpa edilsin?</i>"))
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=tr("▶️ Yayımı bərpa et"), callback_data=f"vr:resume:{chat_id}"),
            InlineKeyboardButton(text=tr("✖️ Ləğv et"), callback_data=f"vr:dismiss:{chat_id}"),
        ]])
        with contextlib.suppress(Exception):
            await self.ctx.bot.send_message(chat_id, "\n".join(lines), parse_mode="HTML", reply_markup=kb)

    async def _auto_restore(self, items: dict):
        sem = asyncio.Semaphore(3)

        async def one(str_cid: str, data: dict):
            cid = int(str_cid)
            ok = False
            try:
                async with sem:
                    ok = await self.restore_chat(cid, data)
            except Exception as e:
                logger.warning(f"🎙 Avtomatik bərpa alınmadı ({cid}): {e}")
            finally:
                ev = getattr(self, "_recover_events", {}).get(cid)
                if ev is not None:
                    ev.set()                                   # gözləyən /skip /play komandaları davam etsin
            if ok:
                logger.info(f"🎙 ♻️ Yayım avtomatik bərpa olundu ({cid})")
                with contextlib.suppress(Exception):
                    note = await self.ctx.bot.send_message(
                        cid, tr("♻️ <b>Bot yenidən başladı — yayım bərpa olundu.</b>"), parse_mode="HTML")
                    await asyncio.sleep(10)
                    await note.delete()
            else:
                self._pending_recoveries[str_cid] = data       # alınmadı — əvvəlki kimi düymə ilə
                await self._ask_recovery(cid, data)

        await asyncio.gather(*(one(k, v) for k, v in items.items()))

    async def check_restart_recovery(self):
        """Restartdan sonra: planlı restartdırsa yayım avtomatik bərpa olunur; deyilsə (çökmə) qrupa təsdiq göndərilir."""
        if not os.path.exists(SESSIONS_FILE):
            return
        try:
            with open(SESSIONS_FILE, encoding="utf-8") as f:
                sessions = json.load(f)
        except Exception:
            sessions = {}
        with contextlib.suppress(Exception):
            os.remove(SESSIONS_FILE)
        planned = self._planned_restart()
        with contextlib.suppress(OSError):
            os.remove(PLANNED_FLAG)

        now = time.time()
        valid = {k: v for k, v in sessions.items() if now - v.get("saved_at", 0) < 3600 * 3}
        if not valid:
            return
        self._pending_recoveries = {}
        auto = {}
        for str_cid, data in valid.items():
            if not (data.get("current") or data.get("queue")):
                continue
            if planned and int(str_cid) in getattr(self, "_recover_events", {}):
                auto[str_cid] = data
            else:
                self._pending_recoveries[str_cid] = data
        for str_cid, data in list(self._pending_recoveries.items()):
            await self._ask_recovery(int(str_cid), data)
        if auto:
            logger.info(f"🎙 ♻️ Planlı restart: {len(auto)} yayım avtomatik bərpa olunur")
            await self._auto_restore(auto)

    async def reload_chat(self, chat_id: int):
        """Səsli söhbət axınını hot-reconnect edir, növbəni saxlayır."""
        st = self.chats.get(chat_id)
        if not st:
            return
        sl = self.slot_for(chat_id)
        if sl and sl.calls:
            with contextlib.suppress(Exception):
                await sl.calls.leave(chat_id)
        await asyncio.sleep(1)
        if st.current:
            await self._play(st, st.current)
        elif st.queue:
            await self.next(chat_id)

    async def _progress_ticker(self, st: ChatState):
        """Hər 5 saniyədən bir 'İndi oxunur' mesajındakı saniyələri və progress barı yeniləyir."""
        chat_id = st.chat_id
        while not getattr(st, "stopped", False) and chat_id in self.chats and st.current is not None:
            await asyncio.sleep(5)
            if getattr(st, "stopped", False) or chat_id not in self.chats or st.current is None:
                break
            if not st.paused and st.np_msg:
                with contextlib.suppress(Exception):
                    await self.refresh_np(st)

    # ── "indi oxunur" mesajı ──
    def np_view(self, st: ChatState, ended=False):
        if ended or st.current is None:
            return tr("✅ <b>Növbə bitdi</b>\n/play ilə yeni mahnı əlavə et"), None
        t = st.current
        src = {"depo": "📦 depo", "link": "🔗 link", "ytm": "🎵 YT Music", "yt": "▶️ YouTube",
               "mix": "🔀 Mix"}.get(t.src, "")
        q = " · ".join(x for x in (t.fmt, "🔊 norm" if self.normalize and (t.path or "").endswith("_norm.flac") else "") if x)
        state_label = tr("Fasilə") if st.paused else tr("İndi oxunur")
        elapsed = st.elapsed()
        prog = progress_bar(elapsed, t.duration)
        pct = f"({int((elapsed / (t.duration or 1)) * 100)}%)" if t.duration else ""
        dur_line = f"⏱ <b>{fmt_dur(elapsed)}</b> {prog} <b>{fmt_dur(t.duration)}</b> {pct}".strip()
        lines = [f"{'⏸' if st.paused else '🎙'} <b>{state_label}</b>"
                 + (f"  <i>{q}</i>" if q else ""),
                 f"🎵 <b>{escape(t.title[:80])}</b>",
                 dur_line + (f" · {src}" if src else "") + ("  📦" if t.depo and t.src != "depo" else "")]
        if t.by:
            lines.append(f"👤 <i>{escape(t.by)}</i>")
        if st.radio:
            lines.append(tr("🔀 <b>Mix rejimi</b>") + (tr(" · ♾ bitəndə özü davam edəcək") if self.mix_auto else ""))
        if st.queue:
            lines.append("\n" + tr("📋 <b>Növbədə: {n}</b>", n=len(st.queue)))
            for i, q in enumerate(list(st.queue)[:5], 1):
                lines.append(f"{i}. {escape(q.title[:50])} <i>({fmt_dur(q.duration)})</i>")
            if len(st.queue) > 5:
                lines.append(tr("<i>... və daha {n}</i>", n=len(st.queue) - 5))
        kb_rows = [
            [
                InlineKeyboardButton(text="▶️" if st.paused else "⏸", callback_data="vp:toggle"),
                InlineKeyboardButton(text="⏭", callback_data="vp:skip"),
                InlineKeyboardButton(text="⏹", callback_data="vp:stop"),
                InlineKeyboardButton(text="🔄", callback_data="vp:refresh"),
            ],
            [
                InlineKeyboardButton(
                    text=tr("🔀 Mix: Aktiv ✅") if st.radio else tr("🔀 Mixplay"),
                    callback_data="vp:radio"
                ),
                InlineKeyboardButton(
                    text=tr("🗑 Növbəni təmizlə ({n})", n=len(st.queue)) if st.queue else tr("🗑 Növbəni təmizlə"),
                    callback_data="vp:clear"
                ),
            ],
        ]
        kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
        return "\n".join(lines), kb

    async def announce(self, st: ChatState, ended=False):
        text, kb = self.np_view(st, ended)
        bot = self.ctx.bot
        with ui_styled("voice"):
            if st.np_msg and not ended and st.current is not None:
                # yeni mahnı — köhnə mesaj silinir, yenisi aşağıda görünsün
                with contextlib.suppress(Exception):
                    await bot.delete_message(st.chat_id, st.np_msg)
                st.np_msg = None
            if st.np_msg:
                with contextlib.suppress(Exception):
                    await bot.edit_message_text(chat_id=st.chat_id, message_id=st.np_msg, text=text,
                                                parse_mode="HTML", reply_markup=kb)
                    return
            with contextlib.suppress(Exception):
                msg = await bot.send_message(st.chat_id, text, parse_mode="HTML", reply_markup=kb,
                                             disable_notification=True)
                st.np_msg = msg.message_id

    async def refresh_np(self, st: ChatState):
        if not st.np_msg:
            return
        text, kb = self.np_view(st)
        with contextlib.suppress(Exception):
            with ui_styled("voice"):
                await self.ctx.bot.edit_message_text(chat_id=st.chat_id, message_id=st.np_msg, text=text,
                                                     parse_mode="HTML", reply_markup=kb)

    # ── menyu ──
    def label(self) -> str:
        if not self.enabled:
            return "🎙 Canlı yayım · 🔴"
        if not self.running:
            return "🎙 Canlı yayım · ⚠️"
        n = sum(1 for s in self.chats.values() if s.current)
        return "🎙 Canlı yayım · 🟢" + (f" {n} yayım" if n else "")


