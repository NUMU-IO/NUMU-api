# Manual payment rails — InstaPay & Vodafone Cash

Two Egyptian payment methods on NUMU are **not** gateway integrations.
There is no API call, no webhook, no redirect. The merchant publishes a
destination, the customer pushes funds out-of-band, uploads a
screenshot, and NUMU acts as the notary: per-order reference code,
proof upload, auto-approval rules, merchant review.

| | InstaPay | Vodafone Cash |
|---|---|---|
| Destination | IPA — `merchant@cib` | Wallet number — `010…` |
| Reference prefix | `NU-XXXXXX` | `VF-XXXXXX` |
| Scannable QR | yes | **no** — dial `*9#` or the Ana Vodafone app |
| Transfer fee | none | charged to the sender |
| Default amount tolerance | 100 bps (1%) | 300 bps (3%) |
| Allowed as a COD deposit gateway | yes | no |

Both run through **one** implementation. The rail is a `method`
discriminator on the intent, not a fork in the code.

## Where the code lives

```
core/entities/instapay.py
    ManualPaymentMethod          the discriminator (StrEnum)
    ManualPaymentIntent          one per order; snapshots the destination
    PaymentProof                 one-or-many per order
    InstapayIntent / …Status     back-compat aliases, same objects

infrastructure/external_services/manual_transfer/
    payment_service.py           ManualTransferPaymentService  ← the engine
    destinations.py              wallet-number / IPA validation
    merchant_config.py           read/write store.settings["payment"][rail]
    auto_approval.py             the rules engine (rail-agnostic)
    qr_generator.py              InstaPay only

infrastructure/external_services/instapay/
    …                            thin re-export shims over the above
```

`instapay_intents` keeps its table name — it predates the second rail
and renaming it would buy a coordinated deploy and nothing else. The
`method` column is the discriminator; `display_destination` (formerly
`display_ipa`) is the rail-neutral destination snapshot.

## The trap: Vodafone Cash is not an API gateway

`gateway_validators/payment_validators.py` used to define a
`VodafoneCashValidator` requiring `merchant_id` / `api_key` / `pin`,
with the note *"full validation requires API partnership"*.

That models Vodafone's **merchant API** — a different product. It needs
a commercial partnership and an aggregator; a single store cannot get
it, and NUMU does not use it. The consequence was subtle and expensive:

1. No merchant could ever supply those credentials.
2. So `is_configured` could never become `true`.
3. So `PATCH /settings/payment` with `vodafone_cash_enabled` always
   400'd with *"Contact administrator"*.
4. So Vodafone Cash looked half-built, when in fact everything except
   that one validator was fine.

The validator now validates a **wallet number** and nothing else,
because a wallet number is the only credential this rail has. If you
find yourself reaching for an API key here, you are building the wrong
product.

## Adding a third rail (e.g. bank transfer)

1. Add a member to `ManualPaymentMethod` + a `_REFERENCE_PREFIXES`
   entry. The column is `varchar`, not a PG enum, so no `ALTER TYPE`.
2. Add its `store.settings["payment"]` key to `_SETTINGS_KEY`, its
   label to `_HUMAN_NAME`, and its `PaymentProvider` member.
3. Teach `destinations.py` how to validate its destination string.
4. Add the three `/settings/payment/<rail>/credentials` routes — they
   are ~15 lines each; the work happens in `merchant_config`.
5. Add its label to `checkout_config._PROVIDER_LABELS` and the
   storefront's `railCopy()`.

Checkout dispatch, the intent, the proof upload, dedup, OCR,
auto-approval, the expiry sweeper, the merchant review queue and the
notification handlers all pick it up with no further changes.

## Things that are decisions, not code

* **Personal vs merchant wallet.** Personal Vodafone Cash wallets have
  monthly receive limits, and heavy commercial use may breach the terms.
  A merchant wallet raises the ceiling. Nothing technical depends on
  this; everything operational does.
* **Who absorbs the transfer fee.** Vodafone charges the sender, so the
  amount landing on the merchant's wallet can be short of the order
  total. The default 3% tolerance absorbs that. Tightening it to
  InstaPay's 1% sends nearly every order to manual review.
* **Fraud.** A screenshot is not a payment. The mitigations are the
  same as InstaPay's — reference-code matching, thresholds, OCR
  cross-checks — but a sender-name mismatch is easier to fake on a
  wallet SMS than on a bank receipt. Keep auto-approval tight at first
  and widen it once you have volume to look at.
* **Reconciliation.** With no API there is no statement to reconcile
  against. The merchant is the ledger.

## The customer-facing loop

```
checkout  ->  confirmation email  ->  resume page  ->  proof upload  ->  decision
                    |                     |                                  |
              ?ref=<code> link      /instapay/<order_id>            auto-approve
                                    /vodafone-cash/<order_id>        or merchant
                                                                       review
```

The resume page (`numu-storefront`,
`src/components/checkout/ManualPaymentResume.tsx`) is one component for
both rails, reading the rail off `GET .../instapay-status` rather than
the URL. It renders five states, in this precedence:

1. **paid** — payment_status/intent paid, or the latest proof approved.
2. **in review** — a proof is awaiting the merchant. Outranks *expired*
   on purpose: the intent's expiry is the deadline for **paying**, not
   for the merchant to review, and the backend allows a 48h grace
   window past it. A buyer who uploaded in time must not be told their
   window closed while their receipt sits in the queue.
3. **expired** — past expiry with no pending proof. The upload endpoint
   410s here, so no form is offered.
4. **rejected + retryable** — reason shown, then the form again.
5. **still owing** — instructions + upload form.

### Why the link carries ?ref=

The proof endpoints authorize on **either** the customer's session
cookie **or** the intent reference code. A buyer following a link out
of their email has neither a session (most check out as guests) nor any
way to type the code, so `resume_url()` appends it. The storefront's
`/api/payment-proof/[orderId]` proxy deliberately forwards **only** the
reference and never cookies: no ambient authority means no CSRF surface,
and an attacker who already has the code can call the API directly
anyway.

The pages set `referrer: no-referrer` for the same reason — the code is
in the URL, and a Referer header would leak it to any host the page
links out to. If a link arrives without the code (older emails, a
copy-paste that dropped the query string) the page asks for it rather
than dead-ending.
