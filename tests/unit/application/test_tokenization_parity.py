"""Cross-repo tokenization parity (P1-7.5 — the NUMU→Trust-Network contribution feed).

NUMU's ``phone_hash`` MUST be byte-identical to the Trust Network's token so that, with
a shared salt (TN ``TN_NETWORK_TOKEN_KEY`` = NUMU ``PLATFORM_SECRET_SALT``), NUMU's
existing ``phone_hash`` values *are* network tokens and the contribution feed joins TN's
decision-time lookups.

This golden table is the **identical copy** carried by the Trust Network repo
(``services/api/tests/test_tokenization_parity.py``). If either repo's normalization
drifts, its copy fails — so the two cannot silently diverge. Regenerate only
intentionally, and update both repos together.
"""

from __future__ import annotations

from src.application.use_cases.shopify.phone_hash import normalize_and_hash

# Fixed, documented, NOT a real secret. Both repos pin this exact salt + table.
PARITY_SALT = "numu-tn-parity-golden-v1"

# phone input -> expected 64-hex token (or None when unnormalizable).
GOLDEN: dict[str, str | None] = {
    "01012345678": "c5388a6ed48e875320d28dce4e841a7e8f8a1339a9dafe7c6f11ee03a9daab0f",
    "01112345678": "78760b7ca88b4a271054990f9e3b18fdc7d5ade43f52837ef0d1e384dfc6cc3a",
    "01212345678": "aa172dd2046b1be65a66483aa1fe5f0d2c706055aebc0f9c3ee9fa6667f53dd6",
    "01512345678": "2884c1efe748d28290fc14d2966d5f726faf019c551e5422c3a8c8383deb9266",
    "+201012345678": "c5388a6ed48e875320d28dce4e841a7e8f8a1339a9dafe7c6f11ee03a9daab0f",
    "201012345678": "c5388a6ed48e875320d28dce4e841a7e8f8a1339a9dafe7c6f11ee03a9daab0f",
    "010 1234 5678": "c5388a6ed48e875320d28dce4e841a7e8f8a1339a9dafe7c6f11ee03a9daab0f",
    "(010)-1234-5678": "c5388a6ed48e875320d28dce4e841a7e8f8a1339a9dafe7c6f11ee03a9daab0f",
    "+201000000001": "69236ec358721d1c68e58783a952d9ea9d66708772af4a038745768c60157052",
    "+201000000666": "3d984ff4dce32dd82d43a86451f8bca3804384c1e0754ca27d302ce58e0ead59",
    "+201000000999": "4cf629cd2506c080aff00a0cbe4a224c8033c6c087d9af17261f1def1d8c30b4",
    "0501234567": None,
    "0111234": None,
    "not-a-phone": None,
    "": None,
}


def test_phone_hash_matches_golden_vectors() -> None:
    """NUMU's tokenization matches the pinned cross-repo golden table byte-for-byte."""
    for phone, expected in GOLDEN.items():
        assert normalize_and_hash(phone, PARITY_SALT) == expected, f"drift on {phone!r}"


def test_input_formats_collapse_to_one_token() -> None:
    """0X / +20X / 20X / spaced / punctuated all normalize to the same token."""
    forms = [
        "01012345678",
        "+201012345678",
        "201012345678",
        "010 1234 5678",
        "(010)-1234-5678",
    ]
    tokens = {normalize_and_hash(f, PARITY_SALT) for f in forms}
    assert tokens == {GOLDEN["01012345678"]}
