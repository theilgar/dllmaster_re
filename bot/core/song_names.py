"""
🏷 Mahnı adlarının təmizlənməsi və müqayisəsi — bot/core/song_names.py

  clean_title("Eminem - Lose Yourself (Official Video) [HD] 12.05.2024")  → "Eminem - Lose Yourself"
  split_title(...)          → ("Eminem", "Lose Yourself")   (artist yoxdursa "YouTube")
  clean_track / clean_artist — ayrıca sahələr üçün (song.link / Spotify adları da: "- Remastered 2011")
  meta_matches(artist, track, yt_title) — song.link adı YouTube adına həqiqətən uyğundurmu
  refine_with_channel(raw_title, artist, track, kanal) — kanal adı ilə təsdiqlənəndə artist / ad səhvini düzəldir
  tokens / query_score / same_song — depo axtarışı və dublikatlar üçün
"""
import re
import unicodedata
from html import unescape

# Mötərizə içində bunlardan biri varsa, mötərizə tamamilə atılır (reklam, format, kredit — adın hissəsi ola bilməz)
_BRACKET_NOISE = re.compile(
    r"\b(official|offical|oficial|video|audio|lyrics?|visuali[sz]er|mv|m/v|hd|hq|4k|8k|uhd|"
    r"klip|clip|rəsmi|resmi|premiere|premyera|премьера|клип|клипа|exclusive|eksklüziv|ekskluziv|"
    r"mp3|m4a|\d{2,3}\s*kbps|kbps|bass\s*boost(?:ed)?|prod|produced|directed|"
    r"remaster(?:ed)?|explicit|shot\s+by|subtitles?|altyaz[ıi]|"
    r"tekst|текст|sözləri|sozleri|full\s+(?:version|song|track|audio|video|album|hd)|full\s+length(?:\s+version)?|"
    r"(?:yeni|new)\s+(?:version|versiya|versiyon|mahn[ıi]|song|single|release|clip|klip|video))\b"
    r"|\bdir\.|^\s*(?:19|20)\d\d\s*$"                     # (2023)
    r"|\d{1,2}[./-]\d{1,2}[./-]\d{2,4}"                       # (12.05.2024)
    r"|^\s*\d+\s*$|https?://|www\.|@", re.I)
# "Yeni Başlanğıc", "Hit Mahnı" kimi sözlər adın özü ola bilər: mötərizənin BÜTÜN sözləri bunlardandırsa atılır
_BRACKET_WEAK = {"yeni", "new", "trend", "tiktok", "hit", "full"}
# "(Official Remix)" → "(Remix)": mötərizədə bunlardan biri varsa yalnız "zibil" sözlər atılır, mötərizə qalır
_KEEP_WORD = re.compile(r"\b(remix|mix|cover|slowed|reverb|sped\s*up|speed\s*up|nightcore|instrumental|karaoke)\b",
                        re.I)
_YEAR_WORD = re.compile(r"\b(?:19|20)\d\d\b")
_BRACKETS = re.compile(r"\s*[\(\[【{«]([^\)\]】}»]*)[\)\]】}»]")
_URL = re.compile(r"(?:https?://|www\.)\S+|\bt\.me/\S+", re.I)
_MENTION = re.compile(r"(?<![^\W_])[@#][\w.]+")
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u2190-\u21FF\u2500-\u25FF\u2660-\u266F"
    "\uFE0F\u200B-\u200F\u2060\u00A9\u00AE\u2122]+")
_DATE = re.compile(r"\b\d{1,2}[./]\d{1,2}[./](?:19|20)?\d\d\b|\b(?:19|20)\d\d[./-]\d{1,2}[./-]\d{1,2}\b")
_YEAR_PHRASE = re.compile(
    r"\b(?:en\s+yeni|yeni\s+mahn[ıi]|new\s+song|yeni|new|hit|trend|tiktok)\s*(?:19|20)\d\d\b"
    r"(?:\s+(?:yeni|new|klip|clip))?"
    r"|\b(?:19|20)\d\d\s*(?:yeni|new|hit|trend|klip|clip|mahn[ıi]s[ıi]?)\b(?:\s+(?:klip|clip))?", re.I)
_TRAILING = re.compile(
    r"(?:\s*[-|/:]\s*|\s+)(?:official|offical|rəsmi|resmi)?\s*(?:music\s+|lyrics?\s+|lyric\s+)?"
    r"(?:video|audio|klip|clip|visuali[sz]er|lyrics?)(?:\s+clip)?\s*$"
    r"|(?:\s*[-|/:]\s*|\s+)(?:official|rəsmi|resmi|premiere|premyera|премьера|hd|hq|4k|mp3|"
    r"\d{2,3}\s*kbps)\s*$", re.I)
