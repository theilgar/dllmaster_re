"""
🌐 Əlavə tərcümələr — common/i18n_extra.py

CATALOG-a (common/i18n.py) qoşulur: kodda _() / tr() ilə bükülmüş, amma əvvəl tərcümə olunmamış mətnlər
(səsli yayım, toplu yükləmə, VK linkləri, Shazam, audio redaktor, userbot paneli, «Adminə bildir», TikTok ...).
Açar — Azərbaycanca mətnin özü; yer tutucular ({n}, {title} ...) və HTML teqləri hər dildə eynidir.
lang/<kod>.json faylındakı eyni açar bu mətndən üstündür.
"""


def _t(en, ru, tr):
    return {"en": en, "ru": ru, "tr": tr}


EXTRA = {
    # ── səsli yayım / növbə ──
    "🎙 Canlı yayımda oxut": _t("🎙 Play in live stream", "🎙 Воспроизвести в эфире", "🎙 Canlı yayında çal"),
    "🎙 Canlı yayıma əlavə edilir...": _t("🎙 Adding to live stream...", "🎙 Добавление в эфир...", "🎙 Canlı yayına ekleniyor..."),
    "🔴 Canlı yayım asistanı söndürülüb və ya qoşulmayıb": _t(
        "🔴 Voice assistant is disabled or not connected",
        "🔴 Голосовой ассистент отключён или не подключён",
        "🔴 Canlı yayın asistanı devre dışı veya bağlı değil"),
    "🎙 <b>{name}</b>\n<i>Canlı yayıma əlavə edilir...</i>": _t(
        "🎙 <b>{name}</b>\n<i>Adding to live stream...</i>",
        "🎙 <b>{name}</b>\n<i>Добавление в эфир...</i>",
        "🎙 <b>{name}</b>\n<i>Canlı yayına ekleniyor...</i>"),
    "🎙 <b>{name}</b>\n✅ <b>{n}</b> mahnı canlı yayıma əlavə edildi!": _t(
        "🎙 <b>{name}</b>\n✅ <b>{n}</b> songs added to live stream!",
        "🎙 <b>{name}</b>\n✅ <b>{n}</b> песен добавлено в эфир!",
        "🎙 <b>{name}</b>\n✅ <b>{n}</b> şarkı canlı yayına eklendi!"),
    "❌ Siyahıda mahnı yoxdur": _t("❌ No songs in the list", "❌ В списке нет песен", "❌ Listede şarkı yok"),
    "<b>{n}</b> mahnı qalıb": _t("<b>{n}</b> songs left", "Осталось песен: <b>{n}</b>", "<b>{n}</b> şarkı kaldı"),
    "<i>... və daha {n} mahnı</i>": _t("<i>... and {n} more songs</i>", "<i>... и ещё {n} песен</i>", "<i>... ve {n} şarkı daha</i>"),
    "<i>Səsli yayım təkrar açılsın və növbə bərpa edilsin?</i>": _t(
        "<i>Reopen the voice stream and restore the queue?</i>",
        "<i>Открыть голосовой эфир снова и восстановить очередь?</i>",
        "<i>Sesli yayın yeniden açılsın ve sıra geri yüklensin?</i>"),
    "Artıq bitib və ya dayandırılıb": _t("Already finished or stopped", "Уже завершено или остановлено", "Zaten bitti veya durduruldu"),
    "Bu mahnı üçün sözlər tapılmadı.": _t("No lyrics found for this song.", "Для этой песни текст не найден.", "Bu şarkı için söz bulunamadı."),
    "Bu sorğunun vaxtı bitib və ya artıq cavablandırılıb.": _t(
        "This request has expired or was already answered.",
        "Срок этого запроса истёк или на него уже ответили.",
        "Bu isteğin süresi doldu veya zaten yanıtlandı."),
    "CPU Hovuzu": _t("CPU pool", "Пул CPU", "CPU havuzu"),
    "Canlı": _t("Live", "Онлайн", "Canlı"),
    "Dəyəri daxil edin": _t("Enter the value", "Введите значение", "Değeri girin"),
    "Gecikmə": _t("Delay", "Задержка", "Gecikme"),
    "Köhnəlib və ya artıq davam etdirilib": _t("Expired or already resumed", "Устарело или уже возобновлено", "Süresi doldu veya zaten devam ettirildi"),
    "Köhnəlib — yenidən başlat": _t("Expired — start again", "Устарело — начните заново", "Süresi doldu — yeniden başlat"),
    "Məlumat tapılmadı və ya vaxtı bitib": _t("Data not found or expired", "Данные не найдены или устарели", "Veri bulunamadı veya süresi doldu"),
    "Orta sürət:": _t("Average speed:", "Средняя скорость:", "Ortalama hız:"),
    "Performans": _t("Performance", "Производительность", "Performans"),
    "Rəqəm": _t("Number", "Число", "Sayı"),
    "Status": _t("Status", "Статус", "Durum"),
    "Səsli yayım dayandırıldı": _t("Voice stream stopped", "Голосовой эфир остановлен", "Sesli yayın durduruldu"),
    "VK divar postunda audio və ya video tapılmadı": _t(
        "No audio or video found in the VK wall post", "В посте на стене VK нет аудио или видео",
        "VK duvar gönderisinde ses veya video bulunamadı"),
    "VK ifaçısının mahnıları tapılmadı": _t("No songs found for this VK artist", "Песни исполнителя VK не найдены", "Bu VK sanatçısının şarkıları bulunamadı"),
    "VK mahnısı tapılmadı": _t("VK song not found", "Песня VK не найдена", "VK şarkısı bulunamadı"),
    "VK playlistində mahnı tapılmadı": _t("No songs found in the VK playlist", "В плейлисте VK песен не найдено", "VK çalma listesinde şarkı bulunamadı"),
    "Watchdog": _t("Watchdog", "Watchdog", "Watchdog"),
    "Yayım bərpa olunur...": _t("Restoring the stream...", "Эфир восстанавливается...", "Yayın geri yükleniyor..."),
    "dayandırıldı": _t("stopped", "остановлено", "durduruldu"),
    "depodan": _t("from storage", "из хранилища", "depodan"),
    "həmişəlik": _t("forever", "навсегда", "sonsuza kadar"),
    "maksimum": _t("maximum", "максимум", "maksimum"),
    "minimum": _t("minimum", "минимум", "minimum"),
    "minimum / söndür": _t("minimum / off", "минимум / выключить", "minimum / kapat"),
    "tam interaktiv panel üçün bot çatında /menu yazın": _t(
        "send /menu in the bot chat for the full interactive panel",
        "для полной интерактивной панели напишите /menu в чате с ботом",
        "tam etkileşimli panel için bot sohbetinde /menu yazın"),
    "və daha {n}": _t("and {n} more", "и ещё {n}", "ve {n} tane daha"),
    "yüklənir": _t("downloading", "загружается", "indiriliyor"),
    "ℹ️ Mahnı artıq növbədə deyil": _t("ℹ️ The song is no longer in the queue", "ℹ️ Песни уже нет в очереди", "ℹ️ Şarkı artık sırada değil"),
    "↩️ Geri": _t("↩️ Back", "↩️ Назад", "↩️ Geri"),
    "⌛ Vaxt: {t}": _t("⌛ Time: {t}", "⌛ Время: {t}", "⌛ Süre: {t}"),
    "⏭ <b>Mahnı çox uzundur</b>\n{dur} (maksimum {max_dur})": _t(
        "⏭ <b>The song is too long</b>\n{dur} (maximum {max_dur})",
        "⏭ <b>Песня слишком длинная</b>\n{dur} (максимум {max_dur})",
        "⏭ <b>Şarkı çok uzun</b>\n{dur} (en fazla {max_dur})"),
    "⏱ Gözləmə intervalı": _t("⏱ Wait interval", "⏱ Интервал ожидания", "⏱ Bekleme aralığı"),
    "⏱ Musiqi: <b>{d}</b>": _t("⏱ Music: <b>{d}</b>", "⏱ Музыка: <b>{d}</b>", "⏱ Müzik: <b>{d}</b>"),
    "⏳ <b>Bot restart üçün gözləmədədir.</b>\nCari mahnılar və yükləmələr bitdikdən sonra bot yenidən başlayacaq (1-2 dəqiqə).\nZəhmət olmasa bir az sonra yenidən cəhd edin.": _t(
        "⏳ <b>The bot is waiting to restart.</b>\nIt will restart once current songs and downloads finish (1-2 minutes).\nPlease try again a bit later.",
        "⏳ <b>Бот ожидает перезапуска.</b>\nОн перезапустится, когда закончатся текущие песни и загрузки (1-2 минуты).\nПопробуйте чуть позже.",
        "⏳ <b>Bot yeniden başlatılmayı bekliyor.</b>\nMevcut şarkılar ve indirmeler bitince yeniden başlayacak (1-2 dakika).\nLütfen biraz sonra tekrar deneyin."),
    "⏳ <b>Səsli yayım bərpa olunur...</b>": _t(
        "⏳ <b>Restoring the voice stream...</b>", "⏳ <b>Голосовой эфир восстанавливается...</b>",
        "⏳ <b>Sesli yayın geri yükleniyor...</b>"),
    "⏳ <i>İşlənir...</i>": _t("⏳ <i>Processing...</i>", "⏳ <i>Обработка...</i>", "⏳ <i>İşleniyor...</i>"),
    "⏳ Bot restart üçün gözləmədədir. Zəhmət olmasa bir neçə dəqiqə sonra cəhd edin.": _t(
        "⏳ The bot is waiting to restart. Please try again in a few minutes.",
        "⏳ Бот ожидает перезапуска. Попробуйте через несколько минут.",
        "⏳ Bot yeniden başlatılmayı bekliyor. Lütfen birkaç dakika sonra deneyin."),
    "⏳ Növbəti mahnı hazırlanır...": _t("⏳ Preparing the next song...", "⏳ Готовится следующая песня...", "⏳ Sıradaki şarkı hazırlanıyor..."),
    "⏳ Telegram flood — {n} san. gözləyir": _t(
        "⏳ Telegram flood — waiting {n} s", "⏳ Лимит Telegram — ожидание {n} с", "⏳ Telegram flood — {n} sn bekleniyor"),
    "⏸ Yüklənməmiş qaldı: {n}": _t("⏸ Not downloaded: {n}", "⏸ Не загружено: {n}", "⏸ İndirilmeden kaldı: {n}"),
    "⏹ Dayandırıldı": _t("⏹ Stopped", "⏹ Остановлено", "⏹ Durduruldu"),
    "▶️ Bu mahnı artıq oxunur — keçmək üçün /skip yazın": _t(
        "▶️ This song is already playing — type /skip to skip it",
        "▶️ Эта песня уже играет — напишите /skip, чтобы пропустить",
        "▶️ Bu şarkı zaten çalıyor — geçmek için /skip yazın"),
    "▶️ Davam etdir ({n})": _t("▶️ Continue ({n})", "▶️ Продолжить ({n})", "▶️ Devam et ({n})"),
    "▶️ Qalanları yüklə ({n})": _t("▶️ Download the rest ({n})", "▶️ Загрузить остальные ({n})", "▶️ Kalanları indir ({n})"),
    "▶️ Yayımı bərpa et": _t("▶️ Restore the stream", "▶️ Восстановить эфир", "▶️ Yayını geri yükle"),
    "▶️ {n} mahnı yüklənir...": _t("▶️ Downloading {n} songs...", "▶️ Загружается песен: {n}...", "▶️ {n} şarkı indiriliyor..."),
    "♻️ <b>Yarımçıq qalmış yükləmə</b> — bot yenidən başladı": _t(
        "♻️ <b>Interrupted download</b> — the bot restarted",
        "♻️ <b>Прерванная загрузка</b> — бот перезапустился",
        "♻️ <b>Yarım kalan indirme</b> — bot yeniden başladı"),
    "⚠️ <b>Bitdi, amma bəziləri alınmadı</b>": _t(
        "⚠️ <b>Finished, but some failed</b>", "⚠️ <b>Завершено, но часть не удалась</b>",
        "⚠️ <b>Bitti, ama bazıları alınamadı</b>"),
    "⚠️ <b>Bot yenidən başladıldı</b>": _t("⚠️ <b>The bot was restarted</b>", "⚠️ <b>Бот был перезапущен</b>", "⚠️ <b>Bot yeniden başlatıldı</b>"),
    "⛔ Bu panel yalnız sahibi üçündür": _t("⛔ This panel is for the owner only", "⛔ Эта панель только для владельца", "⛔ Bu panel yalnızca sahibi içindir"),
    "⛔ Yalnız yükləməni başladan": _t("⛔ Only the one who started the download", "⛔ Только тот, кто начал загрузку", "⛔ Yalnızca indirmeyi başlatan"),
    "✅ <b>Musiqi Shazam ilə tapıldı!</b>\n━━━━━━━━━━━━━━━━━━\n🎵 <b>Ad:</b> {title}\n🎤 <b>Artist:</b> {artist}\n🖼 <b>Thumbnail:</b> {cover}\n\n<i>Məlumatlar tətbiq edildi. Göndərmək üçün '✅ Göndər' vurun.</i>": _t(
        "✅ <b>Song found with Shazam!</b>\n━━━━━━━━━━━━━━━━━━\n🎵 <b>Title:</b> {title}\n🎤 <b>Artist:</b> {artist}\n🖼 <b>Thumbnail:</b> {cover}\n\n<i>The details were applied. Tap '✅ Send' to send it.</i>",
        "✅ <b>Песня найдена через Shazam!</b>\n━━━━━━━━━━━━━━━━━━\n🎵 <b>Название:</b> {title}\n🎤 <b>Исполнитель:</b> {artist}\n🖼 <b>Обложка:</b> {cover}\n\n<i>Данные применены. Нажмите '✅ Отправить', чтобы отправить.</i>",
        "✅ <b>Şarkı Shazam ile bulundu!</b>\n━━━━━━━━━━━━━━━━━━\n🎵 <b>Ad:</b> {title}\n🎤 <b>Sanatçı:</b> {artist}\n🖼 <b>Kapak:</b> {cover}\n\n<i>Bilgiler uygulandı. Göndermek için '✅ Gönder'e basın.</i>"),
    "✅ <b>Növbə bitdi</b>\n/play ilə yeni mahnı əlavə et": _t(
        "✅ <b>The queue is finished</b>\nAdd a new song with /play",
        "✅ <b>Очередь закончилась</b>\nДобавьте новую песню через /play",
        "✅ <b>Sıra bitti</b>\n/play ile yeni şarkı ekle"),
    "⏳ <b>Bot restart olunur.</b>\nNövbədəki <b>{n}</b> mahnı restartdan sonra avtomatik davam edəcək.": _t(
        "⏳ <b>The bot is restarting.</b>\n<b>{n}</b> songs in the queue will continue automatically after the restart.",
        "⏳ <b>Бот перезапускается.</b>\nПесни в очереди (<b>{n}</b>) продолжатся автоматически после перезапуска.",
        "⏳ <b>Bot yeniden başlatılıyor.</b>\nSıradaki <b>{n}</b> şarkı yeniden başlatmadan sonra otomatik devam edecek."),
    "♻️ <b>Bot yenidən başladı — yayım bərpa olundu.</b>": _t(
        "♻️ <b>The bot restarted — the stream was restored.</b>",
        "♻️ <b>Бот перезапущен — эфир восстановлен.</b>",
        "♻️ <b>Bot yeniden başladı — yayın geri yüklendi.</b>"),
    "✅ <b>Səsli yayım və növbə uğurla bərpa olundu!</b>": _t(
        "✅ <b>The voice stream and queue were restored!</b>",
        "✅ <b>Голосовой эфир и очередь успешно восстановлены!</b>",
        "✅ <b>Sesli yayın ve sıra başarıyla geri yüklendi!</b>"),
    "✅ <b>Səsli yayım yeniləndi!</b>\n<i>Axın bərpa olundu, növbə qorundu.</i>": _t(
        "✅ <b>The voice stream was refreshed!</b>\n<i>The stream is back, the queue is kept.</i>",
        "✅ <b>Голосовой эфир обновлён!</b>\n<i>Поток восстановлен, очередь сохранена.</i>",
        "✅ <b>Sesli yayın yenilendi!</b>\n<i>Akış geri geldi, sıra korundu.</i>"),
    "✅ Shazam üz qabığı": _t("✅ Shazam cover", "✅ Обложка Shazam", "✅ Shazam kapağı"),
    "❌ <b>Alınmadı: {n}</b>": _t("❌ <b>Failed: {n}</b>", "❌ <b>Не удалось: {n}</b>", "❌ <b>Alınamadı: {n}</b>"),
    "❌ <b>Səsli yayımın bərpası ləğv edildi.</b>": _t(
        "❌ <b>Voice stream restore was cancelled.</b>", "❌ <b>Восстановление эфира отменено.</b>",
        "❌ <b>Sesli yayının geri yüklenmesi iptal edildi.</b>"),
    "❌ VK qısa linki açılmadı.": _t("❌ Could not open the VK short link.", "❌ Не удалось открыть короткую ссылку VK.", "❌ VK kısa bağlantısı açılamadı."),
    "❌ Ödəniş pəncərəsi açılmadı, bir az sonra yenidən cəhd edin.": _t(
        "❌ The payment window did not open, please try again shortly.",
        "❌ Окно оплаты не открылось, попробуйте чуть позже.",
        "❌ Ödeme penceresi açılmadı, biraz sonra tekrar deneyin."),
    "⬇️ YouTube-dan → depoya əlavə olundu: {n} · {s}": _t(
        "⬇️ From YouTube → added to storage: {n} · {s}", "⬇️ Из YouTube → добавлено в хранилище: {n} · {s}",
        "⬇️ YouTube'dan → depoya eklendi: {n} · {s}"),
    "⬇️ hazırlanır...": _t("⬇️ preparing...", "⬇️ подготовка...", "⬇️ hazırlanıyor..."),
    "⬇️ yüklənir...": _t("⬇️ downloading...", "⬇️ загрузка...", "⬇️ indiriliyor..."),
    "⭐ <b>Bəxşiş göndər</b>\n\nDLLMaster-i bəyənirsinizsə, əməyə dəstək olaraq Telegram Stars ilə bəxşiş göndərə bilərsiniz 💙\n\n👇 Düyməyə basın — bot şəxsi çatda ödəniş pəncərəsini açacaq.": _t(
        "⭐ <b>Send a tip</b>\n\nIf you like DLLMaster, you can support the work with a Telegram Stars tip 💙\n\n👇 Tap the button — the bot will open the payment window in a private chat.",
        "⭐ <b>Отправить чаевые</b>\n\nЕсли вам нравится DLLMaster, вы можете поддержать работу чаевыми в Telegram Stars 💙\n\n👇 Нажмите кнопку — бот откроет окно оплаты в личном чате.",
        "⭐ <b>Bahşiş gönder</b>\n\nDLLMaster'ı beğendiyseniz, emeğe destek olarak Telegram Stars ile bahşiş gönderebilirsiniz 💙\n\n👇 Düğmeye basın — bot özel sohbette ödeme penceresini açacak."),
    "🎙 <b>İstifadə qaydası</b>\n<code>/play mahnı adı</code> və ya mahnıya reply edib <code>/play</code>": _t(
        "🎙 <b>How to use</b>\n<code>/play song name</code> or reply to a song with <code>/play</code>",
        "🎙 <b>Как пользоваться</b>\n<code>/play название песни</code> или ответьте на песню командой <code>/play</code>",
        "🎙 <b>Nasıl kullanılır</b>\n<code>/play şarkı adı</code> veya bir şarkıya yanıt olarak <code>/play</code>"),
    "🎙 Canlı Yayım Paneli": _t("🎙 Live Stream Panel", "🎙 Панель голосового эфира", "🎙 Canlı Yayın Paneli"),
    "🎤 VK ifaçısı": _t("🎤 VK artist", "🎤 Исполнитель VK", "🎤 VK sanatçısı"),
    "🎧 <i>Shazam ilə səs analiz edilir...</i>": _t(
        "🎧 <i>Analyzing the audio with Shazam...</i>", "🎧 <i>Звук анализируется через Shazam...</i>",
        "🎧 <i>Ses Shazam ile analiz ediliyor...</i>"),
    "🎧 Shazam ilə musiqi axtarılır...": _t("🎧 Searching for the music with Shazam...", "🎧 Поиск музыки через Shazam...", "🎧 Shazam ile müzik aranıyor..."),
    "🎧 Shazam ilə tanı": _t("🎧 Identify with Shazam", "🎧 Распознать через Shazam", "🎧 Shazam ile tanı"),
    "🎧 VK playlisti": _t("🎧 VK playlist", "🎧 Плейлист VK", "🎧 VK çalma listesi"),
    "🎵 <b>{n}</b> mahnı göndərildi": _t("🎵 <b>{n}</b> songs sent", "🎵 Отправлено песен: <b>{n}</b>", "🎵 <b>{n}</b> şarkı gönderildi"),
    "🎶 Playlistlərim": _t("🎶 My playlists", "🎶 Мои плейлисты", "🎶 Çalma listelerim"),
    "🐢 Flood gözləmələri: {n} dəfə · {s} san.": _t(
        "🐢 Flood waits: {n} times · {s} s", "🐢 Ожидания из-за лимитов: {n} раз · {s} с", "🐢 Flood beklemeleri: {n} kez · {s} sn"),
    "👮 Bu mahnını yalnız əlavə edən şəxs və ya adminlər silə bilər": _t(
        "👮 Only the person who added this song or admins can remove it",
        "👮 Удалить эту песню может только тот, кто её добавил, или админы",
        "👮 Bu şarkıyı yalnızca ekleyen kişi veya yöneticiler silebilir"),
    "💎 <b>Premium</b> ({until})": _t("💎 <b>Premium</b> ({until})", "💎 <b>Premium</b> ({until})", "💎 <b>Premium</b> ({until})"),
    "💎 Premium ilə daha çox imkan → /premium": _t(
        "💎 More features with Premium → /premium", "💎 Больше возможностей с Premium → /premium",
        "💎 Premium ile daha fazla özellik → /premium"),
    "💾 Həcm: <b>{s}</b>": _t("💾 Size: <b>{s}</b>", "💾 Объём: <b>{s}</b>", "💾 Boyut: <b>{s}</b>"),
    "💿 VK albomu": _t("💿 VK album", "💿 Альбом VK", "💿 VK albümü"),
    "📊 Proses": _t("📊 Progress", "📊 Процесс", "📊 İşlem"),
    "📋 <b>Növbəyə əlavə olundu (#{pos})</b>\n{title}": _t(
        "📋 <b>Added to the queue (#{pos})</b>\n{title}", "📋 <b>Добавлено в очередь (#{pos})</b>\n{title}",
        "📋 <b>Sıraya eklendi (#{pos})</b>\n{title}"),
    "📋 Növbə onsuz da boşdur": _t("📋 The queue is already empty", "📋 Очередь и так пуста", "📋 Sıra zaten boş"),
    "📋 Növbə onsuz da boşdur.": _t("📋 The queue is already empty.", "📋 Очередь и так пуста.", "📋 Sıra zaten boş."),
    "📝 Mahnı sözləri": _t("📝 Lyrics", "📝 Текст песни", "📝 Şarkı sözleri"),
    "📝 VK postu": _t("📝 VK post", "📝 Пост VK", "📝 VK gönderisi"),
    "📦 Depoda yoxdur — depoya yüklənir...": _t(
        "📦 Not in storage — uploading to storage...", "📦 Нет в хранилище — загружается в хранилище...",
        "📦 Depoda yok — depoya yükleniyor..."),
    "📦 Depodan (keşdən): {n} · {s}": _t(
        "📦 From storage (cache): {n} · {s}", "📦 Из хранилища (кэш): {n} · {s}", "📦 Depodan (önbellekten): {n} · {s}"),
    "📦 depodan götürülür...": _t("📦 taking from storage...", "📦 берётся из хранилища...", "📦 depodan alınıyor..."),
    "📩 Almaq üçün botu şəxsi çatda başlat.": _t(
        "📩 Start the bot in a private chat to receive it.", "📩 Чтобы получить, запустите бота в личном чате.",
        "📩 Almak için botu özel sohbette başlatın."),
    "📩 Bota keç": _t("📩 Go to the bot", "📩 Перейти к боту", "📩 Bota geç"),
    "📩 Mahnı şəxsi çatına göndərildi.": _t(
        "📩 The song was sent to your private chat.", "📩 Песня отправлена в ваш личный чат.",
        "📩 Şarkı özel sohbetinize gönderildi."),
    "📷 Kod gəlmir? QR ilə gir": _t("📷 No code? Sign in with QR", "📷 Не приходит код? Войти по QR", "📷 Kod gelmiyor mu? QR ile gir"),
    "📻 <i>Restartdan əvvəl səsli yayımda mahnılar var idi:</i>": _t(
        "📻 <i>Before the restart there were songs in the voice stream:</i>",
        "📻 <i>До перезапуска в голосовом эфире были песни:</i>",
        "📻 <i>Yeniden başlatmadan önce sesli yayında şarkılar vardı:</i>"),
    "🔀 <b>Mixplay — Oxşar mahnılar</b>\n\n🔇 Hazırda canlı yayımda mahnı oxunmur.\n\n<b>İstifadə qaydası:</b>\n• <code>/mixplay mahnı adı</code> və ya link\n• Mahnıya reply edib <code>/mixplay</code>\n• Canlı yayımda mahnı oxunarkən sadəcə <code>/mixplay</code>": _t(
        "🔀 <b>Mixplay — Similar songs</b>\n\n🔇 No song is playing in the live stream right now.\n\n<b>How to use:</b>\n• <code>/mixplay song name</code> or a link\n• Reply to a song with <code>/mixplay</code>\n• While a song is playing, just <code>/mixplay</code>",
        "🔀 <b>Mixplay — Похожие песни</b>\n\n🔇 Сейчас в эфире ничего не играет.\n\n<b>Как пользоваться:</b>\n• <code>/mixplay название песни</code> или ссылка\n• Ответьте на песню командой <code>/mixplay</code>\n• Пока играет песня, просто <code>/mixplay</code>",
        "🔀 <b>Mixplay — Benzer şarkılar</b>\n\n🔇 Şu anda canlı yayında şarkı çalmıyor.\n\n<b>Nasıl kullanılır:</b>\n• <code>/mixplay şarkı adı</code> veya bağlantı\n• Bir şarkıya yanıt olarak <code>/mixplay</code>\n• Şarkı çalarken sadece <code>/mixplay</code>"),
    "🔀 <b>Oxşar mahnılar axtarılır...</b>\n<i>Zəhmət olmasa gözləyin...</i>": _t(
        "🔀 <b>Searching for similar songs...</b>\n<i>Please wait...</i>",
        "🔀 <b>Ищем похожие песни...</b>\n<i>Пожалуйста, подождите...</i>",
        "🔀 <b>Benzer şarkılar aranıyor...</b>\n<i>Lütfen bekleyin...</i>"),
    "🔀 <b>İstifadə qaydası</b>\n<code>/mixplay mahnı adı</code> və ya mahnı oxunarkən sadəcə <code>/mixplay</code>": _t(
        "🔀 <b>How to use</b>\n<code>/mixplay song name</code> or just <code>/mixplay</code> while a song is playing",
        "🔀 <b>Как пользоваться</b>\n<code>/mixplay название песни</code> или просто <code>/mixplay</code>, пока играет песня",
        "🔀 <b>Nasıl kullanılır</b>\n<code>/mixplay şarkı adı</code> veya şarkı çalarken sadece <code>/mixplay</code>"),
    "🔀 Mix: Aktiv ✅": _t("🔀 Mix: Active ✅", "🔀 Mix: Активен ✅", "🔀 Mix: Etkin ✅"),
    "🔀 Mixplay": _t("🔀 Mixplay", "🔀 Mixplay", "🔀 Mixplay"),
    "🔀 Mixplay başladılır...": _t("🔀 Starting Mixplay...", "🔀 Запускается Mixplay...", "🔀 Mixplay başlatılıyor..."),
    "🔀 Mixplay söndürüldü": _t("🔀 Mixplay turned off", "🔀 Mixplay выключен", "🔀 Mixplay kapatıldı"),
    "🔁 Alınmayanları yenidən yüklə ({n})": _t(
        "🔁 Retry the failed ones ({n})", "🔁 Повторить неудавшиеся ({n})", "🔁 Alınamayanları yeniden indir ({n})"),
    "🔁 Hesabı dəyiş": _t("🔁 Change account", "🔁 Сменить аккаунт", "🔁 Hesabı değiştir"),
    "🔁 Yenidən göndər": _t("🔁 Send again", "🔁 Отправить снова", "🔁 Yeniden gönder"),
    "🔁 {n} mahnı yenidən yüklənir...": _t(
        "🔁 Re-downloading {n} songs...", "🔁 Повторная загрузка песен: {n}...", "🔁 {n} şarkı yeniden indiriliyor..."),
    "🔄 <b>Səsli yayım bağlantısı yenilənir...</b>": _t(
        "🔄 <b>Refreshing the voice stream connection...</b>", "🔄 <b>Обновляется подключение к эфиру...</b>",
        "🔄 <b>Sesli yayın bağlantısı yenileniyor...</b>"),
    "🔄 Yenidən qoşul": _t("🔄 Reconnect", "🔄 Подключить заново", "🔄 Yeniden bağlan"),
    "🔄 Yeniləndi": _t("🔄 Refreshed", "🔄 Обновлено", "🔄 Yenilendi"),
    "🔇 Hazırda aktiv səsli yayım yoxdur.": _t(
        "🔇 There is no active voice stream right now.", "🔇 Сейчас нет активного голосового эфира.",
        "🔇 Şu anda etkin sesli yayın yok."),
    "🔇 Hazırda heç nə oxunmur": _t("🔇 Nothing is playing right now", "🔇 Сейчас ничего не играет", "🔇 Şu anda hiçbir şey çalmıyor"),
    "🔎 <b>Axtarılır...</b>\n<i>Zəhmət olmasa gözləyin...</i>": _t(
        "🔎 <b>Searching...</b>\n<i>Please wait...</i>", "🔎 <b>Идёт поиск...</b>\n<i>Пожалуйста, подождите...</i>",
        "🔎 <b>Aranıyor...</b>\n<i>Lütfen bekleyin...</i>"),
    "🔐 <b>Asistan təsdiqi</b>\nAsistanın qrupa əlavə olunması üçün admin təsdiqi gözlənilir — təsdiqdən sonra avtomatik davam edəcək.": _t(
        "🔐 <b>Assistant approval</b>\nWaiting for an admin to approve adding the assistant to the group — it will continue automatically after approval.",
        "🔐 <b>Подтверждение ассистента</b>\nОжидается подтверждение админа на добавление ассистента в группу — после этого всё продолжится автоматически.",
        "🔐 <b>Asistan onayı</b>\nAsistanın gruba eklenmesi için yönetici onayı bekleniyor — onaydan sonra otomatik devam edecek."),
    "🔑 2️⃣ Asistanı qoş (UB2)": _t("🔑 2️⃣ Connect the assistant (UB2)", "🔑 2️⃣ Подключить ассистента (UB2)", "🔑 2️⃣ Asistanı bağla (UB2)"),
    "🔒 Bu çatda musiqi göndərmək qadağandır (adminlərin icazəsi).": _t(
        "🔒 Sending music is not allowed in this chat (admin permission).",
        "🔒 В этом чате отправка музыки запрещена (разрешение админов).",
        "🔒 Bu sohbette müzik göndermek yasak (yönetici izni)."),
    "🔙 Əsas Menyu": _t("🔙 Main menu", "🔙 Главное меню", "🔙 Ana menü"),
    "🗄 Userbot bazası": _t("🗄 Userbot database", "🗄 База юзербота", "🗄 Userbot veritabanı"),
    "🗑 <b>Növbə təmizləndi!</b>\n<i>{n} mahnı növbədən çıxarıldı.</i>": _t(
        "🗑 <b>Queue cleared!</b>\n<i>{n} songs were removed from the queue.</i>",
        "🗑 <b>Очередь очищена!</b>\n<i>Из очереди удалено песен: {n}.</i>",
        "🗑 <b>Sıra temizlendi!</b>\n<i>{n} şarkı sıradan çıkarıldı.</i>"),
    "🗑 <b>Növbədən silindi:</b>\n{title}\n<i>👤 Silən: {by}</i>": _t(
        "🗑 <b>Removed from the queue:</b>\n{title}\n<i>👤 Removed by: {by}</i>",
        "🗑 <b>Удалено из очереди:</b>\n{title}\n<i>👤 Удалил: {by}</i>",
        "🗑 <b>Sıradan silindi:</b>\n{title}\n<i>👤 Silen: {by}</i>"),
    "🗑 Mahnı növbədən silindi": _t("🗑 The song was removed from the queue", "🗑 Песня удалена из очереди", "🗑 Şarkı sıradan silindi"),
    "🗑 Növbə təmizləndi ({n} mahnı silindi)": _t(
        "🗑 Queue cleared ({n} songs removed)", "🗑 Очередь очищена (удалено песен: {n})", "🗑 Sıra temizlendi ({n} şarkı silindi)"),
    "🗑 Növbəni təmizlə": _t("🗑 Clear the queue", "🗑 Очистить очередь", "🗑 Sırayı temizle"),
    "🗑 Növbəni təmizlə ({n})": _t("🗑 Clear the queue ({n})", "🗑 Очистить очередь ({n})", "🗑 Sırayı temizle ({n})"),
    "😔 <b>Musiqi tapılmadı.</b>\n<i>Shazam bu səsi tanıya bilmədi.</i>\n\n": _t(
        "😔 <b>No music found.</b>\n<i>Shazam could not recognize this audio.</i>\n\n",
        "😔 <b>Музыка не найдена.</b>\n<i>Shazam не смог распознать этот звук.</i>\n\n",
        "😔 <b>Müzik bulunamadı.</b>\n<i>Shazam bu sesi tanıyamadı.</i>\n\n"),
    "🚪 Hesabdan çıx": _t("🚪 Sign out", "🚪 Выйти из аккаунта", "🚪 Hesaptan çık"),
    "🛡 <b>Qrupda Musiqi Yükləmə İcazəsi</b>\n\nQrup daxilində musiqi göndərilə bilmirsə, qrup admini tərəfindən bota musiqi/media göndərmə icazəsi verilməli və ya bot admin edilməlidir.\n\n📌 <b>Bota yetki vermək üçün:</b>\n1. Aşağıdakı <b>'🛡 Qrupa yetkilərlə əlavə et'</b> düyməsinə klikləyin.\n2. Qrupunuzu seçin və bota musiqi göndərmə icazəsi verin.\n3. Və ya qrupun parametrlərindən üzv icazələrində <b>'Musiqi göndər'</b> funksiyasını aktiv edin.": _t(
        "🛡 <b>Music permission in the group</b>\n\nIf music cannot be sent in the group, a group admin must allow the bot to send music/media or make the bot an admin.\n\n📌 <b>To give the bot permissions:</b>\n1. Tap the <b>'🛡 Add to group with permissions'</b> button below.\n2. Choose your group and allow the bot to send music.\n3. Or enable <b>'Send music'</b> in the group's member permissions.",
        "🛡 <b>Разрешение на музыку в группе</b>\n\nЕсли в группе нельзя отправлять музыку, админ группы должен разрешить боту отправку музыки/медиа или сделать бота админом.\n\n📌 <b>Чтобы выдать боту права:</b>\n1. Нажмите кнопку <b>'🛡 Добавить в группу с правами'</b> ниже.\n2. Выберите группу и разрешите боту отправлять музыку.\n3. Или включите <b>'Отправка музыки'</b> в правах участников группы.",
        "🛡 <b>Grupta müzik izni</b>\n\nGrupta müzik gönderilemiyorsa, grup yöneticisi bota müzik/medya gönderme izni vermeli veya botu yönetici yapmalıdır.\n\n📌 <b>Bota yetki vermek için:</b>\n1. Aşağıdaki <b>'🛡 Yetkilerle gruba ekle'</b> düğmesine tıklayın.\n2. Grubunuzu seçin ve bota müzik gönderme izni verin.\n3. Veya grubun üye izinlerinden <b>'Müzik gönder'</b> özelliğini etkinleştirin."),
    "🛡 Qrupa yetkilərlə əlavə et": _t("🛡 Add to group with permissions", "🛡 Добавить в группу с правами", "🛡 Yetkilerle gruba ekle"),
    "🧵 Worker sayı": _t("🧵 Worker count", "🧵 Количество воркеров", "🧵 Worker sayısı"),
}


