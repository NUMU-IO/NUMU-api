"""The referral programme pays once per milestone, and only for real ones.

The expensive failure here is paying twice: accrual runs from the activation
handler, the milestone handler, a lead update and an admin recalculate,
because a milestone can be reached through any of them. These tests pin the
rules that stop the same first order from being paid four times.
"""

import pytest

from src.application.services import referral_service


def test_every_milestone_reads_a_real_lead_column():
    """A milestone whose column does not exist would silently never pay.

    The conditions are code, and the columns they read are fill-only
    timestamps on `merchant_leads`. A typo here produces a reward the platform
    promises and never awards, which is worse than not offering it.
    """
    from src.infrastructure.database.models.public.merchant_lead import (
        MerchantLeadModel,
    )

    columns = set(MerchantLeadModel.__table__.c.keys())
    for milestone in referral_service.MILESTONES:
        assert milestone.column in columns, (
            f"{milestone.key} reads {milestone.column!r}, which merchant_leads "
            "does not have"
        )


def test_milestone_keys_are_unique_and_indexed():
    keys = [m.key for m in referral_service.MILESTONES]
    assert len(keys) == len(set(keys))
    assert set(referral_service.MILESTONES_BY_KEY) == set(keys)


def test_the_seeded_amounts_cover_every_declared_milestone():
    """A milestone with no seeded row reads as 0 and inactive — it must be a
    deliberate choice, not a migration that forgot one."""
    import re
    from pathlib import Path

    migration = (
        Path(__file__).resolve().parents[3]
        / "alembic"
        / "versions"
        / "20260909_marketing_and_referrals.py"
    ).read_text(encoding="utf-8")
    seeded = set(re.findall(r'\("(referred_[a-z_]+)",\s*\d+', migration))
    declared = {m.key for m in referral_service.MILESTONES}
    assert declared == seeded, f"not seeded: {declared - seeded}"


def test_referral_codes_avoid_ambiguous_characters():
    """A code is read out loud and typed into a phone. O/0 and I/1/l cost a
    reward and the merchant's trust in the programme."""
    for banned in "O0I1l":
        assert banned not in referral_service._CODE_ALPHABET

    codes = {referral_service.generate_referral_code() for _ in range(200)}
    assert len(codes) == 200, "codes collided in 200 draws"
    for code in codes:
        assert len(code) == referral_service._CODE_LENGTH
        assert code.isalnum() and code.isupper()


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/signup?ref=6P73XBJ5", "6P73XBJ5"),
        ("/signup?utm_source=tiktok&ref=abc123", "ABC123"),
        # Lowercase in the URL, uppercase in the column: codes are minted
        # uppercase, so a link someone lowercased must still match.
        ("/signup?ref=6p73xbj5", "6P73XBJ5"),
        ("/signup", None),
        ("", None),
        # Too short to be one of ours — better no attribution than a wrong one.
        ("/signup?ref=ab", None),
    ],
)
def test_referral_code_is_read_out_of_the_landing_path(path, expected):
    """The link is `/signup?ref=CODE` and the landing path is already captured
    for every lead, so attribution needs no new field anywhere."""
    from src.application.services.merchant_leads import (
        Attribution,
        _referral_code_from,
    )

    assert _referral_code_from(None, Attribution(landing_path=path)) == expected


def test_an_explicit_code_beats_the_landing_path():
    from src.application.services.merchant_leads import (
        Attribution,
        _referral_code_from,
    )

    assert (
        _referral_code_from("explicit1", Attribution(landing_path="/signup?ref=other1"))
        == "EXPLICIT1"
    )


def test_placeholders_render_and_unknown_ones_survive():
    """Blanking an unknown placeholder is how a merchant receives "Hi ,".
    Leaving it visible means the operator sees the typo in the preview."""
    from src.api.v1.routes.admin.marketing import render

    out = render(
        "Hi {{name}}, your link is {{referral_link}}. {{not_a_variable}}",
        {"name": "Hassan", "referral_link": "https://numueg.app/signup?ref=ABC"},
    )
    assert out == (
        "Hi Hassan, your link is https://numueg.app/signup?ref=ABC. {{not_a_variable}}"
    )


def test_whatsapp_link_strips_the_number_and_escapes_the_message():
    """wa.me takes digits only, and the message has to survive the query
    string — an unescaped `&` truncates everything after it."""
    from src.api.v1.routes.admin.marketing import _whatsapp_link

    link = _whatsapp_link("+20 123 000-0731", "Hi & welcome, 50% off?")
    assert link.startswith("https://wa.me/201230000731?text=")
    assert "&" not in link.split("?text=")[1]
    assert "%26" in link and "%3F" in link
