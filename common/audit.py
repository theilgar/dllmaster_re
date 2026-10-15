"""🛡 common/audit.py — Qrup Admin və Moderasiya Auditi, HTML Hesabat Generatoru və Gün Sonu Çatdırılma.

Funksionallıq:
1. Bütün admin komandaları və əməliyyatlarını (ban, mute, kick, promote, demote, pin, unpin, purge, title, photo)
   kimin tərəfindən (Admin ID, Ad, @username) və kimə qarşı (Hədəf ID, Ad, @username) icra olunduğunu bazada qeyd edir.
2. Dublikatların (Telethon UpdateChannelParticipant + ChatAction + Bot hadisələri) qarşısını ağıllı şəkildə alır.
3. Günün sonunda (23:59 AZT) və ya istək əsasında yüksək dizaynlı, interaktiv HTML hesabat hazırlayır.
4. HTML hesabatı təyin edilmiş audit hədəfinə (kanal, qrup, forum topiki və ya yaradıcıya) avtomatik göndərir.
"""
import asyncio
import html
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta

from common.utilities import get_tz, script_json, spawn

logger = logging.getLogger(__name__)

# Botun vaxt zonası (BOT_TZ, default Asia/Baku) — əvvəl UTC+4 sabit idi və BOT_TZ nəzərə alınmırdı
AZT = get_tz()
AUDIT_REPORTS_DIR = os.path.join("data", "audit_reports")

# Dublikat önləyici keş: (chat_id, target_id, action_prefix) -> (log_id, ts, has_admin)
_DEDUP_WINDOW = 15
_recent_events = {}
_pending_exec: dict = {}         # insert hələ bitməyib (log_id=0) — icraçını sonra yaz
_dedup_lock = threading.Lock()

# Hadisələrin vizual məlumatları (nişan rəngi, ikon, azərbaycan dilində adı)
ACTION_META = {
    "ban":        {"name": "Ban / Blok",           "icon": "🚫", "badge": "#ef4444", "cat": "ban"},
    "unban":      {"name": "Ban Ləğvi",            "icon": "🔓", "badge": "#10b981", "cat": "unban"},
    "mute":       {"name": "Mute / Məhdudiyyət",   "icon": "🔇", "badge": "#f59e0b", "cat": "mute"},
    "unmute":     {"name": "Mute Ləğvi",           "icon": "🔊", "badge": "#10b981", "cat": "unban"},
    "kick":       {"name": "Qovulma (Kick)",       "icon": "👢", "badge": "#f97316", "cat": "kick"},
    "promote":    {"name": "Admin Təyinatı",       "icon": "👮", "badge": "#3b82f6", "cat": "promo"},
    "demote":     {"name": "Adminlikdən Alındı",   "icon": "👤", "badge": "#64748b", "cat": "promo"},
    "admin_edit": {"name": "Admin Hüquq Dəyişdi",  "icon": "⚙️", "badge": "#6366f1", "cat": "promo"},
    "pin":        {"name": "Mesaj Bərkidildi",     "icon": "📌", "badge": "#8b5cf6", "cat": "pin"},
    "unpin":      {"name": "Bərkitmə Ləğv Edildi", "icon": "📍", "badge": "#64748b", "cat": "pin"},
    "purge":      {"name": "Mesajların Silinməsi", "icon": "🧹", "badge": "#ec4899", "cat": "del"},
    "del":        {"name": "Mesaj Silindi",        "icon": "🗑", "badge": "#94a3b8", "cat": "del"},
    "title":      {"name": "Qrup Adı Dəyişdi",     "icon": "🏷", "badge": "#14b8a6", "cat": "info"},
    "photo":      {"name": "Qrup Şəkli Dəyişdi",   "icon": "🖼", "badge": "#06b6d4", "cat": "info"},
    "command":    {"name": "Admin Əmri",           "icon": "⚡", "badge": "#eab308", "cat": "cmd"},
}


def _norm_action(act: str) -> str:
    a = (act or "").strip().lower()
    if a in ("adm+", "promo", "promote", "promoted"):
        return "promote"
    if a in ("adm-", "demote", "demoted"):
        return "demote"
    if a in ("adm~", "admin_edit", "rights"):
        return "admin_edit"
    if a in ("kick", "kicked"):
        return "kick"
    if a in ("mute", "muted", "restrict", "restricted"):
        return "mute"
    if a in ("unmute", "unmuted"):
        return "unmute"
    if a in ("unban", "unbanned"):
        return "unban"
    if a in ("ban", "banned"):
        return "ban"
    if a in ("pin", "pinned"):
        return "pin"
    if a in ("unpin", "unpinned"):
        return "unpin"
    if a in ("purge", "clear"):
        return "purge"
    if a in ("title",):
        return "title"
    if a in ("photo",):
        return "photo"
    if a in ("del", "delete", "deleted"):
        return "del"
    return a or "action"


def get_azt_now() -> datetime:
    return datetime.now(AZT)


def get_today_bounds(date_str: str = None) -> tuple[int, int, str]:
    if date_str:
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=AZT)
        except Exception:
            dt = get_azt_now()
    else:
        dt = get_azt_now()

    d_str = dt.strftime("%Y-%m-%d")
    s_dt = datetime(dt.year, dt.month, dt.day, 0, 0, 0, tzinfo=AZT)
    e_dt = datetime(dt.year, dt.month, dt.day, 23, 59, 59, tzinfo=AZT)
    return int(s_dt.timestamp()), int(e_dt.timestamp()), d_str


