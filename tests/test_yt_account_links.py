"""🔘 Google / YouTube hesabının düymələri: basanda hesabın brauzer profili serverin ekranında açılır (yt_accounts, menyu)."""
import asyncio
import json
import subprocess
import types

import pytest

from bot.core import yt_accounts as ya
from bot.menus.handlers import youtube_flow as yf
from bot.menus.state import yta_inp
from bot.menus.views import youtube_accs as V


@pytest.fixture
def store(monkeypatch):
    """Hesab siyahısı üçün saxta ayar anbarı + sabit brauzer mühiti."""
    db = {}
    monkeypatch.setattr(ya, "_get", lambda key, default=None: db.get(key, default))
    monkeypatch.setattr(ya, "_set", lambda key, value: db.__setitem__(key, value))
    monkeypatch.setattr(ya, "_gui_env", lambda: ({"DISPLAY": ":0"}, []))
    yta_inp.update(stage=None, acc=None)
    return db


def add_account(store, **kw):
    acc = {"id": "ab12cd34", "kind": "browser", "name": "Google hesabı 4", "browser": "chrome",
           "profile": "/srv/bot/data/yt_profiles/ab12cd34", "managed": True, "roles": ["depo"], "on": True}
    acc.update(kw)
    rest = [a for a in json.loads(store.get(ya.S_ACCOUNTS, "[]")) if a["id"] != acc["id"]]
    store[ya.S_ACCOUNTS] = json.dumps(rest + [acc])
    return acc


# ───────────── düymələr: saxlanma və yoxlama ─────────────
def test_default_is_a_single_youtube_button_until_the_owner_saves_their_own(store):
    acc = add_account(store)
    assert ya.links(acc) == [{"name": "▶️ YouTube", "url": "https://www.youtube.com/"}]
    ya.add_link(acc["id"], "Gmail", "mail.google.com")
    assert [x["name"] for x in ya.links(ya.get(acc["id"]))] == ["▶️ YouTube", "Gmail"]       # standart düymə də saxlanır
    assert ya.links(ya.get(acc["id"]))[1]["url"] == "https://mail.google.com"


def test_rename_remove_and_the_limit(store):
    acc = add_account(store)
    aid = acc["id"]
    assert ya.rename_link(aid, 0, "  Mənim   YouTube  ") and ya.links(ya.get(aid))[0]["name"] == "Mənim YouTube"
    assert not ya.rename_link(aid, 5, "x") and not ya.rename_link(aid, 0, "   ")
    for i in range(ya.MAX_LINKS - 1):
        ya.add_link(aid, f"L{i}", f"site{i}.com")
    with pytest.raises(ValueError, match="ən çox"):
        ya.add_link(aid, "çox", "x.com")
    assert ya.remove_link(aid, 0) and not ya.remove_link(aid, 99) and len(ya.links(ya.get(aid))) == ya.MAX_LINKS - 1
    assert ya.clean_link_name("a" * 50) == "a" * 24


@pytest.mark.parametrize("raw,expected", [("mail.google.com", "https://mail.google.com"),
                                          ("http://x.org/a?b=1", "http://x.org/a?b=1"),
                                          ("  https://studio.youtube.com ", "https://studio.youtube.com")])
def test_urls_are_normalised(raw, expected):
    assert ya.normalize_url(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "a b.com", "javascript:alert(1)", "file:///etc/passwd", "ftp://x.com", "localhost",
                                 "https://", "-oops.com " * 2, "x" * 400])
def test_dangerous_or_broken_links_are_rejected(raw):
    with pytest.raises(ValueError):
        ya.normalize_url(raw)


def test_add_link_needs_a_name_and_an_account(store):
    acc = add_account(store)
    with pytest.raises(ValueError):
        ya.add_link(acc["id"], "  ", "x.com")
    with pytest.raises(ValueError):
        ya.add_link("yoxdur", "A", "x.com")


def test_only_accounts_with_a_server_profile_get_buttons(store, tmp_path):
    assert ya.can_open(add_account(store))                                              # öz Chrome profilimiz
    assert not ya.can_open(add_account(store, id="f1", kind="file", profile=""))
    assert not ya.can_open(add_account(store, id="b2", managed=False, profile="/yoxdur/Default"))
    assert ya.can_open(add_account(store, id="b3", managed=False, profile=str(tmp_path)))
    assert not ya.can_open(None) and not ya.can_open({"kind": "main"})


# ───────────── brauzer əmri ─────────────
def test_chromium_own_profile_uses_user_data_dir(store, monkeypatch):
    monkeypatch.setattr(ya.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "google-chrome-stable" else None)
    cmd, env = ya.browser_cmd(add_account(store), "https://mail.google.com")
    assert cmd[0] == "/usr/bin/google-chrome-stable" and "--user-data-dir=/srv/bot/data/yt_profiles/ab12cd34" in cmd
    assert cmd[-2:] == ["--new-window", "https://mail.google.com"] and not any(c.startswith("--profile-directory") for c in cmd)
    assert env == {"DISPLAY": ":0"}


