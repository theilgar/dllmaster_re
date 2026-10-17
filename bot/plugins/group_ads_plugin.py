"""
📣 Qrup reklamı — bot/plugins/group_ads_plugin.py

Əsas userbot (context.telethon_client) seçilmiş qruplardakı insanların (üzvlərin) ID-lərini toplayır,
canlı yayım userbotu (context.voice_assistant) isə həmin insanlara reklam mesajını göndərir.

İdarə: /menu → 🎙 Canlı Yayım & VC → 📣 Qrup reklamı (bot/menus/views/group_ads.py).

Nə edir:
  • Qrup siyahısını çıxarır (əsas userbot və asistan hesablarından; 10 dəq. keşlənir)
  • Yalnız panelində seçilmiş qruplara müraciət edir (opt-in)
  • Əsas userbot seçilmiş qruplardan bot olmayan, aktiv üzvlərin ID-lərini toplayır
  • Canlı yayım userbotu (asistan hesabı) həmin üzvlərə reklam mesajını göndərir
  • Üzvlər və qruplar arası fasilə (təsadüfi sapma ilə, ən azı 20 san.), təkrar üçün gözləmə, dövr başına limit
  • FloodWait gözləyir / uzundursa həmin hesabı dayandırır; PeerFlood (hesab spam-limitə düşüb) — dövr dərhal bitir
  • ⏹ Dayandır düyməsi, canlı gedişat, bitəndə creator-a hesabat
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time

from common import group_ads as ads
from common.utilities import spawn

logger = logging.getLogger(__name__)

CATALOG_TTL = 600.0
DIALOGS_TIMEOUT = 90
FLOOD_RETRY_MAX = 90                                   # bu qədər saniyəyə qədər FloodWait gözlənilir


def classify(exc: Exception) -> str:
    """Telethon xətası → "flood" | "peerflood" | "skip:<səbəb>" | "fail"."""
    n = type(exc).__name__
    if n == "PeerFloodError":
        return "peerflood"
    if n in ("FloodWaitError", "FloodError"):
        return "flood"
    if n == "SlowModeWaitError":
        return "skip:slow mode"
    if n in ("UserPrivacyRestrictedError", "UserNotMutualContactError"):
        return "skip:məxfilik"
    if n in ("UserIsBlockedError", "YouBlockedUserError"):
        return "skip:blok"
    if n == "InputUserDeactivatedError":
        return "skip:deaktiv"
    if n == "PeerIdInvalidError" or isinstance(exc, ValueError):
        return "skip:tapılmadı"
    if n in ("ChatWriteForbiddenError", "ChatRestrictedError", "UserBannedInChannelError", "ChatAdminRequiredError",
             "ChannelPrivateError", "UserNotParticipantError", "ChatSendPlainForbiddenError", "ChatGuestSendForbiddenError"):
        return "skip:yazmaq olmur"
    return "fail"


class AdRunner:
    def __init__(self, context):
        self.ctx = context
        self.stop_event = asyncio.Event()
        self.task: asyncio.Task | None = None
        self._catalog: tuple[float, list] = (0.0, [])
        self.state = self._fresh()

    @staticmethod
    def _fresh() -> dict:
        return {"running": False, "total": 0, "done": 0, "sent": 0, "skipped": 0, "failed": 0, "current": "",
                "started": 0.0, "reasons": {}, "aborted": ""}

    # ── hesablar ──
    def main_client(self):
        """Əsas userbot client-i (context.telethon_client)."""
        cl = getattr(self.ctx, "telethon_client", None)
        with contextlib.suppress(Exception):
            if cl is not None and cl.is_connected():
                return cl
        return None

    def _effective_main_client(self):
        """Qrup üzvlərini çəkmək üçün client (əsas userbot, tapılmasa asistan hesabı)."""
        cl = self.main_client()
        if cl is not None:
            return cl
        slots = self.slots()
        if slots:
            cl = getattr(slots[0], "client", None)
            with contextlib.suppress(Exception):
                if cl is not None and cl.is_connected():
                    return cl
        return None

    def slots(self) -> list:
        """Canlı yayım userbotunun (asistan hesablarının) slotları."""
        va = getattr(self.ctx, "voice_assistant", None)
        out = []
        for sl in getattr(va, "slots", []):
            cl = getattr(sl, "client", None)
            with contextlib.suppress(Exception):
                if cl is not None and cl.is_connected() and getattr(sl, "voice_enabled", False):
                    out.append(sl)
        return out

    def slot_indexes(self) -> list:
        return [sl.idx for sl in self.slots()]

    # ── qrup siyahısı ──
    async def catalog(self, force: bool = False) -> list:
        """[{id, title, slot}] — qrupların siyahısı."""
        ts, cached = self._catalog
        if cached and not force and time.monotonic() - ts < CATALOG_TTL:
            return cached
        seen, out = set(), []

        # 1️⃣ Əsas userbotun qrupları
        main_cl = self.main_client()
        if main_cl is not None:
            try:
                async with asyncio.timeout(DIALOGS_TIMEOUT):
                    async for d in main_cl.iter_dialogs():
                        if not getattr(d, "is_group", False) or d.id in seen:
                            continue
                        ent = d.entity
                        if any(getattr(ent, a, False) for a in ("left", "kicked", "deactivated")):
                            continue
                        seen.add(d.id)
                        out.append({"id": int(d.id), "title": d.name or str(d.id), "slot": "all"})
            except Exception as e:
                logger.warning(f"📣 Əsas userbot dialoqları alınmadı: {e}")

        # 2️⃣ Asistan hesablarının qrupları
        for sl in self.slots():
            try:
                async with asyncio.timeout(DIALOGS_TIMEOUT):
                    async for d in sl.client.iter_dialogs():
                        if not getattr(d, "is_group", False) or d.id in seen:
                            continue
                        ent = d.entity
                        if any(getattr(ent, a, False) for a in ("left", "kicked", "deactivated")):
                            continue
                        rights = getattr(ent, "default_banned_rights", None)
                        admin = getattr(ent, "creator", False) or getattr(ent, "admin_rights", None)
                        if rights is not None and getattr(rights, "send_messages", False) and not admin:
                            continue
                        seen.add(d.id)
                        out.append({"id": int(d.id), "title": d.name or str(d.id), "slot": sl.idx})
            except Exception as e:
                logger.warning(f"📣 Asistan {getattr(sl, 'idx', '?')} dialoqları alınmadı: {e}")

        out.sort(key=lambda g: (g["title"] or "").lower())
        self._catalog = (time.monotonic(), out)
        return out

    def targets(self, catalog: list) -> list:
        """Göndəriləcək qruplar: seçilmiş + gözləmə bitmiş + hesab filtri; dövr limiti ilə."""
        s = ads.settings()
        sel = ads.selected()
        out = []
        for g in catalog:
            if g["id"] not in sel or ads.on_cooldown(g["id"], s["cooldown"]):
                continue
            if s["slot"] != "all" and str(g.get("slot", "all")) != "all" and str(g["slot"]) != s["slot"]:
                continue
            out.append(g)
        return out[:min(s["max"], ads.HARD_MAX)]

    # ── qrup üzvlərini çəkmək (əsas userbot) ──
    async def fetch_group_members(self, chat_id: int) -> list[dict]:
        """Əsas userbot vasitəsilə qrupdakı üzvlərin ID və istifadəçi məlumatlarını toplayır."""
        cl = self._effective_main_client()
        if cl is None:
            return []
        members = []
        seen = set()
        try:
            if hasattr(cl, "iter_participants"):
                async with asyncio.timeout(DIALOGS_TIMEOUT):
                    async for user in cl.iter_participants(chat_id):
                        if isinstance(user, int):
                            uid = user
                            if uid not in seen:
                                seen.add(uid)
                                members.append({"id": uid, "username": None, "first_name": str(uid)})
                            continue
                        if getattr(user, "bot", False) or getattr(user, "deleted", False) or getattr(user, "is_self", False):
                            continue
                        uid = getattr(user, "id", None)
                        if uid and uid not in seen:
                            seen.add(int(uid))
                            members.append({
                                "id": int(uid),
                                "username": getattr(user, "username", None),
                                "first_name": getattr(user, "first_name", "") or "",
                            })
            elif hasattr(cl, "get_participants"):
                async with asyncio.timeout(DIALOGS_TIMEOUT):
                    parts = await cl.get_participants(chat_id)
                    for user in parts:
                        if isinstance(user, int):
                            uid = user
                            if uid not in seen:
                                seen.add(uid)
                                members.append({"id": uid, "username": None, "first_name": str(uid)})
                            continue
                        if getattr(user, "bot", False) or getattr(user, "deleted", False) or getattr(user, "is_self", False):
                            continue
                        uid = getattr(user, "id", None)
                        if uid and uid not in seen:
                            seen.add(int(uid))
                            members.append({
                                "id": int(uid),
                                "username": getattr(user, "username", None),
                                "first_name": getattr(user, "first_name", "") or "",
                            })
        except Exception as e:
            logger.warning(f"📣 Qrup {chat_id} üzvləri alına bilmədi: {type(e).__name__}: {e}")
        return members

    # ── idarə ──
    def running(self) -> bool:
        return bool(self.task and not self.task.done())

    def stop(self) -> bool:
        if self.running():
            self.stop_event.set()
            return True
        return False

    async def start(self, on_progress=None) -> dict:
        """Dövrü fonda başladır → {"ok": bool, "total": N, "error": ...}"""
        if self.running():
            return {"ok": False, "error": "artıq gedir"}
        if not self.slots():
            return {"ok": False, "error": "işləyən canlı yayım hesabı yoxdur"}
        if self._effective_main_client() is None:
            return {"ok": False, "error": "işləyən əsas userbot yoxdur"}
        targets = self.targets(await self.catalog())
        if not targets:
            return {"ok": False, "error": "göndəriləcək qrup yoxdur (seçilməyib / gözləmədədir / hesab filtri)"}
        self.stop_event = asyncio.Event()
        self.state = self._fresh()
        self.state.update(running=True, total=len(targets), started=time.time())
        self.task = spawn(self._run(targets, on_progress))
        return {"ok": True, "total": len(targets)}

    async def _sleep(self, seconds: float) -> bool:
        """Dayandırmaya həssas yuxu → True: dayandırılıb."""
        try:
            await asyncio.wait_for(self.stop_event.wait(), timeout=seconds)
            return True
        except asyncio.TimeoutError:
            return False

    async def _bot_username(self) -> str:
        try:
            return (await self.ctx.bot.me()).username or ""
        except Exception:
            return ""

    async def _send(self, client, user_id: int, text: str, username: str | None = None) -> str:
        for attempt in (1, 2):
            try:
                try:
                    await client.send_message(user_id, text, parse_mode="html", link_preview=False)
                except ValueError:
                    if username:
                        await client.send_message(username, text, parse_mode="html", link_preview=False)
                    else:
                        raise
                return "ok"
            except Exception as e:
                kind = classify(e)
                if kind == "flood" and attempt == 1:
                    wait = int(getattr(e, "seconds", 0) or 0)
                    if 0 < wait <= FLOOD_RETRY_MAX:
                        if await self._sleep(wait + 1):
                            return "stopped"
                        continue
                if kind == "fail":
                    logger.warning(f"📣 {user_id}: göndərilmədi — {type(e).__name__}: {e}")
                return kind
        return "flood"

    async def _run(self, targets: list, on_progress):
        st, s = self.state, ads.settings()
        text = ads.render(s["text"], await self._bot_username())
        all_slots = self.slots()
        if s["slot"] != "all":
            all_slots = [sl for sl in all_slots if str(sl.idx) == s["slot"]]

        clients = {sl.idx: sl.client for sl in all_slots}
        blocked: set = set()
        max_limit = min(s["max"], ads.HARD_MAX)
        slot_idx = 0

        try:
            for i, g in enumerate(targets):
                if self.stop_event.is_set() or st["sent"] >= max_limit:
                    break

                st["current"] = g["title"]

                # Qrup üçün canlı yayım hesabı təyin edirik
                target_slot = g.get("slot")
                if target_slot is not None and target_slot != "all":
                    if target_slot in blocked:
                        self._count(st, "skip:hesab limitdədir")
                        if on_progress:
                            with contextlib.suppress(Exception):
                                await on_progress()
                        continue
                    client = clients.get(target_slot)
                    if client is None:
                        self._count(st, "skip:hesab limitdədir")
                        if on_progress:
                            with contextlib.suppress(Exception):
                                await on_progress()
                        continue
                    cur_slot_idx = target_slot
                else:
                    available = [sl for sl in all_slots if sl.idx not in blocked]
                    if not available:
                        st["aborted"] = "bütün canlı yayım hesabları limitdədir"
                        break
                    chosen = available[slot_idx % len(available)]
                    slot_idx += 1
                    client = chosen.client
                    cur_slot_idx = chosen.idx

                members = await self.fetch_group_members(g["id"])
                if not members:
                    self._count(st, "skip:üzv tapılmadı")
                    ads.mark_sent(g["id"])
                    if on_progress:
                        with contextlib.suppress(Exception):
                            await on_progress()
                    continue

                for m in members:
                    if self.stop_event.is_set() or st["sent"] >= max_limit:
                        break

                    uid = m["id"]
                    if ads.on_cooldown(uid, s["cooldown"]):
                        continue

                    uname = f" (@{m['username']})" if m.get("username") else ""
                    name = m.get("first_name") or str(uid)
                    st["current"] = f"{name}{uname} ({g['title']})"

                    res = await self._send(client, uid, text, username=m.get("username"))
                    if res == "stopped":
                        break
                    if res == "ok":
                        ads.mark_sent(uid)
                        st["sent"] += 1
                        st["done"] += 1
                    else:
                        self._count(st, res)
                        if res == "peerflood":
                            st["aborted"] = "hesab spam-limitə düşüb (PeerFlood) — dövr dayandırıldı"
                            break
                        if res == "flood":
                            blocked.add(cur_slot_idx)
                            break

                    if st["sent"] < max_limit and not self.stop_event.is_set():
                        if await self._sleep(ads.next_delay(s["delay"])):
                            break

                ads.mark_sent(g["id"])
                if on_progress:
                    with contextlib.suppress(Exception):
                        await on_progress()

                if st["aborted"]:
                    break

                if i < len(targets) - 1 and await self._sleep(ads.next_delay(s["delay"])):
                    break

        except asyncio.CancelledError:
            st["aborted"] = "ləğv edildi"
            raise
        except Exception as e:
            logger.error(f"📣 Reklam dövrü xətası: {e}", exc_info=True)
            st["aborted"] = f"xəta: {e}"
        finally:
            st["running"] = False
            st["stopped"] = self.stop_event.is_set()
            took = int(time.time() - st["started"])
            report = {"sent": st["sent"], "skipped": st["skipped"], "failed": st["failed"], "total": st["total"],
                      "took": took, "stopped": bool(st.get("stopped")), "aborted": st["aborted"],
                      "reasons": dict(st["reasons"]), "ts": int(time.time())}
            ads.save_report(report)
            logger.info(f"📣 Qrup reklamı bitdi: {report}")
            with contextlib.suppress(Exception):
                if on_progress:
                    await on_progress()
            await self._notify_creator(report)

    @staticmethod
    def _count(st: dict, res: str):
        st["done"] += 1
        reason = res.split(":", 1)[1] if ":" in res else res
        st["reasons"][reason] = st["reasons"].get(reason, 0) + 1
        st["skipped" if res.startswith("skip") or res in ("flood",) else "failed"] += 1

    async def _notify_creator(self, r: dict):
        creator = getattr(self.ctx, "creator_id", None)
        if not creator:
            return
        lines = ["📣 <b>Qrup reklamı dövrü bitdi</b>",
                 f"✅ Göndərildi: <b>{r['sent']}</b> · ⏭ Keçildi: {r['skipped']} · ❌ Xəta: {r['failed']} "
                 f"(cəmi {r['total']}, {r['took']} san.)"]
        if r["stopped"]:
            lines.append("⏹ Əl ilə dayandırıldı")
        if r["aborted"]:
            lines.append(f"⚠️ {r['aborted']}")
        if r["reasons"]:
            lines.append("<i>" + " · ".join(f"{k}: {v}" for k, v in r["reasons"].items()) + "</i>")
        with contextlib.suppress(Exception):
            await self.ctx.bot.send_message(creator, "\n".join(lines), parse_mode="HTML")


def setup(context):
    old = getattr(context, "group_ads", None)
    if old is not None:
        old.stop()
    context.group_ads = AdRunner(context)
    logger.info("📣 Qrup reklamı plugin-i hazırdır (/menu → 🎙 Canlı Yayım & VC → 📣 Qrup reklamı)")


def teardown(context):
    runner = getattr(context, "group_ads", None)
    if runner is not None:
        runner.stop()
        if runner.task and not runner.task.done():
            runner.task.cancel()
