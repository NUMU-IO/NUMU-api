# `cod_recovery_offer_v1` — WhatsApp template + `/pay` page contract

Provisioning spec for the **"recover" COD-trust flow** (the second flow). When a
high-risk COD order is allowed under `cod_trust.action == "recover"`, the
backend schedules this template (see `application/services/cod_recovery_service.py`).
This doc is everything Ops + frontend need to make it live; the backend is
already shipped and is a graceful no-op until the template row exists.

---

## 1. Flow recap

```
high-risk COD + action="recover"
  → order created as COD
  → cod_recovery_service schedules cod_recovery_offer_v1 (delay 10 min)
  → WhatsApp dispatcher sends it
  → buyer taps "Pay online" → apex /pay redirect → storefront /pay page
  → buyer pays (Paymob/InstaPay) with the promo → order converts COD → prepaid
  → if never paid → stays COD (merchant auto-RTO sweep handles it later)
```

---

## 2. Meta template definition

| Field | Value |
|---|---|
| **Name** | `cod_recovery_offer_v1` |
| **Category** | **UTILITY** (see §5 — keep it order-centric to avoid the MARKETING frequency cap) |
| **Languages** | `en`, `ar` |
| **Header** | none |
| **Buttons** | one **URL** button, dynamic suffix (the payment deep-link) |

### Body — English (5 variables)
```
Hi {{1}}, your order {{2}} from {{3}} is {{4}}.
{{5}}
Pay online now to secure your order — it's quick and safe.
```

### Body — Arabic
```
مرحباً {{1}}، طلبك {{2}} من {{3}} قيمته {{4}}.
{{5}}
ادفع أونلاين الآن لتأكيد طلبك — سريع وآمن.
```

### Variable examples (required by Meta on submission)
`{{1}}`=Sara · `{{2}}`=ORD-1042 · `{{3}}`=Acme Store · `{{4}}`=250.00 EGP ·
`{{5}}`=Get 10% off when you pay online.

### URL button
- **Text:** `Pay online` (EN) / `ادفع أونلاين` (AR). **No emoji** — Meta rejects
  emoji in button text (error 2388060).
- **Type:** URL, dynamic.
- **Base URL:** `https://numueg.app/pay/{{1}}` where the button variable `{{1}}`
  is the `pay_payload` (`"<subdomain>/<order_id>"`). Example resolved URL:
  `https://numueg.app/pay/acme/3f2c…`.
- **Why the apex domain:** Meta locks the button domain to the registered apex,
  so the link must point at `numueg.app` and be redirected to the tenant
  subdomain by nginx (§4) — the same pattern the cart/track CTAs already use.