def record_audit_event(chat_id: int, chat_title: str,
                       admin_id: int | None, admin_name: str, admin_username: str,
                       target_id: int | None, target_name: str, target_username: str,
                       action: str, details: str = "", ts: float | int | None = None,
                       extra: dict | None = None) -> int:
    """Bütün admin və moderasiya hadisələrini mərkəzi bazada qeyd edir."""
    from common.database import get_db

    now_ts = int(ts or time.time())
    act_norm = _norm_action(action)
    cid = int(chat_id or 0)
    tid = int(target_id or 0)
    aid = int(admin_id or 0) if admin_id else None

    # Təmizləmə
    a_name = (admin_name or "").strip()
    a_uname = (admin_username or "").strip().lstrip("@")
    t_name = (target_name or "").strip()
    t_uname = (target_username or "").strip().lstrip("@")
    c_title = (chat_title or "").strip() or str(cid)
    det = (details or "").strip()
    extra_str = json.dumps(extra, ensure_ascii=False) if extra else None

    # Dedup yoxlaması: son 15 saniyə ərzində eyni çat, hədəf və əməl təkrar olmasın
    dedup_key = (cid, tid, act_norm)
    now_f = time.time()

    def _update(log_id, a_id, name, uname):
        try:
            get_db().update_admin_audit_executor(log_id, a_id, name, uname)
            logger.debug(f"🛡 Audit executor yeniləndi: log_id={log_id}, admin={name} ({a_id})")
        except Exception as e:
            logger.debug(f"Audit executor yeniləmə xətası: {e}")

    def _run_bg(fn, *args):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return fn(*args)
        spawn(asyncio.to_thread(fn, *args))
        return None

    with _dedup_lock:
        for k in [k for k, v in _recent_events.items() if now_f - v[1] > _DEDUP_WINDOW]:
            _recent_events.pop(k, None)
        existing = _recent_events.get(dedup_key)
        if existing:
            log_id, _, has_admin = existing
            # Əgər əvvəlki qeyddə admin bilinmirdisə və indi məlum oldusa, admini yenilə
            if not has_admin and aid and a_name:
                _recent_events[dedup_key] = (log_id, now_f, True)
                if not log_id:
                    # insert hələ thread-dədir: id-siz UPDATE heç bir sətrə toxunmur → icraçı itirdi
                    _pending_exec[dedup_key] = (aid, a_name, a_uname)
                    return 0
            else:
                return log_id
        else:
            _recent_events[dedup_key] = (0, now_f, bool(aid and a_name))
    if existing:
        _run_bg(_update, log_id, aid, a_name, a_uname)
        return log_id

    def _insert():
        try:
            db = get_db()
            lid = db.log_admin_audit(
                chat_id=cid, chat_title=c_title,
                admin_id=aid, admin_name=a_name, admin_username=a_uname,
                target_id=tid if tid else None, target_name=t_name, target_username=t_uname,
                action=act_norm, details=det, ts=now_ts, extra=extra_str
            )
            with _dedup_lock:
                pend = _pending_exec.pop(dedup_key, None)
                _recent_events[dedup_key] = (lid, now_f, bool(aid and a_name) or pend is not None)
            if pend and lid:
                _update(lid, *pend)
            ex_id, ex_name = (pend[0], pend[1]) if pend else (aid, a_name)
            logger.info(f"🛡 Audit qeyd edildi: [{c_title}] {act_norm} | İcraçı: {ex_name or 'Bilinmir'} ({ex_id}) | Hədəf: {t_name or '—'} ({tid})")
            return lid
        except Exception as e:
            with _dedup_lock:
                _pending_exec.pop(dedup_key, None)
            logger.warning(f"❌ Audit qeyd olunarkən xəta: {e}")
            return 0

    res = _run_bg(_insert)
    return res or 0


def _uname_link(uname: str, cls: str = "") -> str:
    """@username linki — mətn istifadəçidən gələ bilər (/ban @...), ona görə tam escape."""
    if not uname:
        return ""
    u = html.escape(uname, quote=True)
    return f"<a href='https://t.me/{u}' target='_blank'{f' class={chr(39)}{cls}{chr(39)}' if cls else ''}>@{u}</a>"


