"""Multi-provider OCR for InstaPay payment-proof screenshots.

Three real providers + a noop, all behind one interface:

  * :class:`GoogleVisionProofService` — Google Cloud Vision
    ``DOCUMENT_TEXT_DETECTION``. Paid, strong Arabic + English,
    fastest p95 by far. Auth via API key.
  * :class:`DeepSeekHFProofService` — DeepSeek-OCR (3B, MIT) wrapped
    by ``merterbak/DeepSeek-OCR-Demo`` on HuggingFace Spaces. Free,
    Latin-only in published benchmarks, cold-starts after inactivity.
  * :class:`GlmHFProofService` — GLM-4.5V (106B, MIT) wrapped by
    ``prithivMLmods/GLM-OCR-Demo``. Free, similar Arabic gap, heavier
    model, slightly broader.
  * :class:`NoopProofVisionService` — returns ``status="skipped"``;
    the default when no provider is configured for a store.

Soft-fail by contract: every method returns a ``ProofVisionResult``
even on transport / parsing / auth errors. The auto-approval engine
treats ``status != "ok"`` as "no signal", preserving the pre-Phase-C
behaviour exactly.

Per-merchant routing happens upstream: see
``get_proof_vision_service_for_store`` in
:mod:`src.api.dependencies.services`.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import unicodedata
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

import httpx

logger = logging.getLogger(__name__)


# Cap raw_text length so a malicious or pathological image (deep DCT
# texture, tiled receipts, etc.) can't blow up the proofs row size.
# 4 KB is well above any real bank-app receipt.
_RAW_TEXT_MAX_CHARS = 4096


# Recognised IPA bank suffixes (right-hand side of ``user@bank``).
# Lowercase, hyphenated where the IPA scheme uses them. Keep in sync
# with the merchant's allowed-IPA registration domain list — but
# this set is for *parsing* OCR text, not for security: the trust
# boundary is the auto-approval rule (extracted_ipa == merchant_ipa),
# not which suffix we matched on.
_IPA_BANK_SUFFIXES = frozenset({
    "instapay",
    "cib",
    "aaib",
    "qnb",
    "nbe",
    "bm",  # Banque Misr
    "bdc",  # Banque du Caire
    "alex",  # Bank of Alexandria
    "hsbc",
    "hdb",
    "adib",  # Abu Dhabi Islamic Bank Egypt
    "fab",  # First Abu Dhabi Bank
    "credit",  # Crédit Agricole Egypt
    "scb",  # Standard Chartered
    "ahli",  # Al Ahli Bank
    "midbank",
    "blom",
    "audi",
    "emirates",
    "egbank",
    "cae",  # Crédit Agricole Egypt sometimes uses .cae
})


# ── Result type ──────────────────────────────────────────────────────


# Specific failure discriminators stamped into ``ocr_status`` so the
# merchant review pane can render a useful message instead of a flat
# "failed". Auto-approval rules only fire on ``ok`` regardless of
# which ``failed_*`` value is set, so existing semantics carry over.
ProofVisionStatus = Literal[
    "ok",
    "skipped",
    "failed",  # generic / unknown — keep as last-resort bucket
    "failed_gpu",  # HF ZeroGPU starvation (the queue gave up)
    "failed_timeout",  # our wall-clock ceiling fired
    "failed_auth",  # provider rejected our credentials / API key
    "failed_transport",  # network-level error before a response
    "failed_parse",  # response decoded but had no usable text shape
    "failed_empty",  # provider returned but the text is empty
]


# Substrings matched against a stringified exception from the HF call
# path. We treat presence of any one as evidence the failure was
# GPU-side rather than ours, so the merchant sees "the OCR engine is
# busy, try again" instead of a generic "couldn't read".
_HF_GPU_STARVATION_HINTS = (
    "no gpu was available",
    "no gpu available",
    "zerogpu queue",
    "gpu task aborted",
)


@dataclass
class ProofVisionResult:
    """Outcome of running OCR on a sanitized proof image.

    ``status`` carries either ``ok`` / ``skipped`` or one of the
    ``failed_*`` discriminators (see :data:`ProofVisionStatus`). The
    auto-approval rules treat any non-``ok`` value as "no signal", so
    the discriminators are purely cosmetic from the rules' POV — they
    exist so the merchant review pane can render a meaningful message
    when a provider is unhappy.
    """

    status: ProofVisionStatus
    provider: str
    processed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    extracted_amount_cents: int | None = None
    extracted_ipa: str | None = None
    raw_text: str = ""
    # Phase C extras — populated by the same parsers as
    # ``extracted_amount_cents`` / ``extracted_ipa``. Each has its
    # own opt-in auto-approval rule on top.
    extracted_note: str | None = None
    extracted_transaction_ref: str | None = None
    extracted_recipient_name: str | None = None

    @classmethod
    def skipped(cls, provider: str = "none") -> ProofVisionResult:
        return cls(status="skipped", provider=provider)

    @classmethod
    def failed(
        cls,
        provider: str,
        reason: str = "",
        *,
        kind: ProofVisionStatus = "failed",
    ) -> ProofVisionResult:
        # ``kind`` lets the caller pick a specific discriminator;
        # ``reason`` is a free-form diagnostic that goes to logs only
        # (kept off the proof row to avoid growing the schema for
        # debug data).
        if reason:
            logger.info(
                "proof_ocr_failed",
                extra={"provider": provider, "kind": kind, "reason": reason},
            )
        return cls(status=kind, provider=provider)


# ── Pure parsers ─────────────────────────────────────────────────────


# Map Arabic-Indic + Eastern Arabic-Indic digits to ASCII digits.
# Only runs over the OCR text before the amount regex so a bank app
# rendering ``٥٠٠ ج.م`` parses the same as ``500 EGP``. Also folds the
# Arabic decimal/thousands separators (U+066B / U+066C) to ASCII so
# ``٥٠٠٫٠٠`` reaches the regex as ``500.00`` and the existing
# ``[.,]`` decimal class matches.
_DIGIT_FOLD = {ord(c): chr(ord("0") + i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")}
_DIGIT_FOLD.update({ord(c): chr(ord("0") + i) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹")})
_DIGIT_FOLD[ord("٫")] = "."  # Arabic decimal separator → "."
_DIGIT_FOLD[ord("٬")] = ","  # Arabic thousands separator → ","


# A real bank-app receipt typically shows the amount in one of two
# orderings: ``500.00 EGP`` (digits first) or ``EGP 500.00`` (currency
# token first — InstaPay's own UI uses this). We compile both forms;
# either matches and the largest hit wins. Also covers the Arabic
# variants ``ج.م``, ``ج م``, ``جنيه`` in either ordering.
_CURRENCY_ALT = r"(?:EGP|egp|ج\.?م|جنيه)"
_AMOUNT_BODY = r"\d{1,3}(?:[,.\s]\d{3})*|\d+"

_AMOUNT_RE_TRAILING = re.compile(
    rf"""
    (?P<int>{_AMOUNT_BODY})
    (?:[.,](?P<frac>\d{{1,2}}))?
    \s*
    {_CURRENCY_ALT}
    """,
    re.VERBOSE,
)
_AMOUNT_RE_LEADING = re.compile(
    rf"""
    {_CURRENCY_ALT}
    \s*
    (?P<int>{_AMOUNT_BODY})
    (?:[.,](?P<frac>\d{{1,2}}))?
    """,
    re.VERBOSE,
)


def parse_amount_egp(raw: str) -> int | None:
    """Extract an EGP amount in **cents** from OCR text.

    Returns ``None`` when no amount is visible. Picks the largest
    plausible match — bank-app screenshots show the headline amount
    in larger type, and the OCR usually surfaces it first/loudest.
    Caller compares to ``order.total`` with a BPS tolerance so this
    just needs to be in the right ballpark.

    Supports ASCII digits, Arabic-Indic (``٠-٩``), and Eastern
    Arabic-Indic (``۰-۹``) — many Egyptian bank apps render Arabic
    digits even with English UI.
    """
    if not raw:
        return None
    folded = raw.translate(_DIGIT_FOLD)
    folded = unicodedata.normalize("NFKC", folded)

    best_cents: int | None = None
    # Scan both orderings (digits-first and currency-first) and pick
    # the largest plausible amount across all hits. Looping over a
    # tuple keeps the two patterns symmetric — neither preempts the
    # other.
    for pattern in (_AMOUNT_RE_TRAILING, _AMOUNT_RE_LEADING):
        for match in pattern.finditer(folded):
            int_part_raw = match.group("int")
            frac_part = match.group("frac") or "00"
            # Strip thousands separators (any of ``,``, ``.`` between
            # 3-digit groups, or whitespace). The regex already
            # required the [,.\s]\d{3} pattern so we can safely drop
            # these.
            int_part = re.sub(r"[,.\s]", "", int_part_raw)
            try:
                cents = int(int_part) * 100 + int(frac_part.ljust(2, "0")[:2])
            except ValueError:
                continue
            # Cap at 1 million EGP (100M cents). Anything larger is
            # almost certainly an OCR artefact (a transaction
            # reference being mistaken for the amount).
            if cents > 100_000_000:
                continue
            if best_cents is None or cents > best_cents:
                best_cents = cents
    return best_cents


# IPA pattern: ``user@bank``. ``bank`` must be in the registered set —
# guards against parsing customer email addresses or "From: foo@bar"
# strings as IPAs.
_IPA_RE = re.compile(r"([A-Za-z0-9._-]{2,})@([A-Za-z][A-Za-z0-9-]{1,30})")

# Section anchors used to classify IPAs as sender-side vs recipient-side.
# Bank-app receipts almost universally label the two sections; using
# the closest-preceding anchor before each IPA hit lets us skip the
# customer's own IPA (which would otherwise trigger a false-positive
# ``ocr_ipa_mismatch`` against the merchant's stored IPA).
_FROM_RE = re.compile(r"\b(?:from|sender)\b|من\s|المرسل", re.IGNORECASE)
_TO_RE = re.compile(r"\b(?:to|recipient|recipient's)\b|إلى\s|المستلم", re.IGNORECASE)


def parse_ipa(raw: str) -> str | None:
    """Extract a recipient InstaPay address from OCR text.

    Bank-app receipts typically show two IPAs: the sender's (the
    customer's, under "From") and — when not masked — the
    recipient's (the merchant's, under "To"). For the OCR-IPA-match
    rule we need the *recipient*; returning the sender's IPA would
    false-positive on every Arab Bank receipt where the recipient
    is masked.

    Heuristic: for each candidate IPA, find the closest preceding
    "From" / "To" anchor in the OCR text. Skip any IPA whose closest
    anchor is "From". Return the first remaining IPA (closest-anchor
    ``To`` or no preceding anchor at all). When only sender IPAs are
    visible, return ``None`` — the rule then silently no-ops, which
    is the right outcome for unverifiable proofs.
    """
    if not raw:
        return None

    from_anchors = [m.start() for m in _FROM_RE.finditer(raw)]
    to_anchors = [m.start() for m in _TO_RE.finditer(raw)]

    for match in _IPA_RE.finditer(raw):
        bank = match.group(2).lower()
        if bank not in _IPA_BANK_SUFFIXES:
            continue
        ipa = f"{match.group(1).lower()}@{bank}"

        pos = match.start()
        last_from = max((p for p in from_anchors if p < pos), default=-1)
        last_to = max((p for p in to_anchors if p < pos), default=-1)

        # In a "To" section (last anchor before this IPA is a "To") →
        # take it. No anchors at all → no way to classify, treat as
        # recipient (matches pre-fix behaviour for documents without
        # From/To labelling). Otherwise keep scanning — there might
        # be a recipient match further down.
        if last_to > last_from:
            return ipa
        if last_from < 0 and last_to < 0:
            return ipa

    # All hits were sender-side. ``None`` lets the rule no-op rather
    # than mismatching the customer against the merchant.
    return None


_NOTE_RE = re.compile(
    r"(?:^|\n)\s*(?:Note|Reason|Memo|ملاحظة|السبب)\b[\s:.\-]*(.+?)(?=\n\s*"
    r"(?:Reference|Date|From|To|Note|Account|POWERED|Status|Amount|Transfer)"
    r"\b|$)",
    re.IGNORECASE | re.DOTALL,
)


def parse_note(raw: str) -> str | None:
    """Extract the bank app's "Note" / "Reason" / "Memo" field from OCR text.

    Egyptian bank apps almost universally label this section in
    English (``Note``) even on Arabic UIs; the Arabic alternates
    (``ملاحظة``, ``السبب``) appear in some apps. Captures everything
    after the label up to the next major section anchor (Reference,
    Date, etc.) or end of text. Trimmed; ``None`` when no label is
    visible at all.

    Used by the ``ocr_note_missing_reference`` rule to verify the
    customer typed our intent reference into the bank-app's note —
    the strongest single fraud signal because a fraudster physically
    can't have typed our short-lived per-order code into someone
    else's transfer.
    """
    if not raw:
        return None
    match = _NOTE_RE.search(raw)
    if not match:
        return None
    note = match.group(1).strip()
    # Cap at 200 chars — bank notes are short by design and a
    # runaway capture (label appears at end of doc → grabs noise) is
    # bounded usefully. Also strips trailing newlines.
    return note[:200] or None


_TRANSACTION_REF_LABEL_RE = re.compile(
    r"\b(?:Reference|Ref|الرقم المرجعي|رقم العملية)\b",
    re.IGNORECASE,
)
_TRANSACTION_REF_DIGITS_RE = re.compile(r"\b(\d{8,20})\b")


def parse_transaction_ref(raw: str) -> str | None:
    """Extract the bank's transaction reference number from OCR text.

    Anchored to the ``Reference`` / ``Ref`` label so an 11-digit
    phone number on the receipt isn't misread as the ref. Two-step
    scan because Google Vision frequently groups labels first and
    values after (column-major output for table-like layouts), so
    requiring the digit run on the *same line* as the label misses
    real receipts. We accept any 8–20-digit run within ~200 chars
    after the label — far enough to skip past the few intermediate
    label rows Vision sometimes emits, short enough that an unrelated
    phone number elsewhere in the receipt doesn't slip in.
    """
    if not raw:
        return None
    label_match = _TRANSACTION_REF_LABEL_RE.search(raw)
    if not label_match:
        return None
    window = raw[label_match.end() : label_match.end() + 200]
    digit_match = _TRANSACTION_REF_DIGITS_RE.search(window)
    return digit_match.group(1) if digit_match else None


# Recipient block: text after a "To" anchor up to the next major
# section. Bank apps mask all but the first visible characters of
# the recipient's name; we capture the whole block as-is for
# forensics. The name-token rule does substring matching against a
# merchant-supplied token (typically the merchant's first name).
_RECIPIENT_BLOCK_RE = re.compile(
    r"(?:^|\n)\s*(?:To|Recipient|إلى|المستلم)\b[\s:.\-]*(.+?)(?=\n\s*"
    r"(?:Reference|Date|From|Note|Account|POWERED|Status)\b|$)",
    re.IGNORECASE | re.DOTALL,
)


def parse_recipient_name(raw: str) -> str | None:
    """Extract the recipient block (between "To" anchor and next section).

    Returns multi-line text since bank apps split recipient name
    across the IPA and a phone line. The merchant's name-token rule
    does case-insensitive substring matching, so getting the *whole*
    block keeps it robust against where the OCR breaks lines.
    """
    if not raw:
        return None
    match = _RECIPIENT_BLOCK_RE.search(raw)
    if not match:
        return None
    name = match.group(1).strip()
    # Same 200-char cap as the note field; recipient blocks are
    # always short in practice.
    return name[:200] or None


def _trim_raw_text(text: str) -> str:
    """Keep ``raw_text`` under the row-storage cap.

    Truncation appends an ellipsis-ish marker so the merchant
    review pane shows that text was clipped rather than silently
    delivering a partial document.
    """
    if len(text) <= _RAW_TEXT_MAX_CHARS:
        return text
    return text[: _RAW_TEXT_MAX_CHARS - 3] + "..."


# ── Interface ────────────────────────────────────────────────────────


class IProofVisionService(ABC):
    """Provider-agnostic OCR contract for InstaPay payment proofs."""

    @abstractmethod
    async def extract(
        self,
        image_bytes: bytes,
        *,
        hint_currency: str = "EGP",
    ) -> ProofVisionResult:
        """Run OCR on the sanitized image; never raise for provider faults."""


# ── Noop ─────────────────────────────────────────────────────────────


class NoopProofVisionService(IProofVisionService):
    """Returns ``status="skipped"`` — used when the store has no provider.

    Existence of this impl means the call site doesn't need a null
    check around ``vision.extract(...)``; it always returns a result.
    """

    PROVIDER_NAME = "none"

    async def extract(
        self,
        image_bytes: bytes,
        *,
        hint_currency: str = "EGP",
    ) -> ProofVisionResult:
        return ProofVisionResult.skipped(provider=self.PROVIDER_NAME)


# ── Google Vision ────────────────────────────────────────────────────


class GoogleVisionProofService(IProofVisionService):
    """Google Cloud Vision via the ``images:annotate`` REST endpoint.

    Auth: API key (``settings.google_vision_api_key``). Service-account
    JSON is the longer-term option but pulls ``google-auth`` for JWT
    signing — adopt when paid traffic justifies the extra dep.

    Soft-fail on every error path. Latency budget 1.5s; the Vision
    p95 on small-to-medium screenshots is well under that.
    """

    PROVIDER_NAME = "google_vision"
    _ENDPOINT = "https://vision.googleapis.com/v1/images:annotate"
    # Higher than Vision's documented p95 (~500ms on small images) to
    # absorb Egypt → nearest-Google-region RTT plus the base64 upload
    # body (~100KB after sanitisation). The proof-submit handler is
    # already a multi-second async call; tightening this further
    # would just turn legitimate slow paths into ``failed_timeout``.
    _TIMEOUT_SECONDS = 5.0

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("Google Vision API key is required")
        self._api_key = api_key

    async def extract(
        self,
        image_bytes: bytes,
        *,
        hint_currency: str = "EGP",
    ) -> ProofVisionResult:
        body = {
            "requests": [
                {
                    "image": {"content": base64.b64encode(image_bytes).decode()},
                    "features": [{"type": "DOCUMENT_TEXT_DETECTION", "maxResults": 1}],
                    # Hinting Arabic + English bumps Vision's Arabic
                    # accuracy materially without disabling Latin
                    # fallback. Order matters: ar first.
                    "imageContext": {"languageHints": ["ar", "en"]},
                }
            ]
        }
        try:
            async with httpx.AsyncClient(timeout=self._TIMEOUT_SECONDS) as client:
                resp = await client.post(
                    self._ENDPOINT,
                    params={"key": self._api_key},
                    json=body,
                )
        except httpx.TimeoutException:
            return ProofVisionResult.failed(
                self.PROVIDER_NAME, reason="timeout", kind="failed_timeout"
            )
        except httpx.HTTPError as exc:
            return ProofVisionResult.failed(
                self.PROVIDER_NAME,
                reason=f"transport: {exc}",
                kind="failed_transport",
            )

        if resp.status_code in (401, 403):
            return ProofVisionResult.failed(
                self.PROVIDER_NAME,
                reason=f"http {resp.status_code}",
                kind="failed_auth",
            )
        if resp.status_code >= 400:
            return ProofVisionResult.failed(
                self.PROVIDER_NAME,
                reason=f"http {resp.status_code}",
                kind="failed_transport",
            )

        try:
            payload = resp.json()
            text = (
                payload.get("responses", [{}])[0]
                .get("fullTextAnnotation", {})
                .get("text", "")
            )
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            return ProofVisionResult.failed(
                self.PROVIDER_NAME, reason=f"parse: {exc}", kind="failed_parse"
            )

        if not text:
            return ProofVisionResult.failed(
                self.PROVIDER_NAME, reason="empty text", kind="failed_empty"
            )

        return ProofVisionResult(
            status="ok",
            provider=self.PROVIDER_NAME,
            extracted_amount_cents=parse_amount_egp(text),
            extracted_ipa=parse_ipa(text),
            extracted_note=parse_note(text),
            extracted_transaction_ref=parse_transaction_ref(text),
            extracted_recipient_name=parse_recipient_name(text),
            raw_text=_trim_raw_text(text),
        )


# ── HuggingFace Space-backed providers ──────────────────────────────


class _HFProofServiceBase(IProofVisionService):
    """Common Gradio-Space wiring for the HF-backed OCR providers.

    Subclasses own the predict-call shape because the two Spaces we
    target expose materially different signatures (multi-arg
    ``/run`` for DeepSeek, single-arg ``/predict`` for GLM at the
    time of writing). The base handles:

      * lazy import of ``gradio_client`` (so the rest of the module
        works in environments without it),
      * temp-file lifecycle for the image bytes,
      * thread-pool dispatch + a hard timeout,
      * uniform soft-fail wrapping into ``ProofVisionResult.failed``.

    Cold starts on free Zero-GPU Spaces can be 30–60s; we cap at
    ``_TIMEOUT_SECONDS`` so a sleeping Space gracefully soft-fails
    on the first hit after sleep. The keep-warm Celery task pings
    the Space every 10 minutes to minimise cold starts in steady
    state — see :mod:`src.infrastructure.messaging.tasks.warm_hf_vision_spaces`.
    """

    PROVIDER_NAME: str
    _SPACE_ID: str
    # HF Spaces have two latency components: queue-wait (ZeroGPU
    # admits one tenant at a time, anonymous traffic last) plus the
    # ~1–3s actual inference. A 90s ceiling lets a logged-in
    # ($0 HF account) request clear the queue in normal load while
    # still bounding the worst case. Anonymous traffic regularly
    # hits HF's own 60s queue timeout and we soft-fail before this.
    _TIMEOUT_SECONDS = 90.0

    def __init__(self, hf_token: str | None = None) -> None:
        self._hf_token = hf_token

    async def extract(
        self,
        image_bytes: bytes,
        *,
        hint_currency: str = "EGP",
    ) -> ProofVisionResult:
        try:
            from gradio_client import Client, handle_file
        except ImportError:
            return ProofVisionResult.failed(
                self.PROVIDER_NAME, reason="gradio_client missing"
            )

        loop = asyncio.get_running_loop()
        try:
            text = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    self._call_space,
                    image_bytes,
                    Client,
                    handle_file,
                ),
                timeout=self._TIMEOUT_SECONDS,
            )
        except TimeoutError:
            return ProofVisionResult.failed(
                self.PROVIDER_NAME, reason="timeout", kind="failed_timeout"
            )
        except Exception as exc:  # noqa: BLE001 — soft-fail every provider error
            return ProofVisionResult.failed(
                self.PROVIDER_NAME,
                reason=f"call: {exc}",
                kind=_classify_hf_exception(exc),
            )

        if not isinstance(text, str) or not text.strip():
            return ProofVisionResult.failed(
                self.PROVIDER_NAME, reason="empty text", kind="failed_empty"
            )

        return ProofVisionResult(
            status="ok",
            provider=self.PROVIDER_NAME,
            extracted_amount_cents=parse_amount_egp(text),
            extracted_ipa=parse_ipa(text),
            extracted_note=parse_note(text),
            extracted_transaction_ref=parse_transaction_ref(text),
            extracted_recipient_name=parse_recipient_name(text),
            raw_text=_trim_raw_text(text),
        )

    def _call_space(
        self,
        image_bytes: bytes,
        client_cls: Any,
        handle_file: Any,
    ) -> str:
        """Sync Gradio call running in the executor thread.

        Subclasses implement :meth:`_predict` with the Space-specific
        signature; this method handles temp-file lifecycle and client
        construction. Returning an empty string from ``_predict`` is
        equivalent to "no text" — the caller maps that to ``failed``.
        """
        import tempfile
        from pathlib import Path

        # Pass the HF token when one's configured. Free accounts get
        # higher ZeroGPU queue priority than anonymous traffic — the
        # difference between regularly soft-failing and getting served.
        # ``gradio_client`` renamed this kwarg from ``hf_token`` to
        # ``token`` somewhere in the 2.x series; we depend on the
        # current name.
        client = (
            client_cls(self._SPACE_ID, token=self._hf_token)
            if self._hf_token
            else client_cls(self._SPACE_ID)
        )
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp.write(image_bytes)
            tmp_path = Path(tmp.name)
        try:
            return self._predict(client, handle_file, str(tmp_path))
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _predict(self, client: Any, handle_file: Any, image_path: str) -> str:
        """Subclass hook — make the actual ``client.predict(...)`` call."""
        del client, handle_file, image_path  # contract args; subclasses use them
        raise NotImplementedError


def _classify_hf_exception(exc: Exception) -> ProofVisionStatus:
    """Map a raw gradio_client exception to a specific failure kind.

    Falls back to the generic ``failed`` bucket so a future HF error
    string we haven't seen still surfaces as something rather than
    crashing here.
    """
    message = str(exc).lower()
    if any(hint in message for hint in _HF_GPU_STARVATION_HINTS):
        return "failed_gpu"
    if "timeout" in message or "timed out" in message:
        return "failed_timeout"
    if "401" in message or "403" in message or "unauthorized" in message:
        return "failed_auth"
    if "connect" in message or "network" in message or "dns" in message:
        return "failed_transport"
    return "failed"


def _coerce_to_text(result: Any) -> str:
    """Best-effort flatten of a Gradio predict result into a text blob.

    Spaces return strings, tuples, dicts (markdown blocks, gallery
    items), file paths, etc. We don't need a single canonical field —
    if the OCR'd amount + IPA show up in *any* string buried in the
    result, the parsers will find them. Joining all string-shaped
    leaves is the simplest, most resilient approach.
    """
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if isinstance(result, list | tuple):
        return "\n".join(_coerce_to_text(x) for x in result)
    if isinstance(result, dict):
        # Gallery items shape: {"image": ..., "caption": "..."}; we
        # only care about the textual ones.
        return "\n".join(
            _coerce_to_text(v) for v in result.values() if isinstance(v, str)
        )
    return str(result)


class DeepSeekHFProofService(_HFProofServiceBase):
    """``merterbak/DeepSeek-OCR-Demo``.

    The Space's ``/run`` endpoint takes five arguments — image,
    file_path, task, custom_prompt, page_num — and returns a
    five-tuple. We send the same image to both ``image`` and
    ``file_path`` so the Space's image-vs-PDF branch lands in the
    image lane, request the "Free OCR" task (raw text dump, no
    prompt), and flatten the entire return tuple via
    :func:`_coerce_to_text` rather than blessing one slot — the
    Space's tuple shape can change with a future commit and we want
    the parsers to find amount/IPA wherever they end up.
    """

    PROVIDER_NAME = "deepseek_hf"
    _SPACE_ID = "merterbak/DeepSeek-OCR-Demo"

    def _predict(self, client: Any, handle_file: Any, image_path: str) -> str:
        result = client.predict(
            image=handle_file(image_path),
            file_path=handle_file(image_path),
            task="📝 Free OCR",
            custom_prompt="",
            page_num=1,
            api_name="/run",
        )
        return _coerce_to_text(result)


class GlmHFProofService(_HFProofServiceBase):
    """``prithivMLmods/GLM-OCR-Demo``.

    The Space's exact predict signature isn't pinned in our docs —
    we attempt the most common shapes (``api_name="/predict"`` with
    a file arg first, then ``/run`` with the same DeepSeek-style
    multi-arg shape) and surface the first one that returns text.
    Defensive because Gradio Spaces don't version their contracts;
    a Space update that breaks the first attempt still has the
    fallback to keep the provider working until we update here.
    """

    PROVIDER_NAME = "glm_hf"
    _SPACE_ID = "prithivMLmods/GLM-OCR-Demo"

    def _predict(self, client: Any, handle_file: Any, image_path: str) -> str:
        # First attempt — single-arg /predict, the simplest shape.
        try:
            result = client.predict(
                handle_file(image_path),
                api_name="/predict",
            )
            text = _coerce_to_text(result)
            if text.strip():
                return text
        except Exception:  # noqa: BLE001 — fall through to the next shape
            pass

        # Fallback — DeepSeek-style /run signature, in case GLM has
        # adopted the same multi-arg convention.
        try:
            result = client.predict(
                image=handle_file(image_path),
                file_path=handle_file(image_path),
                task="📝 Free OCR",
                custom_prompt="",
                page_num=1,
                api_name="/run",
            )
            return _coerce_to_text(result)
        except Exception:  # noqa: BLE001 — soft-fail handled upstream
            return ""
