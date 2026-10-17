# 🚚 Botu VDS-ə köçürmək

Köçürülənlər: **kod** (commit edilməmiş dəyişikliklərlə), `config.env` (tokenlər), **Telegram sessiyaları** (userbot / asistan),
**PostgreSQL bazası** (≈1,1 GB; `audio_cache` = depo indeksi), **Google hesablarının Chrome profilləri** (`data/yt_profiles`, cookie-lər),
`data/userbot.db`, `depo_state.json`. Köçürülmür: `.venv` (yenidən qurulur), loglar, `data/antidelete_media` (~6 GB keş), `dlm_backup*`.
Depo mahnıları Telegram kanallarındadır, ona görə onlar köçmür — yalnız indeks (baza) köçür.

## 0. Əvvəl qərar verin (köçürmədən əvvəl yoxlayın)

| Mövzu | Niyə vacibdir |
|---|---|
| **CPU** | Evdə yük 17–31 idi (16 nüvə): hər YouTube yükləməsi `deno` prosesi açır (140–185% CPU). 2–4 nüvəli VDS-də sürət **xeyli aşağı** olacaq (≈20–40 mahnı/dəq.). Tövsiyə: **8+ nüvə, 16 GB RAM**. Az nüvə ilə 🧵 Worker-i 16–32-yə salın. |
| **Trafik** | Depoya yükləmə **çıxan** trafikdir: ≈100 mahnı/dəq. × ~3,4 MB ≈ 20 GB/saat ≈ 490 GB/gün ≈ 14 TB/ay. Limitli planlarda (1–10 TB) tez bitər, ya da pul tutular. «Limitsiz 1 Gbit» plan seçin və ya sürəti məhdudlaşdırın. |
| **YouTube və IP** | Datacenter IP-ləri YouTube tərəfindən sıx bloklanır (AWS-də hər client `Failed to extract any player response` verdi). Köçürülən 7 Chrome-profilli hesab kömək edir, amma **zəmanət yoxdur**. Əvvəlcə VDS-də 1 mahnı yükləyib yoxlayın. Alternativ: ev IP-sini saxlamaq (🌐 Uzaq server funksiyasının əksi) və ya rezident proxy (`/menu → 🖥 Sistem → 🌐 Proxy / Tor`). |
| **Disk** | ≥ 20 GB (baza 1,1 GB + dump 0,3 GB + kod + müvəqqəti yükləmələr). |
| **OS** | Ubuntu/Debian və ya Arch (`install.sh` ikisini də bilir). Python 3.13+ lazımdır; yoxdursa `restore_vds.sh` özü `uv` ilə 3.14 qurur. |
| **Ekran yoxdur** | Serverdə brauzer pəncərəsi açılmır: yeni Google hesabına girişi (`🖥 Server girişi`) VDS-də edə bilməzsiniz. Hesabları evdə əlavə edib `pack_home.sh` ilə köçürün (və ya cookies.txt göndərin). ⭐ Əsas hesab ev brauzerinin cookie-lərini oxuyurdu — VDS-də avtomatik **söndürülür** (`--vds`). |

## 1. Evdə: paket hazırla

```bash
# botu dayandırın (Ctrl+C və ya systemctl --user stop dllmaster)
cd "<layihə qovluğu>"
bash deploy/pack_home.sh --dry-run     # nə düşəcəyini göstərir
bash deploy/pack_home.sh               # ~/dlm_migrate_<tarix>.tar.gz (≈ 300–400 MB)
scp ~/dlm_migrate_*.tar.gz ISTIFADECI@VDS_IP:~/
```
Paketdə tokenlər, sessiyalar və Google cookie-ləri var: yalnız `scp`/SSH ilə göndərin, VDS-də açandan sonra silin.

## 2. VDS-də: quraşdır və bərpa et

