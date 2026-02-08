# Dependency Security Audit

## Audit Metadata

| Field | Value |
|-------|-------|
| **Date** | 2026-02-08 |
| **Tool** | Safety 3.7.0 + Bandit 1.9.3 |
| **Python** | 3.11.9 |
| **Packages Scanned** | 123 |

## Safety Scan Results

### Baseline (Pre-Fix)

| Package | Version | Vulnerability | CVE | Severity |
|---------|---------|--------------|-----|----------|
| `ecdsa` | 0.19.1 | Minerva timing side-channel attack | CVE-2024-23342 | Medium |
| `ecdsa` | 0.19.1 | No side-channel protection (by design) | PVE-2024-64396 | Medium |

**Root Cause:** `ecdsa` was an indirect dependency of `python-jose==3.5.0`,
which was installed as an orphan package. The project uses `PyJWT[crypto]`
(not `python-jose`) for JWT operations, and no code imports `jose` or `ecdsa`.

### Fix Applied

- **Action:** Uninstalled `python-jose`, `ecdsa`, `rsa`, and `pyasn1`.
- **Verification:** Confirmed no imports of `jose`, `ecdsa`, `rsa`, or `pyasn1`
  exist in `src/`. Confirmed `python-jose` is not declared in `pyproject.toml`.
- **Re-scan result:** 0 vulnerabilities found.

### Post-Fix Result

```
Safety v3.7.0 - 0 vulnerabilities reported - 123 packages scanned
```

## Bandit Scan Results

### Summary

| Severity | Count | Confidence | Action |
|----------|-------|------------|--------|
| High | 0 | - | - |
| Medium | 0 | - | - |
| Low | 22 | Mixed | All false positives (documented below) |

### LOW Findings (All False Positives)

All 22 LOW findings are documented false positives. None require code changes.

#### B105/B106/B107: Hardcoded Password Strings (13 findings)

These findings flag string literals containing words like "password", "secret",
or "token" in variable/argument names. All are false positives:

| Location | String | Reason |
|----------|--------|--------|
| `auth.py` (3x) | `token_type="bearer"` | OAuth2 standard token type, not a password |
| `refresh_token.py` | `"refresh"` | Token type comparison, not a secret |
| `token_service.py` (2x) | `token_type="access"` | Default parameter for token type enum |
| `cod/payment_service.py` | `client_secret=""` | COD has no client secret by design |
| `audit_service.py` (3x) | `"password_change"`, `"password_reset_*"` | Audit event type enum values |
| `messaging_service.py` | `"password_reset"` | Email template type enum value |

**Justification:** These are enum values, OAuth2 standard strings, and template
identifiers. They do not contain actual secrets. Suppressing with `#nosec` is
not needed because Bandit correctly classifies them as LOW confidence.

#### B110: Try/Except/Pass (9 findings)

These flag `except Exception: pass` patterns. All are intentional fire-and-forget
for non-critical operations:

| Location | Purpose |
|----------|---------|
| `invoices.py:376` | Optional R2 service initialization (graceful fallback) |
| `configure_credentials.py:210` | Onboarding step completion (must not block config) |
| `auto_complete.py:37,57` | Onboarding tracking (must not block main operation) |
| `stores.py` (3x) | Store onboarding auto-complete (non-critical) |
| `eta/qr_generator.py:136` | QR code generation fallback (returns raw data) |
| `paymob/service.py` | Webhook notification parsing (best-effort) |

**Justification:** These are documented non-critical operations where failure
should not propagate to the caller. Each has a comment explaining why the pass
is intentional. Logged at appropriate levels upstream.

## Accepted Risks

### 1. In-Memory Rate Limiter (Not Redis-Based)

- **Risk:** Rate limiting uses in-memory storage, which doesn't work across
  multiple worker processes or server instances.
- **Severity:** Low (staging/production behind nginx which provides its own
  rate limiting)
- **Plan:** Migrate to Redis-based rate limiting when scaling to multiple
  workers. Tracked in project backlog.

### 2. passlib[bcrypt] + bcrypt Pin

- **Risk:** `bcrypt` is pinned to `>=4.0.0,<4.1.0` due to a known passlib
  compatibility issue with bcrypt 4.1+.
- **Severity:** Low (bcrypt 4.0.x has no known vulnerabilities)
- **Plan:** Monitor passlib releases for a fix. Consider migrating to
  `argon2-cffi` or direct `bcrypt` usage if passlib remains unmaintained.

## How to Run Scans

```bash
# Safety (dependency vulnerabilities)
python scripts/run_safety_check.py

# Bandit (static analysis)
python scripts/run_bandit_full.py

# Bandit with all severity levels shown
python scripts/run_bandit_full.py --severity low
```
