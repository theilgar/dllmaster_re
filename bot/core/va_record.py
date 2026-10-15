"""
⏺ Asistan qeydi — bot/core/va_record.py

🎙 Canlı yayım asistanı (bot/plugins/voice_assistant_plugin.py) həm musiqi oxudur, həm yayımı qeyd edir —
əsas userbot hesabı yayıma girmir. Nəticə: yayımda səslənən HƏR ŞEY (danışanlar + musiqi) bir mp3-də.

İdarə paneli: qrupda /vrec (bot) və ya .vcrec (userbot) — eyni panel:
  ⏺ Qeydə başla / ⏹ Saxla və göndər · 📍 Nişan
  ✋ Manual / ⚡ Avtomatik — avtomatikdə qrupda səsli yayım başlayan kimi asistan qoşulub qeydə başlayır,
     yayım bitəndə qeydi göndərir (restartdan sonra yayım hələ gedirsə — qeyd yenidən başlayır)
  🎚 musiqi səviyyəsi · 🎧 mp3 keyfiyyəti · 📝 söhbət jurnalı · 🗣 ayrı treklər · 📤 hədəf · 👁 panel harada
Panel default olaraq botun şəxsi çatına gəlir (qrupda iz qalmır); şəxsi çatda /vrec — bütün qruplar.

İki Telegram xüsusiyyəti həllin formasını təyin edir:
  • heç kim öz göndərdiyi səsi geri almır → asistan öz oxutduğu musiqini eşitmir;
  • səs yalnız kimsə danışanda gəlir, sükut göndərilmir → səsi birbaşa fayla yazsaq vaxt "sıxılır".
Ona görə:
  1) danışanlar — kadr-kadr (StreamFrames, hər iştirakçı öz ssrc-si ilə); hər iştirakçı öz ffmpeg FLAC
     kodlayıcısına gedir, aradakı sükut real vaxta görə doldurulur → fayllar qeydin saatı ilə düzülür.
     ssrc → ad: phone.GetGroupParticipants (caption və ayrı treklərin adları üçün)
  2) musiqi    — hər mahnı / pauza / davam / keçid anı timeline.json-a düşür; oxudulan fayl qovluğa hard link
  3) son       — ffmpeg hamısını öz vaxtında üst-üstə qoyur → bir mp3 (musiqi orijinal fayldan)
  + 📝 söhbət jurnalı — asistan hesabı qrupdakı mesajları qeyd vaxtı ilə yazır (.html / .txt)
  + 🗣 ayrı treklər   — hər danışan ayrıca mp3, zip-də (montaj üçün)

Restart / çökmə: timeline.json + FLAC-lar diskdə qalır → növbəti başlanğıcda recover() bitirib göndərir.

Hara göndərilir (📤 hədəf):
  🎯 .rec hədəfi — 📱 əsas userbot qoşulubsa .rec-in 🎙 səs hədəfinə, alınmasa ↓
  💬 şəxsi çat   — bot ilə creator-a (≤ 49 MB), böyükdürsə asistan hesabı ilə creator-a
  alınmasa — fayl data/records/-da qalır, creator-a yeri yazılır.
"""
import asyncio
from collections import Counter
import contextlib
import importlib
import json
import logging
import os
import re
import shutil
import subprocess
import time
import zipfile

from bot.core import panel as ui
from html import escape
from common.utilities import format_bytes, format_clock as fmt_dur, tg_media_label, tg_name
from common.database import Settings
from common.kv import next_of

logger = logging.getLogger(__name__)

REC_ROOT = os.path.join("data", "va_records")         # işçi qovluqlar (qeyd bitib göndərilənə qədər)
OUT_DIR = os.path.join("data", "records")             # hazır fayllar (.rec qeydləri ilə eyni yer)
MUSIC_STEPS = [0, 30, 50, 70, 100]
BITRATES = [128, 192, 256, 320]
TIMER_STEPS = [0, 15, 30, 60, 120]
KEEP_STEPS = [3, 7, 14, 30, 0]          # data/records-da hazır faylları neçə gün saxla (0 — həmişə)
DEST_OPTIONS = ["target", "dm", "here"]
TOGGLE_GUARD = 3.0          # qeyd açılıb-bağlananda bu müddətdə gələn "axın bitdi" hadisəsi mahnını keçirməsin
MIN_PIECE = 0.3             # bundan qısa musiqi parçası atılır
OUTBOX = os.path.join(OUT_DIR, "outbox.json")         # asistan olmadığı üçün göndərilməmiş fayllar
ASSISTANT_WAIT = 90         # göndərmədə asistanın qoşulmasını ən çox bu qədər gözlə (san.)


class AssistantUnavailable(RuntimeError):
    """Səs qeydlərini yalnız asistan göndərir (bot API-nin 50 MB limiti var) — o qoşulu deyil."""
SR, CH = 48000, 1           # danışanların səsi: 48 kHz mono s16le (Telegram yayımının öz formatı)
GAP = 0.25                  # kadrlar arası bundan uzun boşluq — sükut kimi doldurulur
JITTER = 0.4                # boşluqdan sonra bu qədər gözlə: gecikmiş kadrlar dəstəsi gəlirsə — sükut deyil
MAX_MESSAGES = 20000
CALL_CACHE = 15             # yayım açıqdırmı — panel üçün bu qədər san. yadda saxlanır

# ── ayarlar (settings cədvəli) ──
S_MUSIC, S_BITRATE = "va:rec_music", "va:rec_bitrate"
S_TRANSCRIPT, S_SPLIT = "va:rec_transcript", "va:rec_split"
S_DEST, S_PANEL_DM = "va:rec_dest", "va:rec_panel_dm"
S_AUTO, S_GROUPS = "va:rec_auto_chats", "va:rec_groups"
S_TIMER, S_SILENT = "va:rec_timer", "va:rec_silent"
S_KEEP = "va:rec_keep_days"


_S = Settings(empty_is_default=True)
_raw, _put, _flag, _toggle = _S.get, _S.put, _S.flag, _S.toggle


def _int(key, default, allowed):
    return _S.int(key, default, allowed=allowed)


def _cycle(values: list, cur):
    return next_of(values, cur)


def music_level() -> int:
    return _int(S_MUSIC, 70, MUSIC_STEPS)


def set_music_level(v: int) -> int:
    v = min(MUSIC_STEPS, key=lambda x: abs(x - int(v)))
    _put(S_MUSIC, v)
    return v


def bitrate() -> int:
    return _int(S_BITRATE, 192, BITRATES)


def keep_days() -> int:
    return _int(S_KEEP, 14, KEEP_STEPS)


def keep_label() -> str:
    d = keep_days()
    return f"{d} gün" if d else "həmişə"


def prune_records() -> int:
    """data/records-da saxlama müddətini keçmiş faylları sil (göndərilmiş qeydlər) → silinən say."""
    days = keep_days()
    if not days or not os.path.isdir(OUT_DIR):
        return 0
    cutoff, n = time.time() - days * 86400, 0
    try:
        with open(OUTBOX, encoding="utf-8") as f:
            keep = {os.path.abspath(x.get("path", "")) for x in json.load(f)} | {os.path.abspath(OUTBOX)}
    except (OSError, ValueError):
        keep = set()
    for name in os.listdir(OUT_DIR):
        path = os.path.join(OUT_DIR, name)
        if os.path.abspath(path) in keep:
            continue                                     # 📮 hələ göndərilməyib
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
                n += 1
        except OSError:
            pass
    if n:
        logger.info(f"🗑 Köhnə qeydlər silindi: {n} fayl ({days} gündən köhnə)")
    return n


def timer_limit() -> int:
    return _int(S_TIMER, 0, TIMER_STEPS)


def set_timer_limit(v: int) -> int:
    v = min(TIMER_STEPS, key=lambda x: abs(x - int(v)))
    _put(S_TIMER, v)
    return v


def silent_on() -> bool:
    return _flag(S_SILENT, "0")


def toggle_silent() -> bool:
    return _toggle(S_SILENT, "0")


def transcript_on() -> bool:
    return _flag(S_TRANSCRIPT, "1")


def split_on() -> bool:
    return _flag(S_SPLIT, "0")


def dest() -> str:
    d = _raw(S_DEST, "target")
    return d if d in DEST_OPTIONS else "target"


def cycle_dest() -> str:
    cur = dest()
    nxt = _cycle(DEST_OPTIONS, cur)
    _put(S_DEST, nxt)
    return nxt


def rec_dest_desc(s: str = "vc") -> str:
    """Axının cari hədəfini oxuyur: '@kanal', '-100... #42', 'Saved Messages' və s."""
    try:
        from bot.plugins.menu_plugin import _lg, _lgub
        lg, ub = _lg(), _lgub()
        if ub is not None and lg is not None:
            tc, tt, label = lg.get_stream_target(ub, s)
            if tc:
                name = next((x[2] for x in lg.recent_targets(ub) if x[0] == str(tc) and x[2] != str(tc)), "")
                clean = name or str(tc)
                return f"{clean}" + (f" · topic {tt}" if tt else "")
            return "Saved Messages"
    except Exception as _e:
        logger.debug("udulan xəta [bot/core/va_record.py:194]: %r", _e)
    return "Saved Messages"


def rec_dest_summary(short: bool = False) -> str:
    d = dest()
    if d == "dm":
        return "💬 Şəxsi çat"
    if d == "here":
        return "👥 Bu çat"
    desc = rec_dest_desc("vc")
    if short:
        clean = desc.split(" · ")[0]
        if len(clean) > 16:
            clean = clean[:15] + "…"
        topic_part = ""
        if "topic " in desc:
            topic_part = " #" + desc.split("topic ")[1].split()[0]
        return f"🎯 {clean}{topic_part}"
    return f"🎯 {desc}"


def panel_dm() -> bool:
    return _flag(S_PANEL_DM, "1")


def _jdict(key) -> dict:
    try:
        d = json.loads(_raw(key, "{}"))
        return {int(k): v for k, v in d.items()} if isinstance(d, dict) else {}
    except (ValueError, TypeError):
        return {}


def auto_chats() -> dict:
    """{chat_id: başlıq} — ⚡ avtomatik rejimli qruplar."""
    return _jdict(S_AUTO)


def auto_on(chat_id) -> bool:
    return int(chat_id) in auto_chats()


