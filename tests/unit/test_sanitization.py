from src.api.dependencies.sanitization import strip_html_tags


def test_strips_closed_and_unclosed_tags():
    assert strip_html_tags("12 Tahrir St<b>, Cairo</b>") == "12 Tahrir St, Cairo"
    assert strip_html_tags("12 Tahrir St <img src=x onerror=alert(1)") == "12 Tahrir St"
    assert strip_html_tags("Nasr City </p><svg onload=alert(1)") == "Nasr City"


def test_keeps_text_that_cannot_open_a_tag():
    assert strip_html_tags("price < 100 and > 50") == "price < 100 and > 50"
    assert strip_html_tags("شارع 9 < المعادي") == "شارع 9 < المعادي"
