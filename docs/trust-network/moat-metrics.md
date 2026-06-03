# Moat Metrics — proving the COD Trust Network works

> Due-diligence reference. The single most important question about the moat is
> *"does the cross-merchant network actually catch bad COD buyers and pass good
> ones?"* This is the data that answers it. Internal/admin only —
> `GET /api/v1/risk/moat-metrics` (internal-key protected), platform-wide,
> PII-free (phone hashes + counts). Math: `moat_metrics_service` (unit-tested);
> queries assembled by `gather_moat_metrics`.

## The metrics and what each one proves

### 1. Coverage — *the network sees enough buyers to be useful*
```json
"coverage": { "phones_tracked": N, "multi_store_phones": M,
              "cross_store_reach_pct": X, "total_network_orders": O }
```
- **`cross_store_reach_pct`** — share of tracked buyers seen at **more than one
  store**. This is the network effect: the higher it is, the more a new merchant
  inherits reputation they never collected themselves. Trends up as the network
  grows; the `backend-029` fast-lane unlocks at ≥30% EG COD-phone coverage.

### 2. Cross-store catch — *the moat's whole point*
```json
"cross_store_catch": { "phones_with_rtos": R, "cross_store_risk_signals": C,
                       "cross_store_catch_pct": Y }
```
- **`cross_store_catch_pct`** — of buyers who've had an RTO, how many are visible
  at **>1 store**, i.e. how much of the negative signal protects *other*
  merchants. A serial COD abuser flagged at Store A is what Store B sees as a
  first-time buyer. This number is the moat quantified.

### 3. Auto-approve quality — *the network picks GOOD customers*
```json
"auto_approve_quality": { "auto_approved_orders": A,
  "auto_approved_rto_rate_pct": p, "baseline_cod_rto_rate_pct": q,
  "rto_rate_delta_pct": p - q }
```
- **`rto_rate_delta_pct`** — RTO rate of the trust-auto-approved cohort minus the
  COD baseline. **Negative is the proof point**: the orders the network cleared
  for auto-approve return *less* than COD overall, so the trust score is
  genuinely predictive. The kill-switch hard-caps this cohort at 5% RTO, so a
  blown delta self-heals (see `kill_switch_incidents`).

### 4. Context
- **`kill_switch_incidents`** — stores where trust auto-approve self-disabled
  (RTO breached 5%). Low/zero = the safety gating holds.
- **`trust_tier_distribution`** — spread of final-score tiers
  (none/new/bronze/silver/gold). A healthy spread shows the score
  differentiates rather than labelling everyone the same.

## How to read it for DD
A working moat shows, over time: **coverage ↑**, **cross-store-catch
meaningfully > 0**, and a **negative auto-approve RTO delta**. Those three
together demonstrate the network both *catches abusers across stores* and
*safely fast-tracks good buyers* — the value an acquirer is buying.

## Note
The recovery-conversion metric (COD→prepaid win-backs) is intentionally absent
until the recover flow is live (template approved + `/pay` page shipped — see
`docs/whatsapp-templates/cod-recovery-offer-spec.md`); it keys off
`order.metadata.cod_recovered` and slots in here once data exists.
