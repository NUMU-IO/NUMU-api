# numu-api Constitution

This is the durable governance layer for the numu-api FastAPI backend
(`C:\Users\Yahia\NUMU\NUMU-api`). It mirrors the NON-NEGOTIABLE
principles from the companion Shopify-app constitution
(`numu-payments-intelligence/.specify/memory/constitution.md` v1.1.0)
and adds Python-specific operational gates that match the existing
codebase's quality bar.

Every spec, plan, task, and PR in this repository inherits these rules.
Amendments are made deliberately via `/speckit.constitution` and require
an entry in the amendment log.

## Core Principles

### I. Privacy by Hashing (NON-NEGOTIABLE)

Any cross-store identifier — phone number, email, postal address, IP
address — that lands in a network-scoped table is stored as an
**HMAC-SHA256 hash** computed with the `PLATFORM_SECRET_SALT`
environment variable. Raw PII never leaves the merchant's tenant.
The salt rotates yearly; rotation triggers a re-hash migration of
`network_reputation` rows, planned via spec.

*Why:* this is the technical foundation of the cross-merchant trust
network's GDPR Recital 47 legitimate-interest argument. A breach of
the network database must not yield raw PII.

*How to apply:* every spec that introduces a new cross-store data flow
must specify the hashing function, the pepper-rotation strategy, and
the lookup-time reconstruction path. `security-review` rejects code
paths that write raw phone/email/address into a network-scope table.

### II. GDPR Recital 47 Fidelity (NON-NEGOTIABLE)

Every spec that touches customer data must declare:

1. The legitimate-interest justification under GDPR Recital 47
   (or the equivalent under Egypt PDPL 2020, Saudi PDPL 2023,
   Turkey KVKK, UAE PDPL).
2. The DSAR path — how a customer's data is exported on request.
3. The erasure path — how the data is removed on `customers/redact`
   webhook receipt, within the 30-day SLA.
4. The opt-out effect — what happens to network aggregates when a
   merchant leaves the network. Anonymized counters are decremented
   via the existing `network_contribution_log`, NOT deleted.

*Why:* >60% of Shopify App Store rejections are GDPR-webhook
failures. Beyond submission, this is regulatory bedrock.

*How to apply:* `/speckit.analyze` checks each spec for these four
fields. Missing any one blocks `/speckit.tasks`.

### III. Spec-First, Tests From Spec (NON-NEGOTIABLE)

No code merges without a spec at `specs/NNN-<slug>/spec.md` whose
acceptance criteria have been turned into tests. Tests live alongside
code under `tests/` and run in CI (GitHub Actions: lint + typecheck +
test on every PR).

*Why:* the existing repo has 121 test files (~31k LOC tests, 0.85:1
ratio). Maintaining that bar requires every new spec to ship its tests.

*How to apply:* `plan-review-gate` blocks `/speckit.tasks` until
spec.md and plan.md are merged. `security-review.branch` blocks merge
if a PR adds business logic without a corresponding test in the same
PR. Constitution amendments are the only bypass.

### IV. Async-First, Strictly Typed

All new code uses `async def` for I/O paths, `await`-able SQLAlchemy
2.0 sessions, and full type annotations. MyPy strict mode
(`disallow_untyped_defs=True`, `disallow_any_generics=True`) is
enforced in CI; new files MUST type-check cleanly. Pydantic v2 models
validate at every API boundary — no raw dict-passing into route
handlers.

*Why:* the backend is async-first by architecture (FastAPI + asyncpg
+ Celery for blocking work) and strictly-typed by quality bar
(MyPy strict already enforced). New work that breaks either pattern
creates immediate technical debt.

*How to apply:* Ruff config + MyPy config already encode the rule.
Spec/plan-review-gate runs `mypy --strict src/` and rejects any
PR that fails.

### V. Tenant Isolation by RLS, Always

Every tenant-scoped table is created with PostgreSQL row-level
security (RLS) policies that filter by `tenant_id` from the request
context. Tests in `tests/tenant/` and `tests/security/` enforce this:
crossing tenant boundaries returns empty results, not someone else's
data.

*Why:* the backend is multi-tenant. A SQL query that forgets to filter
by `tenant_id` is a compliance incident waiting to happen. RLS makes
the rule unforgeable.

*How to apply:* every Alembic migration creating a tenant-scoped table
must include the `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` and
matching policy. Migrations without it are blocked at PR review.

## Additional Constraints

- **Hybrid sync/async risk scoring.** A synchronous "fast score"
  (network reputation + order value) MUST return within 200ms in the
  webhook handler so Shopify's 5-second webhook timeout is never
  approached. The full multi-factor score MUST complete asynchronously
  via the Celery `risk_scoring_tasks.compute_full_risk_score` queue
  within 10 seconds. UI distinguishes preliminary vs final via the
  `score_type` field on `risk_assessments`.
- **Safe defaults for order mutations.** Auto-cancellation of orders
  is disabled for the first 30 days after each merchant installs the
  app, regardless of their threshold settings. Merchants must
  manually cancel at least 5 orders before the auto-cancel threshold
  takes effect. Enforced in `application/use_cases/shopify/automation_engine.py`.
