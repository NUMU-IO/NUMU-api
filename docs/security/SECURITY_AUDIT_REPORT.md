# Security Audit Report — NUMU API

## Audit Metadata

| Field | Value |
|-------|-------|
| **Date** | 2026-02-08 |
| **Branch** | `security/owasp-bandit-safety-hardening` |
| **Scope** | Middleware, error handling, schemas, dependencies, CI |
| **Tools** | Bandit 1.9.3, Safety 3.7.0, OWASP ZAP (script created), manual code review |
| **Codebase** | FastAPI + SQLAlchemy 2.0 + Alembic + PostgreSQL |
| **Lines Scanned** | 38,700+ |

---

## Executive Summary

The NUMU API was audited for OWASP Top 10 vulnerabilities across its middleware
stack, authentication layer, input handling, error responses, and dependency
chain. The codebase has a strong security posture with RS256 JWT auth, multi-
tenant isolation, comprehensive security headers, and structured error handling.

**Key findings and fixes applied:**

| Category | Before | After |
|----------|--------|-------|
| Dependency vulnerabilities | 2 (CVE-2024-23342 in ecdsa) | **0** |
| Bandit HIGH/MEDIUM | 0 | **0** |
| Info disclosure vectors | 3 | **0** |
| CSP permissiveness | `unsafe-inline`/`unsafe-eval` | `default-src 'none'` |
| Input sanitization | Length-only | **HTML tag stripping** |
| Security scan CI | None | **Bandit + Safety in CI** |

---

## Findings and Fixes

### 1. Dependency Vulnerability — CVE-2024-23342 (ecdsa)

| Field | Value |
|-------|-------|
| **Severity** | Medium |
| **Package** | `ecdsa==0.19.1` (via orphan `python-jose`) |
| **CVE** | CVE-2024-23342 — Minerva timing side-channel attack |
| **Root Cause** | `python-jose` was installed but not used; the project uses `PyJWT` |
| **Fix** | Uninstalled `python-jose`, `ecdsa`, `rsa`, `pyasn1` |
| **Verification** | Safety 3.7.0 reports 0 vulnerabilities |

### 2. Information Disclosure — Root Endpoint

| Field | Value |
|-------|-------|
| **Severity** | Low |
| **Issue** | `GET /` returned app name, version, and description in all environments |
| **Fix** | Production returns `{"status":"ok","health":"/api/v1/public/health"}` only |
| **File** | `src/main.py` |

### 3. Information Disclosure — RequestValidationError

| Field | Value |
|-------|-------|
| **Severity** | Medium |
| **Issue** | FastAPI's default handler returns field names, types, and constraints |
| **Fix** | Custom handler returns generic message in production; full detail in debug |
| **File** | `src/api/middleware/error_handler.py` |

### 4. Information Disclosure — Server Header

| Field | Value |
|-------|-------|
| **Severity** | Low |
| **Issue** | Uvicorn sends `Server: uvicorn` header |
| **Fix** | `SecurityHeadersMiddleware` strips `Server` header from all responses |
| **File** | `src/api/middleware/security_headers.py` |

### 5. Overly Permissive CSP

| Field | Value |
|-------|-------|
| **Severity** | Medium |
| **Issue** | Default CSP included `unsafe-inline` and `unsafe-eval` in `script-src` |
| **Fix** | API CSP tightened to `default-src 'none'`. Docs CSP unchanged (debug only) |
| **File** | `src/api/middleware/security_headers.py` |

### 6. Missing X-DNS-Prefetch-Control Header

| Field | Value |
|-------|-------|
| **Severity** | Low |
| **Issue** | Missing header allows browsers to speculatively resolve DNS |
| **Fix** | Added `X-DNS-Prefetch-Control: off` to all responses |
| **File** | `src/api/middleware/security_headers.py` |

### 7. No HTML Tag Stripping on User Input

| Field | Value |
|-------|-------|
| **Severity** | Medium |
| **Issue** | User-provided strings (names, descriptions, addresses, notes) accepted raw HTML |
| **Fix** | Created `SanitizedStr` type that strips HTML tags via Pydantic `BeforeValidator` |
| **Files** | `src/api/dependencies/sanitization.py` + 6 schema files |

---

## Existing Security Controls (Already Present)

### Authentication & Authorization
- RS256 asymmetric JWT signing (not HS256)
- Separate session secret for admin panel cookies
- Role-based access control (`require_roles`, `require_store_owner`, `require_admin`)
- JWT key validation enforced at startup
- Production secret validation (rejects defaults)

### Multi-Tenant Isolation
- Subdomain-based tenant resolution
- Database search_path switching per request
- Tenant ownership verification on protected endpoints

### Security Headers (Full Coverage)
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Content-Security-Policy: default-src 'none'; ...`
- `Strict-Transport-Security: max-age=31536000; includeSubDomains` (prod)
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Permissions-Policy` (camera, mic, geo, payment, USB disabled)
- `Cross-Origin-Opener-Policy: same-origin`
- `Cross-Origin-Resource-Policy: same-origin`
- `X-Permitted-Cross-Domain-Policies: none`
- `X-DNS-Prefetch-Control: off`
- `Cache-Control: no-store, max-age=0` (default API)

