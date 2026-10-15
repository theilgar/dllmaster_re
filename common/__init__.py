"""🧩 common — bot və userbot-un ikisinin də istifadə etdiyi ümumi nüvə.

  bootstrap  — başlanğıc asılılıq yoxlaması (yalnız standart kitabxana)
  logger     — rəngli konsol + fayl logu
  utilities  — plugin yükləyicisi, fayl adı, restart argv və s.
  database   — əsas PostgreSQL bazası (DATABASE_URL)
  updates    — git / pip / sistem paketlərinin yeniləmə yoxlayıcısı
  context    — AppContext: hər iki hissənin paylaşdığı vəziyyət
  compat     — köhnə `core.*` importları üçün uyğunluq qatı

Burada ağır import yoxdur — `python -m bot.core.ytdl_worker` kimi alt proseslər də rahat yüklənir.
"""
