import asyncio

from bot.plugins import shazam_plugin as sp


def _setup(monkeypatch, tmp_path, hit_at):
    """hit_at — neçənci Shazam cəhdində tapılsın (None — heç vaxt)."""
    calls = {"clips": [], "recog": 0}

    async def fake_clip(inp, out, start, dur, af):
        calls["clips"].append((round(start, 1), dur, af))
        open(out, "wb").write(b"x" * 2000)
        return True

    class FakeShazam:
        async def recognize(self, path):
            calls["recog"] += 1
            if hit_at is not None and calls["recog"] == hit_at:
                return {"track": {"title": "Med Cezir", "subtitle": "Ceza"}}
            return {}

    async def fake_duration(p):
        return 200.0

    monkeypatch.setattr(sp, "_clip", fake_clip)
    monkeypatch.setattr(sp, "_get_shazam", lambda: FakeShazam())
    monkeypatch.setattr(sp, "TEMP_DIR", str(tmp_path))
    import common.media as cm
    monkeypatch.setattr(cm, "duration_of", fake_duration)
    f = tmp_path / "in.mp3"
    f.write_bytes(b"y" * 5000)
    return str(f), calls


def test_stops_at_first_success(monkeypatch, tmp_path):
    f, calls = _setup(monkeypatch, tmp_path, hit_at=1)
    item = asyncio.run(sp.recognize_file(f, verify=False))
    assert item["title"] == "Med Cezir" and item["stage_no"] == 1 and calls["recog"] == 1


def test_hard_song_goes_through_stages(monkeypatch, tmp_path):
    f, calls = _setup(monkeypatch, tmp_path, hit_at=7)
    seen = []
    item = asyncio.run(sp.recognize_file(f, on_stage=lambda i, n, l: seen.append((i, l)), verify=False))
    assert item and item["stage_no"] == 7 and "sped" in item["stage"]
    assert calls["recog"] == 7 and [i for i, _ in seen] == list(range(1, 8))
    starts = {c[0] for c in calls["clips"]}
    assert len(starts) > 3                                  # fərqli hissələr sınandı
    assert any("asetrate" in c[2] for c in calls["clips"])  # sürət/pitch variantı


def test_returns_none_if_all_fail(monkeypatch, tmp_path):
    f, calls = _setup(monkeypatch, tmp_path, hit_at=None)
    assert asyncio.run(sp.recognize_file(f)) is None
    assert calls["recog"] == len(sp.STAGES)


def test_confirmation_flag(monkeypatch, tmp_path):
    f, calls = _setup(monkeypatch, tmp_path, hit_at=1)
    # eyni mahnı ikinci parçada da çıxsın → təsdiqləndi
    import bot.plugins.shazam_plugin as spm

    class Shz:
        n = 0
        def __init__(self, same): self.same = same
        async def recognize(self, path):
            Shz.n += 1
            if Shz.n == 1 or self.same:
                return {"track": {"title": "Med Cezir", "subtitle": "Ceza"}}
            return {}

    monkeypatch.setattr(spm, "_get_shazam", lambda: Shz(True))
    item = asyncio.run(spm.recognize_file(f))
    assert item["confirmed"] is True and item["alt"] is None

    Shz.n = 0
    monkeypatch.setattr(spm, "_get_shazam", lambda: Shz(False))
    item = asyncio.run(spm.recognize_file(f))
    # təsdiq parçası heç nə tapmadı → adi parçadakı nəticə ZƏİF namizəd kimi qalır
    assert item["confirmed"] is False and item["title"] == "Med Cezir"


def test_processed_stage_hit_needs_confirmation(monkeypatch, tmp_path):
    """Səs emalı (gücləndirmə) səs-küyü mahnı kimi göstərə bilər: təsdiqsiz emal nəticəsi atılır."""
    f, calls = _setup(monkeypatch, tmp_path, hit_at=None)
    import bot.plugins.shazam_plugin as spm

    class Shz:
        async def recognize(self, path):
            if "rf_" in path and path.endswith("_5.mp3"):          # yalnız 5-ci (emal olunmuş) mərhələ "tapır"
                return {"track": {"title": "Yanlış", "subtitle": "Hind"}}
            return {}

    monkeypatch.setattr(spm, "_get_shazam", lambda: Shz())
    assert asyncio.run(spm.recognize_file(f)) is None
