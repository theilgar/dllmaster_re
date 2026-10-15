import asyncio
import json

from bot.core import cpu_pool


def test_poll_progress_reads_file_and_calls_back(tmp_path):
    p = tmp_path / "x.prog"
    seen = []

    async def go():
        t = asyncio.ensure_future(cpu_pool._poll_progress(str(p), seen.append))
        await asyncio.sleep(0.2)
        p.write_text(json.dumps({"done": 10, "total": 100, "speed": 5, "eta": 3}))
        await asyncio.sleep(1.3)
        t.cancel()

    asyncio.run(go())
    assert seen and seen[0]["done"] == 10 and seen[0]["speed"] == 5


def test_worker_hook_writes_progress(tmp_path, monkeypatch):
    import sys
    import types
    from bot.core import ytdl_worker

    hooks = {}

    class FakeYDL:
        def __init__(self, opts):
            hooks["h"] = opts["progress_hooks"][0]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=True):
            hooks["h"]({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 200, "speed": 1000, "eta": 2})
            return {"requested_downloads": [{"filepath": str(tmp_path / "a.m4a")}]}

        def sanitize_info(self, info):
            return info

    fake = types.ModuleType("yt_dlp")
    fake.YoutubeDL = FakeYDL
    monkeypatch.setitem(sys.modules, "yt_dlp", fake)
    cancel = str(tmp_path / "c1")
    ytdl_worker.ytdl_download("u", {}, cancel)
    data = json.loads((tmp_path / "c1.prog").read_text())
    assert data == {"done": 50, "total": 200, "speed": 1000, "eta": 2}


def test_worker_writes_finished_summary(tmp_path, monkeypatch):
    import sys
    import types
    from bot.core import ytdl_worker

    hooks = {}

    class FakeYDL:
        def __init__(self, opts): hooks["h"] = opts["progress_hooks"][0]
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, url, download=True):
            hooks["h"]({"status": "finished", "downloaded_bytes": 4000000, "elapsed": 2.0})
            return {"requested_downloads": [{"filepath": str(tmp_path / "a.m4a")}]}
        def sanitize_info(self, info): return info

    fake = types.ModuleType("yt_dlp")
    fake.YoutubeDL = FakeYDL
    monkeypatch.setitem(sys.modules, "yt_dlp", fake)
    ytdl_worker.ytdl_download("u", {}, str(tmp_path / "c2"))
    d = json.loads((tmp_path / "c2.prog").read_text())
    assert d["finished"] and d["avg"] == 2000000 and d["elapsed"] == 2.0


def test_final_progress_delivers_last_value(tmp_path):
    p = tmp_path / "z.prog"
    p.write_text(json.dumps({"finished": True, "done": 1, "avg": 5}))
    got = []
    asyncio.run(cpu_pool._final_progress(str(p), got.append))
    assert got and got[0]["finished"]
