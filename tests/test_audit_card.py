"""Audit log mesajının formatı (acard)."""


def test_card_is_monospace_block_with_clickable_users(logger_mod):
    text = logger_mod.acard([
        ("# Silinən Mesaj", ""),
        ("Qrup", "Test (-1001)"),
        ("Müəllif", logger_mod.U("Əli <b>", 123, "ali_x")),
        ("Mətn", "a<b>&c"),
    ])
    assert text.startswith("<pre>")
    assert "Silinən Mesaj" in text and "a&lt;b&gt;&amp;c" in text
    assert '<a href="tg://user?id=123">Əli &lt;b&gt;</a> @ali_x' in text     # blokun altında, kliklənən
    assert "Müəllif" not in text.split("</pre>")[0]


def test_user_without_username_shows_id(logger_mod):
    html = logger_mod.U("Vəli", 5, None).html()
    assert "tg://user?id=5" in html and "<code>5</code>" in html


def test_long_text_is_wrapped_and_aligned(logger_mod):
    text = logger_mod.acard([("# T", ""), ("Mətn", "salam " * 20)])
    body = text.split("<pre>")[1].split("</pre>")[0].splitlines()
    assert all(len(line) <= 44 for line in body)
    assert body[2].startswith(" " * 6)                          # davam sətri girintili


def test_card_follows_ui_style(logger_mod, monkeypatch):
    import common.menu as menu

    monkeypatch.setattr(menu, "_UI_STYLE_OVERRIDE", "quote")
    text = logger_mod.acard([("# T", ""), ("A", "b")])
    assert text.startswith("<blockquote><code>")