def test_chromium_regular_profile_uses_profile_directory(store, monkeypatch):
    monkeypatch.setattr(ya.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "brave" else None)
    acc = add_account(store, browser="brave", managed=False, profile="/home/u/.config/BraveSoftware/Brave-Browser/Profile 2")
    cmd, _ = ya.browser_cmd(acc, "https://www.youtube.com/")
    assert cmd[0] == "/usr/bin/brave" and "--user-data-dir=/home/u/.config/BraveSoftware/Brave-Browser" in cmd
    assert "--profile-directory=Profile 2" in cmd


def test_firefox_family_opens_its_profile_and_missing_browser_is_reported(store, monkeypatch):
    monkeypatch.setattr(ya.shutil, "which", lambda name: "/usr/bin/firefox" if name == "firefox" else None)
    acc = add_account(store, browser="firefox", managed=False, profile="/home/u/.mozilla/firefox/abc.default")
    cmd, _ = ya.browser_cmd(acc, "https://www.youtube.com/")
    assert cmd == ["/usr/bin/firefox", "--profile", "/home/u/.mozilla/firefox/abc.default", "--new-window",
                   "https://www.youtube.com/"]
    monkeypatch.setattr(ya.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="tapılmadı"):
        ya.browser_cmd(acc, "https://x.com")
    with pytest.raises(RuntimeError, match="Chrome"):
        ya.browser_cmd(add_account(store, id="c9"), "https://x.com")


class Proc:
    def __init__(self, code=None):
        self.code = code

    def wait(self, timeout=None):
        if self.code is None:
            raise subprocess.TimeoutExpired("x", timeout)
        return self.code


def test_open_url_launches_detached_without_a_shell_and_judges_the_exit_code(store, monkeypatch):
    acc = add_account(store)
    monkeypatch.setattr(ya.shutil, "which", lambda name: "/usr/bin/" + name)
    calls = []
    monkeypatch.setattr(ya.subprocess, "Popen", lambda cmd, **kw: (calls.append((cmd, kw)), Proc(None))[1])
    assert ya.open_url(acc["id"], "mail.google.com") == "https://mail.google.com"       # işləyir → pəncərə açıldı
    cmd, kw = calls[0]
    assert isinstance(cmd, list) and kw["start_new_session"] and "shell" not in kw and kw["env"] == {"DISPLAY": ":0"}
    monkeypatch.setattr(ya.subprocess, "Popen", lambda cmd, **kw: Proc(0))              # mövcud prosesə ötürüb çıxdı
    assert ya.open_url(acc["id"], "https://www.youtube.com/")
    monkeypatch.setattr(ya.subprocess, "Popen", lambda cmd, **kw: Proc(1))
    with pytest.raises(RuntimeError, match="kod 1"):
        ya.open_url(acc["id"], "https://www.youtube.com/")


def test_open_url_refuses_bad_targets(store):
    with pytest.raises(RuntimeError, match="profili serverdə yoxdur"):
        ya.open_url(add_account(store, id="f2", kind="file", profile="")["id"], "https://x.com")
    with pytest.raises(RuntimeError):
        ya.open_url("yoxdur", "https://x.com")
    with pytest.raises(ValueError):
        ya.open_url(add_account(store)["id"], "javascript:alert(1)")


# ───────────── görünüşlər ─────────────
def datas(kb):
    return [b.callback_data for r in kb.inline_keyboard for b in r]


def texts(kb):
    return [b.text for r in kb.inline_keyboard for b in r]


def test_account_page_shows_the_named_buttons_for_browser_accounts_only(store):
    add_account(store)
    ya.add_link("ab12cd34", "📧 Gmail (iş)", "mail.google.com")
    text, kb = V.yta_acc_view("ab12cd34")
    assert "menu:yta:ol:ab12cd34:0" in datas(kb) and "menu:yta:ol:ab12cd34:1" in datas(kb)
    assert "📧 Gmail (iş)" in texts(kb) and "▶️ YouTube" in texts(kb) and "menu:yta:lk:ab12cd34" in datas(kb)
    assert "brauzer profili serverin ekranında açılır" in text
    add_account(store, id="f3", kind="file", profile="", name="Fayl hesabı", file="/x.txt")
    text, kb = V.yta_acc_view("f3")
    assert not any(":ol:" in d or ":lk:" in d for d in datas(kb)) and "brauzer profili serverin" not in text


def test_links_page_lists_edits_and_offers_presets(store):
    add_account(store)
    text, kb = V.yta_links_view("ab12cd34")
    assert "Düymələr: <b>1/6</b>" in text and "https://www.youtube.com/" in text
    ds = datas(kb)
    assert "menu:yta:ln:ab12cd34:0" in ds and "menu:yta:ld:ab12cd34:0" in ds
    assert {f"menu:yta:la:ab12cd34:{k}" for k in ("yt", "gm", "ga", "ym", "own")} <= set(ds)
    assert len(ds) == len(set(ds)) and all(len(d.encode()) <= 64 for d in ds)
    assert ds[-3] == "menu:yta:a:ab12cd34"                                              # ⬅️ hesabın səhifəsi
    for i in range(5):
        ya.add_link("ab12cd34", f"L{i}", f"s{i}.com")
    text, kb = V.yta_links_view("ab12cd34")
    assert not any(":la:" in d for d in datas(kb))                                      # limit dolu — əlavə düymələri yoxdur
    assert "brauzer profili yoxdur" in V.yta_links_view("yoxdur")[0]                      # olmayan / profilsiz hesab


