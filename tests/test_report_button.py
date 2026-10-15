from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.plugins import report_plugin as rp


def test_technical_errors_get_button():
    assert rp.is_error_text("❌ <b>Xəta:</b> <code>HTTP Error 403</code>")
    assert rp.is_error_text("❌ <b>Yüklənmədi:</b> video əlçatmazdır")
    assert rp.is_error_text("⚠️ Audio yüklənmədi: timeout")
    assert rp.is_error_text("❌ <b>Mahnı tanınmadı.</b>")
    assert rp.is_error_text("❌ <b>Tam versiya tapılmadı:</b> ceza")


def test_user_mistakes_and_normal_text_do_not():
    assert not rp.is_error_text("❌ <b>Axtarış ləğv edildi.</b>")
    assert not rp.is_error_text("⏹ <b>Yükləmə dayandırıldı</b>")
    assert not rp.is_error_text("Bu siyahı köhnəlib — yenidən axtar")
    assert not rp.is_error_text("❌ <b>Yanlış seçim!</b>")
    assert not rp.is_error_text("⛔ Yalnız bot sahibi üçün")
    assert not rp.is_error_text("🎵 Mahnı yükləndi")
    assert not rp.is_error_text("")


def test_with_report_keeps_existing_buttons_and_is_idempotent():
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄", callback_data="x")]])
    out = rp.with_report(kb)
    assert out.inline_keyboard[0][0].callback_data == "x"
    assert out.inline_keyboard[-1][0].callback_data == rp.CB_GO
    assert rp.has_report_row(out) and not rp.has_report_row(kb)
    assert rp.with_report(None).inline_keyboard[0][0].callback_data == rp.CB_GO
