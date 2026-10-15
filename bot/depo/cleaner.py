"""
🧹 bot/depo/cleaner.py - Mahnı adlarının təmizlənməsi və Telegram linklərinin təhlili.
"""
from __future__ import annotations

import os
import re
from html import unescape

import aiohttp

from bot.core.song_names import _norm_dashes as _names_norm_dashes, fold as _fold

try:
    from telethon.tl.types import DocumentAttributeAudio, DocumentAttributeFilename
    HAS_TELETHON = True
except ImportError:
    HAS_TELETHON = False
    DocumentAttributeAudio = None
    DocumentAttributeFilename = None


# ───────────────────────── 🧹 Mahnı adının təmizlənməsi ─────────────────────────
_AUDIO_EXT = re.compile(r"\.(mp3|m4a|flac|ogg|oga|opus|wav|aac|wma|alac|mp4)$", re.I)
_URL_RE = re.compile(
    r"(?:https?://|www\.)\S+"
    r"|\b[\w-]+\.(?:com|net|org|ru|az|tr|uz|kz|info|fm|me|biz|io|xyz|cc|tv|ws|su|pro|club|top|site|online|"
    r"music|mobi|live)\b(?:/\S*)?", re.I)
_MENTION_RE = re.compile(r"(?<![^\W_])[@#][\w.]+")
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u2190-\u21FF\u2500-\u25FF\u2660-\u266F"
    "\u2700-\u27BF\uFE0F\u200B-\u200F\u2060\u00A9\u00AE\u2122]+")
_BRACKETS = re.compile(r"\s*[\(\[【{<«]([^\)\]】}>»]*)[\)\]】}>»]")
_BRACKET_NOISE = re.compile(
    r"\b(official|video|audio|lyrics?|visuali[sz]er|mv|m/v|hd|hq|4k|klip|clip|rəsmi|premyera|премьера|клип|"
    r"mp3|m4a|flac|\d{2,3}\s*kbps|kbps|bass\s*boost(ed)?|tiktok|trend|hit|yeni|new|full|prod\.?|"
    r"music|muzik|musiqi|музыка|скачать|download)\b|^\s*(?:19|20)\d\d\s*$|^\s*\d+\s*$|https?://|@", re.I)
_TRAILING_NOISE = re.compile(
    r"(?:\s*[-|/]\s*|\s+)(?:official|video|audio|lyrics?|visuali[sz]er|klip|clip|rəsmi|mp3|hd|hq|4k|"
    r"\d{2,3}\s*kbps)\s*$", re.I)
_TRACKNO = re.compile(r"^\s*(?:track\s*)?\d{1,3}\s*(?:[.)_:]|-\s)\s*(?=\S)", re.I)
_JUNK_NAME = re.compile(
    r"^(?:audio|aud|voice|track|trek|file|fayl|document|doc|record|rec|ses|unknown|untitled|"
    r"naməlum|неизвестен|без названия)?[\s_\-.]*[\d_\-\s.:]*$|^[0-9a-f]{16,}$|^track\s*\d+$"
    r"|^(?:aud|ptt|vid|rec|voice|audio)[\s_\-]*\d{6,}.*$", re.I)
_JUNK_PERFORMER = re.compile(
    r"^(?:unknown|unknown artist|<unknown>|naməlum|неизвестен|various artists?|va|artist|ifaçı|"
    r"music|muzik|musiqi|mp3|audio|track)$|(?:mp3|muzik|musiqi|music|kanal|channel|chat|group|qrup|"
    r"download|yüklə|скачать)\b", re.I)


def _norm_dashes(s: str) -> str:
    """song_names qaydası + "~" (kanal postlarında "İfaçı ~ Ad" yazılışı)."""
    return _names_norm_dashes(s.replace("~", "–"))