# ───────────── callback-lər ─────────────
class CB:
    def __init__(self, data):
        self.data, self.answers = data, []
        self.message = types.SimpleNamespace(chat=types.SimpleNamespace(id=5), message_id=9)

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))


@pytest.fixture
def flow(store, monkeypatch):
    shown = []

    async def fake_edit(cb, text, kb=None):
        shown.append((text, kb))

    async def fake_show(view):
        shown.append(view)
    monkeypatch.setattr(yf, "safe_edit_cb", fake_edit)
    monkeypatch.setattr(yf, "yta_show", fake_show)
    add_account(store)
    return shown


def click(data):
    cb = CB(data)
    parts = data.split(":")
    asyncio.run(yf._mcb_yta(cb, parts, "yta"))
    return cb


def test_pressing_a_button_opens_the_url_in_the_accounts_profile(flow, monkeypatch):
    ya.add_link("ab12cd34", "Gmail", "mail.google.com")
    opened = []
    monkeypatch.setattr(ya, "open_url", lambda aid, url: opened.append((aid, url)) or url)
    cb = click("menu:yta:ol:ab12cd34:1")
    assert opened == [("ab12cd34", "https://mail.google.com")] and cb.answers[0] == ("🖥 Brauzer açılır...", False)
    assert "Gmail</b> serverin ekranında açıldı" in flow[-1][0] and "ab12cd34:0" in str(datas(flow[-1][1]))


def test_a_failed_launch_is_shown_on_the_account_page(flow, monkeypatch):
    def boom(aid, url):
        raise RuntimeError("serverdə qrafik sessiya tapılmadı")
    monkeypatch.setattr(ya, "open_url", boom)
    click("menu:yta:ol:ab12cd34:0")
    assert "❌ serverdə qrafik sessiya tapılmadı" in flow[-1][0]
    cb = click("menu:yta:ol:ab12cd34:9")
    assert cb.answers[-1][1] is True                                                      # olmayan düymə → xəbərdarlıq


def test_adding_presets_removing_and_unknown_accounts(flow):
    click("menu:yta:lk:ab12cd34")
    assert "Düymələr" in flow[-1][0]
    click("menu:yta:la:ab12cd34:gm")
    assert [x["name"] for x in ya.links(ya.get("ab12cd34"))] == ["▶️ YouTube", "📧 Gmail"] and "Gmail əlavə olundu" in flow[-1][0]
    click("menu:yta:ld:ab12cd34:0")
    assert [x["name"] for x in ya.links(ya.get("ab12cd34"))] == ["📧 Gmail"] and "silindi" in flow[-1][0]
    cb = click("menu:yta:la:ab12cd34:nope")
    assert cb.answers[-1][1] is True
    cb = click("menu:yta:ol:yoxdur:0")
    assert cb.answers[-1] == ("⚠️ Hesab tapılmadı", True) and "tapılmadı" in flow[-1][0]


def test_naming_a_button_by_typing(flow):
    click("menu:yta:ln:ab12cd34:0")
    assert yta_inp["stage"] == "await_lname" and yta_inp["acc"] == "ab12cd34" and yta_inp["idx"] == 0
    assert "yeni adını yaz" in flow[-1][0]

    class Msg:
        def __init__(self, text):
            self.text, self.from_user = text, types.SimpleNamespace(id=1)

        async def delete(self):
            return None
    asyncio.run(yf.yta_link_input(Msg("  📧   Əsas poçt  ")))
    assert ya.links(ya.get("ab12cd34"))[0]["name"] == "📧 Əsas poçt" and "Ad: 📧 Əsas poçt" in flow[-1][0]
    assert yta_inp["stage"] is None


def test_own_link_flow_adds_a_button_named_after_the_host_and_rejects_bad_links(flow):
    class Msg:
        def __init__(self, text):
            self.text = text

        async def delete(self):
            return None
    click("menu:yta:la:ab12cd34:own")
    assert yta_inp["stage"] == "await_lurl" and "Linki yaz" in flow[-1][0]
    asyncio.run(yf.yta_link_input(Msg("https://www.studio.youtube.com/channel")))
    assert ya.links(ya.get("ab12cd34"))[-1] == {"name": "🔗 studio.youtube.com", "url": "https://www.studio.youtube.com/channel"}
    click("menu:yta:la:ab12cd34:own")
    asyncio.run(yf.yta_link_input(Msg("javascript:alert(1)")))
    assert "⚠️" in flow[-1][0] and len(ya.links(ya.get("ab12cd34"))) == 2                # pis link əlavə olunmadı


def test_text_typed_while_naming_is_not_treated_as_a_music_search():
    from bot.menus.state import TEXT_WAITS
    stages = next(s for st, s in TEXT_WAITS if st is yta_inp)
    assert {"await_lname", "await_lurl"} <= stages