# "Sevgi Yeni" — adın özüdür; "Artist - Ad - Yeni" / "Artist - Ad - Topic" — yalnız AYIRICIDAN sonra və artist saxlanılırsa atılır
_TRAILING_WEAK = re.compile(r"\s*[-|/:]\s*(?:yeni|new|trend|tiktok|topic)\s*$", re.I)
_REMASTER = re.compile(
    r"\s+-\s+(?:(?:19|20)\d\d\s+)?(?:digital(?:ly)?\s+)?remaster(?:ed)?(?:\s+(?:19|20)\d\d)?(?:\s+version)?\s*$"
    r"|\s+-\s+(?:single|album|mono|stereo)\s+version\s*$", re.I)
_TRAILING_YEAR = re.compile(r"(?:\s*[-|/,]\s*|\s+)(?:19|20)\d\d\s*$")
_SEP = re.compile(r"\s+(?:\||//|•|·|‖)\s+|\s*\|\s*")

_CHANNEL_SUFFIX = re.compile(
    r"(?:\s*-\s*topic|vevo|\s+official(?:\s+(?:channel|music|tv|youtube|page))?|\s+music|\s+tv|"
    r"\s+rəsmi(?:\s+kanal[ıi]?)?|\s+resmi)\s*$", re.I)

# "Samir Piriyev “Sən Yoxsan”" — ayırıcı yoxdur, ad dırnaq içindədir
_QUOTED = re.compile(r"^(?P<a>[^\"“”„«»]{1,60}?)\s*[\"“„«](?P<t>[^\"“”„«»]{1,80})[\"”“»]?$")   # sondakı " _base_clean-də kəsilir

_STOP = {"feat", "ft", "featuring", "prod", "with", "x", "vs", "the", "and", "a", "de", "ve", "və", "и"}


def _norm_dashes(s: str) -> str:
    s = re.sub(r"\s*[–—―‒−]\s*", " - ", s)
    return re.sub(r"(?<=\S)\s+-\s*(?=\S)|(?<=\S)\s*-\s+(?=\S)", " - ", s)


def _bracket_repl(m) -> str:
    inner = m.group(1).strip()
    if not inner:
        return ""
    if _BRACKET_NOISE.search(inner):
        if _KEEP_WORD.search(inner):
            kept = _YEAR_WORD.sub(" ", _BRACKET_NOISE.sub(" ", inner))
            kept = re.sub(r"\s{2,}", " ", kept).strip(" -–|/.,:;")
            return f" ({kept})" if re.search(r"[^\W\d_]", kept) else ""
        return ""
    words = re.findall(r"[^\W_]+", inner.lower())
    if words and all(w in _BRACKET_WEAK for w in words):
        return ""
    return m.group(0)


def _drop_brackets(s: str) -> str:
    return _BRACKETS.sub(_bracket_repl, s)


_TIDY = ((re.compile(r"([(\[])\s+"), r"\1"), (re.compile(r"[\s/;,&-]+([)\]])"), r"\1"),
         (re.compile(r"([(\[])[\s/;,&-]+"), r"\1"), (re.compile(r"\s*[(\[]\s*[)\]]"), ""))


def _tidy_brackets(s: str) -> str:
    """Silinmədən sonra qalan "( Edit)", "(Mix /)", "()" kimi qalıqlar və təkrarlanan eyni mötərizə."""
    for rx, to in _TIDY:
        s = rx.sub(to, s)

    def dedupe(part):
        seen = set()

        def once(m):
            key = re.sub(r"\W+", "", m.group(1).casefold())
            if key and key in seen:
                return ""
            seen.add(key)
            return m.group(0)
        return re.sub(r"\s*[(\[]([^)\]]*)[)\]]", once, part)
    return " - ".join(dedupe(p) for p in s.split(" - "))   # "Ad (Live) - Name (Live)": iki dildəki yarılar toxunulmaz


def _base_clean(s: str) -> str:
    s = unescape(str(s or "")).replace("\u00a0", " ")
    s = _drop_brackets(s)
    s = _URL.sub(" ", s)
    s = _MENTION.sub(" ", s)
    s = _EMOJI.sub(" ", s)
    s = _DATE.sub(" ", s)
    s = _YEAR_PHRASE.sub(" ", s)
    s = _drop_brackets(s)                      # tarix silinəndən sonra boş qalan mötərizələr
    if _SEP.search(s):
        parts = [p for p in _SEP.split(s) if p.strip()]
        s = next((p for p in parts if " - " in _norm_dashes(p)), parts[0] if parts else s)
    s = _norm_dashes(s)
    for _ in range(3):
        s = _TRAILING.sub("", s)
        m = _TRAILING_WEAK.search(s)
        if m and " - " in _norm_dashes(s[:m.start()]):
            s = s[:m.start()]
    s = _tidy_brackets(re.sub(r"\s{2,}", " ", s))
    return s.strip(" -–|.,:;*•·'\"`~")


