from src.infrastructure.external_services.resend.email_templates.onboarding import (
    WELCOME_TEMPLATE,
)

welcome_html = WELCOME_TEMPLATE["html_fn"]


def test_arabic_welcome_names_the_store_and_its_link():
    html = welcome_html(
        "سارة",
        "https://merchant.numueg.app",
        language="ar",
        store_name="بيت الخزف",
        store_url="https://beit-el-khazaf.numueg.app",
    )
    assert "أهلاً يا سارة!" in html
    assert "متجرك «بيت الخزف» جاهز على" in html
    assert ">beit-el-khazaf.numueg.app</a>" in html
    assert 'href="https://merchant.numueg.app/products/new"' in html
    assert "أسرع منصة" not in html


def test_english_welcome_and_html_is_escaped():
    html = welcome_html(
        "<b>Sam</b>",
        "https://merchant.numueg.app/",
        language="en",
        store_name="Tom & Jerry",
        store_url="https://shop.example.com",
    )
    assert "Hi &lt;b&gt;Sam&lt;/b&gt;!" in html
    assert "Your store “Tom &amp; Jerry” is ready at" in html
    assert 'href="https://merchant.numueg.app/products/new"' in html


def test_welcome_without_store_details_still_renders():
    html = welcome_html("", "https://merchant.numueg.app", language="ar")
    assert "أهلاً!" in html
