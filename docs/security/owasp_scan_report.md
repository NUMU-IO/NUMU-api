# OWASP ZAP Scan Report

## Scan Metadata

| Field | Value |
|-------|-------|
| **Scan Date** | 2026-02-08 (code-level analysis; live scan pending staging deploy) |
| **Target URL** | `https://staging-api.numu.com` (planned) |
| **ZAP Version** | N/A (script created, live scan to be run post-deploy) |
| **Scan Profile** | Baseline + Active |
| **Scanner** | OWASP ZAP (via `scripts/run_owasp_scan.py`) |

## Summary

This report documents the findings from a code-level security review aligned
with OWASP ZAP typical findings for FastAPI applications. A live ZAP scan should
be run against the staging environment using `scripts/run_owasp_scan.py` after
the hardening fixes in this branch are deployed.

| Risk Level | Baseline (pre-fix) | After Fix |
|------------|---------------------|-----------|
| High | 0 | 0 |
| Medium | 2 | 0 |
| Low | 3 | 0 |
| Informational | 2 | 1 |

## Findings (Baseline) and Remediation

### Medium Risk

#### 1. Content Security Policy (CSP) Header Overly Permissive

- **Category:** OWASP A05:2021 - Security Misconfiguration
- **Description:** The default CSP allowed `'unsafe-inline'` and `'unsafe-eval'`
  in `script-src`, which weakens XSS protection.
- **Root Cause:** Original CSP was written for an app that might serve HTML; the
  API exclusively returns JSON.
- **Fix:** Changed default CSP to `default-src 'none'; frame-ancestors 'none'`
  for API responses. The relaxed Swagger/ReDoc CSP is only applied to `/docs`,
  `/redoc`, `/openapi.json` and only when `debug=True` (these routes are
  disabled in production).
- **File:** `src/api/middleware/security_headers.py`
- **Status:** FIXED

#### 2. Information Disclosure via Verbose Error Responses

- **Category:** OWASP A01:2021 - Broken Access Control / A09 - Security Logging
- **Description:** FastAPI's default `RequestValidationError` handler returns
  detailed field-level validation errors including field names, expected types,
  and constraint values. This leaks internal API structure to attackers.
- **Root Cause:** No custom handler for `RequestValidationError`.
- **Fix:** Added a custom `RequestValidationError` handler that returns full
  details only in debug mode and a generic message in production. Details are
  still logged server-side for debugging.
- **File:** `src/api/middleware/error_handler.py`
- **Status:** FIXED

### Low Risk

#### 3. Information Disclosure - Root Endpoint

- **Category:** OWASP A09:2021 - Security Logging and Monitoring Failures
- **Description:** The root endpoint (`GET /`) returned the application name,
  version, and description in all environments, aiding attacker reconnaissance.
- **Fix:** In production, the root endpoint now returns only `{"status": "ok",
  "health": "/api/v1/public/health"}`. Full metadata is available in debug mode
  only.
- **File:** `src/main.py`
- **Status:** FIXED

#### 4. Server Header Disclosure

- **Category:** OWASP A05:2021 - Security Misconfiguration
- **Description:** Uvicorn adds a `Server: uvicorn` response header, revealing
  the application server software and version.
- **Fix:** The `SecurityHeadersMiddleware` now strips the `Server` header from
  all responses.
- **File:** `src/api/middleware/security_headers.py`
- **Status:** FIXED

#### 5. Missing X-DNS-Prefetch-Control Header

- **Category:** OWASP A05:2021 - Security Misconfiguration
- **Description:** Without `X-DNS-Prefetch-Control: off`, browsers may
  speculatively resolve DNS for links in API responses, leaking information.
- **Fix:** Added `X-DNS-Prefetch-Control: off` to all responses.
- **File:** `src/api/middleware/security_headers.py`
- **Status:** FIXED

### Informational

#### 6. Existing Security Headers (Already Present)

The following headers were already correctly configured before this audit:

| Header | Value | Status |
|--------|-------|--------|
| `X-Content-Type-Options` | `nosniff` | Present |
| `X-Frame-Options` | `DENY` | Present |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | Present |
| `Permissions-Policy` | Restricts camera, microphone, payment, USB, etc. | Present |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` (prod only) | Present |
| `Cross-Origin-Opener-Policy` | `same-origin` | Present |
| `Cross-Origin-Resource-Policy` | `same-origin` | Present |
| `X-Permitted-Cross-Domain-Policies` | `none` | Present |
| `X-XSS-Protection` | `1; mode=block` | Present |
| `Cache-Control` | `no-store, max-age=0` (default for API) | Present |

#### 7. CORS Configuration

CORS is correctly configured:
- Production rejects wildcard `*` origins
- Credentials + wildcard conflict is detected and prevented
- Default development origins are localhost-only
- Preflight caching is enabled in production (600s)

**No changes needed.**

## How to Run a Live Scan

```bash
# Start ZAP in daemon mode
docker run -d --name zap -p 8080:8080 ghcr.io/zaproxy/zaproxy:stable \
    zap.sh -daemon -host 0.0.0.0 -port 8080 -config api.disablekey=true

# Run the scan script
python scripts/run_owasp_scan.py --target https://staging-api.numu.com

# Report will be written to docs/security/owasp_scan_report.md
```
