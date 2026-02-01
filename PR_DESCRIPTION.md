# feat: Implement Secure Credential Configuration System

## Summary

This PR introduces a comprehensive, secure system for managing third-party service credentials (payment gateways, shipping carriers, communication APIs) for the NUMU platform. The system ensures that sensitive credentials never touch the frontend and are always encrypted at rest.

## Problem Statement

E-commerce platforms require integration with numerous third-party services, each with its own set of sensitive API keys, secrets, and tokens. The previous approach of allowing merchants to enter credentials directly in the frontend posed significant security risks:

1. **XSS Vulnerability**: Credentials stored in browser state could be stolen via XSS attacks.
2. **Accidental Exposure**: Credentials could be logged, cached, or transmitted insecurely.
3. **No Audit Trail**: No record of who configured, updated, or revoked credentials.
4. **Poor UX**: Requiring merchants to handle complex API keys is error-prone.

## Solution: Admin-Mediated Configuration

This PR implements an **admin-mediated configuration flow** where:

1. **Merchants** request configuration through the dashboard (no credentials involved).
2. **Admins** receive notifications and configure credentials through a secure backoffice.
3. **Credentials** are validated with the provider, encrypted, and stored securely.
4. **Merchants** are notified when configuration is complete.

## Implementation Details

### Database Models

| Model | Purpose |
|-------|---------|
| `ConfigurationRequest` | Tracks merchant requests with status, priority, and timestamps |
| `ServiceCredential` | Stores encrypted credentials with validation status |
| `CredentialAuditLog` | Immutable audit trail for all credential operations |

### Secrets Management

The `SecretsManager` class provides:
- AES-256-GCM encryption with unique IVs
- Support for AWS KMS, HashiCorp Vault, and local encryption
- Secure key derivation using PBKDF2

### Gateway Validators

A factory pattern implementation for validating credentials:

| Service Type | Validators |
|--------------|------------|
| Payment Gateways | Fawry, Paymob, Vodafone Cash, Stripe, Tap |
| Shipping Carriers | Aramex, Bosta, MylerZ |
| Communication | WhatsApp Business, Twilio |

### API Endpoints

**Merchant Endpoints:**
- `POST /api/v1/configuration-requests` - Create a request
- `GET /api/v1/configuration-requests` - List requests
- `DELETE /api/v1/configuration-requests/{id}` - Cancel a request
- `GET /api/v1/configuration-requests/status/{type}/{name}` - Check status

**Admin Endpoints:**
- `GET /api/v1/admin/credentials/pending-requests` - List pending requests
- `PATCH /api/v1/admin/credentials/requests/{id}` - Update request
- `POST /api/v1/admin/credentials/configure` - Configure credentials
- `POST /api/v1/admin/credentials/validate` - Validate credentials
- `DELETE /api/v1/admin/credentials/{tenant}/{type}/{name}` - Revoke credentials

### Notification Service

Multi-channel notification system:
- Email notifications (SMTP, SendGrid, Mailgun)
- Real-time WebSocket notifications (Redis pub/sub)
- In-app notifications (database storage)

## Files Changed

```
31 files changed, 5015 insertions(+)
```

### New Files

**Database Models:**
- `src/infrastructure/database/models/tenant/configuration.py`

**Secrets Management:**
- `src/infrastructure/external_services/secrets/__init__.py`
- `src/infrastructure/external_services/secrets/secrets_manager.py`

**Gateway Validators:**
- `src/infrastructure/external_services/gateway_validators/__init__.py`
- `src/infrastructure/external_services/gateway_validators/base.py`
- `src/infrastructure/external_services/gateway_validators/payment_validators.py`
- `src/infrastructure/external_services/gateway_validators/shipping_validators.py`
- `src/infrastructure/external_services/gateway_validators/communication_validators.py`
- `src/infrastructure/external_services/gateway_validators/validator_factory.py`

**API Routes:**
- `src/api/v1/routes/tenant/configuration/__init__.py`
- `src/api/v1/routes/tenant/configuration/merchant_routes.py`
- `src/api/v1/routes/tenant/configuration/admin_routes.py`

**Schemas:**
- `src/api/v1/schemas/tenant/configuration/__init__.py`
- `src/api/v1/schemas/tenant/configuration/request_schemas.py`
- `src/api/v1/schemas/tenant/configuration/credential_schemas.py`

**Use Cases:**
- `src/application/use_cases/configuration/__init__.py`
- `src/application/use_cases/configuration/create_request.py`
- `src/application/use_cases/configuration/get_status.py`
- `src/application/use_cases/configuration/list_requests.py`
- `src/application/use_cases/configuration/cancel_request.py`
- `src/application/use_cases/configuration/update_request.py`
- `src/application/use_cases/configuration/configure_credentials.py`
- `src/application/use_cases/configuration/validate_credentials.py`
- `src/application/use_cases/configuration/revoke_credentials.py`
- `src/application/use_cases/configuration/supported_services.py`

**Notification Service:**
- `src/infrastructure/external_services/notifications/__init__.py`
- `src/infrastructure/external_services/notifications/notification_service.py`
- `src/infrastructure/external_services/notifications/email_templates.py`

**Documentation:**
- `docs/features/credential_configuration/README.md`
- `BACKEND_IMPLEMENTATION_SUMMARY.md`

## Security Considerations

- **Encryption**: AES-256-GCM with unique IVs for each credential
- **RBAC**: Admin-only access to credential configuration endpoints
- **Validation**: Credentials are validated with providers before storage
- **Audit Trail**: All actions are logged in an immutable audit log
- **No Frontend Exposure**: Credentials never touch the merchant dashboard

## Testing

To test this feature:

1. **Create a configuration request** (as merchant):
   ```bash
   curl -X POST /api/v1/configuration-requests \
     -H "Authorization: Bearer $MERCHANT_TOKEN" \
     -d '{"service_type": "payment_gateway", "service_name": "fawry"}'
   ```

2. **Configure credentials** (as admin):
   ```bash
   curl -X POST /api/v1/admin/credentials/configure \
     -H "Authorization: Bearer $ADMIN_TOKEN" \
     -d '{"tenant_id": "...", "service_type": "payment_gateway", "service_name": "fawry", "credentials": {...}}'
   ```

## Related PRs

- **Frontend PR #4**: Phase 2 features with secure settings pages
- **Frontend PR #4 (commit 2)**: Security fixes for settings pages

## Checklist

- [x] Database models created
- [x] Secrets management implemented
- [x] Gateway validators implemented
- [x] API endpoints created
- [x] Notification service implemented
- [x] Documentation written
- [ ] Unit tests (to be added)
- [ ] Integration tests (to be added)
- [ ] Database migrations (to be created)

## Next Steps

1. Create database migrations for new models
2. Add unit tests for use cases
3. Add integration tests for API endpoints
4. Configure email provider in production
5. Set up Redis for WebSocket notifications