def clean_track(track: str) -> str:
    """Mahnı adı (artistsiz): tarix, il, (Official Video), "- Remastered 2011" və s. atılır."""
    t = _base_clean(track)
    t = _REMASTER.sub("", t)
    stripped = _TRAILING_YEAR.sub("", t).strip(" -")
    if stripped and re.search(r"[^\W\d_]", stripped):     # "1999" kimi adlar qalsın
        t = stripped
    return t.strip(" -") or (str(track or "").strip())


def clean_artist(artist: str) -> str:
    a = _base_clean(artist)
    for _ in range(2):
        a = _CHANNEL_SUFFIX.sub("", a).strip()
    return a.strip(" -,") or (str(artist or "").strip())


clean_channel = clean_artist


def clean_title(title: str) -> str:
    """Tam YouTube adı ("Artist - Ad ...") → təmiz "Artist - Ad"."""
    t = _base_clean(title)
    if not t:
        return str(title or "").strip()
    parts = re.split(r"\s+-\s+", t, maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        return f"{parts[0].strip()} - {clean_track(parts[1])}"
    q = _QUOTED.match(t)
    if q and re.search(r"[^\W\d_]", q["a"]) and q["t"].strip():
        return f"{q['a'].strip()} - {clean_track(q['t'])}"
    return clean_track(t)


def split_title(title: str):
    """YouTube adını təmizləyib (artist, ad) kimi ayırır; artist yoxdursa "YouTube"."""
    t = clean_title(title)
    parts = re.split(r"\s+-\s+", t, maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        return parts[0].strip(), parts[1].strip()
    return "YouTube", t


# "|" ilə ayrılmış hissənin özü reklam / format sözüdürsə (Official Video, Lyrics ...) ad və ya artist sayılmır
_NOISE_PART = re.compile(
    r"^\s*(?:official|offical|rəsmi|resmi)?\s*(?:music\s+|lyrics?\s+|lyric\s+)?"
    r"(?:video|audio|klip|clip|visuali[sz]er|lyrics?|hd|hq|4k|premiere|premyera|sözləri|mahnı\s+sözləri|yeni)"
    r"\s*(?:clip)?\s*$", re.I)


def _same_name(x: str, ch_tokens: frozenset) -> bool:
    xt = tokens(clean_artist(x))
    return bool(xt) and xt == ch_tokens


def refine_with_channel(raw_title: str, artist: str, track: str, channel: str):
    """Başlıqdan çıxan (artist, ad) kanal adı ilə TƏSDİQLƏNƏNDƏ düzəldilir (təsdiq yoxdursa — dəyişmir):
      • tərs sıra "Ad - Artist": kanal adı "ad" hissəsinə bərabərdir, "artist" hissəsinə yox → yerləri dəyişir;
      • "Ad | Artist | Official Video": artist yoxdur, hissələrdən biri kanal adıdır → o artist, digər hissə ad;
      • "Artist Ad Official Video" (ayırıcısız): ad kanal adı ilə başlayır → kanal artist, qalanı ad.
    → (artist, ad)"""
    ch = clean_artist(channel or "")
    ct = frozenset(tokens(ch))
    if not ct:
        return artist, track
    if artist and artist not in ("YouTube", "Naməlum"):
        if _same_name(track, ct) and not _same_name(artist, ct):
            return clean_artist(track), clean_track(artist)
        return artist, track
    parts = [_base_clean(p) for p in _SEP.split(raw_title or "") if p.strip()]
    parts = [p for p in parts if p and not _NOISE_PART.match(p)]
    for i, part in enumerate(parts):
        if _same_name(part, ct):
            others = [clean_track(o) for j, o in enumerate(parts) if j != i and not _same_name(o, ct)]
            if others:
                return clean_artist(part), others[0]
    cw, tw = ch.split(), (track or "").split()
    if len(tw) > len(cw) and [fold(w) for w in tw[:len(cw)]] == [fold(w) for w in cw]:
        rest = " ".join(tw[len(cw):]).strip(" -–—:|\"“”«»")
        if rest and re.search(r"[^\W\d_]", rest):
            return ch, clean_track(rest)
    return artist, track


# ───────────── müqayisə ─────────────
def fold(s: str) -> str:
    s = (s or "").casefold().replace("ə", "e").replace("ı", "i")
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def tokens(s: str) -> set:
    return {w for w in re.findall(r"\w+", fold(s)) if w not in _STOP and (len(w) >= 2 or w.isdigit())}


def track_tokens(track: str) -> frozenset:
    return frozenset(tokens(clean_track(track)))


def artist_tokens(artist: str) -> frozenset:
    return frozenset(tokens(clean_artist(artist)))


def _tok_hit(q: str, pool: set) -> bool:
    return q in pool or (len(q) >= 3 and any(p.startswith(q) for p in pool))


def _ordered(s: str) -> list:
    return [w for w in re.findall(r"\w+", fold(s)) if w not in _STOP and (len(w) >= 2 or w.isdigit())]


def _glued(words: list) -> set:
    """Yan-yana sözlərin birləşməsi: ["med","cezir"] → {"medcezir"} (sorğuda "medcezir", adda "Med Cezir")."""
    out = set()
    for i in range(len(words) - 1):
        out.add(words[i] + words[i + 1])
        if i + 2 < len(words):
            out.add(words[i] + words[i + 1] + words[i + 2])
    return out


def query_score(query: str, artist: str, track: str) -> float:
    """Axtarış sorğusu depo mahnısına nə qədər uyğundur (0..1). Sözün əvvəli də sayılır: "lose yours".
    Bitişik / ayrı yazılış eyni sayılır: "medcezir" ≡ "Med Cezir"."""
    qw = _ordered(query)
    q = set(qw)
    if not q:
        return 0.0
    aw, tw = _ordered(artist), _ordered(track)
    ta, tt = set(aw), set(tw)
    tt_ext = tt | _glued(tw)                          # adın birləşmiş formaları
    pool = ta | tt | _glued(aw) | _glued(tw) | _glued(aw + tw)
    hit_words = {w for w in q if _tok_hit(w, pool)}
    # sorğudakı yan-yana iki söz adda birləşik yazılıbdırsa ("med cezir" ↔ "Medcezir") — hər ikisi sayılır
    for i in range(len(qw) - 1):
        if qw[i] + qw[i + 1] in pool:
            hit_words |= {qw[i], qw[i + 1]}
    score = len(hit_words) / len(q)
    if tt and not any(_tok_hit(w, tt_ext) for w in q) and not any(
            qw[i] + qw[i + 1] in tt_ext for i in range(len(qw) - 1)):
        score *= 0.5                                  # yalnız artist uyğun gəlir — ifaçının başqa mahnısı
    extra = len(tt - q) / max(1, len(tt))             # sorğuda olmayan çox söz → bir az aşağı
    return score - 0.15 * extra


def meta_matches(artist: str, track: str, yt_title: str) -> bool:
    """song.link / Spotify adı YouTube adı ilə həqiqətən eyni mahnıdırmı."""
    yt = tokens(clean_title(yt_title))
    if not yt:
        return True                                    # müqayisə etmək olmur
    tt, ta = tokens(clean_track(track)), tokens(clean_artist(artist))
    if not tt:
        return False
    t_ratio = len(tt & yt) / len(tt)
    a_hit = bool(ta & yt)
    return t_ratio >= 0.75 or (t_ratio >= 0.5 and a_hit)


def same_song(a_artist, a_track, b_artist, b_track) -> bool:
    ta, tb = track_tokens(a_track), track_tokens(b_track)
    if not ta or ta != tb:
        return False
    aa, ab = artist_tokens(a_artist), artist_tokens(b_artist)
    return bool(aa and ab and (aa & ab))


# ── 🎬 musiqi olmayan YouTube nəticələri (xəbər, film fraqmenti, serial, podcast ...) ──
_NON_MUSIC = ("xəbər", "xeber", "xəbərlər", "haber", "haberler", "son dakika", "news", "magazin", "dedikodu",
              "fragman", "trailer", "teaser", "full movie", "tam film", "film izle", "filmi izle", "kino", "dizi",
              "episode", "bölüm", "bolum", "sezon", "serial", "seriya", "podcast", "röportaj", "reportaj",
              "interview", "müsahibə", "tutorial", "vlog", "gameplay", "walkthrough", "canlı yayın", "live stream",
              "livestream", "reaction", "review", "обзор", "новости", "сериал", "фильм", "трейлер")


def non_music_reason(title: str, duration: int = 0, query: str = "", min_sec: int = 0) -> str:
    """Nəticə musiqiyə oxşamırsa səbəbi (boş sətir — oxşayır). Açar söz sorğuda özü varsa sayılmır.
    min_sec > 0 — bundan qısa videolar (fraqment / reklam) da musiqi sayılmır."""
    low = (title or "").lower()
    q = (query or "").lower()
    for w in _NON_MUSIC:
        if w in low and w not in q:
            return w
    if min_sec and duration and 0 < int(duration) < min_sec:
        return f"{int(duration)} san."
    return ""