# ── «Adminə bildir», TikTok, Shazam (çoxmərhələli), yükləmə sürəti ──
EXTRA2 = {
    "🚨 Adminə bildir": _t("🚨 Report to admin", "🚨 Сообщить админу", "🚨 Yöneticiye bildir"),
    "✅ Admin məlumatlandırıldı": _t("✅ Admin notified", "✅ Админ уведомлён", "✅ Yönetici bilgilendirildi"),
    "✅ Admin artıq məlumatlandırılıb": _t("✅ The admin has already been notified", "✅ Админ уже уведомлён", "✅ Yönetici zaten bilgilendirildi"),
    "⏳ Bir az gözləyin — bildiriş artıq göndərilib": _t(
        "⏳ Please wait a bit — the report was already sent", "⏳ Подождите немного — сообщение уже отправлено",
        "⏳ Biraz bekleyin — bildirim zaten gönderildi"),
    "Bu gün üçün bildiriş limiti dolub": _t(
        "The report limit for today has been reached", "Лимит сообщений на сегодня исчерпан", "Bugün için bildirim limiti doldu"),
    "Admin təyin olunmayıb": _t("No admin is set", "Админ не назначен", "Yönetici atanmamış"),
    "❌ Bildiriş göndərilmədi, bir az sonra yenidən cəhd edin": _t(
        "❌ The report was not sent, please try again shortly", "❌ Сообщение не отправлено, попробуйте чуть позже",
        "❌ Bildirim gönderilemedi, biraz sonra tekrar deneyin"),
    "✅ Adminə göndərildi. Təşəkkürlər!": _t("✅ Sent to the admin. Thank you!", "✅ Отправлено админу. Спасибо!", "✅ Yöneticiye gönderildi. Teşekkürler!"),
    # TikTok
    "🎵 <i>TikTok mahnısı axtarılır...</i>": _t(
        "🎵 <i>Looking for the TikTok song...</i>", "🎵 <i>Ищем песню из TikTok...</i>", "🎵 <i>TikTok şarkısı aranıyor...</i>"),
    "🎵 <i>TikTok mahnısı axtarılır — mərhələ {n}/{m}: {label}...</i>": _t(
        "🎵 <i>Looking for the TikTok song — step {n}/{m}: {label}...</i>",
        "🎵 <i>Ищем песню из TikTok — этап {n}/{m}: {label}...</i>",
        "🎵 <i>TikTok şarkısı aranıyor — adım {n}/{m}: {label}...</i>"),
    "TikTok məlumatı": _t("TikTok data", "данные TikTok", "TikTok bilgisi"),
    "səhifədəki musiqi bloku": _t("music block on the page", "музыкальный блок на странице", "sayfadaki müzik bloğu"),
    "səsin təmiz faylı → Shazam": _t("clean sound file → Shazam", "чистый файл звука → Shazam", "sesin temiz dosyası → Shazam"),
    "videonun səsi → Shazam (çoxmərhələli)": _t(
        "video audio → Shazam (multi-step)", "звук видео → Shazam (многоэтапно)", "videonun sesi → Shazam (çok adımlı)"),
    "TikTok səhifəsi": _t("TikTok page", "страница TikTok", "TikTok sayfası"),
    "Shazam (səs faylı)": _t("Shazam (sound file)", "Shazam (файл звука)", "Shazam (ses dosyası)"),
    "Shazam (video)": _t("Shazam (video)", "Shazam (видео)", "Shazam (video)"),
    "❌ <b>Mahnı tanınmadı.</b>\n<i>Orijinal səs, foto post (səhifə bağlıdır) və ya səs-küylü videodur. Mahnının adını yazsanız, tapıb yükləyərəm.</i>": _t(
        "❌ <b>Song not recognized.</b>\n<i>It is an original sound, a photo post (page is closed) or a noisy video. If you type the song name, I'll find and download it.</i>",
        "❌ <b>Песня не распознана.</b>\n<i>Это оригинальный звук, фото-пост (страница закрыта) или шумное видео. Напишите название песни — я найду и скачаю её.</i>",
        "❌ <b>Şarkı tanınamadı.</b>\n<i>Orijinal ses, fotoğraf gönderisi (sayfa kapalı) veya gürültülü bir video. Şarkının adını yazarsanız bulup indiririm.</i>"),
    "🔎 <b>{q}</b>\n<i>({src}) tam versiya axtarılır...</i>": _t(
        "🔎 <b>{q}</b>\n<i>({src}) looking for the full version...</i>", "🔎 <b>{q}</b>\n<i>({src}) ищем полную версию...</i>",
        "🔎 <b>{q}</b>\n<i>({src}) tam sürüm aranıyor...</i>"),
    "❌ <b>Axtarış alınmadı:</b> <code>{e}</code>": _t(
        "❌ <b>Search failed:</b> <code>{e}</code>", "❌ <b>Поиск не удался:</b> <code>{e}</code>", "❌ <b>Arama başarısız:</b> <code>{e}</code>"),
    "❌ <b>Tam versiya tapılmadı:</b> {q}\n<i>Əl ilə axtarın:</i> <code>/music {q}</code>": _t(
        "❌ <b>Full version not found:</b> {q}\n<i>Search manually:</i> <code>/music {q}</code>",
        "❌ <b>Полная версия не найдена:</b> {q}\n<i>Поищите вручную:</i> <code>/music {q}</code>",
        "❌ <b>Tam sürüm bulunamadı:</b> {q}\n<i>Elle arayın:</i> <code>/music {q}</code>"),
    "⬇️ <b>{t}</b>\n<i>Yüklənir...</i>": _t(
        "⬇️ <b>{t}</b>\n<i>Downloading...</i>", "⬇️ <b>{t}</b>\n<i>Загрузка...</i>", "⬇️ <b>{t}</b>\n<i>İndiriliyor...</i>"),
    "❌ <b>Yüklənmədi:</b> <code>{e}</code>": _t(
        "❌ <b>Download failed:</b> <code>{e}</code>", "❌ <b>Не удалось загрузить:</b> <code>{e}</code>",
        "❌ <b>İndirilemedi:</b> <code>{e}</code>"),
    "⏹ <b>Ləğv edildi.</b>\n<i>Düzgün adı bilirsinizsə:</i> <code>/music {q}</code>": _t(
        "⏹ <b>Cancelled.</b>\n<i>If you know the right name:</i> <code>/music {q}</code>",
        "⏹ <b>Отменено.</b>\n<i>Если знаете правильное название:</i> <code>/music {q}</code>",
        "⏹ <b>İptal edildi.</b>\n<i>Doğru adı biliyorsanız:</i> <code>/music {q}</code>"),
    "✅ Bəli, yüklə": _t("✅ Yes, download", "✅ Да, скачать", "✅ Evet, indir"),
    "❌ Səhvdir": _t("❌ Wrong", "❌ Неверно", "❌ Yanlış"),
    "🤔 <b>Tam əmin deyiləm.</b>\nTanınan: <b>{heard}</b>\nYüklənəcək: <b>{will}</b>\n<i>Düzgündürmü?</i>": _t(
        "🤔 <b>I'm not completely sure.</b>\nRecognized: <b>{heard}</b>\nWill download: <b>{will}</b>\n<i>Is it correct?</i>",
        "🤔 <b>Я не совсем уверен.</b>\nРаспознано: <b>{heard}</b>\nБудет загружено: <b>{will}</b>\n<i>Верно?</i>",
        "🤔 <b>Tam emin değilim.</b>\nTanınan: <b>{heard}</b>\nİndirilecek: <b>{will}</b>\n<i>Doğru mu?</i>"),
    "Müddət bitib": _t("Time has expired", "Время истекло", "Süre doldu"),
    "⛔ Yalnız linki göndərən": _t("⛔ Only the one who sent the link", "⛔ Только тот, кто отправил ссылку", "⛔ Yalnızca bağlantıyı gönderen"),
    "✅ Yüklənir...": _t("✅ Downloading...", "✅ Загружается...", "✅ İndiriliyor..."),
    "Ləğv edildi": _t("Cancelled", "Отменено", "İptal edildi"),
    "ℹ️ <code>/tt TikTok-linki</code> — videonun mahnısını tam versiya yükləyir.\n<i>Və ya linkə reply edib</i> <code>/tt</code>": _t(
        "ℹ️ <code>/tt TikTok-link</code> — downloads the full version of the video's song.\n<i>Or reply to a link with</i> <code>/tt</code>",
        "ℹ️ <code>/tt ссылка-TikTok</code> — скачивает полную версию песни из видео.\n<i>Или ответьте на ссылку командой</i> <code>/tt</code>",
        "ℹ️ <code>/tt TikTok-bağlantısı</code> — videonun şarkısının tam sürümünü indirir.\n<i>Veya bağlantıya yanıt olarak</i> <code>/tt</code>"),
    # yükləmə sürəti
    "~{n} san.": _t("~{n} s", "~{n} с", "~{n} sn"),
    "{n} san.": _t("{n} s", "{n} с", "{n} sn"),
    # Shazam
    "🎧 <b>Musiqi tapıldı (Shazam)</b>": _t("🎧 <b>Music found (Shazam)</b>", "🎧 <b>Музыка найдена (Shazam)</b>", "🎧 <b>Müzik bulundu (Shazam)</b>"),
    "🎵 <b>Mahnı:</b> <b>{v}</b>": _t("🎵 <b>Song:</b> <b>{v}</b>", "🎵 <b>Песня:</b> <b>{v}</b>", "🎵 <b>Şarkı:</b> <b>{v}</b>"),
    "👤 <b>İfaçı:</b> <b>{v}</b>": _t("👤 <b>Artist:</b> <b>{v}</b>", "👤 <b>Исполнитель:</b> <b>{v}</b>", "👤 <b>Sanatçı:</b> <b>{v}</b>"),
    "Albom": _t("Album", "Альбом", "Albüm"),
    "Janr": _t("Genre", "Жанр", "Tür"),
    "Buraxılış ili": _t("Release year", "Год выпуска", "Çıkış yılı"),
    "📥 Yüklə": _t("📥 Download", "📥 Скачать", "📥 İndir"),
    "🔎 Inline axtar": _t("🔎 Inline search", "🔎 Инлайн-поиск", "🔎 Satır içi ara"),
    "📝 Sözlər": _t("📝 Lyrics", "📝 Текст", "📝 Sözler"),
    "✖️ Bağla": _t("✖️ Close", "✖️ Закрыть", "✖️ Kapat"),
    "🎧 <i>Səs dinlənilir və Shazam ilə axtarılır...</i>": _t(
        "🎧 <i>Listening to the audio and searching with Shazam...</i>", "🎧 <i>Слушаю звук и ищу через Shazam...</i>",
        "🎧 <i>Ses dinleniyor ve Shazam ile aranıyor...</i>"),
    "⚠️ <i>Səs faylı yüklənə bilmədi və ya çox qısadır.</i>": _t(
        "⚠️ <i>The audio file could not be downloaded or is too short.</i>",
        "⚠️ <i>Аудиофайл не удалось загрузить, или он слишком короткий.</i>",
        "⚠️ <i>Ses dosyası indirilemedi veya çok kısa.</i>"),
    "🎧 <i>Çətin mahnıdır — mərhələ {i}/{n}: {label}...</i>": _t(
        "🎧 <i>A tough one — step {i}/{n}: {label}...</i>", "🎧 <i>Сложная песня — этап {i}/{n}: {label}...</i>",
        "🎧 <i>Zor bir şarkı — adım {i}/{n}: {label}...</i>"),
    "başlanğıc": _t("start", "начало", "başlangıç"),
    "orta hissə": _t("middle part", "середина", "orta kısım"),
    "səs yaxşılaşdırıldı (başlanğıc)": _t("audio enhanced (start)", "звук улучшен (начало)", "ses iyileştirildi (başlangıç)"),
    "1/4 hissə": _t("1/4 part", "1/4 часть", "1/4 kısım"),
    "səs yaxşılaşdırıldı (orta)": _t("audio enhanced (middle)", "звук улучшен (середина)", "ses iyileştirildi (orta)"),
    "3/4 hissə": _t("3/4 part", "3/4 часть", "3/4 kısım"),
    "sürətləndirilmiş versiya (sped up)": _t("sped-up version", "ускоренная версия (sped up)", "hızlandırılmış sürüm (sped up)"),
    "yavaşladılmış versiya (slowed)": _t("slowed-down version", "замедленная версия (slowed)", "yavaşlatılmış sürüm (slowed)"),
    "😔 <b>Təəssüf ki, heç bir mahnı tapılmadı.</b>\n<i>Səs çox qısa ola bilər, ətraf küy çoxdur və ya bu musiqi Shazam bazasında yoxdur.</i>": _t(
        "😔 <b>Unfortunately, no song was found.</b>\n<i>The audio may be too short, too noisy, or this music is not in the Shazam database.</i>",
        "😔 <b>К сожалению, ни одна песня не найдена.</b>\n<i>Звук может быть слишком коротким или шумным, либо этой музыки нет в базе Shazam.</i>",
        "😔 <b>Maalesef hiçbir şarkı bulunamadı.</b>\n<i>Ses çok kısa veya gürültülü olabilir ya da bu müzik Shazam veritabanında yok.</i>"),
    "⚠️ <i>Musiqi axtarışı zamanı xəta baş verdi: {err}</i>": _t(
        "⚠️ <i>An error occurred while searching for the music: {err}</i>",
        "⚠️ <i>Ошибка при поиске музыки: {err}</i>", "⚠️ <i>Müzik aranırken bir hata oluştu: {err}</i>"),
    "🔍 <b>Shazam — Musiqi Tanıma Bələdçisi</b>\n━━━━━━━━━━━━━━━━━━\nMahnının adını tapmaq üçün 3 asan yol:\n\n1️⃣ <b>Mesaja Cavab (Reply):</b>\n   • Çatdakı səs yazısına, audioya və ya videoya reply edib <code>/shazam</code> yazın.\n\n2️⃣ <b>Birbaşa Səs / Video Göndər:</b>\n   • Şəxsi çatda bota səsli mesaj, video və ya dairəvi video göndərin və <b>[🎧 Shazam ilə tanı]</b> vurun.\n\n3️⃣ <b>Faylla Birlikdə:</b>\n   • Video və ya audio göndərərkən izahat yerinə <code>/shazam</code> yazın.\n\n<i>⚡ Bot bir neçə saniyə ərzində mahnını tapıb adını və yükləmə linkini təqdim edəcək!</i>": _t(
        "🔍 <b>Shazam — Music Recognition Guide</b>\n━━━━━━━━━━━━━━━━━━\n3 easy ways to find a song's name:\n\n1️⃣ <b>Reply to a message:</b>\n   • Reply to a voice note, audio or video in the chat with <code>/shazam</code>.\n\n2️⃣ <b>Send audio / video directly:</b>\n   • In a private chat send the bot a voice message, video or video note and tap <b>[🎧 Identify with Shazam]</b>.\n\n3️⃣ <b>With the file:</b>\n   • When sending a video or audio, write <code>/shazam</code> in the caption.\n\n<i>⚡ In a few seconds the bot will find the song and give you its name and a download link!</i>",
        "🔍 <b>Shazam — Руководство по распознаванию музыки</b>\n━━━━━━━━━━━━━━━━━━\n3 простых способа узнать название песни:\n\n1️⃣ <b>Ответ на сообщение:</b>\n   • Ответьте на голосовое, аудио или видео в чате командой <code>/shazam</code>.\n\n2️⃣ <b>Отправьте звук / видео напрямую:</b>\n   • В личном чате отправьте боту голосовое, видео или кружок и нажмите <b>[🎧 Распознать через Shazam]</b>.\n\n3️⃣ <b>Вместе с файлом:</b>\n   • При отправке видео или аудио напишите <code>/shazam</code> в подписи.\n\n<i>⚡ Через несколько секунд бот найдёт песню и покажет название и ссылку на загрузку!</i>",
        "🔍 <b>Shazam — Müzik Tanıma Rehberi</b>\n━━━━━━━━━━━━━━━━━━\nŞarkının adını bulmak için 3 kolay yol:\n\n1️⃣ <b>Mesaja yanıt:</b>\n   • Sohbetteki sesli mesaja, sese veya videoya yanıt olarak <code>/shazam</code> yazın.\n\n2️⃣ <b>Doğrudan ses / video gönder:</b>\n   • Özel sohbette bota sesli mesaj, video veya yuvarlak video gönderin ve <b>[🎧 Shazam ile tanı]</b>'ya basın.\n\n3️⃣ <b>Dosyayla birlikte:</b>\n   • Video veya ses gönderirken açıklama yerine <code>/shazam</code> yazın.\n\n<i>⚡ Bot birkaç saniye içinde şarkıyı bulup adını ve indirme bağlantısını verecek!</i>"),
    "🔎 Musiqi Axtar (İnline)": _t("🔎 Search music (inline)", "🔎 Поиск музыки (инлайн)", "🔎 Müzik ara (satır içi)"),
    "🎧 Shazam ilə tanı": _t("🎧 Identify with Shazam", "🎧 Распознать через Shazam", "🎧 Shazam ile tanı"),
    "🎤 <b>Səsli mesaj qəbul edildi.</b>\n<i>Bu səsdəki musiqini Shazam ilə axtarmaq üçün aşağıdakı düyməyə vurun:</i>": _t(
        "🎤 <b>Voice message received.</b>\n<i>Tap the button below to search for the music in this audio with Shazam:</i>",
        "🎤 <b>Голосовое сообщение получено.</b>\n<i>Нажмите кнопку ниже, чтобы найти музыку в этом звуке через Shazam:</i>",
        "🎤 <b>Sesli mesaj alındı.</b>\n<i>Bu sesteki müziği Shazam ile aramak için aşağıdaki düğmeye basın:</i>"),
    "📹 <b>Video qeyd qəbul edildi.</b>\n<i>Bu videodakı musiqini Shazam ilə axtarmaq üçün aşağıdakı düyməyə vurun:</i>": _t(
        "📹 <b>Video note received.</b>\n<i>Tap the button below to search for the music in this video with Shazam:</i>",
        "📹 <b>Видеосообщение получено.</b>\n<i>Нажмите кнопку ниже, чтобы найти музыку в этом видео через Shazam:</i>",
        "📹 <b>Video kayıt alındı.</b>\n<i>Bu videodaki müziği Shazam ile aramak için aşağıdaki düğmeye basın:</i>"),
    "🎬 <b>Video qəbul edildi.</b>\n<i>Videodakı arxa fon musiqisini Shazam ilə tapmaq üçün aşağıdakı düyməyə vurun:</i>": _t(
        "🎬 <b>Video received.</b>\n<i>Tap the button below to find the background music in the video with Shazam:</i>",
        "🎬 <b>Видео получено.</b>\n<i>Нажмите кнопку ниже, чтобы найти фоновую музыку в видео через Shazam:</i>",
        "🎬 <b>Video alındı.</b>\n<i>Videodaki fon müziğini Shazam ile bulmak için aşağıdaki düğmeye basın:</i>"),
    "📁 <b>Video fayl qəbul edildi.</b>\n<i>Fayldakı musiqini Shazam ilə tapmaq üçün aşağıdakı düyməyə vurun:</i>": _t(
        "📁 <b>Video file received.</b>\n<i>Tap the button below to find the music in the file with Shazam:</i>",
        "📁 <b>Видеофайл получен.</b>\n<i>Нажмите кнопку ниже, чтобы найти музыку в файле через Shazam:</i>",
        "📁 <b>Video dosyası alındı.</b>\n<i>Dosyadaki müziği Shazam ile bulmak için aşağıdaki düğmeye basın:</i>"),
    "⚠️ Media faylı tapılmadı və ya köhnəlib.": _t(
        "⚠️ The media file was not found or has expired.", "⚠️ Медиафайл не найден или устарел.",
        "⚠️ Medya dosyası bulunamadı veya süresi doldu."),
    "🎧 Shazam ilə axtarılır...": _t("🎧 Searching with Shazam...", "🎧 Ищу через Shazam...", "🎧 Shazam ile aranıyor..."),
    "⚠️ Mahnı məlumatı tapılmadı və ya köhnəlib.": _t(
        "⚠️ The song details were not found or have expired.", "⚠️ Данные о песне не найдены или устарели.",
        "⚠️ Şarkı bilgisi bulunamadı veya süresi doldu."),
    "📥 Mahnı axtarılır və yüklənir...": _t(
        "📥 Searching for and downloading the song...", "📥 Ищу и загружаю песню...", "📥 Şarkı aranıyor ve indiriliyor..."),
    "🎵 <b>{q}</b>\n\nYükləmək üçün əmrdən istifadə edin: <code>/music {q}</code>": _t(
        "🎵 <b>{q}</b>\n\nTo download, use the command: <code>/music {q}</code>",
        "🎵 <b>{q}</b>\n\nЧтобы скачать, используйте команду: <code>/music {q}</code>",
        "🎵 <b>{q}</b>\n\nİndirmek için şu komutu kullanın: <code>/music {q}</code>"),
    "📝 Bu mahnı üçün sözlər tapılmadı.": _t("📝 No lyrics found for this song.", "📝 Для этой песни текст не найден.", "📝 Bu şarkı için söz bulunamadı."),
    "\n\n...(davamı qısaldıldı)": _t("\n\n...(the rest was truncated)", "\n\n...(остальное сокращено)", "\n\n...(devamı kısaltıldı)"),
}
EXTRA.update({k: v for k, v in EXTRA2.items() if k not in EXTRA})

