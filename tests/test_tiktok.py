from bot.plugins import tiktok_plugin as tt


def test_find_url():
    assert tt.find_url("bax https://vm.tiktok.com/ZMabc123/ çox yaxşı") == "https://vm.tiktok.com/ZMabc123/"
    assert tt.find_url("https://www.tiktok.com/@u/video/7123456789?lang=en").startswith("https://www.tiktok.com/@u")
    assert tt.find_url("https://youtube.com/watch?v=abcdefghijk") is None


def test_generic_sound_detection():
    assert tt.is_generic_sound("original sound - user123", "user123")
    assert tt.is_generic_sound("оригинальный звук - Вася")
    assert tt.is_generic_sound("ɴᴏsᴛᴀʟɢɪᴀ - оригінальний звук", "nostalgia")
    assert tt.is_generic_sound("оригінальний звук")
    assert tt.is_generic_sound("originalton - user")
    assert tt.is_generic_sound("orijinal ses - user")
    assert tt.is_generic_sound("")
    assert not tt.is_generic_sound("Medcezir")


def test_official_music_detection():
    assert tt.is_official_music("Medcezir", "Ceza", is_original=False)
    assert not tt.is_official_music("ɴᴏsᴛᴀʟɢɪᴀ - оригінальний звук", "ɴᴏsᴛᴀʟɢɪᴀ", is_original=True)
    assert not tt.is_official_music("Custom Title", "Artist", is_original=True)
    assert not tt.is_official_music("original sound", is_original=False)


def test_info_to_query():
    assert tt.info_to_query({"track": "Med Cezir", "artist": "Ceza"}) == ("Ceza", "Med Cezir")
    assert tt.info_to_query({"track": "original sound - ali", "uploader": "ali"}) is None
    assert tt.info_to_query({"track": "ɴᴏsᴛᴀʟɢɪᴀ - оригінальний звук", "uploader": "nostalgia"}) is None
    assert tt.info_to_query({"track": "Sevgi", "artists": ["Rəsul Gunəşli"]}) == ("Rəsul Gunəşli", "Sevgi")


def test_pick_full_prefers_full_song_not_fragment_or_news():
    res = [
        {"title": "Ceza Haber Bülteni", "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa", "raw_duration": 200},
        {"title": "Ceza - Med Cezir (15 sn)", "url": "https://www.youtube.com/watch?v=bbbbbbbbbbb", "raw_duration": 15},
        {"title": "Ceza - Med Cezir", "url": "https://www.youtube.com/watch?v=ccccccccccc", "raw_duration": 240},
    ]
    best = tt.pick_full("Ceza Med Cezir", res)
    assert best and best["url"].endswith("ccccccccccc")
    assert tt.pick_full("Ceza Med Cezir", [res[0], res[1]]) is None


SAMPLE = ('<html><script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
          '{"__DEFAULT_SCOPE__":{"webapp.video-detail":{"itemInfo":{"itemStruct":{"music":'
          '{"id":"1","title":"Medcezir","authorName":"Ceza","original":false,"playUrl":"https://x/y.mp3"}}}}}}'
          '</script></html>')


def test_parse_page_music_and_query():
    m = tt.parse_page_music(SAMPLE)
    assert m == {"title": "Medcezir", "author": "Ceza", "play_url": "https://x/y.mp3", "original": False}
    assert tt.page_to_query(m) == ("Ceza", "Medcezir")
    assert tt.parse_page_music("<html></html>") is None


def test_page_original_sound_is_skipped_but_playurl_kept():
    html = SAMPLE.replace("Medcezir", "original sound - ali").replace('"Ceza"', '"ali"').replace("false", "true")
    m = tt.parse_page_music(html)
    assert tt.page_to_query(m) is None and m["play_url"]


def test_photo_post_variants_and_regex_fallback():
    assert tt.url_variants("https://www.tiktok.com/@u/photo/123") == [
        "https://www.tiktok.com/@u/photo/123", "https://www.tiktok.com/@u/video/123"]
    html = ('<html>... "music":{"id":"7","title":"Sevgi","authorName":"Rəsul",'
            '"playUrl":"https:\\u002F\\u002Fcdn.x\\u002Fa.mp3","original":false} ...</html>')
    m = tt.parse_page_music(html)           # JSON bloku yoxdur — regex ehtiyatı
    assert m and m["title"] == "Sevgi" and m["author"] == "Rəsul" and m["play_url"] == "https://cdn.x/a.mp3"


def test_tiktok_group_link_modes(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from aiogram import Dispatcher
    import bot.core.group_panel as gp

    dp = Dispatcher()
    ctx = MagicMock()
    ctx.dp = dp
    ctx.creator_id = 999
    ctx.test_mp3_hook = None
    tt.setup(ctx)

    # Find the tt_group handler from dp
    tt_group_handler = None
    tt_confirm_cb = None
    for h in dp.message.handlers:
        if getattr(h.callback, "__name__", "") == "tt_group":
            tt_group_handler = h.callback
    for h in dp.callback_query.handlers:
        if getattr(h.callback, "__name__", "") == "group_tt_confirm_callback":
            tt_confirm_cb = h.callback

    assert tt_group_handler is not None
    assert tt_confirm_cb is not None

    msg = MagicMock()
    msg.text = "bax https://vm.tiktok.com/ZMabc123/"
    msg.caption = None
    msg.chat.id = -10012345
    msg.chat.type = "supergroup"
    msg.from_user.id = 42
    msg.from_user.mention_html.return_value = "User42"
    msg.reply = AsyncMock()

    # 1. Mode: "off" -> Should do nothing
    monkeypatch.setattr(gp, "get_group_link_mode", lambda cid: "off")
    asyncio.run(tt_group_handler(msg))
    msg.reply.assert_not_called()

    # 2. Mode: "confirm" -> Should send confirmation message with buttons
    monkeypatch.setattr(gp, "get_group_link_mode", lambda cid: "confirm")
    asyncio.run(tt_group_handler(msg))
    assert msg.reply.called
    args, kwargs = msg.reply.call_args
    assert "Shazam" in args[0] or "TikTok" in args[0]
    kb = kwargs.get("reply_markup")
    assert kb is not None
    assert any("tt_lk:dl:" in b.callback_data for row in kb.inline_keyboard for b in row)

    # 3. Callback "no" -> Cancels and deletes message
    cb_no = MagicMock()
    # Find token from inline keyboard callback data
    token = kb.inline_keyboard[0][0].callback_data.split(":")[-1]
    cb_no.data = f"tt_lk:no:{token}"
    cb_no.from_user.id = 42
    cb_no.answer = AsyncMock()
    cb_no.message.delete = AsyncMock()
    asyncio.run(tt_confirm_cb(cb_no))
    assert cb_no.message.delete.called

