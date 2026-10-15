"""
🔑 Telethon avtorizasiyası (terminaldan) — userbot/login.py

    python -m userbot.login

Kökdə userbot.session faylını yaradır. Daha rahat yol: botda /menu → 📱 Userbotlar → 🔑 (bot daxilində giriş).
Girişdən sonra botu restart et ki, yeni auth key oxunsun.
"""
import contextlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)                                    # config.env və userbot.session kökdə

from dotenv import load_dotenv                    # noqa: E402
from telethon.sync import TelegramClient          # noqa: E402

def main():
    load_dotenv("config.env")

    api_id = os.getenv("TELETHON_API_ID")
    api_hash = os.getenv("TELETHON_API_HASH")
    if not api_id or not api_hash:
        sys.exit("❌ config.env-də TELETHON_API_ID / TELETHON_API_HASH yoxdur")

    print("📱 Telethon avtorizasiyası başlayır...")
    # Müstəqil rejimdə daxil olub userbot.session faylını yaradırıq
    # tg_session.py ilə eyni yol (TELETHON_SESSION_FILE) — Telethon ".session" uzantısını özü əlavə edir
    session_file = os.getenv("TELETHON_SESSION_FILE", "userbot.session")
    session_name = session_file[:-len(".session")] if session_file.endswith(".session") else session_file
    with TelegramClient(session_name, int(api_id), api_hash) as client:
        # Bu əmr terminalda avtomatik olaraq nömrə və kod istəyəcək
        client.start()
    with contextlib.suppress(OSError):
        os.chmod(session_name + ".session", 0o600)          # auth açarı — başqa istifadəçilər oxumasın
    print(f"✅ Uğurla daxil oldunuz! '{session_name}.session' faylı yaradıldı.")


if __name__ == "__main__":
    main()
