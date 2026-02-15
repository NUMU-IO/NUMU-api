# NUMU Big Chance Plan — Restyled (v1.1, Feb 10 2026)
Audience: founders, engineering leads, investors. Format: concise, execution-first.

## Goal
Launch a production-ready, multi-tenant Shopify-style platform for Egypt/MENA with local payments, shipping, and bilingual UX.

## Scope at a Glance
- Backend (FastAPI + Postgres + Redis + Celery), Merchant Dashboard (React 18 + MUI + RTK Query), Storefront (React 19 + TS + Vite + Tailwind/Radix).
- Security: JWT RS256, TOTP 2FA, PostgreSQL RLS, Sentry, Slack alerts, automated backups.
- Multi-tenant: User → Tenant(s) → Store(s) → Products/Orders/Customers; store by subdomain or custom domain.
- Local rails: Paymob, Fawry, COD; shipping via Bosta, Shippo.

## Current State (Done)
- Backend: clean architecture; product/order/customer CRUD; coupons; ETA e-invoicing; image pipeline to R2; 5 payment gateways; 2 shippers; Redis cache; Celery; SQLAdmin; health checks; backups; Sentry/Slack.
- Dashboard: bilingual RTL UI; analytics; product/order/customer/COD/inventory management; payment+shipping+WhatsApp settings; multi-store selector; protected routes; Docker-ready.
- Storefront: theming (4 presets); catalog browse with filters; product detail modal + gallery + comparison; categories; promos; PWA/offline; multi-tenant resolution; responsive; API integration layer.

## Critical Gaps to Launch (P0)
- Cart: no state, badge, page, or persistence.
- Checkout: no flow/UI, no address/shipping/payment selection, no order success.
- Customer Auth: no login/register, no token storage, account page mocked, no protected routes.
- Search: nav icon placeholder; no search flow.

## Important Gaps (P1)
- Backend: returns/refunds; subscription billing & plan limits; email verification; password reset.
- Dashboard: store settings (logo/theme/domain/SEO); coupon management; category management; returns page.
- Storefront: search experience; wishlist; product reviews (frontend + API).

## Nice-to-Have (Post-launch, P2/P3/P4)
- Real-time order updates; advanced variants; recommendations; abandoned cart emails; multi-currency; blog/CMS; discount rules; staff roles; SSR/SEO; plugin marketplace; GraphQL option.

## Execution Plan with Dates (starting Feb 10 2026)
- Phase 1 “Make It Shoppable” (Feb 10–23): Cart state (Zustand/Context) + page/drawer + badge; auth pages + JWT storage; checkout flow (address → shipping → payment) wired to Paymob JS + COD; order success; real order history; backend email verification + password reset.
- Phase 2 “Make It Complete” (Feb 24–Mar 9): Dashboard store settings, categories, coupons; storefront search; backend returns/refunds + dashboard UI.
- Phase 3 “Make It Profitable” (Mar 10–23): Stripe subscriptions for tenants; plan limits enforcement; dashboard billing page with usage meters; NUMU marketing/landing site.
- Phase 4 “Make It Better” (Mar 24–Apr 6): Wishlist, reviews, WebSocket notifications, abandoned cart, variants, SSR/SEO, PWA polish.

## Launch Acceptance Criteria
- Shopper can discover products, add to cart, checkout with Paymob and COD, and see order confirmation; order appears in dashboard with correct status.
- Customer auth: register, login, email verification, password reset, session persistence.
- Returns/refunds API and dashboard flow for card/COD orders.
- Billing: merchants can subscribe/renew/cancel; plan limits enforced; usage visible in dashboard.
- Reliability: p95 API < 300 ms; checkout success rate ≥ 98%; SLO dashboards live (Sentry + logs).

## Architecture Snapshot
- Backend: FastAPI/SQLAlchemy async, Postgres 15, Redis, Celery, R2, Resend, Sentry, Slack, JWT RS256 + TOTP, RLS, Alembic.
- Dashboard: CRA, MUI, RTK Query, i18next (AR/EN), Apex/Recharts, protected routes, Dockerfile + nginx.
- Storefront: React 19 + TS, Vite 7, Tailwind 4 + Radix, Wouter, Express static server, Three.js accents, theme variables.

## Deployment & Ops Checklist (condensed)
- Managed Postgres + Redis; RSA keys generated; env vars from `.env.example` set.
- R2 bucket live; Sentry projects (backend/dashboard/store); Resend domain verified.
- Paymob/Fawry/Bosta prod creds; wildcard DNS + TLS for *.numu.io; nginx reverse proxy.
- Docker images built; Alembic migrations run; CORS origins locked to prod hosts.
- Rate limits tuned; Slack webhooks set; backup schedule verified; load test (Locust); security audit vs OWASP Top 10; ETA e-invoicing creds validated.

## Risks & Mitigations
- Payments unverified: sandbox end-to-end test with Paymob simulator + capture/void flows.
- Multi-tenant isolation: automated RLS regression tests for every new query.
- Performance: caching strategy for catalog (Redis TTL + client cache headers).
- Compliance: PDPL/GDPR basics—privacy policy, data export/delete before marketing.

## Suggested Updates to the Plan
- Add “Definition of Done” per phase plus demo checklist.
- Add SLA/SLO targets with alert thresholds.
- Security: dependency scanning in CI; 2FA mandatory for dashboard admins; periodic key rotation for JWT and R2.
- Data lifecycle: log retention, backup restore drill, PII minimization for analytics events.
- Go-live dry run: create demo tenant/store; run purchase → refund → subscription in staging.
