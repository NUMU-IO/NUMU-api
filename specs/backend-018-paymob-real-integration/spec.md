# Feature Specification: Paymob Settings Real Integration

**Feature Branch**: `backend-018-paymob-real-integration`
**Created**: 2026-05-09
**Status**: Draft

## Why this exists

The 2026-05-09 audit found `POST /shopify/{store_id}/settings/paymob`
was a stub: it accepted the form, marked `paymob_connected=true`,
and never validated the secret key, never persisted credentials.
Merchants saw "Connected" then payment recovery silently failed
because no credentials had actually landed.

This feature replaces the stub with real validation + persistence.

## Requirements

- **FR-001**: `ConnectPaymobRequest` schema MUST capture the full
  credential set: `secret_key`, `public_key`, `hmac_secret`,
  `card_integration_id`, `wallet_integration_id` (optional).
- **FR-002**: `configure_paymob_credentials` use case MUST validate
  the submitted `secret_key` by hitting Paymob's
  `https://accept.paymob.com/v1/intention/` endpoint with a $0.01
  test charge (`amount=1`) and `is_test: true`.
- **FR-003**: On 200/201 with `client_secret` (or `intention_detail`),
  encrypt the credential dict via `secrets_manager`, base64-encode,
  persist to `store.settings.payment.paymob.encrypted_credentials`
  + `encryption_key_id`. Format matches the existing
  `get_merchant_paymob_credentials` reader.
- **FR-004**: On any non-success response (401, 500, 200-but-
  malformed) OR network error (timeout, connect refused), return
  `ConfigureFailure(reason, status_code?)`. Nothing persisted.
- **FR-005**: Route handler raises 422 on `ConfigureFailure` with
  the upstream reason in the detail. `paymob_connected` remains
  unchanged.

## Success Criteria

- **SC-001**: `pytest tests/api/test_configure_paymob.py -v` green.
- **SC-002**: Validation request payload contains
  `is_test: true` AND `amount: 1` so the connect flow never
  produces real-money charges.

## Out of scope

- Validation against the wallet integration (Paymob's intention
  API doesn't accept multiple integrations in test mode reliably).
  Only the card integration is tested at connect time.
- Re-validation on credential rotation. Merchants who change keys
  hit the same endpoint, which re-encrypts + re-persists.