EXTRA.update({
    '💎 <b>DLLMaster Premium</b>\n\nMahnıları "via" yazısı olmadan, daha çox imkanla alın.\n\n👇 Düyməyə basın — bot şəxsi çatda planları göstərəcək.': _t(
        '💎 <b>DLLMaster Premium</b>\n\nGet songs without the "via" caption and with more features.\n\n👇 Tap the button — the bot will show the plans in a private chat.',
        '💎 <b>DLLMaster Premium</b>\n\nПолучайте песни без подписи "via" и с большим числом возможностей.\n\n👇 Нажмите кнопку — бот покажет планы в личном чате.',
        '💎 <b>DLLMaster Premium</b>\n\nŞarkıları "via" yazısı olmadan, daha fazla özellikle alın.\n\n👇 Düğmeye basın — bot planları özel sohbette gösterecek.'),
})
for _k, _v in list(EXTRA.items()):
    pass

EXTRA.update({
    "🏷 <b>Endirim -{pct}%</b> — {date} tarixinədək": _t(
        "🏷 <b>Discount -{pct}%</b> — until {date}", "🏷 <b>Скидка -{pct}%</b> — до {date}", "🏷 <b>İndirim -{pct}%</b> — {date} tarihine kadar"),
    "🏷 <b>Endirim -{pct}%</b>": _t("🏷 <b>Discount -{pct}%</b>", "🏷 <b>Скидка -{pct}%</b>", "🏷 <b>İndirim -{pct}%</b>"),
})

