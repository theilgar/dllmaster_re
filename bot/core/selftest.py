"""
🧪 Özünütest — bot/core/selftest.py

/menu → Bot idarəsi → 🛠 Texniki iş rejimi → 🧪 Testlər: botun REAL işlək olduğunu yoxlayır
(mahnı axtarışı, YouTube məlumatı, qısa video yükləmə, PO token / cookie hesabları, depo kanalı, baza, ffmpeg,
Shazam, texniki iş rejiminin qapısı). Texniki işdən əvvəl və sonra problemi tez tapmaq üçündür.
Hər test: (ad, ✅/❌, qısa izah, müddət). Testlər heç nəyi dəyişmir (yükləmə testi faylı dərhal silir).
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time

logger = logging.getLogger(__name__)

TEST_VIDEO = "https://www.youtube.com/watch?v=jNQXAC9IVRw"     # YouTube-un ilk videosu (19 san.) — sabit, qısa
TEST_QUERY = "Eminem Lose Yourself"

# açar: (başlıq, uzun çəkə bilər?)
TESTS = {
    "db": ("🗄 Baza", False),
    "ffmpeg": ("🎞 ffmpeg", False),
    "search_ytm": ("🎵 Axtarış: YT Music", False),
    "search_yt": ("▶️ Axtarış: YouTube", False),
    "extract": ("🌐 YouTube məlumatı", True),
    "download": ("⬇️ Real yükləmə (19 san. video)", True),
    "pot": ("🔑 PO token / cookie hesabları", False),
    "depo": ("📦 Depo kanalı", False),
    "shazam": ("🎧 Shazam", False),
    "gate": ("🛠 Texniki iş qapısı", False),
}
ORDER = list(TESTS)


async def _db(ctx):
    from common.database import get_db
    row = await asyncio.to_thread(lambda: get_db().sql_one("SELECT 1 AS ok, COUNT(*) AS n FROM audio_cache"))
    return True, f"işləyir · depoda {row[1]} mahnı"


async def _ffmpeg(ctx):
    path = shutil.which("ffmpeg")
    if not path:
        return False, "ffmpeg tapılmadı (yükləmə çevirməsi işləməyəcək)"
    proc = await asyncio.create_subprocess_exec(path, "-version", stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.DEVNULL)
    out, _ = await asyncio.wait_for(proc.communicate(), 8)
    return True, out.decode(errors="ignore").splitlines()[0][:60]


async def _search(ctx, src: str):
    res = await asyncio.wait_for(ctx.youtube_manager.youtube_search(TEST_QUERY, limit=3, source=src), 30)
    if not res:
        return False, "nəticə yoxdur"
    return True, f"{len(res)} nəticə · «{res[0]['title'][:40]}»"


async def _extract(ctx):
    from bot.core import cpu_pool, youtube_handler as yh
    opts = {"quiet": True, "noplaylist": True, "skip_download": True, "socket_timeout": 20,
            **yh.JS_OPTS, **yh.cookie_opts(), **yh._px().ytdlp_opts("search")}
    info = await cpu_pool.ytdl_extract(TEST_VIDEO, opts, timeout=60)
    return True, f"«{(info.get('title') or '?')[:35]}» · {info.get('duration')} san."


async def _download(ctx):
    from bot.core import youtube_handler as yh
    t0 = time.monotonic()
    path = await asyncio.wait_for(yh.download_as_m4a(TEST_VIDEO, "selftest", "firefox"), 120)
    try:
        size = os.path.getsize(path)
        if size < 10 * 1024:
            return False, f"fayl çox kiçikdir ({size} bayt)"
        from common.formatting import fmt_bytes
        return True, f"{fmt_bytes(size)} · {os.path.splitext(path)[1]} · {time.monotonic() - t0:.1f} san."
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


async def _pot(ctx):
    from bot.core import youtube_handler as yh
    st = await asyncio.to_thread(yh.pot_status)
    bits, ok = [], True
    bits.append("PO token ✅" if st.get("http") else ("PO token ⚠️ port açıq, /ping yoxdur" if st.get("ok") else "PO token ❌ yoxdur"))
    ok = ok and bool(st.get("http"))
    cd, cb = yh.cooldown_left("download"), yh.cookie_block_left("download")
    if cd:
        bits.append(f"cooldown {cd} san.")
        ok = False
    if cb:
        bits.append(f"bütün hesablar bloklu ({cb // 60 + 1} dəq.)")
        ok = False
    elif not cd:
        bits.append("cookie hesabı işlək")
    return ok, " · ".join(bits)


async def _depo(ctx):
    from bot.core import audio_cache
    cid = await audio_cache.depo_chat_id(ctx.bot)
    if not cid:
        return False, (audio_cache.depo_status().get("error") or "kanal əlçatan deyil")[:80]
    return True, f"əlçatandır ({cid})"


async def _shazam(ctx):
    from bot.plugins import shazam_plugin as sp
    sp._get_shazam()
    return True, f"hazırdır · {len(sp.STAGES)} mərhələ"


async def _gate(ctx):
    from bot.plugins import maintenance_plugin as mp, report_plugin
    from common import maintenance as mt
    mode = mt.mode()
    blocked = [c for c in ("music", "play", "mix", "shazam") if c not in mp.ALLOWED_COMMANDS]
    allowed = sorted(mp.ALLOWED_COMMANDS)[:5]
    cb_ok = any("rp:go".startswith(p) for p in mp.ALLOWED_CB_PREFIXES)
    state = "AKTİV" if mt.is_active() else "söndürülüb"
    ok = cb_ok                                          # «Adminə bildir» düyməsi rejimdə də işləməlidir
    return ok, (f"{state} · növ: {mode} · bloklanan: /{', /'.join(blocked)} · açıq: /{', /'.join(allowed)}"
                + ("" if cb_ok else " · ⚠️ «Adminə bildir» düyməsi rejimdə bloklanır"))[:200]


_FN = {"db": _db, "ffmpeg": _ffmpeg, "search_ytm": lambda c: _search(c, "ytm"), "search_yt": lambda c: _search(c, "yt"),
       "extract": _extract, "download": _download, "pot": _pot, "depo": _depo, "shazam": _shazam, "gate": _gate}


async def run_one(ctx, key: str) -> dict:
    title = TESTS[key][0]
    t0 = time.monotonic()
    try:
        ok, detail = await _FN[key](ctx)
    except asyncio.TimeoutError:
        ok, detail = False, "vaxt bitdi"
    except Exception as e:
        logger.info(f"selftest {key}: {e!r}")
        ok, detail = False, f"{type(e).__name__}: {str(e)[:110]}"
    return {"key": key, "title": title, "ok": bool(ok), "detail": detail, "secs": time.monotonic() - t0}


async def run(ctx, keys=None, progress=None) -> list:
    """Seçilmiş testləri ardıcıl işlədir; progress(nəticələr_hələlik, indiki_açar) hər addımdan əvvəl çağırılır."""
    out = []
    for k in (keys or ORDER):
        if progress:
            try:
                r = progress(out, k)
                if asyncio.iscoroutine(r):
                    await r
            except Exception as e:
                logger.debug(f"selftest progress: {e!r}")
        out.append(await run_one(ctx, k))
    return out


def render(results: list, running: str = "") -> str:
    from html import escape
    lines = []
    for r in results:
        lines.append(f"{'✅' if r['ok'] else '❌'} <b>{r['title']}</b> · {r['secs']:.1f} san.\n      <i>{escape(r['detail'])}</i>")
    if running:
        lines.append(f"⏳ <b>{TESTS[running][0]}</b> ...")
    bad = [r for r in results if not r["ok"]]
    if results and not running:
        lines.append("\n" + (f"❌ <b>{len(bad)} problem</b> / {len(results)} test" if bad
                             else f"✅ <b>Hamısı qaydasındadır</b> ({len(results)} test)"))
    return "\n".join(lines)