### CORS
- Production rejects wildcard `*` origins
- Credentials + wildcard conflict detection
- Specific origins required in production
- Preflight caching (600s in production)

### Rate Limiting
- Stricter auth endpoints (5 req/min): login, register, refresh, customer auth
- General API (60 req/min)
- Rate limit headers in responses
- Health checks exempted

### Error Handling
- Generic error messages for 500s (no stack traces)
- Custom handlers for all domain exceptions
- RequestValidationError suppressed in production
- Consistent JSON format on all error responses

### Input Validation
- Pydantic v2 with strict field constraints
- Length limits on all string fields
- Pattern validation (subdomain, language, labels)
- UUID type enforcement on identifiers
- Decimal precision for monetary values
- Whitelist-based field filtering (sparse fieldsets)
- HTML tag stripping via `SanitizedStr`

### SQL Injection Prevention
- SQLAlchemy ORM with parameterized queries throughout
- No raw SQL in application code

---

## Residual Risks

### 1. In-Memory Rate Limiter

| Field | Value |
|-------|-------|
| **Risk** | Rate limiting is per-process; doesn't work across workers |
| **Impact** | Low (Nginx provides additional rate limiting in staging/prod) |
| **Plan** | Migrate to Redis-based rate limiter when scaling to multiple workers |

### 2. passlib + bcrypt Pin

| Field | Value |
|-------|-------|
| **Risk** | `bcrypt` pinned to `<4.1.0` due to passlib compatibility |
| **Impact** | Low (bcrypt 4.0.x has no known vulnerabilities) |
| **Plan** | Monitor passlib releases; consider migrating to `argon2-cffi` |

### 3. Admin Panel CSRF

| Field | Value |
|-------|-------|
| **Risk** | Admin panel uses session cookies (SessionMiddleware) |
| **Impact** | Low (admin panel is internal, requires authenticated session) |
| **Plan** | Verify SameSite cookie attribute; consider adding CSRF tokens |

### 4. Bandit LOW Findings (22)

| Category | Count | Status |
|----------|-------|--------|
| B105/B106/B107 (hardcoded "password" strings) | 13 | False positive — enum values |
| B110 (try/except/pass) | 9 | Intentional fire-and-forget patterns |

All documented in `docs/security/dependency_audit.md`.

---

## Scan Evidence

### Bandit (Post-Fix)
```
Total issues by severity:  HIGH=0  MEDIUM=0  LOW=22
Total issues by confidence: HIGH=9  MEDIUM=13  LOW=0
```

### Safety (Post-Fix)
```
Safety v3.7.0 — 0 vulnerabilities — 123 packages scanned
```

---

## Files Changed in This Audit

| File | Change Type |
|------|-------------|
| `src/api/middleware/error_handler.py` | Modified — new exception handlers |
| `src/api/middleware/security_headers.py` | Modified — CSP, Server header, DNS prefetch |
| `src/api/middleware/compression.py` | Modified — Starlette 0.50 GZipMiddleware rename |
| `src/main.py` | Modified — root endpoint info disclosure |
| `src/api/dependencies/sanitization.py` | **New** — SanitizedStr type |
| `src/api/v1/schemas/public/auth.py` | Modified — SanitizedStr on names |
| `src/api/v1/schemas/public/customer.py` | Modified — SanitizedStr on names, addresses |
| `src/api/v1/schemas/tenant/product.py` | Modified — SanitizedStr on names, descriptions |
| `src/api/v1/schemas/tenant/store.py` | Modified — SanitizedStr on names, descriptions |
| `src/api/v1/schemas/tenant/order.py` | Modified — SanitizedStr on names, addresses, notes |
| `src/api/v1/schemas/storefront/checkout.py` | Modified — SanitizedStr on customer_notes |
| `scripts/run_owasp_scan.py` | **New** — ZAP automation |
| `scripts/run_bandit_full.py` | **New** — Bandit CI script |
| `scripts/run_safety_check.py` | **New** — Safety CI script |
| `tests/security/test_pentest_scenarios.py` | **New** — automated pentest scenarios |
| `docs/security/pentest_checklist.md` | **New** — manual testing procedures |
| `docs/security/owasp_scan_report.md` | **New** — OWASP findings report |
| `docs/security/dependency_audit.md` | **New** — dependency + Bandit audit |
| `docs/security/SECURITY_AUDIT_REPORT.md` | **New** — this document |
| `.github/workflows/security.yml` | **New** — CI security scans |

---

## Recommendations for Next Audit Cycle

1. Run live OWASP ZAP scan against staging after deploying this branch
2. Migrate rate limiter to Redis for multi-worker support
3. Add CSRF tokens to admin panel forms
4. Consider Content-Security-Policy-Report-Only for monitoring
5. Add Dependabot or Renovate for automated dependency updates
6. Implement API request signing for webhook endpoints