def save_auto_chats(d: dict):
    _put(S_AUTO, json.dumps({str(k): v for k, v in d.items()}, ensure_ascii=False))


def set_auto(chat_id, title, on: bool):
    d = auto_chats()
    if on:
        d[int(chat_id)] = title or str(chat_id)
    else:
        d.pop(int(chat_id), None)
    save_auto_chats(d)


def known_groups() -> dict:
    """{chat_id: başlıq} — /vrec paneli açılmış qruplar (şəxsi çatdakı siyahı üçün)."""
    return _jdict(S_GROUPS)


def remember_group(chat_id, title):
    d = known_groups()
    if d.get(int(chat_id)) != title:
        d[int(chat_id)] = title or str(chat_id)
        d = dict(list(d.items())[-30:])
        _put(S_GROUPS, json.dumps({str(k): v for k, v in d.items()}, ensure_ascii=False))


def _record_stream():
    """PLAYBACK → kadrlar Python-a gəlsin (EXTERNAL): hər iştirakçı ayrıca, vaxtı biz qoyuruq."""
    from ntgcalls import MediaSource
    from pytgcalls.types.raw.audio_parameters import AudioParameters
    from pytgcalls.types.raw.audio_stream import AudioStream
    from pytgcalls.types.raw.stream import Stream
    return Stream(microphone=AudioStream(media_source=MediaSource.EXTERNAL, path="",
                                         parameters=AudioParameters(bitrate=SR, channels=CH)),
                  speaker=None, camera=None, screen=None)




# Qrup adminin edə biləcəyi əməllər (öz qrupunda). Qalan hamısı (musiqi, bitreyt, taymer, saxlama, jurnal,
# hədəf ...) BÜTÜN qrupların qeydlərinə təsir edən qlobal ayarlardır — yalnız bot sahibi.
GROUP_ACTS = frozenset({"start", "stop", "mark", "auto", "manual", "open", "refresh", "close", "status"})


def settings_rows(cb) -> list:
    """⏺ Qeyd ayarları — qrup paneli və şəxsi çat siyahısı eyni düymələri göstərir (cb(açar) → callback)."""
    t = timer_limit()
    t_str = f"{t}d" if t else "∞"
    return [[ui.btn(f"🎚 Musiqi: {music_level()}%", cb("music")), ui.btn(f"🎧 {bitrate()} kbps", cb("br"))],
            [ui.btn(f"⏱ Taymer: {t_str}", cb("timer")), ui.btn(f"🗑 {keep_label()}", cb("keep"))],
            [ui.toggle("🔕 Səssiz", silent_on(), cb("silent"), compact=True),
             ui.toggle("📝 Jurnal", transcript_on(), cb("tr"), compact=True)],
            [ui.toggle("🗣 Ayrı treklər", split_on(), cb("split"), compact=True),
             ui.btn("👁 " + ("💬 Şəxsi çatda" if panel_dm() else "👥 Qrupda"), cb("pdm"))],
            [ui.btn(f"📤 {rec_dest_summary(short=True)}", cb("dest"))]]

class VoiceWriter:
    """Bir iştirakçının səsi → FLAC. Sükut real vaxta görə doldurulur ki, fayl qeydin saatı ilə getsin.

    Kadrlar event loop-da yalnız növbəyə qoyulur; ffmpeg-ə yazma ayrıca thread-dədir (pipe dolanda loop bloklanmır).

    Sükut nə vaxt qoyulur: kadrın gəlmə anı ilə yazılmış səs arasında GAP-dan böyük fərq yarananda. Amma bu fərq
    iki səbəbdən olur:
      • danışan susub (həqiqi sükut) — sonrakı kadrlar real vaxt sürəti ilə gəlir;
      • bot məşğul idi (loop gecikdi) — yığılan kadrlar bir dəstədə, real vaxtdan SÜRƏTLİ gəlir.
    Əvvəl ikisi eyni sayılırdı → nitqin ortasına sükut + arxasınca gecikmiş səs: "robot", kəsik, sürüşən səs.
    İndi boşluqdan sonra JITTER müddətində gələn kadrlara baxılır: real vaxtdan artıq gələn səs = gecikmiş səs,
    o qədər sükut çıxılır."""

    def __init__(self, path: str):
        import queue
        import threading
        self.path = path
        self.written = 0                                   # yazılmış nümunə sayı (yalnız yazıcı thread)
        self.sizes = Counter()                             # kadr ölçüləri (diaqnostika)
        self.q = queue.SimpleQueue()
        prefix = []
        try:
            from bot.core import cpu_pool
            prefix = cpu_pool.ffmpeg_prefix()
        except Exception as _e:
            logger.debug("udulan xəta [bot/core/va_record.py:318]: %r", _e)
        self.proc = subprocess.Popen(
            [*prefix, "ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "s16le", "-ar", str(SR), "-ac", str(CH),
             "-i", "pipe:0", "-c:a", "flac", path],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, bufsize=1 << 20)
        self.thread = threading.Thread(target=self._run, name=f"rec-{os.path.basename(path)}", daemon=True)
        self.thread.start()

    def write(self, pcm: bytes, at: float):
        """at — kadrın qeyd başlanğıcından sonra gəldiyi an (san.). Event loop-dan çağırılır — bloklamır."""
        self.q.put((at, pcm))

    # ── yazıcı thread ──
    def _put(self, data: bytes):
        try:
            self.proc.stdin.write(data)
        except (BrokenPipeError, ValueError, OSError):
            pass

    def _silence(self, samples: int):
        zeros = b"\0" * (2 * CH * SR)
        while samples > 0:
            k = min(samples, SR)
            self._put(zeros[:2 * CH * k])
            samples -= k
            self.written += k

    def _pcm(self, pcm: bytes):
        pcm = pcm[:len(pcm) - len(pcm) % (2 * CH)]         # yarımçıq nümunə qalmasın (xırıltı)
        self.sizes[len(pcm)] += 1
        self._put(pcm)
        self.written += len(pcm) // (2 * CH)

    def _run(self):
        import queue
        stop = False
        while not stop:
            item = self.q.get()
            if item is None:
                break
            at, pcm = item
            n = len(pcm) // (2 * CH)
            lag = int(at * SR) - n - self.written
            if lag <= GAP * SR:
                self._pcm(pcm)
                continue
            # boşluq: həqiqi sükut, yoxsa gecikmiş dəstə? JITTER ərzində gələnlərə bax
            batch, got = [item], n
            deadline = time.monotonic() + JITTER
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                try:
                    nxt = self.q.get(timeout=left)
                except queue.Empty:
                    break
                if nxt is None:
                    stop = True
                    break
                batch.append(nxt)
                got += len(nxt[1]) // (2 * CH)
            span = (batch[-1][0] - at) * SR                # bu kadrlar real vaxtda nə qədər müddətə gəldi
            backlog = max(0, int(got - n - span))          # real vaxtdan artıq gələn = gecikib yığılmış səs
            silence = lag - backlog
            if silence > GAP * SR:
                self._silence(silence)
            for _, p in batch:
                self._pcm(p)

    def close(self):
        self.q.put(None)
        self.thread.join(timeout=30)
        with contextlib.suppress(Exception):
            self.proc.stdin.close()
        with contextlib.suppress(Exception):
            self.proc.wait(timeout=15)
        with contextlib.suppress(Exception):
            if self.proc.poll() is None:
                self.proc.kill()
        if self.sizes:
            logger.info(f"⏺ {os.path.basename(self.path)}: kadr ölçüləri {dict(self.sizes.most_common(3))} bayt "
                        f"(10 ms = {SR // 100 * 2 * CH})")


class Session:
    """Bir qrupun qeydi. Qovluqda: v<ssrc>.flac (danışanlar), m<N>.<ext> (mahnılar), timeline.json."""

    SAVED = ("chat_id", "title", "slot_idx", "by", "t0", "ended", "voices", "speakers", "pieces", "open",
             "paused", "tracks", "marks", "messages", "n", "auto")

    def __init__(self, chat_id: int, title: str, slot_idx: int, by: str = "", folder: str = None, auto=False):
        self.chat_id, self.title, self.slot_idx, self.by = chat_id, title or str(chat_id), slot_idx, by
        self.dir = folder or os.path.join(REC_ROOT, f"{chat_id}_{int(time.time())}")
        self.auto = auto
        self.t0 = 0.0                  # qeydin başladığı an (time.time)
        self.ended = 0.0
        self.voices = []               # danışan FLAC faylları
        self.speakers = {}             # "ssrc" → ad
        self.writers = {}              # ssrc → VoiceWriter (yalnız yaddaşda)
        self.pieces = []               # {"file", "pos", "off", "dur", "title"} — bağlanmış musiqi parçaları
        self.open = None               # hazırda səslənən parça (dur hələ yoxdur)
        self.paused = None             # pauzada: {"file", "pos", "title"} — davam edəndə yeni parça açılır
        self.tracks = []               # [(off, title)]
        self.marks = []                # [off] — 📍 nişanlar
        self.messages = []             # [(off, ad, mətn)] — 📝 söhbət jurnalı
        self.n = 0
        self.frames = 0
        self.call = None               # InputGroupCall (ad axtarışı üçün, yaddaşda)
        self._last_save = 0.0

    @property
    def path(self) -> str:
        return os.path.join(self.dir, "timeline.json")

    def now(self) -> float:
        return time.time() - self.t0

    def save(self, done: bool = False):
        data = {k: getattr(self, k) for k in self.SAVED}
        data["done"] = done
        tmp = self.path + ".tmp"
        def _do_write():
            with contextlib.suppress(OSError):
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
                os.replace(tmp, self.path)
        try:
            loop = asyncio.get_running_loop()
            loop.run_in_executor(None, _do_write)
        except RuntimeError:
            _do_write()
        self._last_save = time.time()

    def save_soon(self):
        """Tez-tez gələn hadisələr (söhbət mesajları) üçün — ən çox 10 san.-dən bir yaz."""
        if time.time() - self._last_save > 10:
            self.save()

    @classmethod
    def load(cls, folder: str):
        with open(os.path.join(folder, "timeline.json"), encoding="utf-8") as f:
            d = json.load(f)
        s = cls(d["chat_id"], d.get("title"), d.get("slot_idx", 0), d.get("by", ""), folder)
        for k in cls.SAVED:
            if k in d and k not in ("chat_id", "title", "slot_idx", "by"):
                setattr(s, k, d[k])
        return s, bool(d.get("done"))

    def speaker_names(self) -> list:
        names = []
        for v in self.voices:
            info = self.speakers.get(os.path.basename(v)[1:-5], "?")
            names.append(info.get("name", "?") if isinstance(info, dict) else str(info))
        return names

    # ── danışanlar ──
    def add_frame(self, ssrc: int, pcm: bytes) -> bool:
        """→ True: yeni danışan (adını axtarmaq lazımdır)."""
        w = self.writers.get(ssrc)
        new = False
        if w is None:
            path = os.path.join(self.dir, f"v{ssrc & 0xFFFFFFFF}.flac")
            w = self.writers[ssrc] = VoiceWriter(path)
            if path not in self.voices:
                self.voices.append(path)
                new = True
            self.save()
        w.write(pcm, self.now())
        self.frames += 1
        return new

    def close_writers(self):
        for w in self.writers.values():
            w.close()
        self.writers.clear()

    # ── musiqi jurnalı ──
    def close_piece(self, at: float = None) -> dict | None:
        """Səslənən parçanı bağla → onun {"file", "pos", "title"} davamı (pauza üçün)."""
        p, self.open = self.open, None
        if not p:
            return None
        at = self.now() if at is None else at
        p["dur"] = max(0.0, at - p["off"])
        if p["dur"] >= MIN_PIECE:
            self.pieces.append(p)
        return {"file": p["file"], "pos": p["pos"] + p["dur"], "title": p["title"]}

    def start_piece(self, file: str, pos: float, title: str):
        self.open = {"file": file, "pos": max(0.0, pos), "off": self.now(), "title": title}

    def link_track(self, src: str) -> str | None:
        """Oxudulan faylı qovluğa hard link et (asistan mahnı bitəndə faylı silir)."""
        if not src or not os.path.isfile(src):
            return None
        self.n += 1
        dst = os.path.join(self.dir, f"m{self.n}{os.path.splitext(src)[1] or '.m4a'}")
        try:
            os.link(src, dst)
        except OSError:
            try:
                shutil.copy2(src, dst)
            except OSError as e:
                logger.warning(f"⏺ mahnı faylı qeydə əlavə olunmadı: {e}")
                return None
        return dst