- **Additive Shopify mutations.** Order tags use the `numu-` prefix
  and are appended (never replace existing tags); order notes are
  prefixed with `NUMU:` and appended (never overwrite merchant data).
  Order cancellations use the official cancel mutation with merchant-
  controlled reason codes.
- **Alembic discipline.** Every schema change is a separate migration
  with `up()` and `down()`. Production migrations are forward-only;
  `down()` is for local rollbacks and must be implementable without
  data loss. RLS policies are part of the same migration as the table
  they protect.
- **Celery task naming convention.** Tasks live under
  `src/infrastructure/messaging/tasks/<domain>_tasks.py` with
  fully-qualified names like `numu_api.shopify.compute_full_risk_score`.
  Tasks are idempotent — retry-safe by design.
- **Contract-versioned API responses.** Backward-incompatible changes
  to any `/api/v1/shopify/*` endpoint (field removal, type narrowing,
  semantic redefinition) bump the API version (`/api/v2/...`) or are
  guarded behind a feature flag. Silent field removal is a
  constitution violation.
- **Secret hygiene.** No secrets in code or commits. The
  `application/use_cases/configuration/` module is the canonical
  place for credential handling; secrets are encrypted via
  `infrastructure/external_services/secrets/` (AES-256-GCM). The
  `PLATFORM_SECRET_SALT` rotates yearly per Principle I.
- **Mock data ≠ production data.** Test fixtures live under
  `tests/fixtures/` and are never imported by production code paths.
  Demo accounts use the `demo/` use cases which are explicitly tagged
  and cleaned up by `demo_cleanup_task`.

## Development Workflow

- **One feature, one branch, one spec.** Branch naming follows the
  `git-branching` extension's convention (`feature/NNN-<slug>` for
  forward features, `fix/<slug>` for bugfixes).
- **spec-kit lifecycle is mandatory** for every feature:
  `/speckit.specify` → `/speckit.clarify` → `/speckit.plan` →
  `/speckit.red-team` → `/speckit.tasks` → `/speckit.analyze` →
  `/speckit.implement` → `/speckit.checkpoint.commit` (mid-impl) →
  `/speckit.security-review.branch` (pre-merge) →
  `/speckit.retrospective.analyze` (post-merge).
- **`version-guard` runs before `/speckit.plan`** to catch SDK /
  framework / Pydantic / SQLAlchemy / Celery version drift before a
  plan locks the wrong version.
- **`security-review.branch` runs on every PR** that touches Shopify
  webhook handlers, billing, network-reputation tables, credential
  storage, or Numu-API contract endpoints. Critical findings block
  merge.
- **`retrospective.analyze` runs at the close of every sprint** to
  score adherence and fold drift back into this constitution.

## Cross-repo Consistency

The companion Shopify-app repo
(`C:\Users\Yahia\NUMU\numu-payments-intelligence`) maintains its own
constitution at v1.1.0. The two constitutions share the three
NON-NEGOTIABLE principles above (Privacy by Hashing, GDPR Recital 47
Fidelity, Spec-First) by design. Any amendment that weakens or removes
a NON-NEGOTIABLE here MUST be paired with the matching amendment in
the Shopify-app repo, or vice versa. Drift between the two is a
constitution violation in itself.

The contract surface between the two repos is documented per-feature.
The current contracts:

- Shopify app → numu-api: webhook relay, dashboard reads, risk
  actions, automation rule CRUD, settings, payment-channel
  aggregates, billing-sync, usage-record-callback.
- numu-api → Shopify app: usage-record relay (POST
  `/api/billing/usage-record` on the Shopify-app side, called from
  numu-api when a merchant exceeds their verification cap).

## Governance

This constitution supersedes any conflicting guidance in CLAUDE.md,
README.md, or ad-hoc PR conversations. Amendments require:

1. A spec under `specs/000-constitution-amendment-<slug>/spec.md`.
2. `/speckit.red-team` adversarial review of the proposed amendment.
3. Owner approval (currently: Yahia, sole maintainer).
4. Increment of the version below per semantic-versioning rules:
   - **MAJOR**: removing or weakening a NON-NEGOTIABLE principle.
   - **MINOR**: adding a new principle, section, or strengthening
     existing rules.
   - **PATCH**: clarifications, typos, formatting.

All PRs and `/speckit.analyze` runs must verify compliance.

## Amendment Log

- **v1.0.0 (2026-05-08)** — Initial constitution ratified for the
  numu-api FastAPI backend. Five core principles (three
  NON-NEGOTIABLE: Privacy by Hashing, GDPR Recital 47 Fidelity,
  Spec-First/Tests From Spec; plus Async-First Strictly Typed and
  Tenant Isolation by RLS). Operational constraints for hybrid risk
  scoring, safe order mutations, additive Shopify mutations, Alembic
  discipline, Celery task naming, contract versioning, secret hygiene,
  and mock-data isolation. Workflow lifecycle bound to spec-kit + the
  curated extension set installed on this repo (brownfield,
  security-review, version-guard, plan-review-gate, conduct,
  checkpoint, ship, retrospective, red-team, git). spectest deferred
  (upstream validation bug as of 2026-05-08).

**Version**: 1.0.0 | **Ratified**: 2026-05-08 | **Last Amended**: 2026-05-08