EXTRA.update({
    "🔥 <b>Premium endirimi -{pct}%</b>": _t("🔥 <b>Premium discount -{pct}%</b>", "🔥 <b>Скидка на Premium -{pct}%</b>", "🔥 <b>Premium indirimi -{pct}%</b>"),
    "{date} tarixinədək": _t("until {date}", "до {date}", "{date} tarihine kadar"),
    "🔥 Premium -{pct}%": _t("🔥 Premium -{pct}%", "🔥 Premium -{pct}%", "🔥 Premium -%{pct}"),
})

# ── ⌨️ menyu düymələri (bot/core/menu_button.py, bot/plugins/menu_button_plugin.py) ──
EXTRA.update({
    # klaviatura düymələri (Telegram düymə mətni ≤ ~30 simvol saxlayın)
    "📋 Menyu": _t("📋 Menu", "📋 Меню", "📋 Menü"),
    "🔎 Axtar": _t("🔎 Search", "🔎 Поиск", "🔎 Ara"),
    "🎧 Format": _t("🎧 Format", "🎧 Формат", "🎧 Format"),
    "📚 Pleylistlər": _t("📚 Playlists", "📚 Плейлисты", "📚 Çalma listeleri"),
    "💎 Premium": _t("💎 Premium", "💎 Премиум", "💎 Premium"),
    "🎁 Dəvət et": _t("🎁 Invite", "🎁 Пригласить", "🎁 Davet et"),
    "Mahnı adı və ya link yazın...": _t("Type a song name or link...", "Введите название песни или ссылку...",
                                        "Şarkı adı veya bağlantı yazın..."),
    # komanda siyahısı (Telegram "Menu" düyməsi)
    "Botu başlat": _t("Start the bot", "Запустить бота", "Botu başlat"),
    "Menyunu aç": _t("Open the menu", "Открыть меню", "Menüyü aç"),
    "Mahnı axtar": _t("Search for a song", "Найти песню", "Şarkı ara"),
    "Oxşar mahnılar (mahnıya cavab verin)": _t("Similar songs (reply to a song)", "Похожие песни (ответом на песню)",
                                               "Benzer şarkılar (bir şarkıya yanıt verin)"),
    "Mahnını tanı (audioya cavab verin)": _t("Recognize a song (reply to audio)", "Распознать песню (ответом на аудио)",
                                             "Şarkıyı tanı (bir sese yanıt verin)"),
    "Audio formatı": _t("Audio format", "Формат аудио", "Ses formatı"),
    "Pleylistlərim": _t("My playlists", "Мои плейлисты", "Çalma listelerim"),
    "Premium statusu": _t("Premium status", "Статус Premium", "Premium durumu"),
    "Dili dəyiş": _t("Change language", "Сменить язык", "Dili değiştir"),
    "Klaviatura düymələrini aç / bağla": _t("Show / hide the keyboard buttons", "Показать / скрыть кнопки клавиатуры",
                                            "Klavye düğmelerini aç / kapat"),
    "Depo doldurucu": _t("Depot filler", "Наполнитель хранилища", "Depo doldurucu"),
    "Plugin-lər": _t("Plugins", "Плагины", "Eklentiler"),
    "Loglar": _t("Logs", "Логи", "Günlükler"),
    "Menyu düymələri və komanda siyahısı": _t("Menu buttons and command list", "Кнопки меню и список команд",
                                              "Menü düğmeleri ve komut listesi"),
    "Botu yenidən başlat": _t("Restart the bot", "Перезапустить бота", "Botu yeniden başlat"),
    # mesajlar
    "⌨️ Klaviatura aktivdir. Söndürmək: /keyboard": _t("⌨️ Keyboard is on. Turn off: /keyboard",
                                                       "⌨️ Клавиатура включена. Выключить: /keyboard",
                                                       "⌨️ Klavye açık. Kapatmak için: /keyboard"),
    "⌨️ Klaviatura söndürüldü": _t("⌨️ Keyboard turned off", "⌨️ Клавиатура выключена", "⌨️ Klavye kapatıldı"),
    "⌨️ Klaviatura yeniləndi": _t("⌨️ Keyboard updated", "⌨️ Клавиатура обновлена", "⌨️ Klavye güncellendi"),
    "⌨️ Klaviatura yalnız şəxsi çatda işləyir": _t("⌨️ The keyboard only works in a private chat",
                                                   "⌨️ Клавиатура работает только в личном чате",
                                                   "⌨️ Klavye yalnızca özel sohbette çalışır"),
    "⌨️ Klaviatura bağlıdır — /keyboard ilə yenidən aça bilərsiniz": _t(
        "⌨️ The keyboard is off — you can turn it on again with /keyboard",
        "⌨️ Клавиатура выключена — включить снова можно командой /keyboard",
        "⌨️ Klavye kapalı — /keyboard ile yeniden açabilirsiniz"),
    "🔎 Inline axtarış": _t("🔎 Inline search", "🔎 Инлайн-поиск", "🔎 Satır içi arama"),
    "🔎 Mahnı adını yazın, YouTube/Spotify linki göndərin və ya inline axtarışdan istifadə edin.": _t(
        "🔎 Type a song name, send a YouTube/Spotify link or use inline search.",
        "🔎 Напишите название песни, отправьте ссылку YouTube/Spotify или используйте инлайн-поиск.",
        "🔎 Şarkı adını yazın, YouTube/Spotify bağlantısı gönderin veya satır içi aramayı kullanın."),
})

EXTRA.update({
    "⌨️ Tam menyu klaviaturası aktivdir — bütün bölmələr düymələrlə açılır. Söndürmək: /keyboard off": _t(
        "⌨️ Full menu keyboard is on — every section opens with a button. Turn off: /keyboard off",
        "⌨️ Клавиатура полного меню включена — каждый раздел открывается кнопкой. Выключить: /keyboard off",
        "⌨️ Tam menü klavyesi açık — her bölüm bir düğmeyle açılır. Kapatmak için: /keyboard off"),
})
