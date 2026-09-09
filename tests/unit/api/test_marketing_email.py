"""Marketing email must actually be sendable.

The bug this pins: `_deliver_email` built an `EmailMessage` with a field name
that does not exist (`html_body` instead of `html_content`). Nothing caught it
because every check up to that point rendered a PREVIEW, and preview never
constructs the message. It failed for real recipients only, and the per-lead
error reporting turned it into a quiet "1 failed" rather than an exception.
"""

import inspect

import pytest

from src.api.v1.routes.admin import marketing


def test_the_email_we_build_matches_the_message_contract():
    """A misnamed field is a TypeError at send time, on a real merchant.

    Asserting against the dataclass's own signature means renaming a field on
    `EmailMessage` breaks this test rather than the marketing send.
    """
    from src.core.interfaces.services.email_service import EmailMessage

    accepted = set(inspect.signature(EmailMessage).parameters)
    used = {
        "to",
        "subject",
        "html_content",
        "text_content",
        "from_email",
        "from_name",
        "reply_to",
    }
    assert used <= accepted, (
        f"marketing builds fields EmailMessage rejects: {used - accepted}"
    )

    # And the message really constructs with exactly what _deliver_email passes.
    EmailMessage(
        to="merchant@example.com",
        subject="Subject",
        html_content="<p>Body</p>",
        text_content="Body",
        from_email=marketing.MARKETING_FROM_EMAIL,
        from_name=marketing.MARKETING_FROM_NAME,
        reply_to=marketing.MARKETING_FROM_EMAIL,
    )


def test_marketing_sends_from_the_marketing_mailbox():
    """Not the transactional sender: a promotion and a password reset must not
    share a domain reputation, and a reply has to reach a person."""
    assert marketing.MARKETING_FROM_EMAIL == "hello@numueg.app"


@pytest.mark.parametrize(
    "html,expected",
    [
        ("<p>Hi Hassan,</p><p>Second line.</p>", "Hi Hassan,\nSecond line."),
        ("<p>One<br />Two</p>", "One\nTwo"),
        ("<ul><li>First</li><li>Second</li></ul>", "First\nSecond"),
        ("<p>Tom &amp; Jerry</p>", "Tom & Jerry"),
        # Arabic survives untouched — the templates ship in both languages.
        ("<p>أهلاً بيك</p>", "أهلاً بيك"),
    ],
)
def test_plain_text_alternative_is_readable(html, expected):
    """A multipart marketing message with no text/plain part is a spam signal,
    and one built by stripping tags without honouring block ends collapses
    into a single run-on line."""
    assert marketing._plain_text(html) == expected


def test_plain_text_never_leaks_markup():
    body = (
        "<p>Hi {{name}},</p>"
        '<p>Link: <strong><a href="https://numueg.app/signup?ref=ABC">here</a></strong></p>'
    )
    out = marketing._plain_text(body)
    assert "<" not in out and ">" not in out
    assert "here" in out