def generate_html_report(events: list[dict], date_str: str, metadata: dict | None = None) -> str:
    """Müasir, responsiv, tam oflayn işləyən interaktiv HTML hesabat generasiya edir."""
    metadata = metadata or {}
    gen_time_str = get_azt_now().strftime("%d.%m.%Y %H:%M:%S")

    total_events = len(events)
    counts = {
        "ban": 0, "unban": 0, "mute": 0, "kick": 0,
        "promote": 0, "demote": 0, "pin": 0, "purge": 0, "other": 0
    }
    admin_stats = {}   # admin_key -> {"name", "uname", "id", "total", "ban", "mute", "kick", "promo", "other"}
    chats_seen = set()

    formatted_events = []
    for idx, ev_raw in enumerate(events, 1):
        ev = dict(ev_raw) if hasattr(ev_raw, "keys") else (ev_raw if isinstance(ev_raw, dict) else {})
        action = _norm_action(ev.get("action", ""))
        ts_val = int(ev.get("ts") or time.time())
        ev_time = datetime.fromtimestamp(ts_val, AZT).strftime("%H:%M:%S")

        c_id = ev.get("chat_id") or 0
        c_title = ev.get("chat_title") or str(c_id)
        chats_seen.add((c_id, c_title))

        a_id = ev.get("admin_id") or 0
        a_name = ev.get("admin_name") or "Bilinmir"
        a_uname = (ev.get("admin_username") or "").lstrip("@")

        t_id = ev.get("target_id") or 0
        t_name = ev.get("target_name") or "—"
        t_uname = (ev.get("target_username") or "").lstrip("@")

        details = ev.get("details") or ""

        # Sayğaclar
        if action in counts:
            counts[action] += 1
        elif action in ("admin_edit",):
            counts["promote"] += 1
        else:
            counts["other"] += 1

        # Admin statistikası
        if a_id or a_name != "Bilinmir":
            adm_k = a_id if a_id else a_name
            if adm_k not in admin_stats:
                admin_stats[adm_k] = {
                    "name": a_name, "uname": a_uname, "id": a_id,
                    "total": 0, "ban": 0, "mute": 0, "kick": 0, "promo": 0, "other": 0
                }
            admin_stats[adm_k]["total"] += 1
            if action in ("ban", "unban"):
                admin_stats[adm_k]["ban"] += 1
            elif action in ("mute", "unmute"):
                admin_stats[adm_k]["mute"] += 1
            elif action == "kick":
                admin_stats[adm_k]["kick"] += 1
            elif action in ("promote", "demote", "admin_edit"):
                admin_stats[adm_k]["promo"] += 1
            else:
                admin_stats[adm_k]["other"] += 1

        meta = ACTION_META.get(action, {"name": action.title(), "icon": "⚡", "badge": "#64748b"})
        formatted_events.append({
            "num": idx,
            "time": ev_time,
            "ts": ts_val,
            "chat_id": c_id,
            "chat_title": c_title,
            "action": action,
            "action_name": meta["name"],
            "action_icon": meta["icon"],
            "action_badge": meta["badge"],
            "admin_id": a_id,
            "admin_name": a_name,
            "admin_uname": a_uname,
            "target_id": t_id,
            "target_name": t_name,
            "target_uname": t_uname,
            "details": details,
        })

    # Adminləri ən aktivdən başlayaraq sırala
    sorted_admins = sorted(admin_stats.values(), key=lambda x: x["total"], reverse=True)

    # HTML Şablonu
    rows_html = []
    for item in formatted_events:
        a_link = _uname_link(item["admin_uname"], "uname")
        t_link = _uname_link(item["target_uname"], "uname")

        admin_badge = f"""
        <div class="user-cell">
            <span class="user-name">{html.escape(item['admin_name'])}</span>
            {a_link}
            <span class="user-id">ID: {item['admin_id'] if item['admin_id'] else '—'}</span>
        </div>
        """

        target_badge = f"""
        <div class="user-cell">
            <span class="user-name">{html.escape(item['target_name'])}</span>
            {t_link}
            <span class="user-id">ID: {item['target_id'] if item['target_id'] else '—'}</span>
        </div>
        """ if item["target_name"] != "—" else "<span class='empty-cell'>—</span>"

        det_html = html.escape(item["details"]) if item["details"] else "<span class='empty-cell'>—</span>"

        rows_html.append(f"""
        <tr data-action="{html.escape(item['action'])}" data-admin="{html.escape(str(item['admin_id']))}" data-chat="{html.escape(str(item['chat_id']))}">
            <td class="col-num">{item['num']}</td>
            <td class="col-time"><code>{item['time']}</code></td>
            <td class="col-chat">
                <div class="chat-title">{html.escape(item['chat_title'])}</div>
                <div class="chat-id"><code>{item['chat_id']}</code></div>
            </td>
            <td class="col-action">
                <span class="action-badge" style="background-color: {item['action_badge']}22; color: {item['action_badge']}; border-color: {item['action_badge']}55;">
                    <span class="icon">{item['action_icon']}</span>
                    <span>{html.escape(item['action_name'])}</span>
                </span>
            </td>
            <td class="col-admin">{admin_badge}</td>
            <td class="col-target">{target_badge}</td>
            <td class="col-details">{det_html}</td>
        </tr>
        """)

    admin_rows_html = []
    for rank, adm in enumerate(sorted_admins[:15], 1):
        uname_s = _uname_link(adm["uname"]) or "—"
        admin_rows_html.append(f"""
        <tr>
            <td class="col-num"><b>#{rank}</b></td>
            <td><b>{html.escape(adm['name'])}</b></td>
            <td>{uname_s}</td>
            <td><code>{adm['id']}</code></td>
            <td class="text-right font-bold text-accent">{adm['total']}</td>
            <td class="text-right text-red">{adm['ban']}</td>
            <td class="text-right text-yellow">{adm['mute']}</td>
            <td class="text-right text-orange">{adm['kick']}</td>
            <td class="text-right text-blue">{adm['promo']}</td>
            <td class="text-right text-gray">{adm['other']}</td>
        </tr>
        """)

    # Qrup seçimləri filter üçün
    chat_options_html = "\n".join([f'<option value="{c[0]}">{html.escape(c[1])} ({c[0]})</option>' for c in sorted(chats_seen, key=lambda x: x[1])])
    admin_options_html = "\n".join([f'<option value="{a["id"]}">{html.escape(a["name"])} (ID: {a["id"]})</option>' for a in sorted_admins if a["id"]])

    full_html = f"""<!DOCTYPE html>
<html lang="az">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🛡 Admin Audit Hesabatı — {date_str}</title>
    <style>
        :root {{
            --bg: #090d16;
            --surface: #111827;
            --surface-hover: #1f2937;
            --border: #1e293b;
            --text: #f1f5f9;
            --text-muted: #94a3b8;
            --accent: #38bdf8;
            --accent-glow: rgba(56, 189, 248, 0.15);
            --danger: #ef4444;
            --warning: #f59e0b;
            --success: #10b981;
            --font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
        }}
        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}
        body {{
            background-color: var(--bg);
            color: var(--text);
            font-family: var(--font-family);
            line-height: 1.5;
            padding: 24px;
            font-size: 14px;
        }}
        .container {{
            max-width: 1440px;
            margin: 0 auto;
        }}
        header {{
            background: linear-gradient(135deg, #111827 0%, #1e293b 100%);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 24px 32px;
            margin-bottom: 24px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 16px;
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5);
        }}
        .brand-title {{
            display: flex;
            align-items: center;
            gap: 12px;
        }}
        .brand-icon {{
            font-size: 32px;
            background: rgba(56, 189, 248, 0.1);
            padding: 10px;
            border-radius: 12px;
            border: 1px solid rgba(56, 189, 248, 0.2);
        }}
        h1 {{
            font-size: 24px;
            font-weight: 700;
            color: #fff;
            letter-spacing: -0.5px;
        }}
        .subtitle {{
            color: var(--text-muted);
            font-size: 13px;
            margin-top: 2px;
        }}
        .header-meta {{
            display: flex;
            gap: 12px;
            align-items: center;
        }}
        .badge {{
            padding: 6px 14px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 600;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            border: 1px solid transparent;
        }}
        .badge-primary {{
            background: rgba(56, 189, 248, 0.15);
            color: #38bdf8;
            border-color: rgba(56, 189, 248, 0.3);
        }}
        .badge-date {{
            background: rgba(16, 185, 129, 0.15);
            color: #10b981;
            border-color: rgba(16, 185, 129, 0.3);
        }}
        .btn {{
            background: #1e293b;
            color: #fff;
            border: 1px solid #334155;
            padding: 8px 16px;
            border-radius: 10px;
            cursor: pointer;
            font-weight: 600;
            font-size: 13px;
            transition: all 0.2s;
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }}
        .btn:hover {{
            background: #334155;
            border-color: var(--accent);
        }}

        /* KPI Grid */
        .grid-stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
            gap: 14px;
            margin-bottom: 24px;
        }}
        .stat-card {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 14px;
            padding: 16px;
            transition: transform 0.2s, border-color 0.2s;
        }}
        .stat-card:hover {{
            transform: translateY(-2px);
            border-color: rgba(56, 189, 248, 0.4);
        }}
        .stat-header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            color: var(--text-muted);
            font-size: 12px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .stat-value {{
            font-size: 26px;
            font-weight: 700;
            margin-top: 6px;
            color: #fff;
        }}

        /* Leaderboard */
        .card {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 20px;
            margin-bottom: 24px;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3);
        }}
        .card-title {{
            font-size: 16px;
            font-weight: 700;
            margin-bottom: 16px;
            display: flex;
            align-items: center;
            gap: 8px;
        }}

        /* Table */
        .table-responsive {{
            overflow-x: auto;
        }}
        table {{
            width: 100%;
            border-collapse: separate;
            border-spacing: 0;
            text-align: left;
        }}
        th {{
            background: #1e293b;
            color: var(--text-muted);
            font-weight: 600;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            padding: 12px 14px;
            border-bottom: 2px solid var(--border);
        }}
        th:first-child {{ border-top-left-radius: 10px; border-bottom-left-radius: 10px; }}
        th:last-child {{ border-top-right-radius: 10px; border-bottom-right-radius: 10px; }}
        td {{
            padding: 12px 14px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
            vertical-align: middle;
        }}
        tr:hover td {{
            background: rgba(255, 255, 255, 0.02);
        }}

        /* Filter Controls */
        .filters-bar {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 14px;
            padding: 16px;
            margin-bottom: 16px;
            display: flex;
            flex-wrap: wrap;
            gap: 12px;
            align-items: center;
        }}
        .search-box {{
            flex: 1 1 240px;
            background: #090d16;
            border: 1px solid var(--border);
            color: #fff;
            padding: 10px 16px;
            border-radius: 10px;
            font-size: 13px;
            outline: none;
            transition: border-color 0.2s;
        }}
        .search-box:focus {{
            border-color: var(--accent);
        }}
        .select-filter {{
            background: #090d16;
            border: 1px solid var(--border);
            color: #fff;
            padding: 10px 14px;
            border-radius: 10px;
            font-size: 13px;
            outline: none;
            cursor: pointer;
        }}
        .filter-counter {{
            margin-left: auto;
            color: var(--text-muted);
            font-size: 13px;
            font-weight: 500;
        }}

        /* Elements */
        .action-badge {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 4px 10px;
            border-radius: 8px;
            font-weight: 600;
            font-size: 12px;
            border: 1px solid;
            white-space: nowrap;
        }}
        .user-cell {{
            display: flex;
            flex-direction: column;
            gap: 2px;
        }}
        .user-name {{
            font-weight: 600;
            color: #fff;
        }}
        .uname {{
            color: var(--accent);
            text-decoration: none;
            font-size: 12px;
        }}
        .uname:hover {{
            text-decoration: underline;
        }}
        .user-id {{
            color: var(--text-muted);
            font-size: 11px;
            font-family: monospace;
        }}
        .chat-title {{
            font-weight: 600;
            color: #e2e8f0;
        }}
        .chat-id code, .col-time code {{
            color: #94a3b8;
            font-size: 12px;
        }}
        .empty-cell {{
            color: #475569;
        }}
        .text-right {{ text-align: right; }}
        .font-bold {{ font-weight: 700; }}
        .text-accent {{ color: var(--accent); }}
        .text-red {{ color: var(--danger); }}
        .text-yellow {{ color: var(--warning); }}
        .text-orange {{ color: #f97316; }}
        .text-blue {{ color: #3b82f6; }}
        .text-gray {{ color: #94a3b8; }}

        footer {{
            text-align: center;
            padding: 30px;
            color: #475569;
            font-size: 13px;
        }}

        @media (max-width: 768px) {{
            body {{ padding: 12px; }}
            header {{ padding: 16px; flex-direction: column; align-items: flex-start; }}
            .filters-bar {{ flex-direction: column; align-items: stretch; }}
            .filter-counter {{ margin-left: 0; text-align: right; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <!-- Header -->
        <header>
            <div class="brand-title">
                <div class="brand-icon">🛡</div>
                <div>
                    <h1>Qrup Moderasiya & Admin Audit Hesabatı</h1>
                    <div class="subtitle">Bütün admin komandaları, icraçıları və hərəkətlərin tam jurnalı</div>
                </div>
            </div>
            <div class="header-meta">
                <span class="badge badge-date">📅 {date_str}</span>
                <span class="badge badge-primary">⚡ {total_events} Hadisə</span>
                <button class="btn" onclick="window.print()">🖨 Çap / PDF</button>
                <button class="btn" onclick="exportJSON()">💾 JSON</button>
            </div>
        </header>

        <!-- KPI Grid -->
        <div class="grid-stats">
            <div class="stat-card">
                <div class="stat-header"><span>Cəmi Əməl</span><span>📊</span></div>
                <div class="stat-value text-accent">{total_events}</div>
            </div>
            <div class="stat-card">
                <div class="stat-header"><span>Banlar</span><span>🚫</span></div>
                <div class="stat-value text-red">{counts['ban']}</div>
            </div>
            <div class="stat-card">
                <div class="stat-header"><span>Mute-lər</span><span>🔇</span></div>
                <div class="stat-value text-yellow">{counts['mute']}</div>
            </div>
            <div class="stat-card">
                <div class="stat-header"><span>Qovulmalar</span><span>👢</span></div>
                <div class="stat-value text-orange">{counts['kick']}</div>
            </div>
            <div class="stat-card">
                <div class="stat-header"><span>Admin Təyinatı</span><span>👮</span></div>
                <div class="stat-value text-blue">{counts['promote']}</div>
            </div>
            <div class="stat-card">
                <div class="stat-header"><span>Cəza Ləğvləri</span><span>🔓</span></div>
                <div class="stat-value text-gray">{counts['unban']}</div>
            </div>
            <div class="stat-card">
                <div class="stat-header"><span>Aktiv Admin</span><span>👥</span></div>
                <div class="stat-value">{len(admin_stats)}</div>
            </div>
            <div class="stat-card">
                <div class="stat-header"><span>Qruplar</span><span>🏢</span></div>
                <div class="stat-value">{len(chats_seen)}</div>
            </div>
        </div>

        <!-- Leaderboard: Ən aktiv adminlər -->
        {"<div class='card'><div class='card-title'>🏆 Ən Aktiv İcraçı Adminlər (Top)</div><div class='table-responsive'><table><thead><tr><th>#</th><th>Admin Adı</th><th>İstifadəçi Adı</th><th>ID</th><th class='text-right'>Cəmi</th><th class='text-right'>Ban</th><th class='text-right'>Mute</th><th class='text-right'>Kick</th><th class='text-right'>Promo</th><th class='text-right'>Digər</th></tr></thead><tbody>" + "".join(admin_rows_html) + "</tbody></table></div></div>" if admin_rows_html else ""}

        <!-- Filter Bar -->
        <div class="filters-bar">
            <input type="text" id="searchInput" class="search-box" placeholder="🔍 Canlı axtarış (Admin, istifadəçi, qrup, səbəb, ID)..." oninput="applyFilters()">
            
            <select id="actionFilter" class="select-filter" onchange="applyFilters()">
                <option value="">🎯 Bütün Əməllər (Hamısı)</option>
                <option value="ban">🚫 Banlar</option>
                <option value="mute">🔇 Mute-lər</option>
                <option value="kick">👢 Kick / Qovulma</option>
                <option value="promote">👮 Admin Təyinatı</option>
                <option value="demote">👤 Adminlikdən Çıxarılma</option>
                <option value="unban">🔓 Ban Ləğvi</option>
                <option value="unmute">🔊 Mute Ləğvi</option>
                <option value="pin">📌 Mesaj Bərkidilməsi</option>
                <option value="purge">🧹 Mesaj Silinməsi</option>
                <option value="title">🏷 Ad Dəyişikliyi</option>
                <option value="photo">🖼 Şəkil Dəyişikliyi</option>
            </select>

            <select id="adminFilter" class="select-filter" onchange="applyFilters()">
                <option value="">👤 Bütün Adminlər</option>
                {admin_options_html}
            </select>

            <select id="chatFilter" class="select-filter" onchange="applyFilters()">
                <option value="">🏢 Bütün Qruplar</option>
                {chat_options_html}
            </select>

            <button class="btn" onclick="resetFilters()">🔄 Sıfırla</button>

            <div id="filterCounter" class="filter-counter">
                Göstərilir: <b>{total_events}</b> / {total_events}
            </div>
        </div>

        <!-- Ətraflı Xronoloji Jurnal Cədvəli -->
        <div class="card">
            <div class="card-title">📜 Xronoloji Audit Jurnalı</div>
            <div class="table-responsive">
                <table id="auditTable">
                    <thead>
                        <tr>
                            <th style="width: 50px;">#</th>
                            <th style="width: 90px;">Vaxt</th>
                            <th>Qrup</th>
                            <th>Əməl / Hadisə</th>
                            <th>İcra Edən Admin</th>
                            <th>Hədəf Şəxs</th>
                            <th>Təfərrüat / Parametrlər</th>
                        </tr>
                    </thead>
                    <tbody id="tableBody">
                        {"".join(rows_html) if rows_html else "<tr><td colspan='7' style='text-align: center; padding: 40px; color: #64748b;'>Bu gün üçün heç bir qeydə alınmış admin əməliyyatı tapılmadı.</td></tr>"}
                    </tbody>
                </table>
            </div>
        </div>

        <footer>
            DLLMaster Bot & Userbot • Tərtib olunma: {gen_time_str} • Bütün hüquqlar qorunur
        </footer>
    </div>

    <script>
        const auditData = {script_json(formatted_events)};

        function applyFilters() {{
            const searchVal = document.getElementById('searchInput').value.toLowerCase().trim();
            const actionVal = document.getElementById('actionFilter').value.toLowerCase();
            const adminVal = document.getElementById('adminFilter').value;
            const chatVal = document.getElementById('chatFilter').value;

            const rows = document.querySelectorAll('#tableBody tr');
            let visibleCount = 0;

            rows.forEach(row => {{
                if (row.cells.length < 5) return;
                const rAction = row.getAttribute('data-action') || '';
                const rAdmin = row.getAttribute('data-admin') || '';
                const rChat = row.getAttribute('data-chat') || '';
                const rowText = row.innerText.toLowerCase();

                let match = true;
                if (actionVal && rAction !== actionVal) match = false;
                if (adminVal && rAdmin !== adminVal) match = false;
                if (chatVal && rChat !== chatVal) match = false;
                if (searchVal && !rowText.includes(searchVal)) match = false;

                if (match) {{
                    row.style.display = '';
                    visibleCount++;
                }} else {{
                    row.style.display = 'none';
                }}
            }});

            document.getElementById('filterCounter').innerHTML = `Göstərilir: <b>${{visibleCount}}</b> / ${{rows.length}}`;
        }}

        function resetFilters() {{
            document.getElementById('searchInput').value = '';
            document.getElementById('actionFilter').value = '';
            document.getElementById('adminFilter').value = '';
            document.getElementById('chatFilter').value = '';
            applyFilters();
        }}

        function exportJSON() {{
            const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(auditData, null, 2));
            const dlAnchor = document.createElement('a');
            dlAnchor.setAttribute("href", dataStr);
            dlAnchor.setAttribute("download", `audit_${date_str}.json`);
            dlAnchor.click();
        }}
    </script>
</body>
</html>
"""
    return full_html