```bash
# repo kodu paketin içindədir; ayrıca klon lazım deyil. restore_vds.sh-ı paketdən çıxarın:
tar -xzf dlm_migrate_*.tar.gz deploy/restore_vds.sh install.sh -C /tmp && cp /tmp/deploy/restore_vds.sh ~/
bash ~/restore_vds.sh ~/dlm_migrate_*.tar.gz --dry-run     # nə edəcəyini göstərir
bash ~/restore_vds.sh ~/dlm_migrate_*.tar.gz               # hədəf: ~/dllmaster
```
Skript: paketi açır → `install.sh --yes --service` (sistem paketləri, PostgreSQL, `.venv`, PO token konteyneri, systemd) →
bazanı yükləyir → bazadakı köhnə yolları düzəldir (`yt:accounts` içindəki Chrome profil yolları) → ⭐ Əsas hesabı və 🌐 Uzaq server-i söndürür.
Botu **işə salmır**.

## 3. Keçid (eyni anda yalnız BİR yerdə!)

1. **Evdəki botu dayandırın** və avtomatik başlamasın (systemd/autostart).
   Eyni bot tokeni iki yerdə işləsə `TelegramConflictError`; eyni Telethon sessiyası iki IP-də işləsə Telegram sessiyanı **ləğv edə bilər** (yenidən giriş lazım olar).
2. VDS-də: `systemctl --user start dllmaster && journalctl --user -u dllmaster -f`

## 4. İşə salandan sonra (yoxlama siyahısı)

- [ ] Logda `🧩 Userbot pluginləri yükləndi (17)` və `🩺 ✅ ...` sətirləri var, `ERROR` yoxdur.
- [ ] `/menu` açılır; `/depo` → «Depo: 7xxxxx mahnı» (baza düzgün yüklənib).
- [ ] `.alive` userbot-da cavab verir (sessiya işləyir).
- [ ] `/menu` → 🎵 YouTube → 🔑 hesablar: hesablar «🟢» (🧪 Test). Hamısı qırmızıdırsa — IP bloku; proxy və ya ev IP-si.
- [ ] 1 mahnı yükləyin (inline və ya link) — YouTube VDS-dən işləyirmi?
- [ ] `/depo` → 🧵 Worker: nüvə sayına görə seçin (nüvə × 4 başlanğıc üçün münasibdir).
- [ ] `systemctl --user enable dllmaster` + `loginctl enable-linger $USER` (VDS yenidən başlayanda bot qalxsın).
- [ ] Bir gün sonra evdəki `.venv`/kodu saxlayın (geri qayıtmaq üçün), amma botu orada **işə salmayın**.

## Geri qayıtmaq

VDS-də botu dayandırın, evdə `python init.py` ilə başladın. Evdəki baza dəyişməyib; VDS-də gedən yükləmələr isə yalnız VDS bazasındadır
(yəni geri qayıdanda ara dövrdə depoya düşənlər evdəki indeksdə olmaz — `/depo` → 🧹 Depo sinxronu ilə kanaldan bərpa olunur).

## Problemlər

| Əlamət | Səbəb / həll |
|---|---|
| `AuthKeyDuplicatedError` / sessiya ləğv | Evdəki bot hələ işləyirdi. Dayandırın, `/menu` → 📱 Userbotlar → yenidən giriş. |
| Hesablar «profil tapılmadı» | `python deploy/fix_paths.py --old "<köhnə yol>" --new "$PWD" --vds` (köhnə yol `MANIFEST.txt`-də). |
| `pg_restore`/`psql` xətaları | `restore_db.log`-a baxın; PG versiyası fərqi xəbərdarlıqları normaldır, `audio_cache` sətir sayı eyni olmalıdır. |
| YouTube hamısı 403 / `Sign in to confirm` | VDS IP-si bloklanıb: proxy (`/menu → 🖥 Sistem → 🌐 Proxy / Tor`) və ya ev IP-si. PO token konteyneri işləyirmi: `docker ps` (`bgutil-pot`, 4416). |
| Yükləmə çox yavaş | Nüvə sayı az: 🧵 Worker-i azaldın; `top`-da `deno` proseslərinə baxın. |
