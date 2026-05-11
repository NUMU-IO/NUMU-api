# Meta WhatsApp Business Template Submission Packet

**Purpose:** ready-to-paste content for the 5 WABA templates required by P1 + P2 backend code (`recovery_step_1_offer`, `recovery_step_2_reminder`, `recovery_step_3_deposit`, `otp_verification_en`, `otp_verification_ar`).

**How to submit:** open **Meta Business Manager → WhatsApp Manager → Message Templates → Create Template**. For each template below, set the listed name + category + language + body, then submit for review. Meta SLA: typically 1–3 business days per template.

**Submit all 5 in parallel** the day you intend to launch — they're independent. The earliest one returning APPROVED unblocks the corresponding code path in NUMU-api.

---

## Naming convention

All templates use **snake_case** names that match the literal strings in `DEFAULT_RECOVERY_CADENCE` (`src/core/entities/recovery_flow.py`) and the OTP service. **Do not rename** — the backend resolves templates by exact string match, no fallback.

---

## Template 1: `recovery_step_1_offer`

| Field | Value |
|---|---|
| **Name** | `recovery_step_1_offer` |
| **Category** | UTILITY |
| **Languages** | English + Arabic (two language variants under the same name) |
| **Header** | None (or optional store-name TEXT header — keep it `{{1}}` for the store) |
| **Footer** | `Numu — Powered by NUMU.` |
| **Buttons** | One URL button — *Confirm or pay online* — URL: `{{1}}` (the signed payment-link from `PaymentLinkSession`) |

### English body

```
Hi {{1}}, your order {{2}} is on the way. To make sure your delivery succeeds, please confirm or prepay below — it takes 30 seconds.
```

**Variables:**
- `{{1}}` — customer first name (always populated; falls back to "there" if missing — handled in template builder)
- `{{2}}` — Shopify order number (e.g., `#1042`)

### Arabic body (Egyptian register)

```
أهلاً {{1}}، طلبك {{2}} في الطريق. عشان نتأكد إن الطلب يوصلك، أكّد أو ادفع أونلاين من اللينك تحت — ٣٠ ثانية بس.
```

**Sample preview (EN):** *"Hi Yahia, your order #1042 is on the way. To make sure your delivery succeeds, please confirm or prepay below — it takes 30 seconds."*

**Compliance notes:** Strictly UTILITY-category — no "discount", "limited time", or marketing language. The URL button is a Numu-hosted payment surface (`/p/{session_id}`); link is signed + per-customer.

---

## Template 2: `recovery_step_2_reminder`

| Field | Value |
|---|---|
| **Name** | `recovery_step_2_reminder` |
| **Category** | UTILITY |
| **Languages** | English + Arabic |
| **Buttons** | Same URL button — *Confirm or pay online* — `{{1}}` |

### English body

```
Just a quick reminder, {{1}} — your order {{2}} is still waiting for confirmation. Tap below when you're ready.
```

### Arabic body

```
تذكير سريع يا {{1}}، طلبك {{2}} لسه بيستنى التأكيد. اضغط تحت لما تكون جاهز.
```

**Variables:** `{{1}}` first name, `{{2}}` order number.

**Compliance notes:** non-shaming tone per constitution v1.2.0 Principle VI. No urgency language ("hurry", "last chance"). The reminder reads as service, not chase.

---

## Template 3: `recovery_step_3_deposit`

| Field | Value |
|---|---|
| **Name** | `recovery_step_3_deposit` |
| **Category** | UTILITY |
| **Languages** | English + Arabic |
| **Buttons** | URL — *Pay deposit* — `{{1}}` |

### English body

```
Hi {{1}}, we can ship your order {{2}} today if you pay a small deposit of {{3}} now. The rest you pay on delivery as usual. Tap below to continue.
```

### Arabic body

```
يا {{1}}، نقدر نشحن طلبك {{2}} النهاردة لو دفعت مقدم بسيط {{3}} دلوقتي. الباقي تدفعه عند الاستلام عادي. اضغط تحت للمتابعة.
```