async def send_daily_audit_report(context=None, date_str: str = None, force: bool = False,
                                  target_chat: int | str = None, chat_id: int | None = None,
                                  chat_title: str | None = None,
                                  thread_id: int | None = None) -> tuple[str | None, int, str]:
    """Audit hesabatını generasiya edir və şəxsi çata (creator və ya target_chat) HTML faylı kimi göndərir.

    chat_id verilərsə hesabat YALNIZ həmin qrupun hadisələrini əhatə edir (qrup admininin /audit-i üçün).
    """
    import asyncio
    from common.database import get_db

    start_ts, end_ts, d_str = get_today_bounds(date_str)
    db = get_db()

    events = await asyncio.to_thread(db.get_admin_audit_logs, chat_id=chat_id, start_ts=start_ts,
                                     end_ts=end_ts, limit=5000)
    total = len(events)

    if total == 0 and not force:
        logger.info(f"ℹ️ {d_str} tarixi üçün audit hadisəsi yoxdur — hesabat göndərilmədi.")
        return None, 0, "no_events"

    # HTML məzmununu yarat
    html_content = await asyncio.to_thread(generate_html_report, events, d_str)
    os.makedirs(AUDIT_REPORTS_DIR, exist_ok=True)
    suffix = f"_{abs(int(chat_id))}" if chat_id else ""
    file_path = os.path.join(AUDIT_REPORTS_DIR, f"audit_report_{d_str}{suffix}.html")

    def _write():
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(html_content)
    await asyncio.to_thread(_write)

    logger.info(f"📄 HTML hesabat saxlandı: {file_path} ({total} əməl)")

    # Hədəf: target_chat (şəxsi çat, qrup, kanal, @ad) + istəyə görə forum mövzusu; yoxdursa bot sahibi
    creator_id = getattr(context, "creator_id", None) or os.getenv("CREATOR_ID")
    creator_id = int(creator_id) if creator_id and str(creator_id).isdigit() else creator_id
    s_tc = str(target_chat or "").strip()
    if re.fullmatch(r"[+-]?\d+", s_tc):
        dest_chat = int(s_tc.lstrip("+"))
    elif s_tc:
        dest_chat = s_tc
    else:
        dest_chat = creator_id
    if not dest_chat:
        logger.warning("⚠️ Audit hesabatı göndərmək üçün hədəf tapılmadı!")
        return file_path, total, "no_target"

    # Statistik xülasə başlıq mətni
    stats = await asyncio.to_thread(db.get_audit_summary_stats, start_ts=start_ts, end_ts=end_ts, chat_id=chat_id)
    caption = (
        f"🛡 <b>Günlük Admin Audit Hesabatı</b> — <code>{d_str}</code>\n"
        + (f"🏘 <b>{html.escape(chat_title or str(chat_id))}</b>\n" if chat_id else "") + "\n"
        f"📊 <b>Günün Statistikası:</b>\n"
        f"• Cəmi əməliyyat: <b>{stats.get('total', total)}</b>\n"
        f"• 🚫 Ban: <b>{stats.get('bans', 0)}</b> · 🔇 Mute: <b>{stats.get('mutes', 0)}</b> · 👢 Kick: <b>{stats.get('kicks', 0)}</b>\n"
        f"• 👮 Admin təyinatı: <b>{stats.get('promotes', 0)}</b> · 🔓 Unban: <b>{stats.get('unbans', 0)}</b>\n"
        f"• 👥 Aktiv adminlər: <b>{stats.get('admins_count', 0)}</b>\n"
        f"• 🏢 İzlənən qruplar: <b>{stats.get('chats_count', 0)}</b>\n\n"
        f"📎 <i>Bütün admin komandaları, icraçıları və hədəfləri əlavə edilmiş HTML faylında ətraflı qeyd olunub.</i>"
    )

    # Göndərmə: Aiogram Bot və ya Telethon Client vasitəsilə
    sent = False
    bot = getattr(context, "bot", None) if context else None
    if bot:
        try:
            from aiogram.types import FSInputFile
            input_file = FSInputFile(file_path, filename=f"audit_{d_str}.html")
            kw = {"message_thread_id": int(thread_id)} if thread_id else {}
            await bot.send_document(dest_chat, input_file, caption=caption, parse_mode="HTML", **kw)
            sent = True
            logger.info(f"✅ Audit hesabatı Bot vasitəsilə göndərildi → {dest_chat}")
        except Exception as e:
            logger.warning(f"Bot ilə hesabat göndərilmədi ({dest_chat}): {e} — Userbot yoxlanılır...")

    if not sent:
        client = getattr(context, "telethon_client", None) if context else None
        if client is None and context is not None:                  # bot-only rejim: asistan hesabı ilə
            for sl in getattr(getattr(context, "voice_assistant", None), "slots", []):
                if getattr(sl, "running", False) and getattr(sl, "client", None) is not None:
                    client = sl.client
                    break
        if client:
            try:
                await client.send_file(dest_chat, file_path, caption=caption, parse_mode="html",
                                       **({"reply_to": int(thread_id)} if thread_id else {}))
                sent = True
                logger.info(f"✅ Audit hesabatı Userbot vasitəsilə göndərildi → {dest_chat}")
            except Exception as e:
                logger.error(f"❌ Userbot ilə hesabat göndərilmədi: {e}")

    if sent and not chat_id:
        db.set_setting("audit:last_daily_report_date", d_str)

    return file_path, total, str(dest_chat)


