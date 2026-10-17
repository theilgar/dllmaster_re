"""📊 «GitHub-a göndərilir» proqres barı: git push faizləri → bar (botun /menu → Görünüş ayarı ilə) (bot/updater/progress.py)."""
import asyncio
import subprocess

import pytest

import bot.updater.git_client as G
import bot.updater.progress as P
import common.menu as cm


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    bare, work = tmp_path / "remote.git", tmp_path / "work"
    sh(tmp_path, "git", "init", "--bare", "-b", "main", str(bare))
    sh(tmp_path, "git", "clone", str(bare), str(work))
    sh(work, "git", "config", "user.name", "T")
    sh(work, "git", "config", "user.email", "t@t")
    (work / "a.txt").write_text("1\n")
    sh(work, "git", "add", "a.txt")
    sh(work, "git", "commit", "-m", "ilk")
    sh(work, "git", "push", "-u", "origin", "main")
    monkeypatch.setattr(G, "_token", lambda: "")
    monkeypatch.delenv("GITHUB_REMOTE", raising=False)
    return work


# ───────────── git faizlərinin oxunması ─────────────
@pytest.mark.parametrize("line,expected", [
    ("Counting objects:   0% (0/20)", 0.0), ("Counting objects: 100% (20/20), done.", 0.10),
    ("Compressing objects:  50% (5/10)", 0.25), ("Compressing objects: 100% (10/10), done.", 0.40),
    ("Writing objects:  50% (10/20)", 0.70), ("Writing objects: 100% (20/20), 1.2 MiB | 2 MiB/s, done.", 1.0),
    ("remote: Resolving deltas: 100% (3/3)", None), ("To https://github.com/x/y.git", None), ("", None),
])
def test_git_progress_lines_map_to_the_push_stage(line, expected):
    got = G.push_fraction(line)
    assert (got is None) if expected is None else abs(got - expected) < 1e-9


def test_git_stream_hands_every_progress_line_to_the_callback(repo):
    (repo / "b.txt").write_text("x" * 5000)
    sh(repo, "git", "add", "b.txt")
    sh(repo, "git", "commit", "-m", "ikinci")
    lines = []
    rc, out, err = G.git_stream(repo, "push", "--progress", "origin", "main", on_line=lines.append)
    assert rc == 0 and any("Writing objects" in ln for ln in lines)
    fractions = [f for f in map(G.push_fraction, lines) if f is not None]
    assert fractions and fractions == sorted(fractions) and fractions[-1] == 1.0     # artan və 100%-ə çatır


def test_git_stream_reports_failures_and_survives_a_broken_callback(repo):
    rc, out, err = G.git_stream(repo, "push", "--progress", "yoxdur", "main", on_line=lambda ln: 1 / 0)
    assert rc != 0 and "yoxdur" in err
    rc, _, err = G.git_stream(repo, "status", "--short")
    assert rc == 0 and err == ""


def test_git_stream_times_out(repo, monkeypatch):
    real = subprocess.Popen
    monkeypatch.setattr(G.subprocess, "Popen", lambda cmd, **kw: real(["sleep", "30"], **kw))
    rc, out, err = G.git_stream(repo, "push", timeout=0.3)
    assert rc == 124 and "vaxt bitdi" in err


# ───────────── push_update mərhələləri ─────────────
def test_push_update_reports_increasing_stages_ending_at_100(repo):
    (repo / "a.txt").write_text("2\n")
    seen = []
    res = G.push_update(repo, ["a.txt"], "yeni", progress=lambda f, label: seen.append((f, label)))
    assert res["ok"]
    fracs = [f for f, _ in seen]
    assert fracs == sorted(fracs) and fracs[0] < 0.1 and fracs[-1] == 1.0
    labels = [label for _, label in seen]
    for expected in ("Repo yoxlanılır", "Fayllar seçilir", "Fayllar əlavə olunur (1)", "Commit yaradılır", "GitHub-a yüklənir", "Hazırdır"):
        assert expected in labels
    assert labels.index("Commit yaradılır") < labels.index("GitHub-a yüklənir") < labels.index("Hazırdır")


def test_failed_push_never_claims_100(repo, monkeypatch):
    sh(repo, "git", "remote", "set-url", "origin", "/yoxdur/repo.git")
    (repo / "a.txt").write_text("3\n")
    seen = []
    res = G.push_update(repo, ["a.txt"], "yeni", progress=lambda f, label: seen.append(f))
    assert not res["ok"] and res["committed"] and max(seen) < 1.0


