"""Which TikTok Events API answers are worth retrying.

TikTok reports failure differently from Meta: the transport succeeds, the HTTP
status is **200**, and the real outcome lives in a numeric ``code`` in the body
(``0`` means delivered). `tiktok_capi.py` knew that much — its own comment says
"TikTok answers 200 with a non-zero ``code`` on logical errors" — but then
treated *every* non-zero code as permanent and dropped the event without a
retry. A TikTok-side blip therefore looked exactly like a malformed payload.

That is the same shape as the Meta defect recorded in `remains.md` §5.1, where
throttling arrived as HTTP 400 and "4xx is permanent" discarded it. Both hide a
transient condition inside a status that reads like a verdict.

Deliberately reuses `FailureKind` from `meta_delivery_policy` rather than
declaring a parallel enum: one vocabulary across both vendors is what makes the
eventual shared module (remains.md R-10) a move rather than a merge. When that
lands, the enum moves to a neutral module and this file keeps only the TikTok
code table.
"""

from __future__ import annotations

import re
from typing import Any

from src.core.services.meta_delivery_policy import FailureKind

# TikTok's server-error family. Documented as 5xxxx in the Business API error
# taxonomy and unambiguously transient — the request never reached a verdict.
_SERVER_CODE_FLOOR = 50_000

# Auth and permission. Retrying cannot fix any of these; a human must
# reconnect the account or grant the scope.
#   40001  app not authorised for this advertiser / resource
#   40002  token sees the advertiser but lacks resource-level permission
#   40105  access-token problem
_CREDENTIAL_CODES: frozenset[int] = frozenset({40_001, 40_002, 40_105})

# ── The 40100 problem ─────────────────────────────────────────────────
# Sources disagree, and the disagreement is load-bearing:
#
#   * TikTok's own rate-limit documentation (business-api.tiktok.com,
#     /portal/docs/rate-limits/v1.3) states that once the limit is met "the
#     server returns code 40100" and the caller should wait — i.e. RETRYABLE.
#   * Several third-party error references describe 40100 as an invalid,
#     expired or revoked access token — i.e. PERMANENT.
#
# Both may be true: TikTok reuses codes across product surfaces, and the
# consumer developers.tiktok.com API has a different error space from the
# Business API this client talks to.
#
# Rather than bet the behaviour on a number whose meaning is contested, the
# message text decides, and the DEFAULT when it is silent is retryable. The
# asymmetry is deliberate:
#
#   wrongly retryable  -> a dead token is retried 12 times over ~17h, then
#                         dead-letters. Bounded, visible, costs nothing real.
#   wrongly permanent  -> a throttled event is dropped for good. That is the
#                         exact bug this module exists to remove.
_AMBIGUOUS_CODES: frozenset[int] = frozenset({40_100})

_CREDENTIAL_WORDS = re.compile(
    r"token|auth|expired|revoked|permission|scope|credential|unauthor",
    re.I,
)
_THROTTLE_WORDS = re.compile(r"rate|limit|throttl|qps|qpm|qpd|frequen|too many", re.I)


def _message_of(body: Any) -> str:
    if not isinstance(body, dict):
        return ""
    for key in ("message", "msg", "error_message"):
        value = body.get(key)
        if isinstance(value, str):
            return value
    return ""


def classify_response(
    http_status: int,
    code: int | None,
    body: Any = None,
) -> FailureKind | None:
    """Classify one Events API answer. ``None`` means delivered.

    Success is ``HTTP 2xx`` **and** ``code == 0`` — external-contracts.md row 5.
    A 2xx with a non-zero code is a failure that merely looks like a success.
    """
    if 200 <= http_status < 300 and code == 0:
        return None

    # Transport-level signals first: unambiguous, and TikTok does use them.
    if http_status == 429:
        return FailureKind.RATE_LIMITED
    if http_status >= 500:
        return FailureKind.SERVER_ERROR

    message = _message_of(body)

    if code is not None:
        if code >= _SERVER_CODE_FLOOR:
            return FailureKind.SERVER_ERROR
        if code in _CREDENTIAL_CODES:
            return FailureKind.INVALID_CREDENTIALS
        if code in _AMBIGUOUS_CODES:
            if _CREDENTIAL_WORDS.search(message):
                return FailureKind.INVALID_CREDENTIALS
            return FailureKind.RATE_LIMITED

    # No code to go on (unparseable body, proxy HTML). Let the words decide
    # before falling back to "we built something wrong".
    if _THROTTLE_WORDS.search(message):
        return FailureKind.RATE_LIMITED
    if _CREDENTIAL_WORDS.search(message):
        return FailureKind.INVALID_CREDENTIALS

    if http_status in (401, 403):
        return FailureKind.INVALID_CREDENTIALS

    return FailureKind.INVALID_PAYLOAD