_daily_report_override: bool | None = None


def is_daily_report_enabled() -> bool:
    """Gündəlik 23:59 avtomatik HTML hesabatının aktiv olub-olmaması."""
    global _daily_report_override
    if _daily_report_override is not None:
        return _daily_report_override
    env_val = os.getenv("AUDIT_DAILY_REPORT")
    if env_val is not None:
        return env_val.lower() not in ("0", "false", "no", "off")
    try:
        from common.database import get_db
        val = get_db().get_setting("audit:daily_report_enabled")
        if val is not None:
            return val not in ("0", "false", "no", "off")
    except Exception as _e:
        logger.debug("udulan xəta [common/audit.py:959]: %r", _e)
    return True


def set_daily_report_enabled(enabled: bool) -> None:
    """Gündəlik 23:59 avtomatik HTML hesabatını açır və ya bağlayır."""
    global _daily_report_override
    _daily_report_override = bool(enabled)
    try:
        from common.database import get_db
        get_db().set_setting("audit:daily_report_enabled", "1" if enabled else "0")
    except Exception as e:
        logger.debug(f"set_daily_report_enabled DB yazma xətası: {e}")


_SCHEDULER_RUNNING = False


def _daily_groups() -> dict:
    """Hesabatı açıq qruplar ({chat_id: ad}) — qrup ayarları userbot/plugins/logger.py-dadır."""
    try:
        from types import SimpleNamespace

        from userbot.core.userbot_db import get_udb
        from userbot.plugins import logger as lg
        return lg.daily_groups(SimpleNamespace(db=get_udb()))
    except Exception as e:
        logger.debug(f"Hesabat qrupları oxunmadı: {e}")
        return {}