def test_a_broken_progress_callback_cannot_break_the_push(repo):
    (repo / "a.txt").write_text("4\n")
    res = G.push_update(repo, ["a.txt"], "yeni", progress=lambda f, label: 1 / 0)
    assert res["ok"]
    (repo / "a.txt").write_text("5\n")
    assert G.push_update(repo, ["a.txt"], "yeni")["ok"]                       # progress verilməyibsə əvvəlki kimi


def test_push_all_passes_progress_through(repo):
    (repo / "c.txt").write_text("yeni fayl\n")
    seen = []
    res = G.push_all(repo, "tam", None, progress=lambda f, label: seen.append(f))
    assert res["ok"] and seen[-1] == 1.0


# ───────────── görünüş: botun /ui ayarı ─────────────
@pytest.mark.parametrize("bar,expected", [("block", "[▰▰▰▰▱▱▱▱▱▱] 40%"), ("solid", "[■■■■......] 40%"), ("vert", "[||||      ] 40%")])
def test_bar_follows_the_menus_bar_style(monkeypatch, bar, expected):
    monkeypatch.setattr(cm, "get_ui_bar", lambda: bar)
    assert P.bar_line(0.4) == expected
    assert P.bar_line(1.5).endswith("100%") and P.bar_line(-1).endswith("0%") and P.bar_line(None).endswith("0%")


@pytest.mark.parametrize("style,open_tag", [("box", "<pre>"), ("script", "<blockquote expandable><code>"), ("quote", "<blockquote><code>")])
def test_panel_text_follows_the_menus_block_style(monkeypatch, style, open_tag):
    monkeypatch.setattr(cm, "get_ui_bar", lambda: "block")
    monkeypatch.setattr(cm, "get_ui_style", lambda: style)
    text = P.panel_text(0.7, "GitHub-a yüklənir", files=12)
    assert text.startswith("⏳ <b>GitHub-a göndərilir</b>\n" + open_tag)
    assert "[▰▰▰▰▰▰▰▱▱▱] 70%" in text and "Mərhələ: GitHub-a yüklənir" in text and "Fayl: 12" in text


def test_panel_text_escapes_labels(monkeypatch):
    monkeypatch.setattr(cm, "get_ui_bar", lambda: "block")
    assert "&lt;b&gt;" in P.panel_text(0.1, "<b>pis</b>")


def test_button_text_is_one_short_line(monkeypatch):
    monkeypatch.setattr(cm, "get_ui_bar", lambda: "block")
    t = P.button_text(0.3, "Commit yaradılır")
    assert t == "⏳ [▰▰▱▱▱▱▱▱] 30% · Commit yaradılır" and len(P.button_text(0.3, "x" * 200)) <= 60


# ───────────── run_with_progress ─────────────
@pytest.mark.asyncio
async def test_progress_is_rendered_while_the_work_runs_and_result_is_returned():
    import time
    shown = []

    async def render(frac, label):
        shown.append((frac, label))

    def work(a, b, progress=None):
        progress(0.2, "bir")
        time.sleep(0.06)
        progress(0.6, "iki")
        time.sleep(0.06)
        return a + b
    assert await P.run_with_progress(render, work, 1, 2, interval=0.02) == 3
    assert shown[0] == (0.0, "Hazırlanır") and (0.2, "bir") in shown and (0.6, "iki") in shown
    assert [f for f, _ in shown] == sorted(f for f, _ in shown)


@pytest.mark.asyncio
async def test_render_errors_do_not_stop_the_work_and_work_errors_propagate():
    calls = []

    async def bad_render(frac, label):
        calls.append(frac)
        raise RuntimeError("telegram flood")

    def ok(progress=None):
        import time
        time.sleep(0.05)
        return "bitdi"
    assert await P.run_with_progress(bad_render, ok, interval=0.01) == "bitdi" and calls

    def boom(progress=None):
        raise ValueError("push alınmadı")
    with pytest.raises(ValueError, match="push alınmadı"):
        await P.run_with_progress(bad_render, boom, interval=0.01)
    assert not [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]   # ticker təmizlənib


@pytest.mark.asyncio
async def test_same_state_is_not_re_rendered():
    shown = []

    async def render(frac, label):
        shown.append(frac)

    def work(progress=None):
        import time
        time.sleep(0.1)
    await P.run_with_progress(render, work, interval=0.01)
    assert shown == [0.0]                                                      # dəyişiklik yoxdursa Telegram-a təkrar sorğu getmir
