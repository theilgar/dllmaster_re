"""
bot.core.va_transcript
Canlı yayım üçün interaktiv HTML/CSS/JS və TXT jurnalı generatoru.
"""
import contextlib
import os
import re
import time
from collections import Counter
from html import escape

from common.utilities import format_duration as fmt_dur, js_arg, script_json


def generate_transcript(sess, out_dir: str = "data/records") -> str | None:
    if not sess.messages and not sess.marks and not sess.tracks:
        return None

    # Ehtiyat olaraq sadə txt faylını da yazırıq
    txt_events = [(m[0], f"{m[1]}: {m[2]}") for m in sess.messages]
    txt_events += [(off, f"🎵 ▶️ {title}") for off, title in sess.tracks]
    txt_events += [(off, f"📍 Nişan {i}") for i, off in enumerate(sess.marks, 1)]
    txt_events.sort(key=lambda e: e[0])
    path_txt = os.path.abspath(os.path.join(out_dir, f"va_{sess.chat_id}_{int(sess.t0)}.txt"))
    start_str = time.strftime("%d.%m.%Y %H:%M", time.localtime(sess.t0))
    dur_str = fmt_dur(sess.ended - sess.t0)
    speakers = sess.speaker_names()

    with contextlib.suppress(Exception):
        with open(path_txt, "w", encoding="utf-8") as f:
            f.write(f"⏺ Canlı yayım jurnalı — {sess.title}\n{start_str} · {dur_str}\n"
                    f"Danışanlar: {', '.join(speakers) or '—'}\n" + "─" * 40 + "\n")
            for off, line in txt_events:
                f.write(f"[{fmt_dur(off)}] {line}\n")

    # Telegram mobil tətbiqi ilə 1:1 eyni interaktiv HTML jurnalı
    dur = max(0.0, sess.ended - sess.t0)
    dur_str = fmt_dur(dur)
    end_str = time.strftime("%H:%M", time.localtime(sess.ended)) if sess.ended else ""
    msg_counter = Counter()
    user_meta = {}

    # Səsli iştirakçıların məlumatları (əgər varsa)
    for v in sess.voices:
        k = os.path.basename(v)[1:-5]
        info = sess.speakers.get(k)
        if isinstance(info, dict):
            sname = info.get("name", "")
            if sname and sname not in user_meta:
                user_meta[sname] = {"id": info.get("id", 0), "username": info.get("username", "")}

    for m in sess.messages:
        name = m[1]
        msg_counter[name] += 1
        uid = m[3] if len(m) >= 4 else 0
        uname = m[4] if len(m) >= 5 else ""
        if name not in user_meta or (not user_meta[name].get("username") and uname):
            user_meta[name] = {"id": uid or 0, "username": uname or ""}

    total_msgs = len(sess.messages)

    COLORS = ["#e17076", "#faa774", "#a695e7", "#7bc862", "#6ec9cb", "#65aadd", "#ee7aae", "#2996f5"]
    def user_color(u: str) -> str:
        return COLORS[abs(hash(u)) % len(COLORS)]

    def format_msg_text(text: str) -> str:
        esc = escape(text)
        linked = re.sub(
            r'(https?://[^\s<]+)',
            r'<a href="\1" target="_blank" rel="noopener noreferrer" class="chat-link" onclick="event.stopPropagation();">\1</a>',
            esc
        )
        return linked.replace("\n", "<br>")

    items = []
    for m in sess.messages:
        if len(m) >= 5:
            off, name, text, uid, uname = m[0], m[1], m[2], m[3], m[4]
        elif len(m) == 4:
            off, name, text, uid = m[0], m[1], m[2], m[3]
            uname = ""
        else:
            off, name, text = m[0], m[1], m[2]
            uid, uname = 0, ""
        items.append((off, "msg", name, text, uid, uname))

    for off, title in sess.tracks:
        items.append((off, "track", "Musiqi", title, 0, ""))
    for i, off in enumerate(sess.marks, 1):
        items.append((off, "mark", "Nişan", f"Nişan #{i}", 0, ""))
    items.sort(key=lambda e: e[0])

    events_html = []
    for item in items:
        off, kind, name, content = item[0], item[1], item[2], item[3]
        uid = item[4] if len(item) >= 5 else 0
        uname = item[5] if len(item) >= 6 else ""
        t_str = fmt_dur(off)
        esc_name = escape(name)
        if kind == "msg":
            color = user_color(name)
            html_body = format_msg_text(content)
            init = name.strip()[:1].upper() if name.strip() else "?"
            esc_uname = escape(uname) if uname else ""
            uname_badge = f'<a href="https://t.me/{esc_uname}" target="_blank" rel="noopener noreferrer" class="sender-handle" onclick="event.stopPropagation();">@{esc_uname}</a>' if uname else ""
            events_html.append(f"""
            <div class="chat-row" data-type="msg" data-sender="{esc_name}" data-uid="{uid}" data-uname="{esc_uname}">
              <div class="user-avatar" style="background:{color};" title="{esc_name}" onclick="openProfileCard({js_arg(name)}, {js_arg(uid)}, {js_arg(uname)}, {js_arg(color)}); event.stopPropagation();">{escape(init)}</div>
              <div class="message-bubble" onclick="onBubbleClick(this, event)">
                <div class="bubble-tail"><svg viewBox="0 0 11 20" width="11" height="20"><path d="M0 20C5 20 8 16 9 13L11 0C11 10 7 17 0 20Z" fill="var(--tg-bubble-bg)"/></svg></div>
                <div class="bubble-header">
                  <span class="sender-name" style="color: {color};" onclick="openProfileCard({js_arg(name)}, {js_arg(uid)}, {js_arg(uname)}, {js_arg(color)}); event.stopPropagation();">{esc_name}</span>
                  {uname_badge}
                </div>
                <div class="text-content">{html_body}</div>
                <div class="bubble-footer">
                  <span class="timestamp">{t_str}</span>
                  <span class="tick-icon">✓✓</span>
                  <button class="copy-btn" onclick="copyText(this, event)" title="Mətni kopyala">
                    <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor"><path d="M16 1H4C2.9 1 2 1.9 2 3v14h2V3h12V1zm3 4H8C6.9 5 6 5.9 6 7v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z"/></svg>
                  </button>
                </div>
              </div>
            </div>""")
        elif kind == "track":
            esc_title = escape(content)
            events_html.append(f"""
            <div class="chat-row track-row" data-type="track">
              <div class="track-bubble" onclick="toggleTrack(this, {js_arg(content)}, event)">
                <div class="track-play-icon">
                  <svg viewBox="0 0 24 24" width="18" height="18" fill="white"><path d="M8 5v14l11-7z"/></svg>
                </div>
                <div class="track-info">
                  <div class="track-title">{esc_title}</div>
                  <div class="track-sub">
                    <span>Musiqi</span>
                    <span class="waveform">
                      <i style="height:35%"></i><i style="height:65%"></i><i style="height:90%"></i>
                      <i style="height:50%"></i><i style="height:80%"></i><i style="height:40%"></i>
                      <i style="height:70%"></i><i style="height:95%"></i><i style="height:60%"></i>
                      <i style="height:45%"></i><i style="height:85%"></i><i style="height:30%"></i>
                    </span>
                  </div>
                </div>
                <div class="track-time">{t_str}</div>
              </div>
            </div>""")
        elif kind == "mark":
            esc_mark = escape(content)
            events_html.append(f"""
            <div class="chat-row mark-row" data-type="mark">
              <div class="system-pill">📍 {esc_mark} <span class="pill-time">{t_str}</span></div>
            </div>""")

    # Leaderboard
    leaderboard_html = []
    for spk, cnt in msg_counter.most_common():
        pct = (cnt / total_msgs * 100) if total_msgs else 0
        c = user_color(spk)
        m_info = user_meta.get(spk, {})
        u_id = m_info.get("id", 0)
        u_name = m_info.get("username", "")
        leaderboard_html.append(f"""
        <div class="stat-user-row" onclick="openProfileCard({js_arg(spk)}, {js_arg(u_id)}, {js_arg(u_name)}, {js_arg(c)}); closeModal('statsModal');">
          <div class="stat-user-avatar" style="background:{c}">{escape(spk[:1].upper())}</div>
          <div class="stat-user-info">
            <div class="stat-user-name">
              <span>{escape(spk)}</span>
              <span class="stat-user-count">{cnt} mesaj ({pct:.0f}%)</span>
            </div>
            <div class="stat-bar-bg"><div class="stat-bar-fill" style="width:{pct:.0f}%; background:{c};"></div></div>
          </div>
        </div>""")
    if not leaderboard_html:
        leaderboard_html.append('<div class="empty-state">Mesaj qeydə alınmayıb</div>')

    track_list_html = []
    for off, title in sess.tracks:
        track_list_html.append(f"""
        <div class="stat-track-item">
          <span class="track-dot">🎵</span>
          <span class="stat-track-name">{escape(title)}</span>
          <span class="stat-track-time">{fmt_dur(off)}</span>
        </div>""")
    if not track_list_html:
        track_list_html.append('<div class="empty-state">Musiqi səsləndirilməyib</div>')

    # Raw TXT serialization
    raw_txt_str = f"⏺ Canlı yayım jurnalı — {sess.title}\n{start_str} · {dur_str}\nDanışanlar: {', '.join(speakers) or '—'}\n" + "─" * 40 + "\n"
    for m in sess.messages:
        raw_txt_str += f"[{fmt_dur(m[0])}] {m[1]}: {m[2]}\n"
    for off, title in sess.tracks:
        raw_txt_str += f"[{fmt_dur(off)}] 🎵 ▶️ {title}\n"
    for i, off in enumerate(sess.marks, 1):
        raw_txt_str += f"[{fmt_dur(off)}] 📍 Nişan #{i}\n"

    raw_txt_json = script_json(raw_txt_str)
    title_json = script_json(sess.title)

    html_content = f"""<!DOCTYPE html>
<html lang="az">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
  <meta name="theme-color" content="#17212b">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <title>⏺ {escape(sess.title)} — Canlı Yayım Jurnalı</title>
  <style>
:root {{
  --tg-bg: #0e1621;
  --tg-header-bg: #17212b;
  --tg-header-border: rgba(255, 255, 255, 0.08);
  --tg-bubble-bg: #182533;
  --tg-bubble-border: rgba(255, 255, 255, 0.05);
  --tg-text: #f5f5f5;
  --tg-text-muted: #7e8b98;
  --tg-accent: #5288c1;
  --tg-accent-hover: #4378b0;
  --tg-pill-bg: rgba(23, 33, 43, 0.85);
  --tg-pill-border: rgba(255, 255, 255, 0.08);
  --tg-track-bg: #1f2f40;
  --tg-track-border: #294766;
  --tg-modal-bg: #17212b;
  --tg-card-inner: #242f3d;
  --tg-modal-overlay: rgba(0, 0, 0, 0.65);
  --tg-font-size: 15px;
  --tg-safe-top: env(safe-area-inset-top, 0px);
  --tg-safe-bottom: env(safe-area-inset-bottom, 0px);
  --tg-pattern-opacity: 0.035;
}}
body.theme-oled {{
  --tg-bg: #000000;
  --tg-header-bg: #121212;
  --tg-header-border: #222222;
  --tg-bubble-bg: #181818;
  --tg-bubble-border: #262626;
  --tg-text: #ffffff;
  --tg-text-muted: #888888;
  --tg-accent: #2996f5;
  --tg-accent-hover: #1f82d6;
  --tg-pill-bg: #1c1c1e;
  --tg-pill-border: #2c2c2e;
  --tg-track-bg: #181818;
  --tg-track-border: #2a2a2a;
  --tg-modal-bg: #121212;
  --tg-card-inner: #1c1c1e;
  --tg-pattern-opacity: 0.02;
}}
body.theme-light {{
  --tg-bg: #8ea3b7;
  --tg-header-bg: #527da3;
  --tg-header-border: rgba(0, 0, 0, 0.1);
  --tg-bubble-bg: #ffffff;
  --tg-bubble-border: rgba(0, 0, 0, 0.06);
  --tg-text: #111111;
  --tg-text-muted: #707579;
  --tg-accent: #2481cc;
  --tg-accent-hover: #1c6fad;
  --tg-pill-bg: rgba(0, 0, 0, 0.35);
  --tg-pill-border: transparent;
  --tg-track-bg: #eaf3fa;
  --tg-track-border: #b8d4ed;
  --tg-modal-bg: #ffffff;
  --tg-card-inner: #f1f4f8;
  --tg-modal-overlay: rgba(0, 0, 0, 0.5);
  --tg-pattern-opacity: 0.06;
}}
* {{
  box-sizing: border-box;
  -webkit-tap-highlight-color: transparent;
  margin: 0;
  padding: 0;
}}
html, body {{
  width: 100%;
  height: 100%;
  background-color: var(--tg-bg);
  background-image: radial-gradient(rgba(255,255,255,var(--tg-pattern-opacity)) 1px, transparent 1px);
  background-size: 24px 24px;
  color: var(--tg-text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Oxygen, Ubuntu, Cantarell, "Helvetica Neue", sans-serif;
  font-size: var(--tg-font-size);
  overflow-x: hidden;
  line-height: 1.4;
}}
/* Telegram App Header */
.app-header {{
  position: sticky;
  top: 0;
  z-index: 200;
  background: var(--tg-header-bg);
  border-bottom: 1px solid var(--tg-header-border);
  padding: calc(var(--tg-safe-top) + 6px) 10px 8px 10px;
  display: flex;
  align-items: center;
  gap: 10px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.25);
  user-select: none;
  -webkit-user-select: none;
}}
.back-btn {{
  background: none;
  border: none;
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  padding: 4px;
  border-radius: 50%;
}}
.header-avatar {{
  width: 42px;
  height: 42px;
  border-radius: 50%;
  background: linear-gradient(135deg, #5288c1, #2b5278);
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  font-size: 18px;
  color: #fff;
  flex-shrink: 0;
}}
.header-details {{
  flex: 1;
  min-width: 0;
}}
.header-title {{
  font-size: 16px;
  font-weight: 600;
  color: #fff;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}
.header-sub {{
  font-size: 12.5px;
  color: var(--tg-text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-top: 1px;
  display: flex;
  align-items: center;
  gap: 5px;
}}
.live-dot {{
  display: inline-block;
  width: 7px;
  height: 7px;
  background: #4ade80;
  border-radius: 50%;
}}
body.theme-light .header-sub {{
  color: rgba(255, 255, 255, 0.85);
}}
.header-actions {{
  display: flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
}}
.icon-btn {{
  width: 38px;
  height: 38px;
  border-radius: 50%;
  background: transparent;
  border: none;
  color: var(--tg-text);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: background 0.15s ease;
}}
body.theme-light .app-header .icon-btn, body.theme-light .back-btn {{
  color: #ffffff;
}}
.icon-btn:active {{
  background: rgba(255, 255, 255, 0.12);
}}
/* Search & Filter Controls */
.controls-wrapper {{
  position: sticky;
  top: 57px;
  z-index: 190;
  background: var(--tg-header-bg);
  border-bottom: 1px solid var(--tg-header-border);
  padding: 8px 12px;
  user-select: none;
  -webkit-user-select: none;
}}
.search-box {{
  display: flex;
  align-items: center;
  background: rgba(0, 0, 0, 0.22);
  border: 1px solid var(--tg-bubble-border);
  border-radius: 10px;
  padding: 0 10px;
  height: 38px;
}}
body.theme-light .search-box {{
  background: rgba(255, 255, 255, 0.25);
  border-color: rgba(255, 255, 255, 0.3);
}}
.search-input {{
  flex: 1;
  background: transparent;
  border: none;
  outline: none;
  color: var(--tg-text);
  font-size: 14px;
  padding: 0 8px;
}}
body.theme-light .search-input {{
  color: #fff;
}}
body.theme-light .search-input::placeholder {{
  color: rgba(255, 255, 255, 0.7);
}}
.search-count-badge {{
  font-size: 11px;
  color: var(--tg-accent);
  background: rgba(82, 136, 193, 0.15);
  padding: 2px 6px;
  border-radius: 8px;
  margin-right: 6px;
  white-space: nowrap;
  display: none;
}}
.clear-search-btn {{
  background: none;
  border: none;
  color: var(--tg-text-muted);
  cursor: pointer;
  font-size: 16px;
  display: none;
  padding: 4px;
}}
.chips-bar {{
  display: flex;
  gap: 6px;
  overflow-x: auto;
  margin-top: 8px;
  padding-bottom: 2px;
  scrollbar-width: none;
  -webkit-overflow-scrolling: touch;
}}
.chips-bar::-webkit-scrollbar {{
  display: none;
}}
.filter-chip {{
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid var(--tg-bubble-border);
  border-radius: 16px;
  padding: 4px 12px;
  font-size: 12.5px;
  font-weight: 500;
  color: var(--tg-text-muted);
  white-space: nowrap;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 5px;
  transition: all 0.15s ease;
}}
body.theme-light .filter-chip {{
  background: rgba(255, 255, 255, 0.2);
  color: #fff;
  border-color: rgba(255, 255, 255, 0.3);
}}
.filter-chip.active {{
  background: var(--tg-accent);
  color: #fff;
  border-color: var(--tg-accent);
}}
.speaker-filter-banner {{
  display: none;
  background: rgba(82, 136, 193, 0.18);
  border: 1px solid var(--tg-accent);
  border-radius: 8px;
  padding: 6px 12px;
  margin-top: 8px;
  font-size: 13px;
  color: #64b5f6;
  justify-content: space-between;
  align-items: center;
}}
.speaker-filter-banner button {{
  background: none;
  border: none;
  color: #fff;
  cursor: pointer;
  font-size: 13px;
  font-weight: 600;
}}
/* Chat Area */
.chat-container {{
  max-width: 680px;
  margin: 0 auto;
  padding: 14px 10px calc(var(--tg-safe-bottom) + 80px) 10px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}}
.chat-row {{
  display: flex;
  align-items: flex-end;
  gap: 8px;
  width: 100%;
}}
.chat-row.track-row, .chat-row.mark-row {{
  justify-content: center;
}}
.user-avatar {{
  width: 36px;
  height: 36px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 600;
  font-size: 14px;
  color: #fff;
  flex-shrink: 0;
  margin-bottom: 2px;
  user-select: none;
  -webkit-user-select: none;
  cursor: pointer;
  transition: transform 0.1s ease;
}}
.user-avatar:active {{
  transform: scale(0.92);
}}
/* Authentic Telegram Message Bubble with Tail */
.message-bubble {{
  background: var(--tg-bubble-bg);
  border-radius: 16px 16px 16px 4px;
  padding: 6px 12px 6px 12px;
  max-width: 86%;
  position: relative;
  box-shadow: 0 1px 2px rgba(0,0,0,0.22);
  word-wrap: break-word;
  word-break: break-word;
  cursor: pointer;
  transition: filter 0.15s ease;
}}
.bubble-tail {{
  position: absolute;
  bottom: 0;
  left: -8px;
  width: 11px;
  height: 20px;
  pointer-events: none;
}}
.message-bubble:active {{
  filter: brightness(0.93);
}}
.bubble-header {{
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 2px;
  line-height: 1.2;
}}
.sender-name {{
  font-size: 13.5px;
  font-weight: 600;
  cursor: pointer;
  display: inline-block;
}}
.sender-name:hover {{
  text-decoration: underline;
}}
.sender-handle {{
  font-size: 12px;
  color: var(--tg-text-muted);
  text-decoration: none;
  cursor: pointer;
}}
.sender-handle:hover {{
  color: var(--tg-accent);
  text-decoration: underline;
}}
.text-content {{
  font-size: var(--tg-font-size);
  line-height: 1.42;
  color: var(--tg-text);
  white-space: pre-wrap;
}}
.chat-link {{
  color: var(--tg-accent);
  text-decoration: underline;
  word-break: break-all;
}}
.bubble-footer {{
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 4px;
  margin-top: 2px;
  float: right;
  margin-left: 12px;
  user-select: none;
  -webkit-user-select: none;
}}
.timestamp {{
  font-size: 11px;
  color: var(--tg-text-muted);
  font-variant-numeric: tabular-nums;
}}
.tick-icon {{
  font-size: 11px;
  color: var(--tg-accent);
  letter-spacing: -2px;
  margin-right: 2px;
}}
.copy-btn {{
  background: transparent;
  border: none;
  color: var(--tg-text-muted);
  cursor: pointer;
  padding: 2px;
  display: flex;
  align-items: center;
  opacity: 0.6;
}}
.copy-btn:hover {{
  opacity: 1;
}}
/* Telegram Music Card */
.track-bubble {{
  background: var(--tg-track-bg);
  border: 1px solid var(--tg-track-border);
  border-radius: 16px;
  padding: 10px 14px;
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  max-width: 440px;
  cursor: pointer;
  box-shadow: 0 1px 3px rgba(0,0,0,0.2);
  transition: transform 0.15s ease;
}}
.track-bubble:active {{
  transform: scale(0.98);
}}
.track-play-icon {{
  width: 42px;
  height: 42px;
  border-radius: 50%;
  background: var(--tg-accent);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  box-shadow: 0 2px 5px rgba(0,0,0,0.25);
}}
.track-info {{
  flex: 1;
  min-width: 0;
}}
.track-title {{
  font-size: 14px;
  font-weight: 600;
  color: #fff;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}
body.theme-light .track-title {{
  color: #111;
}}
.track-sub {{
  font-size: 11.5px;
  color: var(--tg-text-muted);
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 3px;
}}
.waveform {{
  display: inline-flex;
  align-items: center;
  gap: 2px;
  height: 14px;
}}
.waveform i {{
  display: inline-block;
  width: 2.5px;
  background: var(--tg-accent);
  border-radius: 1px;
  opacity: 0.8;
  transition: height 0.2s ease;
}}
@keyframes waveAnim {{
  0% {{ transform: scaleY(0.4); }}
  100% {{ transform: scaleY(1.2); }}
}}
.track-bubble.playing .waveform i:nth-child(odd) {{
  animation: waveAnim 0.7s ease-in-out infinite alternate;
}}
.track-bubble.playing .waveform i:nth-child(even) {{
  animation: waveAnim 0.9s ease-in-out infinite alternate;
}}
.track-time {{
  font-size: 11.5px;
  color: var(--tg-text-muted);
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
}}
.system-pill {{
  background: var(--tg-pill-bg);
  backdrop-filter: blur(8px);
  border: 1px solid var(--tg-pill-border);
  border-radius: 16px;
  padding: 5px 14px;
  font-size: 12px;
  font-weight: 500;
  color: #fff;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  box-shadow: 0 1px 2px rgba(0,0,0,0.25);
}}
.pill-time {{
  color: var(--tg-text-muted);
  font-size: 11px;
}}
.fab-container {{
  position: fixed;
  right: 16px;
  bottom: calc(var(--tg-safe-bottom) + 20px);
  display: flex;
  flex-direction: column;
  gap: 10px;
  z-index: 150;
  user-select: none;
  -webkit-user-select: none;
}}
.fab-btn {{
  width: 44px;
  height: 44px;
  border-radius: 50%;
  background: var(--tg-header-bg);
  border: 1px solid var(--tg-bubble-border);
  color: var(--tg-text);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  box-shadow: 0 4px 10px rgba(0,0,0,0.35);
  transition: transform 0.15s ease, background 0.15s ease;
}}
.fab-btn:active {{
  transform: scale(0.92);
}}
.toast {{
  position: fixed;
  bottom: calc(var(--tg-safe-bottom) + 75px);
  left: 50%;
  transform: translateX(-50%) translateY(30px);
  background: rgba(23, 33, 43, 0.95);
  color: #fff;
  padding: 9px 18px;
  border-radius: 20px;
  font-size: 13.5px;
  font-weight: 500;
  box-shadow: 0 4px 14px rgba(0,0,0,0.4);
  z-index: 500;
  opacity: 0;
  pointer-events: none;
  transition: all 0.25s cubic-bezier(0.175, 0.885, 0.32, 1.275);
  border: 1px solid rgba(255,255,255,0.1);
  display: flex;
  align-items: center;
  gap: 6px;
  user-select: none;
  -webkit-user-select: none;
}}
.toast.show {{
  opacity: 1;
  transform: translateX(-50%) translateY(0);
}}
.modal-overlay {{
  position: fixed;
  inset: 0;
  background: var(--tg-modal-overlay);
  z-index: 300;
  display: none;
  align-items: flex-end;
  justify-content: center;
  opacity: 0;
  transition: opacity 0.2s ease;
}}
.modal-overlay.open {{
  display: flex;
  opacity: 1;
}}
.bottom-sheet {{
  background: var(--tg-modal-bg);
  border-radius: 20px 20px 0 0;
  width: 100%;
  max-width: 580px;
  max-height: 85vh;
  overflow-y: auto;
  padding: 12px 18px calc(var(--tg-safe-bottom) + 24px) 18px;
  box-shadow: 0 -4px 20px rgba(0,0,0,0.4);
  transform: translateY(100%);
  transition: transform 0.25s ease-out;
  border-top: 1px solid var(--tg-header-border);
}}
.modal-overlay.open .bottom-sheet {{
  transform: translateY(0);
}}
.sheet-handle {{
  width: 40px;
  height: 4px;
  background: var(--tg-text-muted);
  border-radius: 2px;
  margin: 0 auto 14px auto;
  opacity: 0.5;
}}
.sheet-header {{
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}}
.sheet-title {{
  font-size: 17px;
  font-weight: 700;
  color: #fff;
}}
body.theme-light .sheet-title {{
  color: #111;
}}
.sheet-close {{
  background: none;
  border: none;
  font-size: 20px;
  color: var(--tg-text-muted);
  cursor: pointer;
}}
/* Profile Sheet Styling (Authentic Telegram User Profile Card) */
.profile-sheet {{
  text-align: center;
  padding-top: 10px;
}}
.profile-header {{
  display: flex;
  flex-direction: column;
  align-items: center;
  margin-bottom: 20px;
}}
.profile-avatar {{
  width: 72px;
  height: 72px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 28px;
  font-weight: 700;
  color: #fff;
  margin-bottom: 10px;
  box-shadow: 0 4px 14px rgba(0,0,0,0.3);
}}
.profile-name {{
  font-size: 19px;
  font-weight: 700;
  color: var(--tg-text);
  word-break: break-word;
}}
.profile-status {{
  font-size: 13px;
  color: var(--tg-text-muted);
  margin-top: 3px;
}}
.profile-items-card {{
  background: var(--tg-card-inner);
  border-radius: 14px;
  padding: 4px 12px;
  margin-bottom: 18px;
  text-align: left;
}}
.profile-item {{
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 4px;
  cursor: pointer;
  border-bottom: 1px solid var(--tg-bubble-border);
}}
.profile-item:last-child {{
  border-bottom: none;
}}
.profile-item-icon {{
  font-size: 18px;
  color: var(--tg-accent);
  width: 24px;
  text-align: center;
}}
.profile-item-content {{
  flex: 1;
  min-width: 0;
}}
.profile-item-val {{
  font-size: 14.5px;
  font-weight: 600;
  color: var(--tg-text);
  word-break: break-all;
}}
.profile-item-lbl {{
  font-size: 12px;
  color: var(--tg-text-muted);
  margin-top: 2px;
}}
.profile-copy-icon {{
  background: none;
  border: none;
  color: var(--tg-text-muted);
  cursor: pointer;
}}
.profile-actions-grid {{
  display: flex;
  flex-direction: column;
  gap: 10px;
}}
.profile-action-btn {{
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  width: 100%;
  padding: 13px;
  border-radius: 12px;
  font-size: 14.5px;
  font-weight: 600;
  text-decoration: none;
  cursor: pointer;
  transition: filter 0.15s ease;
  border: none;
}}
.profile-action-btn.primary-btn {{
  background: var(--tg-accent);
  color: #fff;
}}
.profile-action-btn.primary-btn:active {{
  filter: brightness(0.9);
}}
.profile-action-btn.secondary-btn {{
  background: var(--tg-card-inner);
  color: var(--tg-text);
}}
.profile-action-btn.secondary-btn:active {{
  filter: brightness(0.9);
}}
/* Stats & Settings */
.stats-cards-grid {{
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
  margin-bottom: 18px;
}}
.stat-card {{
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid var(--tg-bubble-border);
  border-radius: 12px;
  padding: 12px;
}}
.stat-card-val {{
  font-size: 18px;
  font-weight: 700;
  color: var(--tg-accent);
}}
.stat-card-lbl {{
  font-size: 11.5px;
  color: var(--tg-text-muted);
  margin-top: 2px;
}}
.stat-section-title {{
  font-size: 13.5px;
  font-weight: 600;
  color: var(--tg-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin: 16px 0 10px 0;
}}
.stat-user-row {{
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px;
  border-radius: 10px;
  cursor: pointer;
  transition: background 0.15s ease;
}}
.stat-user-row:active {{
  background: rgba(255, 255, 255, 0.08);
}}
.stat-user-avatar {{
  width: 32px;
  height: 32px;
  border-radius: 50%;
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 600;
  font-size: 13px;
  flex-shrink: 0;
}}
.stat-user-info {{
  flex: 1;
}}
.stat-user-name {{
  display: flex;
  justify-content: space-between;
  font-size: 13.5px;
  font-weight: 500;
}}
.stat-user-count {{
  font-size: 12px;
  color: var(--tg-text-muted);
}}
.stat-bar-bg {{
  background: rgba(255, 255, 255, 0.08);
  height: 4px;
  border-radius: 2px;
  margin-top: 6px;
  overflow: hidden;
}}
.stat-bar-fill {{
  height: 100%;
  border-radius: 2px;
}}
.stat-track-item {{
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 0;
  border-bottom: 1px solid var(--tg-bubble-border);
  font-size: 13px;
}}
.stat-track-name {{
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}}
.stat-track-time {{
  font-size: 11px;
  color: var(--tg-text-muted);
}}
.settings-group {{
  margin-bottom: 18px;
}}
.settings-group-label {{
  font-size: 13px;
  color: var(--tg-text-muted);
  margin-bottom: 8px;
  font-weight: 500;
}}
.btn-toggle-group {{
  display: flex;
  gap: 8px;
}}
.btn-toggle {{
  flex: 1;
  padding: 9px 0;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid var(--tg-bubble-border);
  color: var(--tg-text);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  text-align: center;
  user-select: none;
  -webkit-user-select: none;
}}
.btn-toggle.active {{
  background: var(--tg-accent);
  color: #fff;
  border-color: var(--tg-accent);
}}
.action-row-btn {{
  width: 100%;
  padding: 12px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid var(--tg-bubble-border);
  color: var(--tg-text);
  font-size: 14px;
  font-weight: 500;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  cursor: pointer;
  margin-top: 8px;
  user-select: none;
  -webkit-user-select: none;
}}
.action-row-btn:active {{
  background: rgba(255, 255, 255, 0.12);
}}
.empty-state {{
  font-size: 13px;
  color: var(--tg-text-muted);
  text-align: center;
  padding: 14px 0;
}}
@media print {{
  .app-header, .controls-wrapper, .fab-container, .modal-overlay, #toast, .copy-btn {{
    display: none !important;
  }}
  body {{
    background: #fff !important;
    color: #000 !important;
  }}
  .message-bubble, .track-bubble, .system-pill {{
    border: 1px solid #ccc !important;
    background: #fff !important;
    color: #000 !important;
    box-shadow: none !important;
  }}
}}
  </style>
</head>
<body class="theme-dark">
  <header class="app-header">
<button class="back-btn" onclick="history.back()" title="Geri">
  <svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor"><path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"/></svg>
</button>
<div class="header-avatar">🎙</div>
<div class="header-details">
  <div class="header-title">{escape(sess.title)}</div>
  <div class="header-sub">
    <span class="live-dot"></span>
    <span>{dur_str} · {total_msgs} mesaj · {len(sess.tracks)} mahnı</span>
  </div>
</div>
<div class="header-actions">
  <button class="icon-btn" onclick="openModal('statsModal')" title="Statistika">
    <svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor"><path d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zM9 17H7v-7h2v7zm4 0h-2V7h2v10zm4 0h-2v-4h2v4z"/></svg>
  </button>
  <button class="icon-btn" onclick="openModal('settingsModal')" title="Parametrlər">
    <svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor"><path d="M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58c.18-.14.23-.41.12-.61l-1.92-3.32c-.12-.22-.37-.29-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54c-.04-.24-.24-.41-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.09.63-.09.94s.02.64.07.94l-2.03 1.58c-.18.14-.23.41-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z"/></svg>
  </button>
</div>
  </header>

  <div class="controls-wrapper">
<div class="search-box">
  <svg viewBox="0 0 24 24" width="18" height="18" fill="var(--tg-text-muted)"><path d="M15.5 14h-.79l-.28-.27C15.41 12.59 16 11.11 16 9.5 16 5.91 13.09 3 9.5 3S3 5.91 3 9.5 5.91 16 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg>
  <input type="text" id="searchInput" class="search-input" placeholder="Axtarış..." oninput="onSearchInput()">
  <span id="searchCountBadge" class="search-count-badge"></span>
  <button id="clearSearchBtn" class="clear-search-btn" onclick="clearSearch()">✕</button>
</div>
<div class="chips-bar">
  <button class="filter-chip active" data-type="all" onclick="setTypeFilter(this, 'all')">Hamısı ({len(items)})</button>
  <button class="filter-chip" data-type="msg" onclick="setTypeFilter(this, 'msg')">💬 Mesajlar ({total_msgs})</button>
  <button class="filter-chip" data-type="track" onclick="setTypeFilter(this, 'track')">🎵 Mahnılar ({len(sess.tracks)})</button>
  {f'<button class="filter-chip" data-type="mark" onclick="setTypeFilter(this, \'mark\')">📍 Nişanlar ({len(sess.marks)})</button>' if sess.marks else ''}
</div>
<div id="speakerFilterBanner" class="speaker-filter-banner">
  <span id="speakerFilterText">👤 Danışan: </span>
  <button onclick="clearSpeakerFilter()">✕ Sıfırla</button>
</div>
  </div>

  <main class="chat-container" id="timelineContainer">
{''.join(events_html)}
  </main>

  <div class="fab-container">
<button class="fab-btn" onclick="scrollToTop()" title="Yuxarı">
  <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M7.41 15.41L12 10.83l4.59 4.58L18 14l-6-6-6 6z"/></svg>
</button>
<button class="fab-btn" onclick="scrollToBottom()" title="Aşağı">
  <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6z"/></svg>
</button>
  </div>

  <div id="toast" class="toast">
<span id="toastIcon">📋</span>
<span id="toastMsg">Kopyalandı</span>
  </div>

  <!-- Telegram Profile Bottom Sheet Modal -->
  <div id="profileModal" class="modal-overlay" onclick="onModalOverlayClick(event, 'profileModal')">
<div class="bottom-sheet profile-sheet">
  <div class="sheet-handle"></div>
  <div class="profile-header">
    <div id="profAvatar" class="profile-avatar">?</div>
    <div class="profile-name" id="profName">İstifadəçi Adı</div>
    <div class="profile-status" id="profStatus">Telegram İstifadəçisi</div>
  </div>

  <div class="profile-items-card">
    <div class="profile-item" id="profUsernameRow" onclick="copyProfItem('profUsernameVal', 'Username kopyalandı')">
      <div class="profile-item-icon">@</div>
      <div class="profile-item-content">
        <div id="profUsernameVal" class="profile-item-val">—</div>
        <div class="profile-item-lbl">İstifadəçi adı (Username)</div>
      </div>
      <button class="profile-copy-icon" title="Kopyala">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M16 1H4C2.9 1 2 1.9 2 3v14h2V3h12V1zm3 4H8C6.9 5 6 5.9 6 7v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z"/></svg>
      </button>
    </div>

    <div class="profile-item" id="profIdRow" onclick="copyProfItem('profIdVal', 'ID kopyalandı')">
      <div class="profile-item-icon">🆔</div>
      <div class="profile-item-content">
        <div id="profIdVal" class="profile-item-val">—</div>
        <div class="profile-item-lbl">Telegram ID</div>
      </div>
      <button class="profile-copy-icon" title="Kopyala">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M16 1H4C2.9 1 2 1.9 2 3v14h2V3h12V1zm3 4H8C6.9 5 6 5.9 6 7v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z"/></svg>
      </button>
    </div>
  </div>

  <div class="profile-actions-grid">
    <a id="profTgLink" href="#" target="_blank" class="profile-action-btn primary-btn" rel="noopener noreferrer">
      <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm4.64 6.8c-.15 1.58-.8 5.42-1.13 7.19-.14.75-.42 1-.68 1.03-.58.05-1.02-.38-1.58-.75-.88-.58-1.38-.94-2.23-1.5-.99-.65-.35-1.01.22-1.59.15-.15 2.71-2.48 2.76-2.69.01-.03.01-.14-.07-.19-.08-.05-.19-.02-.27 0-.12.03-1.99 1.27-5.62 3.72-.53.36-1.01.54-1.44.53-.47-.01-1.38-.27-2.06-.49-.83-.27-1.49-.42-1.43-.88.03-.24.37-.49 1.02-.75 3.98-1.73 6.64-2.87 7.97-3.44 3.79-1.61 4.58-1.89 5.09-1.9.11 0 .37.03.54.17.14.12.18.28.2.45-.02.07-.02.21-.04.34z"/></svg>
      <span id="profTgBtnText">Telegram-da Profili Aç</span>
    </a>
    <button id="profFilterBtn" class="profile-action-btn secondary-btn" onclick="filterFromProfile()">
      <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M15.5 14h-.79l-.28-.27C15.41 12.59 16 11.11 16 9.5 16 5.91 13.09 3 9.5 3S3 5.91 3 9.5 5.91 16 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg>
      <span>Mesajlarını Süz</span>
    </button>
  </div>
</div>
  </div>

  <!-- Stats Modal -->
  <div id="statsModal" class="modal-overlay" onclick="onModalOverlayClick(event, 'statsModal')">
<div class="bottom-sheet">
  <div class="sheet-handle"></div>
  <div class="sheet-header">
    <div class="sheet-title">📊 Yayım Statistikası</div>
    <button class="sheet-close" onclick="closeModal('statsModal')">✕</button>
  </div>
  <div class="stats-cards-grid">
    <div class="stat-card">
      <div class="stat-card-val">{dur_str}</div>
      <div class="stat-card-lbl">Müddət ({start_str} - {end_str})</div>
    </div>
    <div class="stat-card">
      <div class="stat-card-val">{total_msgs}</div>
      <div class="stat-card-lbl">Ümumi Mesaj Sayı</div>
    </div>
    <div class="stat-card">
      <div class="stat-card-val">{len(sess.tracks)}</div>
      <div class="stat-card-lbl">Səslənən Mahnı</div>
    </div>
    <div class="stat-card">
      <div class="stat-card-val">{len(speakers)}</div>
      <div class="stat-card-lbl">Fəal Danışan</div>
    </div>
  </div>

  <div class="stat-section-title">Aktiv Danışanlar</div>
  <div class="stat-users-list">
    {''.join(leaderboard_html)}
  </div>

  <div class="stat-section-title">Səslənən Mahnılar</div>
  <div class="stat-tracks-list">
    {''.join(track_list_html)}
  </div>
</div>
  </div>

  <!-- Settings Modal -->
  <div id="settingsModal" class="modal-overlay" onclick="onModalOverlayClick(event, 'settingsModal')">
<div class="bottom-sheet">
  <div class="sheet-handle"></div>
  <div class="sheet-header">
    <div class="sheet-title">⚙️ Tənzimləmələr</div>
    <button class="sheet-close" onclick="closeModal('settingsModal')">✕</button>
  </div>

  <div class="settings-group">
    <div class="settings-group-label">Mövzu (Görünüş):</div>
    <div class="btn-toggle-group">
      <button class="btn-toggle active" id="themeDarkBtn" onclick="setTheme('dark')">🌙 Telegram</button>
      <button class="btn-toggle" id="themeOledBtn" onclick="setTheme('oled')">🖤 Qara</button>
      <button class="btn-toggle" id="themeLightBtn" onclick="setTheme('light')">☀️ İşıqlı</button>
    </div>
  </div>

  <div class="settings-group">
    <div class="settings-group-label">Şrift Ölçüsü:</div>
    <div class="btn-toggle-group">
      <button class="btn-toggle" onclick="setFontSize('13.5px', this)">A- Kiçik</button>
      <button class="btn-toggle active" onclick="setFontSize('15px', this)">A Standart</button>
      <button class="btn-toggle" onclick="setFontSize('17px', this)">A+ Böyük</button>
    </div>
  </div>

  <div class="settings-group">
    <div class="settings-group-label">Xronologiya Sırası:</div>
    <div class="btn-toggle-group">
      <button class="btn-toggle active" id="orderNormalBtn" onclick="setOrder(false)">⬇️ Köhnədən yeniyə</button>
      <button class="btn-toggle" id="orderReverseBtn" onclick="setOrder(true)">⬆️ Yenidən köhnəyə</button>
    </div>
  </div>

  <div class="settings-group">
    <div class="settings-group-label">İxrac və Paylaşım:</div>
    <button class="action-row-btn" onclick="downloadTxtFile()">
      <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z"/></svg>
      Mətn kimi yüklə (.txt)
    </button>
    <button class="action-row-btn" onclick="window.print()">
      <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M19 8H5c-1.66 0-3 1.34-3 3v6h4v4h12v-4h4v-6c0-1.66-1.34-3-3-3zm-4 11H9v-5h6v5zm4-7c-.55 0-1-.45-1-1s.45-1 1-1 1 .45 1 1-.45 1-1 1zm1-9H4v4h16V3z"/></svg>
      Çap et / PDF saxla
    </button>
  </div>
</div>
  </div>

  <script>
const RAW_TXT = {raw_txt_json};
const DOC_TITLE = {title_json};

let activeType = 'all';
let activeSpeaker = null;
let searchQuery = '';
let currentProfileUser = '';

function showToast(msg, icon = '📋') {{
  const t = document.getElementById('toast');
  document.getElementById('toastMsg').innerText = msg;
  document.getElementById('toastIcon').innerText = icon;
  t.classList.add('show');
  clearTimeout(window._toastTimeout);
  window._toastTimeout = setTimeout(() => t.classList.remove('show'), 2200);
}}

function onBubbleClick(el, evt) {{
  if (window.getSelection && window.getSelection().toString()) return;
  const text = el.querySelector('.text-content').innerText;
  copyToClipboard(text, 'Mesaj kopyalandı');
}}

function copyText(btn, evt) {{
  evt.stopPropagation();
  const bubble = btn.closest('.message-bubble');
  const text = bubble.querySelector('.text-content').innerText;
  copyToClipboard(text, 'Mesaj kopyalandı');
}}

function copyTrack(title, evt) {{
  copyToClipboard(title, 'Mahnı adı kopyalandı');
}}

function toggleTrack(bubble, title, evt) {{
  evt.stopPropagation();
  const isPlaying = bubble.classList.contains('playing');
  document.querySelectorAll('.track-bubble').forEach(b => b.classList.remove('playing'));
  if (!isPlaying) {{
    bubble.classList.add('playing');
    showToast('🎵 ' + title, '▶️');
  }} else {{
    showToast('⏸ Dayandırıldı', '⏸');
  }}
}}

function copyToClipboard(text, successMsg) {{
  if (navigator.clipboard && navigator.clipboard.writeText) {{
    navigator.clipboard.writeText(text).then(() => {{
      showToast(successMsg, '✓');
    }}).catch(() => {{
      fallbackCopy(text, successMsg);
    }});
  }} else {{
    fallbackCopy(text, successMsg);
  }}
}}

function fallbackCopy(text, successMsg) {{
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  try {{
    document.execCommand('copy');
    showToast(successMsg, '✓');
  }} catch (e) {{
    showToast('Kopyalamaq mümkün olmadı', '✕');
  }}
  document.body.removeChild(ta);
}}

function copyProfItem(elementId, msg) {{
  const val = document.getElementById(elementId).innerText.trim();
  if (val && val !== '—' && val !== 'Təyin edilməyib') {{
    copyToClipboard(val.replace(/^@/, ''), msg);
  }}
}}

function openProfileCard(name, uid, uname, color) {{
  currentProfileUser = name;
  const avatar = document.getElementById('profAvatar');
  avatar.innerText = (name || '?').trim().charAt(0).toUpperCase();
  avatar.style.background = color || '#5288c1';
  document.getElementById('profName').innerText = name;

  const uRow = document.getElementById('profUsernameRow');
  const uVal = document.getElementById('profUsernameVal');
  const idVal = document.getElementById('profIdVal');
  const tgLink = document.getElementById('profTgLink');
  const tgBtnText = document.getElementById('profTgBtnText');

  if (uname) {{
    uVal.innerText = '@' + uname;
    tgLink.href = 'https://t.me/' + uname;
    tgBtnText.innerText = 'Telegram-da Aç (@' + uname + ')';
    tgLink.style.display = 'flex';
  }} else if (uid && String(uid) !== '0') {{
    uVal.innerText = 'Təyin edilməyib';
    tgLink.href = 'tg://user?id=' + uid;
    tgBtnText.innerText = 'Telegram-da Aç (ID: ' + uid + ')';
    tgLink.style.display = 'flex';
  }} else {{
    uVal.innerText = 'Təyin edilməyib';
    tgLink.style.display = 'none';
  }}

  if (uid && String(uid) !== '0') {{
    idVal.innerText = uid;
  }} else {{
    idVal.innerText = '—';
  }}

  openModal('profileModal');
}}

function filterFromProfile() {{
  if (currentProfileUser) {{
    closeModal('profileModal');
    filterBySpeaker(currentProfileUser);
  }}
}}

function onSearchInput() {{
  const inp = document.getElementById('searchInput');
  searchQuery = inp.value.trim().toLowerCase();
  document.getElementById('clearSearchBtn').style.display = searchQuery ? 'block' : 'none';
  applyFilters();
}}

function clearSearch() {{
  const inp = document.getElementById('searchInput');
  inp.value = '';
  searchQuery = '';
  document.getElementById('clearSearchBtn').style.display = 'none';
  applyFilters();
}}

function setTypeFilter(btn, type) {{
  document.querySelectorAll('.filter-chip').forEach(c => c.classList.remove('active'));
  btn.classList.add('active');
  activeType = type;
  applyFilters();
}}

function filterBySpeaker(name) {{
  activeSpeaker = name;
  const banner = document.getElementById('speakerFilterBanner');
  document.getElementById('speakerFilterText').innerText = '👤 Danışan: ' + name;
  banner.style.display = 'flex';
  applyFilters();
  showToast('Yalnız ' + name + ' filtrləndi', '👤');
}}

function clearSpeakerFilter() {{
  activeSpeaker = null;
  document.getElementById('speakerFilterBanner').style.display = 'none';
  applyFilters();
  showToast('Danışan filtri sıfırlandı');
}}

function applyFilters() {{
  const rows = document.querySelectorAll('.chat-row');
  let visibleCount = 0;
  rows.forEach(r => {{
    const rType = r.dataset.type;
    const rSender = r.dataset.sender;
    const rText = r.innerText.toLowerCase();

    const matchType = (activeType === 'all' || rType === activeType);
    const matchSpeaker = (!activeSpeaker || rSender === activeSpeaker);
    const matchSearch = (!searchQuery || rText.includes(searchQuery));

    const show = (matchType && matchSpeaker && matchSearch);
    r.style.display = show ? 'flex' : 'none';
    if (show) visibleCount++;
  }});

  const badge = document.getElementById('searchCountBadge');
  if (searchQuery) {{
    badge.innerText = visibleCount + ' tapıldı';
    badge.style.display = 'inline-block';
  }} else {{
    badge.style.display = 'none';
  }}
}}

function scrollToTop() {{
  window.scrollTo({{ top: 0, behavior: 'smooth' }});
}}

function scrollToBottom() {{
  window.scrollTo({{ top: document.body.scrollHeight, behavior: 'smooth' }});
}}

function openModal(id) {{
  const m = document.getElementById(id);
  m.classList.add('open');
  document.body.style.overflow = 'hidden';
}}

function closeModal(id) {{
  const m = document.getElementById(id);
  m.classList.remove('open');
  document.body.style.overflow = '';
}}

function onModalOverlayClick(evt, id) {{
  if (evt.target.classList.contains('modal-overlay')) {{
    closeModal(id);
  }}
}}

function setTheme(th) {{
  document.body.classList.remove('theme-dark', 'theme-oled', 'theme-light');
  document.getElementById('themeDarkBtn').classList.remove('active');
  document.getElementById('themeOledBtn').classList.remove('active');
  document.getElementById('themeLightBtn').classList.remove('active');

  if (th === 'oled') {{
    document.body.classList.add('theme-oled');
    document.getElementById('themeOledBtn').classList.add('active');
  }} else if (th === 'light') {{
    document.body.classList.add('theme-light');
    document.getElementById('themeLightBtn').classList.add('active');
  }} else {{
    document.body.classList.add('theme-dark');
    document.getElementById('themeDarkBtn').classList.add('active');
  }}
}}

function setFontSize(sz, btn) {{
  document.documentElement.style.setProperty('--tg-font-size', sz);
  btn.parentElement.querySelectorAll('.btn-toggle').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
}}

function setOrder(reverse) {{
  const cont = document.getElementById('timelineContainer');
  cont.style.flexDirection = reverse ? 'column-reverse' : 'column';
  document.getElementById('orderNormalBtn').classList.toggle('active', !reverse);
  document.getElementById('orderReverseBtn').classList.toggle('active', reverse);
}}

function downloadTxtFile() {{
  const blob = new Blob([RAW_TXT], {{ type: 'text/plain;charset=utf-8' }});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = (DOC_TITLE || 'jurnal').replace(/[^a-zA-Z0-9_]/g, '_') + '.txt';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
  showToast('TXT faylı endirildi', '💾');
}}
  </script>
</body>
</html>
"""
    path_html = os.path.abspath(os.path.join(out_dir, f"va_{sess.chat_id}_{int(sess.t0)}.html"))
    with open(path_html, "w", encoding="utf-8") as f:
        f.write(html_content)

    return path_html



__all__ = ["generate_transcript"]