def group_report_dest(cid: int):
    """(chat, thread) — qrupun hesabat hədəfi: qrupun 📄 hədəfi → qrupun 🎯 «hamısı» hədəfi → 📍 Hədəflər «audit» →
    (None, None) = bot sahibinin şəxsi çatı."""
    try:
        from types import SimpleNamespace

        from userbot.core.userbot_db import get_udb
        from userbot.plugins import logger as lg
        ub = SimpleNamespace(db=get_udb())
        if lg.has_group_target(ub, cid, "report") or lg.has_group_target(ub, cid, "au"):    # yalnız QRUP səviyyəsi
            tc, tt, _ = lg.get_stream_target(ub, "report", cid)
            if tc:
                return tc, (int(tt) if tt and str(tt).isdigit() else None)
    except Exception as e:
        logger.debug(f"Qrup hesabat hədəfi oxunmadı ({cid}): {e}")
    try:
        from common import destinations
        d = destinations.get("audit")
        if d:
            return d["chat_id"], d.get("thread_id")
    except Exception as e:
        logger.debug(f"audit hədəfi oxunmadı: {e}")
    return None, None


async def _send_group_reports(context, date_str: str, catch_up: bool = False) -> int:
    """Hər qrup üçün ayrıca HTML hesabat göndərir (bir gündə bir dəfə / qrup). Hadisəsi olmayan qrup üçün göndərilmir."""
    import asyncio
    from common.database import get_db

    db = get_db()
    sent = 0
    for cid, title in _daily_groups().items():
        key = f"audit:last_daily:{cid}"
        try:
            if await asyncio.to_thread(db.get_setting, key) == date_str:
                continue
            if catch_up:                       # dünənki: yalnız hadisə varsa
                s_ts, e_ts, _ = get_today_bounds(date_str)
                if not await asyncio.to_thread(db.get_admin_audit_logs, chat_id=cid, start_ts=s_ts, end_ts=e_ts, limit=1):
                    continue
            tc, tt = group_report_dest(cid)
            fp, total, dest = await send_daily_audit_report(context, date_str=date_str, force=False,
                                                            chat_id=cid, chat_title=title,
                                                            target_chat=tc, thread_id=tt)
            if fp and dest not in ("no_target", "no_events"):
                await asyncio.to_thread(db.set_setting, key, date_str)
                sent += 1
        except Exception as e:
            logger.warning(f"Qrup hesabatı göndərilmədi ({cid}): {e}")
    return sent