> **`{{5}}` must never be empty.** Meta rejects blank variables. `cod_recovery
> _service` passes the merchant's `cod_trust.recovery_promo`; when it's blank it
> MUST fall back to a non-empty default line (e.g. "Pay online to secure your
> order."). See §6.

---

## 3. Runtime registry entry (`EGYPTIAN_TEMPLATES`)

Add to `infrastructure/external_services/whatsapp/messaging_service.py`'s
`EGYPTIAN_TEMPLATES`, mirroring the `order_confirmation_request_v2` entry shape.
The named keys MUST match the `template_params` the scheduler sends
(`customer_name, store_name, order_number, total, promo, pay_payload`):

```python
"cod_recovery_offer_v1": {
    "category": "UTILITY",
    "languages": ["en", "ar"],
    "components": [
        # Body params in positional order {{1}}..{{5}}:
        {"type": "body", "parameters": [
            "customer_name",   # {{1}}
            "order_number",    # {{2}}
            "store_name",      # {{3}}
            "total",           # {{4}}
            "promo",           # {{5}}
        ]},
        # URL button dynamic suffix:
        {"type": "button", "sub_type": "url", "index": "0",
         "parameters": ["pay_payload"]},
    ],
},
```

Scheduler → template variable mapping (already emitted, see
`cod_recovery_service.schedule_cod_recovery_offer`):

| `template_params` key | Template var | Source |
|---|---|---|
| `customer_name` | {{1}} | `customer.first_name + last_name` |
| `order_number` | {{2}} | `order.order_number` |
| `store_name` | {{3}} | `store.name` |
| `total` | {{4}} | `"{total/100:.2f} {currency}"` |
| `promo` | {{5}} | `store.settings.cod_trust.recovery_promo` (default if blank) |
| `pay_payload` | button {{1}} | `"<subdomain>/<order_id>"` |

---

## 4. nginx CTA redirect (apex → tenant subdomain)

Add a `return 302` rule in the apex server block of
`/opt/numu/docker/nginx/nginx.conf` (CRLF, bind-mounted, not git-tracked — edit
inode-safely, then `docker exec numu-nginx-staging nginx -s reload`):

```nginx
# Recovery payment deep-link: /pay/<sub>/<uuid> → https://<sub>.numueg.app/pay/<uuid>
location ~ ^/pay/(?<sub>[^/]+)/(?<oid>[^/]+)$ {
    return 302 https://$sub.numueg.app/pay/$oid;
}
```

Mirrors the existing `/o/<sub>/<uuid>` (track) and `/cart/<sub>` rules — pure
nginx, no API/DB hop, no code deploy.

---

## 5. The storefront `/pay/<order_id>` page contract

A force-dynamic tenant route on the storefront (`<sub>.numueg.app/pay/<order_id>`):

1. **Load** the order by id (scoped to the subdomain's store). 404 if not found
   or not this store's.
2. **Guard** state: only offer payment when the order is COD and still
   unpaid/open (PENDING / CONFIRMED / PROCESSING). If already paid/cancelled/
   delivered, show a friendly "nothing to pay" state.
3. **Apply** the merchant's `cod_trust.recovery_promo` as a discount on the
   amount due (display the discounted total).
4. **Render** the store's enabled prepaid methods (Paymob card/wallet, InstaPay)
   — reuse the existing checkout payment step.
5. **On success** (gateway callback), convert the order **COD → prepaid**:
   set `payment_method` to the gateway, `mark_as_paid(payment_id, method)`, and
   record `metadata.cod_recovered = true` so the merchant feed + moat-metrics
   can attribute the recovery.
6. **No PII / token leakage** — the page keys off the order id only; it never
   exposes the trust score or network internals.

Backend support already present: `Order.mark_as_paid`, the Paymob/InstaPay
payment flow used by checkout, and promotions.

**Shipped** (`api/v1/routes/storefront/pay.py`):
- `GET  /storefront/store/{store_id}/pay/{order_id}` — the pay-view (sanitised
  order, amount due, `recovery_promo` copy, enabled online methods, payable
  guard). UUID + store scoped, no auth (mirrors order-tracking).
- `POST /storefront/store/{store_id}/pay/{order_id}` — initiate Paymob (hosted
  Unified Checkout `payment_url`) or Kashier (session URL) for the existing
  order; stamps `metadata.cod_recovery_initiated`.
- `webhooks/paymob.py` — on success, when the charge was recovery-initiated,
  stamps `metadata.cod_recovered`; `mark_as_paid` flips `payment_method`
  COD → paymob. (9 unit tests in `tests/unit/api/test_pay_order.py`.)
- Storefronts: `numu-storefront` `/[domain]/pay/[orderId]` and bazaar
  `app/(store)/pay/[orderId]` both ship the page.

> v1 charges the **full order total** (the moat value is the prepaid
> conversion). A monetary discount from the promo is a documented follow-up —
> it needs proper order-level adjustment modelling to keep accounting
> consistent; `recovery_promo` is shown as incentive copy only.

---

## 6. Required code follow-up (one line)

`cod_recovery_service` must guarantee a non-empty `promo` (Meta §2 rule). Change:

```python
promo = (
    str(cod_trust.get("recovery_promo") or "").strip()
    or "Pay online to secure your order."
)
```

(or localize the default by `store.default_language`).

---

## 7. Provisioning checklist

- [ ] Submit `cod_recovery_offer_v1` (en + ar) to Meta as **UTILITY** with the
      bodies + URL button above; provide all variable examples.
- [ ] On **APPROVED**, seed the `whatsapp_templates` row per store (name +
      `meta_template_id` + `status=APPROVED`) — the scheduler resolves by name
      and prefers APPROVED.
- [x] Add the `EGYPTIAN_TEMPLATES` entry (§3). *(shipped)*
- [ ] Add the nginx `/pay/<sub>/<oid>` 302 (§4) + reload. *(ops)*
- [x] Ship the storefront `/pay` page (§5) on both storefronts. *(shipped — backend endpoints + both storefront pages)*
- [x] Apply the `promo` non-empty fallback (§6). *(shipped in `cod_recovery_service`)*
- [ ] Per store: set `cod_trust.action = "recover"` + `recovery_promo`.

---

## 8. Category note (UTILITY vs MARKETING)

A message about a **specific order the buyer just placed** (pay for *your* order
#X) is **UTILITY** and is *not* subject to Meta's per-recipient MARKETING
frequency cap (error 131049 — see the marketing-frequency-cap notes). Keep the
copy order-centric and the promo a *secondary* line so it stays UTILITY; if Meta
recategorizes to MARKETING, the existing 24h per-customer cooldown +
131049/131050-at-WARNING handling already applies, but deliverability drops, so
UTILITY framing is strongly preferred.