class Recorder:
    def __init__(self, va):
        self.va = va                 # voice_assistant_plugin.Assistant
        self.sessions = {}           # chat_id → Session
        self.guard = {}              # chat_id → son açma / bağlama anı
        self.starting = set()        # eyni anda iki başlatma (bot + asistan hadisəsi) olmasın
        self.tasks = set()
        self._calls = {}             # chat_id → (vaxt, yayım açıqdırmı)
        self._attached = set()       # hadisə dinləyicisi qoşulmuş asistan client-ləri

    # ── vəziyyət ──
    def active(self, chat_id) -> bool:
        return chat_id in self.sessions

    def recent_toggle(self, chat_id) -> bool:
        return time.time() - self.guard.get(chat_id, 0) < TOGGLE_GUARD

    def status_text(self) -> str:
        if not self.sessions:
            return "⏺ Aktiv qeyd yoxdur"
        lines = [f"⏺ Aktiv qeydlər ({len(self.sessions)}):"]
        for cid, s in self.sessions.items():
            lines.append(f"• {escape(s.title)}: {fmt_dur(s.now())} (🗣 {len(s.voices)} · 🎵 {len(s.tracks)})")
        return "\n".join(lines)

    async def _timer_worker(self, chat_id: int):
        while chat_id in self.sessions:
            limit_m = timer_limit()
            if limit_m > 0:
                sess = self.sessions.get(chat_id)
                if sess and sess.now() >= limit_m * 60:
                    logger.info(f"⏱ Qeyd taymeri bitdi ({limit_m} dəq.): {sess.title}")
                    await self.stop(chat_id, reason=f"⏱ {limit_m} dəqiqəlik taymer bitdi")
                    break
            await asyncio.sleep(5)

    def _slot(self, sess: Session):
        sl = self.va.bind.get(sess.chat_id)
        if sl is None:
            sl = next((x for x in self.va.slots if x.idx == sess.slot_idx), None)
        return sl

    def _spawn(self, coro):
        task = asyncio.ensure_future(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        task.add_done_callback(lambda t: t.cancelled() or not t.exception()
                               or logger.warning(f"⏺ fon işi: {t.exception()!r}"))

    async def call_active(self, chat_id) -> bool | None:
        """Qrupda səsli yayım açıqdırmı (asistan hesabı ilə; None — bilinmir). 15 san. yadda saxlanır."""
        cached = self._calls.get(chat_id)
        if cached and time.time() - cached[0] < CALL_CACHE:
            return cached[1]
        client = self.va.any_client()
        if client is None:
            return None
        try:
            full = await asyncio.wait_for(self._full(client, chat_id), 5)
            val = getattr(full.full_chat, "call", None) is not None
        except Exception:
            return None
        self._calls[chat_id] = (time.time(), val)
        return val

    @staticmethod
    async def _full(client, chat_id):
        from telethon.tl.functions.channels import GetFullChannelRequest
        from telethon.tl.functions.messages import GetFullChatRequest
        from telethon.tl.types import InputPeerChannel
        ent = await client.get_input_entity(chat_id)
        if isinstance(ent, InputPeerChannel):
            return await client(GetFullChannelRequest(ent))
        return await client(GetFullChatRequest(abs(chat_id)))

    # ── başlat ──
    async def start(self, chat_id: int, title: str, by: str = "", auto: bool = False) -> str:
        va = self.va
        if self.active(chat_id):
            return f"⏺ Bu qrupda qeyd artıq gedir ({fmt_dur(self.sessions[chat_id].now())})."
        if chat_id in self.starting:
            return "⏳ Qeyd artıq başladılır..."
        if not va.running:
            raise RuntimeError("asistan işləmir — /menu → 🎙 Canlı yayım asistanı")
        self.starting.add(chat_id)
        try:
            sl = await asyncio.wait_for(va.assign(chat_id), 30)
            if not getattr(sl.calls, "v2", False):
                raise RuntimeError("qeyd üçün py-tgcalls 2.x+ lazımdır")
            await asyncio.wait_for(va.ensure_member(chat_id, sl), 30)
            st = va.chats.get(chat_id)
            playing = bool(st and st.current and st.started and va.bind.get(chat_id) is sl)
            if not playing:
                await asyncio.wait_for(va.ensure_call(chat_id, sl), 30)
            self.attach(sl)
            sess = Session(chat_id, title, sl.idx, by, auto=auto)
            os.makedirs(sess.dir, exist_ok=True)
            self.guard[chat_id] = time.time()
            sess.t0 = time.time()                        # kadrlar record()-dan dərhal sonra gələ bilər
            self.sessions[chat_id] = sess
            self._spawn(self._timer_worker(chat_id))
            try:
                await asyncio.wait_for(sl.calls.app.record(chat_id, _record_stream()), 90)
            except BaseException:
                self.sessions.pop(chat_id, None)
                sess.close_writers()
                shutil.rmtree(sess.dir, ignore_errors=True)
                raise
        finally:
            self.starting.discard(chat_id)
        va.bind[chat_id] = sl
        self._calls[chat_id] = (time.time(), True)
        if playing and st.current.path:                  # mahnı artıq gedirdi — ortasından qoşul
            self.on_track(chat_id, st.current, pos=st.elapsed())
            if st.paused:
                self.on_pause(chat_id)
        sess.save()
        logger.info(f"⏺ {sl.icon} qeyd başladı{' (⚡ avto)' if auto else ''}: {title} ({chat_id})")
        return f"⏺ Qeyd başladı — {escape(sess.title)} ({sl.name()})"

    # ── asistan hesabının hadisələri: söhbət jurnalı + ⚡ avtomatik rejim ──
    def attach(self, sl):
        """Asistan client-inə dinləyici qoş (bir dəfə): qeyd gedən qrupların mesajları və yayım başladı/bitdi."""
        client = getattr(sl, "client", None)
        if client is None or id(client) in self._attached:
            return
        from telethon import events
        from telethon.tl.types import MessageActionGroupCall
        self._attached.add(id(client))

        @client.on(events.NewMessage(incoming=True, func=lambda e: e.is_group or e.is_channel))
        async def _on_msg(event):
            cid = event.chat_id
            action = getattr(event.message, "action", None)
            if isinstance(action, MessageActionGroupCall):
                title = getattr(event.chat, "title", None) or str(cid)
                if action.duration is None:
                    await self.on_call_started(cid, title)
                else:
                    await self.on_call_ended(cid)
                return
            sess = self.sessions.get(cid)
            if sess is None or not transcript_on() or len(sess.messages) >= MAX_MESSAGES:
                return
            with contextlib.suppress(Exception):
                sender = event.sender or await event.get_sender()
                text = (event.raw_text or "").strip() or (f"[{tg_media_label(event.message)}]" if event.message.media else "")
                if text:
                    uid = getattr(sender, "id", None) or getattr(event, "sender_id", 0) or 0
                    uname = getattr(sender, "username", "") or ""
                    sess.messages.append((round(sess.now(), 1), tg_name(sender), text[:2000], uid, uname))
                    sess.save_soon()

    async def on_call_started(self, chat_id, title):
        """Qrupda səsli yayım başladı (bot servis mesajı və ya asistan hadisəsi) — ⚡ avtomatikdirsə qoşul."""
        self._calls[chat_id] = (time.time(), True)
        if not auto_on(chat_id) or self.active(chat_id) or chat_id in self.starting:
            return
        if not (self.va.enabled and self.va.running):
            await self.notify(f"⚠️ <b>⚡ Avtomatik qeyd başlamadı</b> — {escape(title)}\nAsistan söndürülüb.")
            return
        await asyncio.sleep(2)                           # yayım Telegram-da tam açılsın
        try:
            note = await self.start(chat_id, title, by="⚡ avtomatik", auto=True)
        except Exception as e:
            logger.warning(f"⏺ avtomatik qeyd başlamadı ({chat_id}): {e}")
            await self.notify(f"⚠️ <b>⚡ Avtomatik qeyd başlamadı</b> — {escape(title)}\n"
                              f"<code>{escape(str(e)[:300])}</code>")
            return
        await self.notify(f"⚡ {note}\n<i>Səsli yayım başladı — qrupda /vrec ilə panel</i>")

    async def on_call_ended(self, chat_id):
        self._calls[chat_id] = (time.time(), False)
        if self.active(chat_id):
            await self.stop(chat_id, reason="səsli yayım bitdi", call_gone=True)

    async def boot_auto(self):
        """Başlanğıc: ⚡ avtomatik qruplarda yayım hələ gedirsə — qeydə yenidən başla."""
        await asyncio.sleep(5)
        for cid, title in auto_chats().items():
            if self.active(cid):
                continue
            if await self.call_active(cid):
                await self.on_call_started(cid, title)

    # ── danışanların kadrları (Calls on_update → StreamFrames) ──
    def on_frames(self, chat_id, update):
        sess = self.sessions.get(chat_id)
        if not sess or "INCOMING" not in str(getattr(update, "direction", "")).upper():
            return
        for fr in getattr(update, "frames", None) or ():
            data = getattr(fr, "frame", None)
            if data:
                ssrc = int(getattr(fr, "ssrc", 0) or 0)
                if sess.add_frame(ssrc, data):
                    self._spawn(self._resolve_speaker(sess, ssrc))

    async def _resolve_speaker(self, sess: Session, ssrc: int):
        """ssrc → iştirakçının adı (phone.GetGroupParticipants). Alınmasa "Danışan N"."""
        key = str(ssrc & 0xFFFFFFFF)
        sess.speakers.setdefault(key, f"Danışan {len(sess.speakers) + 1}")
        sl = self._slot(sess)
        client = getattr(sl, "client", None)
        if client is None:
            return
        from telethon.tl.functions.phone import GetGroupParticipantsRequest
        signed = ssrc - (1 << 32) if ssrc >= (1 << 31) else ssrc
        for attempt in range(3):
            try:
                if sess.call is None:
                    sess.call = getattr((await self._full(client, sess.chat_id)).full_chat, "call", None)
                if sess.call is None:
                    return
                res = await client(GetGroupParticipantsRequest(call=sess.call, ids=[], sources=[signed],
                                                               offset="", limit=10))
                for p in res.participants:
                    if (p.source & 0xFFFFFFFF) != (ssrc & 0xFFFFFFFF):
                        continue
                    pid = getattr(p.peer, "user_id", None) or getattr(p.peer, "channel_id", None)
                    ent = next((u for u in [*res.users, *res.chats] if u.id == pid), None)
                    if ent is not None:
                        sess.speakers[key] = {
                            "name": tg_name(ent),
                            "id": getattr(ent, "id", 0) or 0,
                            "username": getattr(ent, "username", "") or ""
                        }
                        sess.save()
                    return
            except Exception as e:
                logger.debug(f"⏺ danışanın adı alınmadı ({ssrc}): {e}")
            await asyncio.sleep(5)                       # təzə qoşulan iştirakçı siyahıya gec düşür

    # ── oxutma hadisələri (voice_assistant_plugin çağırır) ──
    def on_track(self, chat_id, track, pos: float = 0.0):
        sess = self.sessions.get(chat_id)
        if not sess:
            return
        sess.close_piece()
        sess.paused = None
        file = sess.link_track(getattr(track, "path", None))
        title = getattr(track, "title", "") or "?"
        sess.tracks.append((round(sess.now(), 1), title))
        if file:
            sess.start_piece(file, pos, title)
        sess.save()

    def on_pause(self, chat_id):
        sess = self.sessions.get(chat_id)
        if sess and sess.open:
            sess.paused = sess.close_piece()
            sess.save()

    def on_resume(self, chat_id):
        sess = self.sessions.get(chat_id)
        if sess and sess.paused:
            p, sess.paused = sess.paused, None
            sess.start_piece(p["file"], p["pos"], p["title"])
            sess.save()

    def on_track_end(self, chat_id):
        sess = self.sessions.get(chat_id)
        if sess and (sess.open or sess.paused):
            sess.close_piece()
            sess.paused = None
            sess.save()

    def mark(self, chat_id) -> str:
        sess = self.sessions.get(chat_id)
        if not sess:
            return "⚠️ Qeyd getmir — nişan qoymaq olmur"
        sess.marks.append(round(sess.now(), 1))
        sess.save()
        return f"📍 Nişan {len(sess.marks)} — {fmt_dur(sess.marks[-1])}"

    async def silence(self, chat_id):
        """Qeyd gedərkən /stop: yayımdan çıxmadan musiqini kəs (1 san.-lik sükut faylı)."""
        sess = self.sessions.get(chat_id)
        sl = self._slot(sess) if sess else None
        if not sl or not sl.calls:
            return
        path = os.path.abspath(os.path.join(REC_ROOT, "silence.flac"))
        if not os.path.isfile(path):
            os.makedirs(REC_ROOT, exist_ok=True)
            prefix = []
            try:
                from bot.core import cpu_pool
                prefix = cpu_pool.ffmpeg_prefix()
            except Exception as _e:
                logger.debug("udulan xəta [bot/core/va_record.py:815]: %r", _e)
            proc = await asyncio.create_subprocess_exec(
                *prefix, "ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                "anullsrc=r=48000:cl=stereo", "-t", "1", path,
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL)
            await proc.wait()
        self.guard[chat_id] = time.time()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(sl.calls.play(chat_id, path), 20)

    # ── dayandır ──
    async def stop(self, chat_id: int, reason: str = "", call_gone: bool = False) -> str:
        """Qeydi bağla, yayımdan (lazımsa) çıx, arxa planda birləşdir və göndər."""
        sess = self.sessions.pop(chat_id, None)
        if not sess:
            return "⏺ Bu qrupda qeyd yoxdur."
        sess.close_piece()
        sess.paused = None
        sess.ended = time.time()
        self.guard[chat_id] = time.time()
        va = self.va
        sl = self._slot(sess)
        st = va.chats.get(chat_id)
        if not call_gone and sl and sl.calls:
            with contextlib.suppress(Exception):
                if st is not None and st.current is not None:
                    await asyncio.wait_for(sl.calls.app.record(chat_id, None), 20)   # musiqi davam edir
                else:
                    await sl.calls.leave(chat_id)                                   # yalnız qeyd üçün idik
        if st is None:
            va.bind.pop(chat_id, None)
        await asyncio.to_thread(sess.close_writers)      # FLAC-lar düzgün bağlansın
        sess.save()
        logger.info(f"⏺ qeyd bitdi: {sess.title} ({chat_id}) — {fmt_dur(sess.ended - sess.t0)} · "
                    f"{len(sess.voices)} danışan · {sess.frames} kadr" + (f" · {reason}" if reason else ""))
        self._spawn(self.finalize(sess, reason))
        return (f"⏹ Qeyd bitdi — {escape(sess.title)} · {fmt_dur(sess.ended - sess.t0)} · "
                f"🗣 {len(sess.voices)} · 🎵 {len(sess.tracks)}. Hazırlanır və göndəriləcək...")

    async def stop_all(self, reason: str):
        for chat_id in list(self.sessions):
            with contextlib.suppress(Exception):
                await self.stop(chat_id, reason)

    def save_all(self):
        """Restart / söndürmə: jurnalı saxla, FLAC-ları bağla — növbəti başlanğıcda recover() bitirəcək."""
        for sess in self.sessions.values():
            sess.close_piece()
            sess.ended = time.time()
            sess.close_writers()
            sess.save()

    # ── hazırla ──
    @staticmethod
    async def _ffmpeg(args: list, limit: float):
        from bot.core import cpu_pool
        proc = await asyncio.create_subprocess_exec(
            *cpu_pool.ffmpeg_prefix(), "ffmpeg", "-nostdin", "-y", "-hide_banner", "-loglevel", "error", *args,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        try:
            _, err = await asyncio.wait_for(proc.communicate(), limit)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            raise RuntimeError("ffmpeg vaxtında bitmədi")
        if proc.returncode != 0:
            raise RuntimeError("ffmpeg: " + err.decode(errors="ignore")[-200:])

    async def mix(self, sess: Session) -> tuple:
        """→ (mp3 yolu, müddət). Danışanlar və musiqi qeydin saatı ilə üst-üstə qoyulur."""
        wall = max(1.0, (sess.ended or time.time()) - sess.t0)
        voices = [v for v in sess.voices if os.path.isfile(v) and os.path.getsize(v) > 100]
        gain = music_level() / 100
        pieces = [p for p in sess.pieces if p.get("dur", 0) >= MIN_PIECE and os.path.isfile(p["file"])] \
            if gain > 0 else []
        if not voices and not pieces:
            raise RuntimeError("yayımda heç bir səs olmayıb (danışan da, mahnı da yoxdur)")
        os.makedirs(OUT_DIR, exist_ok=True)
        out = os.path.abspath(os.path.join(OUT_DIR, f"va_{sess.chat_id}_{int(sess.t0)}.mp3"))
        # 0-cı giriş — qeydin tam uzunluğunda sükut: bütün səslər onun üstünə, nəticə dəqiq qeyd boyda
        args = ["-f", "lavfi", "-t", f"{wall:.3f}", "-i", f"anullsrc=r={SR}:cl=stereo"]
        fmt = f"aformat=sample_rates={SR}:channel_layouts=stereo"
        graph, labels = [f"[0:a]{fmt}[base]"], ["[base]"]
        idx = 1
        for v in voices:
            args += ["-i", v]
            graph.append(f"[{idx}:a]{fmt}[v{idx}]")
            labels.append(f"[v{idx}]")
            idx += 1
        for p in pieces:
            args += ["-ss", f"{p['pos']:.3f}", "-t", f"{p['dur']:.3f}", "-i", p["file"]]
            graph.append(f"[{idx}:a]{fmt},adelay={max(0, int(p['off'] * 1000))}:all=1,volume={gain:.2f}[m{idx}]")
            labels.append(f"[m{idx}]")
            idx += 1
        graph.append("".join(labels) + f"amix=inputs={len(labels)}:duration=first:normalize=0,"
                     "alimiter=limit=0.95[out]")
        script = os.path.join(sess.dir, "filter.txt")
        with open(script, "w", encoding="utf-8") as f:
            f.write(";".join(graph))
        args += ["-/filter_complex", script, "-map", "[out]", "-c:a", "libmp3lame", "-b:a", f"{bitrate()}k",
                 "-metadata", f"title=Canlı yayım — {sess.title}", out]
        await self._ffmpeg(args, max(300.0, wall))
        return out, wall

    def transcript(self, sess: Session) -> str | None:
        from bot.core.va_transcript import generate_transcript
        return generate_transcript(sess, OUT_DIR)

    async def split_zip(self, sess: Session) -> str | None:
        """🗣 Hər danışan ayrıca mp3 (qeydin saatı ilə, sükut daxil) → zip."""
        voices = [v for v in sess.voices if os.path.isfile(v) and os.path.getsize(v) > 100]
        if not voices:
            return None
        names, files = {}, []
        for v, name in zip(voices, sess.speaker_names()):
            base = "".join(c for c in name if c.isalnum() or c in " _-").strip() or "danisan"
            names[base] = names.get(base, 0) + 1
            base = base if names[base] == 1 else f"{base} {names[base]}"
            mp3 = os.path.join(sess.dir, f"{base}.mp3")
            await self._ffmpeg(["-i", v, "-c:a", "libmp3lame", "-b:a", "96k", mp3], 600)
            files.append(mp3)
        path = os.path.abspath(os.path.join(OUT_DIR, f"va_{sess.chat_id}_{int(sess.t0)}_treklər.zip"))
        with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
            for f in files:
                z.write(f, os.path.basename(f))
        return path

    # ── mesajlar: .rec / audit kartları ilə eyni kod bloku (fastfetch görünüşü, <pre>) ──
    def _ff_title(self, name: str) -> str:
        ub = getattr(self.va.ctx, "userbot", None)
        with contextlib.suppress(Exception):
            return ub.title(name)
        return f"{name}@dllmaster"

    @staticmethod
    def _ff_len(html: str) -> int:
        """Telegram-ın saydığı uzunluq (teqlər və &amp; kimi escape-lər çıxılır)."""
        import html as _html
        return len(_html.unescape(re.sub(r"<[^>]+>", "", html)))

    def caption(self, sess: Session, dur: float, size: int, note: str = "") -> str:
        from userbot.core.userbot_api import ff
        names = sess.speaker_names()
        t0 = time.localtime(sess.t0)
        t1 = time.localtime(sess.t0 + dur)
        body = [
            ("Qrup", sess.title[:60]),
            ("Tarix", time.strftime("%d.%m.%Y %H:%M", t0) + time.strftime("–%H:%M", t1)),
            ("Müddət", fmt_dur(dur) + (" · ⚡ avto" if sess.auto else " · ✋ manual")),
            ("Danışan", f"{len(names)}" + (": " + ", ".join(names[:12]) + (f" +{len(names) - 12}" if len(names) > 12 else "")
                                           if names else "")),
            ("Musiqi", f"{len(sess.tracks)} mahnı · {music_level()}%"),
        ]
        if sess.marks:
            body.append(("Nişan", f"{len(sess.marks)}"))
        if sess.messages:
            body.append(("Mesaj", f"{len(sess.messages)}"))
        body.append(("Fayl", f"{format_bytes(size, space=True)} · {bitrate()}k mp3"))
        if note:
            body.append(("Qeyd", note))
        footer = "canlı yayım qeydi · 🎙 asistan"
        items = sorted([(off, f"📍 Nişan {i}") for i, off in enumerate(sess.marks, 1)]
                       + [(off, f"🎵 {title}") for off, title in sess.tracks])
        if items:
            body.append("# Zaman xətti")
            for i, (off, text) in enumerate(items):          # Telegram caption limiti — 1024 simvol
                trial = body + [(fmt_dur(off), text[:60]), ("…", f"+{len(items) - i - 1}")]
                if self._ff_len(ff(self._ff_title("vcrec"), trial, footer=footer)) > 1000:
                    body.append(("…", f"+{len(items) - i} daha (jurnalda)"))
                    break
                body.append((fmt_dur(off), text[:60]))
        return ff(self._ff_title("vcrec"), body, footer=footer)

    def extra_caption(self, sess: Session, label: str, dur: float, path: str) -> str:
        from userbot.core.userbot_api import ff
        what = {"📝 jurnal": "söhbət jurnalı (.html)", "🗣 treklər": "hər danışan ayrıca mp3 (.zip)"}.get(label, label)
        body = [("Qrup", sess.title[:60]),
                ("Tarix", time.strftime("%d.%m.%Y %H:%M", time.localtime(sess.t0))),
                ("Müddət", fmt_dur(dur)),
                ("Fayl", f"{what} · {format_bytes(os.path.getsize(path), space=True)}")]
        if label == "📝 jurnal":
            body.append(("Sətir", f"{len(sess.messages)} mesaj · {len(sess.tracks)} mahnı · {len(sess.marks)} nişan"))
        else:
            body.append(("Danışan", ", ".join(sess.speaker_names()[:12]) or "—"))
        return ff(self._ff_title("vcrec_tr" if label == "📝 jurnal" else "vcrec_spl"), body,
                  footer=f"canlı yayım qeydi · {label}")

    @staticmethod
    def _parse_chat_target(target):
        if target is None:
            return None
        s = str(target).strip()
        if not s or s.lower() == "me":
            return "me"
        clean = s.lstrip("-")
        if clean.isdigit():
            return int(s)
        return s

    def _get_assistant_client(self, sess: Session = None):
        """Səs asistantı telethon müştərisini və slotunu qaytarır."""
        if sess:
            sl = self._slot(sess)
            if sl and getattr(sl, "client", None) and sl.client.is_connected():
                return sl.client, sl
        slots = getattr(self.va, "slots", [])
        for sl in slots:
            if getattr(sl, "running", False) and getattr(sl, "client", None) and sl.client.is_connected():
                return sl.client, sl
        for sl in slots:
            if getattr(sl, "client", None) and sl.client.is_connected():
                return sl.client, sl
        c = getattr(self.va, "any_client", lambda: None)()
        if c and c.is_connected():
            return c, (slots[0] if slots else None)
        return None, None

    async def _wait_assistant(self, sess=None, wait: float = ASSISTANT_WAIT):
        """Qoşulu asistan client-i; yoxdursa yenidən qoşulmanı gözlə (restart, qısa qopma)."""
        deadline = time.time() + wait
        while True:
            client, sl = self._get_assistant_client(sess)
            if client is not None or time.time() >= deadline or not getattr(self.va, "enabled", True):
                return client, sl
            await asyncio.sleep(3)

    # ── 📮 göndərilməmiş fayllar: asistan qoşulanda göndərilir ──
    @staticmethod
    def _outbox_load() -> list:
        try:
            with open(OUTBOX, encoding="utf-8") as f:
                return [x for x in json.load(f) if os.path.isfile(x.get("path", ""))]
        except (OSError, ValueError):
            return []

    @staticmethod
    def _outbox_save(items: list):
        os.makedirs(OUT_DIR, exist_ok=True)
        tmp = OUTBOX + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False)
        os.replace(tmp, OUTBOX)

    def _outbox_add(self, sess, path, caption, dur, stream):
        items = self._outbox_load()
        items.append({"path": path, "caption": caption, "dur": dur, "stream": stream,
                      "chat_id": sess.chat_id, "title": sess.title, "slot_idx": getattr(sess, "slot_idx", 0)})
        self._outbox_save(items)

    async def flush_outbox(self):
        """Asistan qoşulanda növbədəki qeydləri göndər."""
        items = self._outbox_load()
        if not items:
            return
        from types import SimpleNamespace
        left, sent = [], []
        for it in items:
            sess = SimpleNamespace(chat_id=it["chat_id"], title=it["title"], slot_idx=it.get("slot_idx", 0))
            try:
                where = await self.deliver(sess, it["path"], it["caption"], it.get("dur", 0), stream=it["stream"])
                sent.append(f"{it['title'][:30]} → {where}")
            except Exception as e:
                logger.warning(f"📮 növbədəki qeyd göndərilmədi ({it['path']}): {e}")
                left.append(it)
        self._outbox_save(left)
        if sent:
            logger.info(f"📮 növbədəki qeydlər göndərildi: {sent}")
            await self.notify("📮 <b>Gözləyən səs qeydləri asistan ilə göndərildi:</b>\n"
                              + "\n".join(f"• {escape(x)}" for x in sent))

    async def _check_bot_in_chat(self, chat_target) -> tuple[bool, str]:
        """Əsas botun (ctx.bot) verilmiş çat/qrupda olub-olmadığını yoxlayır."""
        bot = getattr(self.va.ctx, "bot", None)
        if not bot:
            return False, "əsas bot aktiv deyil"
        cid = self._parse_chat_target(chat_target)
        if not cid or cid == "me":
            return True, "ok"
        try:
            bot_me = getattr(self.va.ctx, "bot_me", None)
            if not bot_me:
                bot_me = await bot.get_me()
                setattr(self.va.ctx, "bot_me", bot_me)
            m = await bot.get_chat_member(chat_id=cid, user_id=bot_me.id)
            st = str(getattr(m.status, "value", m.status)).lower()
            if st in ("member", "administrator", "creator") or (st == "restricted" and getattr(m, "is_member", True)):
                return True, "ok"
            return False, f"əsas bot qrupda deyil (status: {st})"
        except Exception as e:
            return False, f"əsas bot yoxlanışı xətası: {e}"

    async def _check_asst_in_chat(self, client, sl, chat_target) -> tuple[bool, str]:
        """Səs asistantının verilmiş çat/qrupda olub-olmadığını yoxlayır."""
        if client is None or not client.is_connected():
            return False, "səs asistantı hesabı qoşulmayıb"
        cid = self._parse_chat_target(chat_target)
        if not cid or cid == "me":
            return True, "ok"

        # 1. Bot API ilə asistantın user_id-sini yoxla
        bot = getattr(self.va.ctx, "bot", None)
        asst_uid = getattr(getattr(sl, "me", None), "id", None)
        if not asst_uid:
            try:
                me = await client.get_me()
                if me:
                    asst_uid = me.id
                    if sl:
                        sl.me = me
            except Exception as _e:
                logger.debug("udulan xəta [bot/core/va_record.py:1126]: %r", _e)

        if bot and asst_uid:
            try:
                m = await bot.get_chat_member(chat_id=cid, user_id=asst_uid)
                st = str(getattr(m.status, "value", m.status)).lower()
                if st in ("member", "administrator", "creator") or (st == "restricted" and getattr(m, "is_member", True)):
                    return True, "ok"
                if st in ("kicked", "left"):
                    return False, f"səs asistantı qrupda deyil (status: {st})"
            except Exception as _e:
                logger.debug("udulan xəta [bot/core/va_record.py:1137]: %r", _e)

        # 2. Telethon müştərisi ilə yoxla
        try:
            ent = await client.get_entity(cid)
            if getattr(ent, "left", False):
                return False, "səs asistantı qrupdan çıxıb"
            try:
                perms = await client.get_permissions(ent, "me")
                if perms is not None:
                    return True, "ok"
            except Exception as pe:
                pe_str = str(pe).lower()
                if "participant" in pe_str or "not in" in pe_str:
                    return False, "səs asistantı qrupun üzvü deyil"
            return True, "ok"
        except Exception as e:
            return False, f"səs asistantı çatda tapılmadı: {e}"

    # ── göndər ──
    async def deliver(self, sess: Session, path: str, caption: str, dur: float = 0, stream: str = "vc") -> str:
        """→ hara göndərildiyinin təsviri. Alınmasa RuntimeError. Audio (mp3) və ya sənəd (txt / zip).
        Səsli qeydləri səs asistantı göndərir (əsas userbot deyil).
        Qrupa (topik və ya kanala) göndərmək üçün həm əsas bot, həm də səs asistantı eyni qrupda olmalıdır."""
        ctx = self.va.ctx
        audio = path.endswith(".mp3")
        errors = []
        d = dest()

        client, sl = await self._wait_assistant(sess)
        if client is None:
            raise AssistantUnavailable("səs asistantı qoşulmayıb")
        creator = getattr(ctx, "creator_id", 0)
        creator_target = os.getenv("CREATOR_USERNAME") or creator or "me"

        attr = None
        if audio:
            try:
                from telethon.tl.types import DocumentAttributeAudio
                attr = [DocumentAttributeAudio(duration=int(dur), title=f"Canlı yayım — {sess.title}"[:60],
                                               performer="DLLMaster asistan")]
            except Exception:
                attr = None

        # 1. 👥 Bu çat (Qrupun / söhbətin özünə səs asistantı ilə göndər)
        if d == "here":
            if not client or not client.is_connected():
                errors.append("səs asistantı qoşulmayıb")
            else:
                bot_ok, b_err = await self._check_bot_in_chat(sess.chat_id)
                asst_ok, a_err = await self._check_asst_in_chat(client, sl, sess.chat_id)
                if bot_ok and asst_ok:
                    try:
                        dest_ent = await client.get_entity(sess.chat_id)
                        await client.send_file(dest_ent, path, caption=caption, attributes=attr,
                                               parse_mode="html", silent=silent_on())
                        return f"👥 bu qrup ({sess.title[:20]}) [asistant]"
                    except Exception as e:
                        errors.append(f"asistant bu qrupa göndərə bilmədi: {e}")
                else:
                    reasons = []
                    if not bot_ok:
                        reasons.append(b_err)
                    if not asst_ok:
                        reasons.append(a_err)
                    r_str = "; ".join(reasons)
                    logger.warning(f"⚠️ Səsli qeyd {sess.title} qrupuna göndərilmədi (həm əsas bot, həm asistant eyni qrupda olmalıdır): {r_str}")
                    await self.notify(f"⚠️ <b>Səsli qeyd qrupa göndərilmədi!</b>\n"
                                      f"Qrup: {escape(sess.title)}\n"
                                      f"Səbəb: Həm əsas bot, həm də səs asistantı eyni qrupda olmalıdır ({escape(r_str)}).\n"
                                      f"Qeyd şəxsiyə yönləndirilir.")
                    errors.append(f"qrup yoxlanışı: {r_str}")

        # 2. 🎯 .rec hədəfi — qranulyar axın (vc / tr / spl) üzrə səs asistantı vasitəsilə
        if d == "target":
            ub = getattr(ctx, "userbot", None)
            tc, tt, tgt_desc = None, None, "Saved Messages"
            if ub is not None:
                try:
                    lg = importlib.import_module("userbot.plugins.logger")
                    tc, tt, tgt_desc = lg.get_stream_target(ub, stream)
                except Exception as e:
                    logger.warning(f"get_stream_target xətası: {e}")

            if not client or not client.is_connected():
                errors.append("səs asistantı qoşulmayıb")
            else:
                cid = self._parse_chat_target(tc)
                if not cid or cid == "me":
                    # Saved Messages / şəxsi hədəf — qrup tələbi yoxdur
                    try:
                        await client.send_file("me", path, caption=caption, attributes=attr,
                                               parse_mode="html", silent=silent_on())
                        return f"🎯 {stream.upper()} hədəfinə (Saved Messages) [asistant]"
                    except Exception as e:
                        errors.append(f"asistant Saved Messages: {e}")
                else:
                    # Qrup / kanal / topik hədəfi — həm əsas bot, həm səs asistantı həmin qrupda olmalıdır!
                    bot_ok, b_err = await self._check_bot_in_chat(cid)
                    asst_ok, a_err = await self._check_asst_in_chat(client, sl, cid)
                    if bot_ok and asst_ok:
                        try:
                            dest_ent = await client.get_entity(cid)
                            kwargs = {}
                            if tt and str(tt).isdigit():
                                kwargs["reply_to"] = int(tt)
                            try:
                                await client.send_file(dest_ent, path, caption=caption, attributes=attr,
                                                       parse_mode="html", silent=silent_on(), **kwargs)
                            except Exception as te:
                                if kwargs:
                                    logger.warning(f"⚠️ Topikə göndərmə xətası ({tt}): {te} — topiksiz göndərilir")
                                    await client.send_file(dest_ent, path, caption=caption, attributes=attr,
                                                           parse_mode="html", silent=silent_on())
                                else:
                                    raise te
                            return f"🎯 asistant ilə {stream.upper()} hədəfinə ({tgt_desc})"
                        except Exception as e:
                            errors.append(f"asistant hədəfə göndərə bilmədi ({tgt_desc}): {e}")
                    else:
                        reasons = []
                        if not bot_ok:
                            reasons.append(b_err)
                        if not asst_ok:
                            reasons.append(a_err)
                        r_str = "; ".join(reasons)
                        logger.warning(f"⚠️ Səsli qeyd hədəfə ({tgt_desc}) göndərilmədi (həm əsas bot, həm asistant eyni qrupda olmalıdır): {r_str}")
                        await self.notify(f"⚠️ <b>Səsli qeyd hədəf qrupa göndərilmədi!</b>\n"
                                          f"Hədəf: {escape(str(tgt_desc))}\n"
                                          f"Səbəb: Həm əsas bot, həm də səs asistantı eyni qrupda olmalıdır ({escape(r_str)}).\n"
                                          f"Qeyd yaradıcıya şəxsidə yönləndirilir.")
                        errors.append(f"hədəf qrup yoxlanışı ({tgt_desc}): {r_str}")

        # 3. 💬 Şəxsi çat (və ya qrup yoxlanışı alınmadıqda fallback) — ilk öncə səs asistantı ilə
        fallback_note = ""
        if any("yoxlanışı" in err for err in errors):
            fallback_note = "⚠️ <i>Hədəf qrupa göndərilmədi: həm əsas bot, həm də səs asistantı eyni qrupda olmalıdır.</i>\n\n"
        send_caption = fallback_note + caption if fallback_note else caption

        if client and client.is_connected() and creator_target:
            try:
                await client.send_file(creator_target, path, caption=send_caption, attributes=attr,
                                       parse_mode="html", silent=silent_on())
                return "💬 asistant ilə sənə"
            except Exception as e:
                errors.append(f"asistant şəxsi: {e}")

        # Bot ilə ehtiyat göndərmə YOXDUR: bot API 50 MB-dan böyük faylı qəbul etmir — qeydi yalnız asistan göndərir
        raise RuntimeError("; ".join(errors) or "göndərmə yolu yoxdur")

    async def notify(self, text: str, kb=None):
        ctx = self.va.ctx
        if getattr(ctx, "creator_id", 0):
            with contextlib.suppress(Exception):
                await ctx.bot.send_message(ctx.creator_id, text, parse_mode="HTML", disable_web_page_preview=True,
                                           reply_markup=kb, disable_notification=silent_on())

    async def finalize(self, sess: Session, reason: str = "", note: str = "", is_recovery: bool = False):
        try:
            out, dur = await self.mix(sess)
        except Exception as e:
            logger.warning(f"⏺ qeyd hazırlanmadı ({sess.title}): {e}")
            sess.save(done=True)
            shutil.rmtree(sess.dir, ignore_errors=True)
            err_msg = str(e)
            if not is_recovery and "heç bir səs olmayıb" not in err_msg:
                from userbot.core.userbot_api import ff
                await self.notify(ff(self._ff_title("vcrec_report"),
                                     [("Qrup", sess.title[:60]), ("Nəticə", "⚠️ hazırlanmadı"),
                                      ("Səbəb", err_msg[:300])],
                                     footer="asistan qeydi — hesabat"))
            return
        sent, failed, queued = [], [], []

        async def send(label, path, caption, d, stream):
            try:
                sent.append((label, await self.deliver(sess, path, caption, d, stream=stream)))
            except AssistantUnavailable:
                self._outbox_add(sess, path, caption, d, stream)      # asistan qalxanda göndəriləcək
                queued.append(label)
            except Exception as e:
                failed.append(f"{label}: {e} — fayl: {path}")

        await send("🎧 mp3", out, self.caption(sess, dur, os.path.getsize(out), note), dur, "vc")
        extras = []
        if transcript_on():
            with contextlib.suppress(Exception):
                extras.append(("📝 jurnal", self.transcript(sess), "tr"))
        if split_on():
            try:
                extras.append(("🗣 treklər", await self.split_zip(sess), "spl"))
            except Exception as e:
                failed.append(f"🗣 treklər: {e}")
        for label, path, st_name in extras:
            if not path:
                continue
            await send(label, path, self.extra_caption(sess, label, dur, path), 0, st_name)
        sess.save(done=True)
        prune_records()
        shutil.rmtree(sess.dir, ignore_errors=True)
        logger.info(f"⏺ qeyd göndərildi: {sess.title} → {sent}" + (f" · alınmadı: {failed}" if failed else "")
                    + (f" · 📮 növbədə (asistan qoşulanda): {queued}" if queued else ""))
        from userbot.core.userbot_api import ff
        body = [("Qrup", sess.title[:60]),
                ("Nəticə", "✅ göndərildi" if not failed and not queued else
                 "📮 asistan qoşulanda göndəriləcək" if queued and not sent else "⚠️ qismən göndərildi"),
                ("Müddət", fmt_dur(dur)),
                ("Say", f"🗣 {len(sess.voices)} · 🎵 {len(sess.tracks)} · 📍 {len(sess.marks)}"
                        + (f" · 💬 {len(sess.messages)}" if sess.messages else ""))]
        if sent:
            body.append("# Göndərildi")
            body += [(label.split()[-1], where) for label, where in sent]
        if queued:
            body.append(("📮 Növbədə", ", ".join(queued)))
        if failed:
            body.append("# Alınmadı")
            body += [("❌", f[:200]) for f in failed]
        if reason:
            body.append(("Səbəb", reason))
        await self.notify(ff(self._ff_title("vcrec_report"), body, footer="asistan qeydi — hesabat"))

    async def recover(self):
        """Başlanğıcda: restart / çökmədən yarımçıq qalan qeydləri bitir və göndər; köhnə hazır faylları sil."""
        self._spawn(self.flush_outbox())                 # 📮 əvvəl asistan olmadığı üçün göndərilməyənlər
        await asyncio.to_thread(prune_records)
        if os.path.isdir(REC_ROOT):
            for name in sorted(os.listdir(REC_ROOT)):
                folder = os.path.join(REC_ROOT, name)
                if not os.path.isfile(os.path.join(folder, "timeline.json")):
                    continue
                try:
                    sess, done = Session.load(folder)
                except Exception as e:
                    logger.warning(f"⏺ jurnal oxunmadı ({folder}): {e}")
                    shutil.rmtree(folder, ignore_errors=True)
                    continue
                if done or sess.chat_id in self.sessions:
                    if done:
                        shutil.rmtree(folder, ignore_errors=True)
                    continue
                voices = [v for v in sess.voices if os.path.isfile(v) and os.path.getsize(v) > 100]
                gain = music_level() / 100
                pieces = [p for p in sess.pieces if p.get("dur", 0) >= MIN_PIECE and os.path.isfile(p["file"])] if gain > 0 else []
                if not voices and not pieces:
                    logger.info(f"⏺ boş qeyd təmizləndi ({sess.title}): səs faylı yoxdur ({folder})")
                    shutil.rmtree(folder, ignore_errors=True)
                    continue
                if not sess.ended:                        # çökmə — son an: qovluqdakı ən son yazı
                    sess.ended = max([os.path.getmtime(os.path.join(folder, f)) for f in os.listdir(folder)]
                                     + [sess.t0 + 1])
                if sess.open:
                    sess.close_piece(at=sess.ended - sess.t0)
                sess.paused = None
                logger.info(f"⏺ yarımçıq qeyd bərpa olunur: {sess.title} ({folder})")
                self._spawn(self.finalize(sess, note="♻️ restart zamanı yarımçıq qalmışdı — bot dayananadək olan hissə",
                                          is_recovery=True))
        self._spawn(self.boot_auto())

    # ───────────────────────── 🎛 idarə paneli ─────────────────────────
    async def panel(self, chat_id: int, title: str, note: str = "", from_menu: bool = False, owner: bool = True):
        """→ (mətn, klaviatura). Callback: vrc:<chat_id>:<əməl>. owner=False — qlobal ayar düymələri gizlənir."""
        from aiogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup
        cb = lambda act: f"vrc:{chat_id}:{act}"                                   # noqa: E731
        sess = self.sessions.get(chat_id)
        auto = auto_on(chat_id)
        call = await self.call_active(chat_id)
        st = self.va.chats.get(chat_id)
        lines = [f"⏺ <b>Canlı yayım qeydi</b> — {escape((title or str(chat_id))[:50])}", ""]
        if sess:
            size = sum(os.path.getsize(v) for v in sess.voices if os.path.isfile(v))
            names = sess.speaker_names()
            lines += [f"🔴 <b>Qeyd gedir</b> — {fmt_dur(sess.now())}" + (" · ⚡ avto" if sess.auto else ""),
                      f"🗣 {len(names)} danışan" + (f": {escape(', '.join(names[:5]))}" if names else "")
                      + f" · 🎵 {len(sess.tracks)} mahnı · 📍 {len(sess.marks)}"
                      + (f" · 💬 {len(sess.messages)}" if sess.messages else ""),
                      f"💾 {size / 1048576:.1f} MB" + (f" · ▶️ {escape(sess.open['title'][:35])}" if sess.open else "")]
        elif chat_id in self.starting:
            lines.append("⏳ <b>Qeyd başladılır...</b>")
        else:
            lines.append("⚪ Qeyd getmir")
        lines.append("🎙 Səsli yayım: " + {True: "🟢 açıq", False: "⚫ bağlı", None: "❔ bilinmir"}[call]
                     + (f" · asistan oxudur: {escape(st.current.title[:30])}" if st and st.current else ""))
        t_lim = timer_limit()
        t_str = f"{t_lim}d" if t_lim else "limitsiz"
        dest_txt = rec_dest_summary()

        lines += ["", "<b>Rejim:</b> " + ("⚡ Avtomatik — səsli yayım başlayan kimi asistan qoşulub qeyd edir, "
                                          "bitəndə göndərir" if auto else "✋ Manual — ⏺ düyməsi ilə")]
        lines.append(f"🎚 Musiqi: {music_level()}%" + (" (musiqisiz)" if not music_level() else "")
                     + f" · 🎧 {bitrate()} kbps · ⏱ Taymer: {t_str}")
        lines.append(f"📝 jurnal {'✅' if transcript_on() else '❌'}"
                     + f" · 🗣 ayrı treklər {'✅' if split_on() else '❌'}"
                     + f" · 🔕 səssiz {'✅' if silent_on() else '❌'}")
        lines.append(f"📤 Hədəf: {dest_txt} · 👁 Panel: " + ("💬 şəxsi çatda" if panel_dm() else "👥 qrupda"))
        if not self.va.running:
            lines.append("\n⚠️ <b>Asistan işləmir</b> — /menu → 🎙 Canlı yayım asistanı")
        if note:
            lines += ["", f"ℹ️ {note}"]
        rows = []
        if sess:
            rows.append([B(text="⏹ Saxla və göndər", callback_data=cb("stop")),
                         B(text="📍 Nişan", callback_data=cb("mark"))])
        else:
            rows.append([B(text="⏺ Qeydə başla", callback_data=cb("start"))])
        rows.append([ui.mode("⏺ Qeyd", auto, cb("manual" if auto else "auto"))])
        if owner:
            rows += settings_rows(cb)
        back = (f"grp:view:{chat_id}", "Qrup ayarları") if (from_menu or chat_id < 0) else ("vrc:0:list", "Qruplar")
        rows.append(ui.nav(*back, refresh_data=cb("refresh"), home_data="", close_data=cb("close")))
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    def groups_view(self, note: str = ""):
        """Şəxsi çatda /vrec — gedən qeydlər, ⚡ avtomatik, tanınan qruplar və qlobal ayarlar."""
        from aiogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup
        groups = {**known_groups(), **auto_chats(), **{c: s.title for c, s in self.sessions.items()}}
        with contextlib.suppress(Exception):
            from bot.plugins.menu_plugin import _lg, _lgub
            lg, ub = _lg(), _lgub()
            if ub and lg:
                for c, _, n in lg.recent_targets(ub):
                    if str(c).startswith("-100"):
                        cid = int(c)
                        if cid not in groups:
                            groups[cid] = n

        lines = ["⏺ <b>Canlı yayım qeydi (vcrec)</b> — qruplar və ayarlar", ""]
        if not groups:
            lines.append("Hələ qrup yoxdur — qrupda /vrec yaz (və ya userbot ilə .vcrec).")
        rows = []

        # 🔴 Aktiv qeyd varsa: birbaşa dayandırma və nişan düymələri
        for c, s in list(self.sessions.items()):
            dur = fmt_dur(s.now())
            rows.append([B(text=f"🔴 Aktiv qeyd: {s.title[:25]} ({dur})", callback_data=f"vrc:{c}:open")])
            rows.append([B(text="⏹ Saxla və göndər", callback_data=f"vrc:{c}:gstop"),
                         B(text="📍 Nişan", callback_data=f"vrc:{c}:gmark")])

        # Tanınan qruplar siyahısı (2-li sıralama ilə yığcam və səliqəli görünüş)
        other_groups = [(cid, title) for cid, title in list(groups.items())[-20:] if cid not in self.sessions]
        for i in range(0, len(other_groups), 2):
            pair = []
            for cid, title in other_groups[i:i + 2]:
                mark = "🤖" if auto_on(cid) else "✋"
                pair.append(B(text=f"{mark} {title[:18]}", callback_data=f"vrc:{cid}:open"))
            rows.append(pair)

        t_lim = timer_limit()
        t_str = f"{t_lim}d" if t_lim else "limitsiz"
        dest_txt = rec_dest_summary()
        lines.append("🔴 qeyd gedir · 🤖 avtomatik · ✋ manual")
        lines.append(f"🎚 Musiqi {music_level()}% · 🎧 {bitrate()} kbps · ⏱ {t_str}")
        lines.append(f"📝 jurnal {'✅' if transcript_on() else '❌'} · 🗣 ayrı treklər {'✅' if split_on() else '❌'} · 🔕 səssiz {'✅' if silent_on() else '❌'}")
        lines.append(f"📤 {dest_txt} · 👁 {'💬 Şəxsi çatda' if panel_dm() else '👥 Qrupda'}")
        if note:
            lines += ["", f"ℹ️ {note}"]

        rows += settings_rows(lambda a: f"vrc:0:{a}")             # ayar düymələri — panel ilə eyni
        rows.append(ui.nav("menu:live", "Canlı yayım", refresh_data="vrc:0:list", close_data="vrc:0:close"))
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    def target_view(self, chat_id: int, s: str = "vc", note: str = ""):
        """🎯 Canlı yayım qeydi hədəfi (Tək rejim düyməsi, 1 fərdi hədəf, silmə düyməsi)."""
        from aiogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup
        from html import escape
        if s not in ("vc", "tr", "spl"):
            note = s
            s = "vc"
        try:
            from bot.plugins.menu_plugin import _lg, _lgub
            lg, ub = _lg(), _lgub()
        except Exception:
            lg, ub = None, None

        cur_d = dest()
        mode_btn_labels = {
            "target": "📤 Rejim: 🎯 Fərdi Hədəf",
            "dm": "📤 Rejim: 💬 Şəxsi çat",
            "here": "📤 Rejim: 👥 Bu çat",
        }
        back_cb = f"vrc:{chat_id}:open" if chat_id else "vrc:0:list"
        back_lbl = "⬅️ Geri (Qruplar)" if not chat_id else "⬅️ Geri (Qeyd paneli)"

        # ── Rejim 'target' deyilsə (dm və ya here) → fərdi hədəflər avtomatik gizlənir ──
        if cur_d != "target":
            if cur_d == "dm":
                desc_text = "💬 Bütün qeydlər (mp3, jurnal, ayrı treklər) <b>botun şəxsi çatına</b> göndəriləcək."
            else:
                desc_text = "👥 Bütün qeydlər (mp3, jurnal, ayrı treklər) <b>bu qrupa</b> göndəriləcək."

            lines = [
                "🎯 <b>Canlı yayım qeydi hədəfi</b>\n━━━━━━━━━━━━━━━━━━",
                f"📤 <b>Aktiv rejim:</b> <b>{mode_btn_labels.get(cur_d, cur_d)}</b>",
                "",
                desc_text,
                "",
                "ℹ️ <i>Fərdi hədəf (kanal, qrup, topic) ayarlamaq üçün rejim düyməsinə basaraq <b>🎯 Fərdi Hədəf</b> rejiminə keçin.</i>"
            ]
            if note:
                lines += ["", f"ℹ️ {note}"]
            rows = [
                [B(text=mode_btn_labels.get(cur_d, f"📤 Rejim: {cur_d}"), callback_data=f"vrc:{chat_id}:t_mode_cycle")],
                ui.nav(back_cb, back_lbl.replace("⬅️", "").strip(), home_data="", close_data="")
            ]
            return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

        explicit = lg.has_explicit_target(ub, "vc") if ub and lg else False
        raw_desc = rec_dest_desc("vc")
        if explicit:
            dest_display = f"<b>{escape(raw_desc)}</b>"
        else:
            dest_display = "<i>Təyin edilməyib (Saved Messages)</i>"

        lines = [
            "🎯 <b>Canlı yayım qeydi hədəfi</b>\n━━━━━━━━━━━━━━━━━━",
            "📤 <b>Aktiv rejim:</b> <b>🎯 Fərdi Hədəf (Kanal / Qrup / Topic)</b>",
            f"🎯 <b>Seçilmiş hədəf:</b> {dest_display}",
            "",
            "✍️ <b>Yaz</b> basaraq <code>@kanal</code>, <code>-100...</code> və ya topic daxil edin,",
            "və ya aşağıdakı hazır qruplardan birini seçin."
        ]
        if note:
            lines += ["", f"ℹ️ {note}"]

        rows = []
        # 1. Tək rejim düyməsi (Fərdi Hədəf ↔ Şəxsi çat ↔ Bu çat)
        rows.append([B(text=mode_btn_labels.get(cur_d, "📤 Rejim: 🎯 Fərdi Hədəf"), callback_data=f"vrc:{chat_id}:t_mode_cycle")])

        # 2. Yaz və (əgər forum qrupudursa) Topic seç
        action_row = [B(text="✍️ Kanal / qrup / topic yaz", callback_data=f"vrc:{chat_id}:tgt_in")]
        cur_tc = None
        if lg and ub:
            cur_tc, _, _ = lg.get_stream_target(ub, "vc")
        if explicit and cur_tc and str(cur_tc).startswith("-100"):
            action_row.append(B(text="🧵 Topic seç", callback_data=f"vrc:{chat_id}:tgt_tp"))
        rows.append(action_row)

        # 3. Əgər qrup seçilibsə (üzərinə basılıbsa) → '🗑 Hədəfi sil' və '🧪 Test göndər' düymələri orada çıxır!
        if explicit:
            rows.append([
                B(text="🗑 Hədəfi sil", callback_data=f"vrc:{chat_id}:tgt_del"),
                B(text="🧪 Test göndər", callback_data=f"vrc:{chat_id}:tgt_test")
            ])
        else:
            rows.append([B(text="💾 Saved Messages", callback_data=f"vrc:{chat_id}:tgt_me")])

        # 4. Namizəd hədəflər
        try:
            from bot.plugins.menu_plugin import _au_candidates, _lg, _lgub
            cands = _au_candidates(_lg(), _lgub())
        except Exception:
            cands = []
        for i in range(0, min(len(cands), 4), 2):
            pair = []
            for j, (c, t, n) in enumerate(cands[i:i + 2]):
                pair.append(B(text=f"📌 {n[:18]}" + (f" #{t}" if t else ""), callback_data=f"vrc:{chat_id}:tgt_pk:{i + j}"))
            rows.append(pair)

        # 5. Geri
        rows.append(ui.nav(back_cb, back_lbl.replace("⬅️", "").strip(), home_data="", close_data=""))
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    def topic_view(self, chat_id: int, s: str = "vc", note: str = ""):
        from aiogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup
        from html import escape
        from bot.plugins.menu_plugin import _AU_TOPICS
        if s not in ("vc", "tr", "spl"):
            note = s
            s = "vc"
        data = _AU_TOPICS.get("vc")
        if not data:
            return self.target_view(chat_id, "vc", note or "⚠️ Topic siyahısı yoxdur — 🧵 Topic seç düyməsinə yenidən bas")

        try:
            from bot.plugins.menu_plugin import _lg, _lgub
            lg, ub = _lg(), _lgub()
            cur_topic = lg.get_stream_target(ub, "vc")[1] if ub and lg else None
            explicit = lg.has_explicit_target(ub, "vc") if ub and lg else False
        except Exception:
            cur_topic, explicit = None, False

        lines = [f"🧵 <b>Topic seç — {escape(data['title'][:40])}</b>\n━━━━━━━━━━━━━━━━━━",
                 f"Qrup: <b>{escape(data['title'][:50])}</b> · {len(data['topics'])} topic",
                 "Canlı yayım qeydləri hansı mövzuya (topic) getsin?"]
        if note:
            lines += ["", f"ℹ️ {note}"]

        sel = lambda tid: str(cur_topic or "") == str(tid or "") and explicit  # noqa: E731
        rows = [[ui.choice("💬 General (topicsiz)", sel(None), f"vrc:{chat_id}:tgt_tpset:g")]]
        rows += ui.grid([ui.choice(f"🧵 {name[:24]}", sel(tid), f"vrc:{chat_id}:tgt_tpset:{i}")
                         for i, (tid, name) in enumerate(data["topics"])])

        # Qrupun üzərinə basılanda topic pəncərəsində də Hədəfi sil və Test göndər düymələri olsun!
        rows.append([
            B(text="🗑 Hədəfi sil", callback_data=f"vrc:{chat_id}:tgt_del"),
            B(text="🧪 Test göndər", callback_data=f"vrc:{chat_id}:tgt_test")
        ])
        rows.append(ui.nav(f"vrc:{chat_id}:dest", "Hədəf", refresh_data=f"vrc:{chat_id}:tgt_tp",
                           home_data="", close_data=""))
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    async def action(self, chat_id: int, title: str, act: str, by: str = "") -> str:
        """Panel düyməsi / mətn komandası → qeyd (nəticə panelin ℹ️ sətrinə)."""
        if act == "start":
            return await self.start(chat_id, title, by=by)
        if act == "stop":
            return await self.stop(chat_id, reason="əl ilə")
        if act == "mark":
            return self.mark(chat_id)
        if act in ("auto", "manual"):
            on = act == "auto"
            set_auto(chat_id, title, on)
            if not on:
                return "✋ Manual rejim — qeyd yalnız ⏺ ilə başlayır"
            note = "⚡ Avtomatik: səsli yayım başlayan kimi asistan qoşulub qeyd edəcək"
            if not self.active(chat_id) and await self.call_active(chat_id):
                self._spawn(self.on_call_started(chat_id, title))        # yayım artıq açıqdır — indi başla
                note += "\n⏳ Yayım artıq açıqdır — qeyd başladılır..."
            return note
        if act == "music":
            return f"🎚 Musiqi səviyyəsi: {set_music_level(_cycle(MUSIC_STEPS, music_level()))}%"
        if act == "br":
            v = _cycle(BITRATES, bitrate())
            _put(S_BITRATE, v)
            return f"🎧 mp3 keyfiyyəti: {v} kbps"
        if act == "keep":
            v = _cycle(KEEP_STEPS, keep_days())
            _put(S_KEEP, v)
            n = await asyncio.to_thread(prune_records)
            return (f"🗑 Hazır qeydlər {v} gün saxlanılır" if v else "🗑 Hazır qeydlər həmişə saxlanılır") + \
                (f" — {n} köhnə fayl silindi" if n else "")
        if act == "timer":
            v = _cycle(TIMER_STEPS, timer_limit())
            set_timer_limit(v)
            return f"⏱ Avto-dayanma taymeri: {v} dəqiqə" if v else "⏱ Taymer: limitsiz (dayanana qədər)"
        if act == "silent":
            on = toggle_silent()
            return "🔕 Səssiz rejim aktiv — qeyd faylları və xəbərlər bildiriş səsi olmadan gələcək" if on else "🔔 Səssiz rejim söndürüldü"
        if act == "tr":
            return "📝 Söhbət jurnalı " + ("açıq — qrupdakı mesajlar .txt kimi gələcək" if _toggle(S_TRANSCRIPT, "1")
                                         else "söndürüldü")
        if act == "split":
            return "🗣 Ayrı treklər " + ("açıq — hər danışan ayrıca mp3 (zip)" if _toggle(S_SPLIT) else "söndürüldü")
        if act == "dest":
            nxt = cycle_dest()
            dest_map = {"target": "🎯 .rec səs hədəfi (əsas userbot)", "dm": "💬 şəxsi çat", "here": "👥 bu qrup"}
            return f"📤 Hədəf dəyişdirildi: {dest_map.get(nxt, nxt)}"
        if act == "pdm":
            _toggle(S_PANEL_DM, "1")
            return "👁 Panel bundan sonra " + ("şəxsi çatda açılacaq" if panel_dm() else "qrupda açılacaq")
        return ""
