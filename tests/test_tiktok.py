from bot.plugins import tiktok_plugin as tt


def test_find_url():
    assert tt.find_url("bax https://vm.tiktok.com/ZMabc123/ çox yaxşı") == "https://vm.tiktok.com/ZMabc123/"
    assert tt.find_url("https://www.tiktok.com/@u/video/7123456789?lang=en").startswith("https://www.tiktok.com/@u")
    assert tt.find_url("https://youtube.com/watch?v=abcdefghijk") is None


def test_generic_sound_detection():
    assert tt.is_generic_sound("original sound - user123", "user123")
    assert tt.is_generic_sound("оригинальный звук - Вася")
    assert tt.is_generic_sound("")
    assert not tt.is_generic_sound("Medcezir")


def test_info_to_query():
    assert tt.info_to_query({"track": "Med Cezir", "artist": "Ceza"}) == ("Ceza", "Med Cezir")
    assert tt.info_to_query({"track": "original sound - ali", "uploader": "ali"}) is None
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
