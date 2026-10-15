cd "/run/media/user/Новый том/dllmaster_restructured" || exit 1
export VIRTUAL_ENV="/run/media/user/Новый том/dllmaster_restructured/.venv" PATH="/run/media/user/Новый том/dllmaster_restructured/.venv/bin:$PATH" PYTHONUNBUFFERED=1
python3 app.py 2>&1 | tee >(sed -u 's/\x1b\[[0-9;]*m//g' >> bot_logs/console.out)
echo; echo '⛔ Bot dayandı — yenidən başlatmaq: python3 app.py'
exec bash
