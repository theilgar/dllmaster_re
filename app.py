#!/usr/bin/env python3
"""
Köhnə giriş nöqtəsi — indi init.py-a yönləndirir.

systemd / screen / tmux-da `python app.py` yazılıbsa dəyişmək lazım deyil, əvvəlki kimi
bot + userbot birlikdə başlayır. Yeni yazılış: `python init.py [all|bot|userbot]`.
"""
from init import main

if __name__ == "__main__":
    main()