**Variables:**
- `{{1}}` — first name
- `{{2}}` — order number
- `{{3}}` — formatted deposit amount (e.g., `EGP 80`) — comes from `formatMoney()` (locale-aware per constitution v1.2.0 FR-007)

**Compliance notes:** "Deposit" framing is critical — Meta UTILITY allows discussing partial payments tied to an existing order; do not frame as a discount.

---

## Template 4: `otp_verification_en`

| Field | Value |
|---|---|
| **Name** | `otp_verification_en` |
| **Category** | **AUTHENTICATION** (not UTILITY — OTPs are a different Meta tier) |
| **Language** | English only |
| **Header** | None |
| **Footer** | `Do not share this code with anyone.` |
| **Buttons** | One COPY_CODE button — *Copy code* — copies `{{1}}` |

### Body

```
Your verification code is {{1}}. It expires in 5 minutes. Do not share this code with anyone.
```

**Variables:**
- `{{1}}` — 6-digit numeric code (cleartext at send time; HMAC-hashed before persistence per backend-025 FR-006)

**Compliance notes:** Meta AUTHENTICATION-category templates have stricter rules — body MUST include the code and an expiry. Approval is usually faster than UTILITY (≤ 24h) because the template is well-defined.

---

## Template 5: `otp_verification_ar`

| Field | Value |
|---|---|
| **Name** | `otp_verification_ar` |
| **Category** | **AUTHENTICATION** |
| **Language** | Arabic only |
| **Footer** | `لا تشارك هذا الكود مع أي شخص.` |
| **Buttons** | COPY_CODE button — *نسخ الكود* — copies `{{1}}` |

### Body

```
كود التحقق بتاعك هو {{1}}. ينتهي خلال ٥ دقائق. لا تشاركه مع أي شخص.
```

**Variables:** `{{1}}` — 6-digit code.

---

## Pre-submission checklist

For each template:

- [ ] Name matches exactly (no spaces, no caps)
- [ ] Category is correct (UTILITY for recovery; AUTHENTICATION for OTP)
- [ ] All `{{N}}` variables have at least one sample value entered (Meta requires examples in the submission form)
- [ ] EN and AR are submitted as two language variants under the **same template name**, not as two separate templates
- [ ] The merchant's WABA business is verified (otherwise UTILITY approval is heavily restricted)
- [ ] At least one URL button uses `numueg.app` or your store's verified domain (cross-domain redirects can trigger reviewer concern)

---

## After approval

1. Templates appear in the merchant's WhatsApp Business account as APPROVED.
2. NUMU-api's `recovery_send_step` task automatically picks them up — no code change needed (it resolves by name).
3. The OTP service in backend-025 starts sending real codes once `otp_verification_ar` is APPROVED (Arabic is the default locale; English is fallback).
4. Use `MessageLog` rows to monitor delivery: filter by `template_name` to see per-template success rates.

---

## Pacing recommendation

- **Day 1**: submit all 5 templates in parallel.
- **Day 2**: AUTHENTICATION templates typically return first; OTP path can go live the day they're approved.
- **Day 3–5**: UTILITY templates return. As each is APPROVED, the corresponding cadence step becomes operational.
- **Day 5+**: full P1 recovery cadence live. The Celery beat schedule for `tasks.courier_stats.refresh_all` + `tasks.trust_kill_switch.evaluate_all_stores` runs nightly regardless of template approval status.

If a template gets REJECTED, Meta provides a reason. Common rejections:
- "Promotional content in UTILITY template" → soften the language (remove "now", "today" urgency); resubmit
- "Missing variable in body but declared in form" → re-check the `{{N}}` numbering
- "Tone too marketing-y" → reframe with constitution VI's "Worth confirming" / "Recovery-first" tone

The templates above are pre-cleared against these common failures. Resubmission after a rejection takes the same SLA as a fresh submission.