async def start_audit_scheduler(context):
    """Gün sonunda (23:59 AZT) avtomatik hesabat hazırlayıb göndərən fon xidməti."""
    global _SCHEDULER_RUNNING
    if _SCHEDULER_RUNNING:
        return
    _SCHEDULER_RUNNING = True

    logger.info("⏰ Audit gün sonu scheduler işə düşdü (hər gün 23:59 AZT)")

    # Açılışda: dünənki hesabat göndərilməyibsə — hər qrup üçün (hesabatı açıq qruplar) ayrıca çatdır
    try:
        now_dt = get_azt_now()
        yesterday_str = (now_dt - timedelta(days=1)).strftime("%Y-%m-%d")
        if is_daily_report_enabled():
            await _send_group_reports(context, yesterday_str, catch_up=True)
    except Exception as e:
        logger.debug(f"Scheduler startup yoxlama xətası: {e}")

    # Hər gün 23:59 AZT — sadə dövrə (əvvəl bunun üçün ayrıca common/scheduler.py modulu var idi)
    import asyncio
    from common.database import get_db
    while True:
        now = get_azt_now()
        target = now.replace(hour=23, minute=59, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        today_str = get_azt_now().strftime("%Y-%m-%d")
        try:
            # 1. Həmişə köhnə audit loqlarını təmizlə (baza baxımı)
            pruned = await asyncio.to_thread(get_db().cleanup_old_audit_logs, 90)
            if pruned > 0:
                logger.info(f"🧹 Köhnə audit loqları təmizləndi: {pruned} qeyd")

            # 2. Hər qrup üçün öz hesabatı (qrup ayarlarında 📅 açıq olanlar)
            if is_daily_report_enabled():
                logger.info(f"🚀 Gün sonu: {today_str} üçün qrup hesabatları göndərilir...")
                await _send_group_reports(context, today_str)
            else:
                logger.debug(f"Gün sonu: {today_str} üçün avto audit hesabatı söndürülüb (skip).")
        except Exception as e:
            logger.error(f"⏰ Gündəlik audit hesabatı xətası: {e}", exc_info=True)
        await asyncio.sleep(90)                 # eyni dəqiqədə ikinci dəfə işləməsin