def _clean_part(s: str) -> str:
    if not s:
        return ""
    s = unescape(str(s)).replace("%20", " ").replace("\u00a0", " ")
    s = _AUDIO_EXT.sub("", s.strip())
    s = _BRACKETS.sub(lambda m: "" if (not m.group(1).strip() or _BRACKET_NOISE.search(m.group(1))
                                       or _URL_RE.search(m.group(1))) else m.group(0), s)
    s = _URL_RE.sub(" ", s)
    s = _MENTION_RE.sub(" ", s)
    s = s.replace("_", " ")
    s = _EMOJI_RE.sub(" ", s)
    s = re.split(r"\s+(?:\||//|•|·)\s+", s, maxsplit=1)[0] if re.search(r"\s(?:\||//|•|·)\s", s) else s
    s = _TRACKNO.sub("", s)
    s = _norm_dashes(s)
    for _ in range(3):
        s = _TRAILING_NOISE.sub("", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip(" -–|.,:;*•·'\"`")


_STOP = {"feat", "ft", "featuring", "prod", "with", "x", "vs", "the", "and", "a"}


def _toks(s: str) -> set:
    return {w for w in re.findall(r"\w+", _fold(s)) if len(w) >= 2 and w not in _STOP}


def _same(a: str, b: str) -> bool:
    ta, tb = _toks(a), _toks(b)
    return bool(ta) and bool(tb) and (ta <= tb or tb <= ta)


def clean_song(performer=None, title=None, file_name=None, caption=None, chat_title=None):
    """Telegram audio məlumatından (artist, ad) — oxunmursa None."""
    p, t = _clean_part(performer), _clean_part(title)
    if performer and re.search(r"@|https?://|t\.me/|www\.", str(performer), re.I):
        p = ""
    if p and (_JUNK_PERFORMER.search(p) or (chat_title and _same(p, chat_title))):
        p = ""
    if t and (_JUNK_NAME.match(t) or (chat_title and _fold(t) == _fold(chat_title))):
        t = ""
    if not t and file_name:
        f = _clean_part(os.path.splitext(file_name)[0] if _AUDIO_EXT.search(file_name) else file_name)
        if f and not _JUNK_NAME.match(f):
            t = f
    if not t and caption:
        for line in str(caption).splitlines():
            line = _clean_part(line)
            if line and " - " in line and len(line) <= 120:
                t = line
                break
    if not t or not re.search(r"\w", t):
        return None
    if " - " in t:
        a, b = (x.strip() for x in t.split(" - ", 1))
        if a and b:
            if not p:
                p, t = a, b
            elif _same(a, p):
                t = b
            elif _same(b, p):
                t = a
    if p and _fold(t).startswith(_fold(p) + " "):
        rest = t[len(p):].strip(" -")
        t = rest or t
    t = t.strip(" -")
    return (p.strip(" -"), t) if t else None


def tg_item(artist: str, title: str, duration: int = 0) -> dict:
    artist, title = (artist or "").strip(), (title or "").strip()
    full = f"{artist} - {title}" if artist else title
    base = _BRACKETS.sub("", title).strip() or title
    return {
        "title": full,
        "query": f"{artist} {base}".strip(),
        "url": None,
        "raw_duration": int(duration or 0),
        "meta": None,
        "thumb": None,
        "sp_id": None,
        "lookup": True,
        "clean": {"artist": artist, "track": title},
    }


def tl_message_item(msg, chat_title: str = ""):
    """Telethon mesajından növbə elementi (səs mesajı / adsız fayl → None)."""
    doc = getattr(msg, "audio", None) or getattr(msg, "document", None)
    if not doc:
        return None
    title = performer = fname = None
    duration = 0
    is_audio = False
    for a in getattr(doc, "attributes", None) or []:
        if DocumentAttributeAudio and isinstance(a, DocumentAttributeAudio):
            if a.voice:
                return None
            is_audio = True
            title, performer, duration = a.title, a.performer, a.duration or 0
        elif DocumentAttributeFilename and isinstance(a, DocumentAttributeFilename):
            fname = a.file_name
    if not is_audio and not (fname and _AUDIO_EXT.search(fname)):
        return None
    res = clean_song(performer, title, fname, getattr(msg, "message", None), chat_title)
    if not res:
        return None
    return tg_item(res[0], res[1], duration)


# ───────────────────────── ✈️ Telegram linkləri ─────────────────────────
TG_PAGES = 15
_RESERVED = {"s", "c", "joinchat", "share", "addstickers", "addemoji", "proxy", "socks", "addlist", "iv", "login"}
_TG_LINK = re.compile(r"^(?:https?://)?(?:t\.me|telegram\.me|telegram\.dog)/(\S+)$", re.I)
_SIZE_RE = re.compile(r"^\s*[\d.,]+\s*(?:B|KB|MB|GB)\s*$", re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def parse_tg_target(text: str):
    """→ {"type": user|invite|id, ...}, mesaj sayı (limit) və ya (None, None)."""
    words = (text or "").split()
    target, tw = None, None
    for w in words:
        w = w.strip().rstrip(",")
        if re.fullmatch(r"-?\d{7,}", w):
            target = {"type": "id", "id": int(w)}
        elif re.fullmatch(r"@[A-Za-z][A-Za-z0-9_]{3,31}", w):
            target = {"type": "user", "name": w[1:]}
        else:
            m = _TG_LINK.match(w)
            if not m:
                continue
            parts = m.group(1).split("?")[0].strip("/").split("/")
            if parts[0].startswith("+"):
                target = {"type": "invite", "hash": parts[0][1:]}
            elif parts[0].lower() == "joinchat" and len(parts) > 1:
                target = {"type": "invite", "hash": parts[1]}
            elif parts[0].lower() == "c" and len(parts) > 1 and parts[1].isdigit():
                target = {"type": "id", "id": int("-100" + parts[1])}
            else:
                name = parts[1] if parts[0].lower() == "s" and len(parts) > 1 else parts[0]
                if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", name) and name.lower() not in _RESERVED:
                    target = {"type": "user", "name": name}
        if target:
            tw = w
            break
    limit = next((int(w) for w in words if w != tw and w.isdigit() and len(w) <= 6), None)
    return target, limit


def _clean_html(html_part: str) -> str:
    return unescape(_TAG_RE.sub("", html_part or "")).strip()


def parse_tg_page(html: str) -> list:
    """t.me/s/... səhifəsindən mahnılar: audio postları (ad + ifaçı) və "Artist - Ad" mətnləri."""
    items = []
    for block in re.split(r'<div class="tgme_widget_message_wrap', html)[1:]:
        found = False
        for m in re.finditer(
                r'tgme_widget_message_document_title[^>]*>(.*?)</div>\s*'
                r'<div class="tgme_widget_message_document_extra[^>]*>(.*?)</div>', block, re.S):
            title, extra = _clean_html(m.group(1)), _clean_html(m.group(2))
            if not title:
                continue
            performer = "" if (not extra or _SIZE_RE.match(extra)) else extra
            res = clean_song(performer, title, title)
            if res:
                items.append(tg_item(*res))
            found = True
        if not found:
            tm = re.search(r'tgme_widget_message_text[^>]*>(.*?)</div>', block, re.S)
            text = _clean_html((tm.group(1) if tm else "").replace("<br/>", "\n").replace("<br>", "\n"))
            first = text.split("\n")[0].strip() if text else ""
            if " - " in _norm_dashes(first) and len(first) <= 120 and "http" not in first:
                res = clean_song("", first)
                if res and res[0]:
                    items.append(tg_item(*res))
    return items


async def tg_channel_items(name: str, pages: int = TG_PAGES) -> dict:
    """Public kanalın son postlarından mahnı siyahısı (yeni → köhnə) — userbot olmayanda."""
    items, title, before = [], name, None
    timeout = aiohttp.ClientTimeout(total=20)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36"}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        for _ in range(pages):
            url = f"https://t.me/s/{name}" + (f"?before={before}" if before else "")
            async with session.get(url) as r:
                if r.status != 200:
                    break
                html = await r.text()
            if "tgme_channel_info" not in html and "tgme_widget_message" not in html:
                raise RuntimeError("Kanal tapılmadı və ya public deyil")
            t = re.search(r'tgme_channel_info_header_title[^>]*>(.*?)</div>', html, re.S)
            if t:
                title = _clean_html(t.group(1)) or title
            items += parse_tg_page(html)[::-1]
            m = re.search(r'data-before="(\d+)"', html) or re.search(r'\?before=(\d+)', html)
            nb = m.group(1) if m else None
            if not nb or nb == before:
                break
            before = nb
    seen, uniq = set(), []
    for it in items:
        k = it["query"].lower()
        if k and k not in seen:
            seen.add(k)
            uniq.append(it)
    return {"name": title, "items": uniq}
