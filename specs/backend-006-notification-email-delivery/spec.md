# Feature Specification: Email Notification Delivery

**Feature Branch**: `backend-006-notification-email-delivery`
**Created**: 2026-05-09
**Status**: Draft

## Why this exists

Two related stubs in numu-api today silently drop emails:

1. `infrastructure/external_services/notifications/notification_service.py`
   `_send_via_smtp` is a `# TODO: Implement actual SMTP sending`.
   The "smtp" provider is the unconfigured default, so any call into
   `NotificationService.send_notification(payload, channels=["email"])`
   logs and discards the message.
2. `infrastructure/messaging/tasks/notification_tasks.py` —
   `send_order_confirmation_email_task`,
   `send_shipping_notification_email_task`, and
   `send_delivery_confirmation_email_task` already build
   `ResendEmailService()` with no renderer + no email-log repo, so
   merchant-customized templates and the audit-log row are skipped on
   every async send. (The FastAPI request path uses DI to inject both;
   the Celery worker doesn't.)

This spec closes both gaps. The Resend integration is already
production-grade (see commit `53e5d75` "real Resend templates"). The
fix is plumbing — point the SMTP fallback at Resend, and give the
Celery tasks a per-task `AsyncSession` they can use to construct the
renderer + log repo.

## User Story 1 — `NotificationService` actually delivers email (P1)

As the platform, when `NotificationService.send_notification` is called
with `channels=["email"]`, the email reaches the recipient via Resend.
A `# TODO` is no longer the only thing standing between a configuration
event and the merchant's inbox.

**Independent Test**: with a fake `ResendEmailService` injected,
calling `NotificationService.send_notification(payload)` records exactly
one `send_email(EmailMessage)` call with the payload's recipient,
title, and message in body.

**Acceptance Scenarios**:

1. **Given** a `NotificationPayload` with a non-null `recipient_email`,
   **When** `send_notification(payload, channels=["email"])` is called,
   **Then** the injected email service receives a single
   `send_email(EmailMessage)` call carrying the payload's title +
   message and a default `from_email` of `noreply@numueg.app`.
2. **Given** an `EMAIL_PROVIDER` setting of `"smtp"` (the legacy
   default) AND no SMTP server configured, **When** the SMTP path is
   taken, **Then** the call falls through to Resend rather than
   silently logging-and-dropping. Production-correct behavior, even if
   the legacy setting hasn't been migrated yet.
3. **Given** the Resend send raises, **When** the email branch
   handles the error, **Then** the exception is logged via the
   existing `logger.error` path and `send_notification` returns
   `False` for the email channel — *no* exception leaks past the
   channel boundary so other channels (websocket, in_app) still fire.

## User Story 2 — Celery email tasks use the renderer + audit log (P1)

As the platform, when a Celery task sends a transactional email, it
opens a per-task `AsyncSession`, constructs an
`EmailTemplateRepository` + `EmailLogRepository`, builds an
`EmailTemplateRenderer`, and passes both into `ResendEmailService` —
exactly the same shape the FastAPI DI graph builds. Merchant template
overrides apply to async sends; the email-log audit row exists for
every send, regardless of code path.

**Independent Test**: a small helper
`_build_resend_service_with_session()` returns a configured service
plus the session it owns. Calling it inside a `run_async` coroutine
inside a test exercises the construction path without spawning a
real Celery worker.

**Acceptance Scenarios**:

1. **Given** a Celery email task, **When** `run_async(...)` enters its
   coroutine, **Then** the coroutine opens an `AsyncSession`, builds
   the renderer + log repo, constructs `ResendEmailService(renderer=...,
   email_log_repo=...)`, calls the per-event send method, and closes
   the session before returning.
2. **Given** the renderer raises during template lookup, **When** the
   email send completes, **Then** the legacy string-interpolation
   path runs (already implemented in `ResendEmailService` via the
   internal `_render_or_legacy` fallback) and the email goes out.

## Requirements

- **FR-001**: `NotificationService` MUST accept an optional
  `IEmailService` injection in the constructor for tests; when none
  is provided, it MUST construct `ResendEmailService()` lazily on
  first send.
- **FR-002**: `_send_via_smtp` MUST be replaced with a call into
  `IEmailService.send_email(EmailMessage(...))`. Failures MUST be
  logged but MUST NOT propagate past `send_notification`.
- **FR-003**: The default `EMAIL_PROVIDER` MUST resolve to
  `"resend"`. Existing `"sendgrid"`/`"mailgun"` branches stay (they
  hit Resend's fallback Resend if either explicit branch is selected
  but env vars are missing).
- **FR-004**: A reusable helper
  `_build_resend_service_with_session(session)` MUST live in
  `notification_tasks.py` (or a sibling helper module) and assemble
  a fully-wired `ResendEmailService` (renderer + email_log_repo)
  from the session.
- **FR-005**: Each Celery email task MUST open
  `async with AsyncSessionLocal() as session:` and use the helper
  to construct the service. The TODO comments MUST be removed.

## Out of scope

- Migration of the `EMAIL_PROVIDER` env var on running deployments —
  defaulting in code is enough; ops will clean up env files
  separately.
- New transactional email types — this spec is wiring, not new
  functionality.
- Inbound webhook handling for Resend bounces / complaints — already
  scaffolded in `routes/webhooks/resend.py`.

## Success Criteria

- **SC-001**: `pytest tests/api/test_notification_email_delivery.py
  -v` green.
- **SC-002**: Grep for `# TODO: Implement actual SMTP sending` and
  `TODO(email-templates): wire renderer` returns zero matches.
