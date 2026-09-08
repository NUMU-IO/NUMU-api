# NUMU Agent — build plan

**Status:** plan, 2026-09-08. Supersedes the sequencing in `OVERVIEW-AGENT.md`; keeps its
inventory and its Phase 2–4 feature design. Companion: `MODEL-SELECTION.md` (which LLM, cost).

Everything below builds on what is already merged to `dev`. Nothing here proposes a rewrite.

---

## 0. Where we actually are — verified, not assumed

Checked against production on 2026-09-08, not inferred from code:

| Claim | Evidence | Verdict |
|---|---|---|
| The panel is hidden from merchants | `GET /api/v1/public/merchant-hub-nav` → `{"key":"assistant","visible":false,"order":71}` | **hidden** |
| The agent code is deployed | `/app/src/api/v1/agent/` exists inside the running prod image | **deployed** |
| Prod has the LLM keys | `grep '^AGENT_' /opt/numu-api/.env` → **nothing**. Only `GOOGLE_AI_API_KEY` exists | **no keys** |
| The agent has ever run | `agent_conversations` → **0 rows** | **never ran** |
| The knowledge base is seeded | `numu_knowledge_chunks` → **0 rows** | **empty** |
| pgvector is available | `SELECT 1 FROM pg_extension WHERE extname='vector'` → true; all four agent tables exist | **ready** |
| n8n is alive | `n8n.numueg.app` → 200, cert valid to 2026-11-11 | **alive** |

So the live state is: **an agent that is fully deployed, correctly switched off at the hub, with no
key, no knowledge and no history.** It is unlaunched, not broken-and-exposed. That is a far better
starting position than it looked: the remaining work is entirely ours to sequence, with no merchant
currently seeing anything.

Two corrections to `OVERVIEW-AGENT.md`, both in the safe direction:

- It says "None of it is on `main`/`prod`. The agent tree is absent from `origin/main`." The **running
  prod image contains the agent package**, and prod's nav code already knows the `assistant` key.
- It says both gates fail open. Neither does, in practice. `agent_enabled` is checked in `deps.py` and
  404s correctly (it is on by default, which is not the same as failing open), and the hub's
  `?? true` default never gets a chance to fire because the row exists and is `false`.

The fail-open default in `useNavConfig` is still worth fixing — a key that is ever removed from the
config silently turns its feature on for everyone — but it is not currently exposing anything.

---

## 1. The RAG decision: Postgres stays, n8n gets the cold path

**Retrieval stays in-process against pgvector in Supabase. It does not move to n8n or Node-RED.**

Five reasons, in order of weight:

1. **It is already built and the extension is already live on prod.** Two knowledge layers, tenant
   indexer, chunker, coverage endpoint, and a `search` that takes `tenant_id`. Moving it buys no
   capability that does not exist today.
2. **Latency.** Retrieval sits inside the merchant's chat turn. Postgres answers it on a connection
   the request already holds. Routing it through n8n adds `API → n8n (different EC2) → vector store →
   n8n → API` to a path the merchant is watching a spinner on.
3. **Failure domain.** n8n is currently async-only: if it is down, background work is delayed.
   Putting retrieval there makes n8n's uptime the chat's uptime, on a box whose TLS renewal has
   already lapsed once.
4. **The tenant boundary belongs in SQL.** Today the filter is a `WHERE tenant_id = ...` plus RLS. In
   n8n it becomes a parameter inside a flow that a human can edit in a browser. That is the wrong
   place for the thing that stops store A reading store B's notes.
5. **Node-RED is a worse fit than n8n for this**, not a better one: it is an IoT flow runtime with no
   multi-tenant auth model and no connector advantage here. Nothing about this problem points at it.

**Where n8n genuinely earns its place — and this is real work, not a consolation prize.**

`POST /agent/knowledge/refresh` exists but **nothing calls it on a schedule**. There is no Celery beat
entry for knowledge. The corpus is therefore frozen at whatever was last ingested by hand — which,
per §0, is nothing. Keeping a knowledge base current is scheduled, bulk, retry-tolerant work that
nobody watches. That is exactly n8n's shape, and the `trigger_workflow` tool with its allow-list is
already built for handing work to it.

**But be honest about the alternative:** Celery beat already runs in this stack, already has a
worker, and the refresh is one `@celery_app.task` plus a schedule entry. If the only source is the
in-repo corpus, **Celery is the lazier and better answer** — no second system in the ingest path.

Pick n8n for ingestion only if one of these is true:

- Non-engineers will edit which sources get ingested.
- Sources are external SaaS (Google Drive, Notion, Sheets, a WhatsApp export) where n8n's connectors
  replace real code.
- You want a visual run history for ingestion failures that is not CloudWatch.

Otherwise: **Celery for corpus refresh, n8n for merchant-facing async work.** Retrieval in Postgres
either way. This is Phase I.

---

## 2. Phases

Each phase is a gate. Do not start the next until the previous one's verification passes.
Phases B–C are not features — they are the reason the agent cannot be switched on yet.

### Phase A — Keep it hidden ✅ already true

`assistant` is already `visible: false` in the live nav config, so no merchant can reach the panel.
Nothing to do. The only rule for the phases below: **the flag stays false until Phase D's
verification passes.**

The one related fix worth carrying into Phase C: `useNavConfig` returns `?? true` for an unknown key,
so deleting a row from the config silently switches its feature on for every merchant. Default to
hidden for keys the config does not mention.

---

### Phase B — The three security fixes (one PR, api)

These are from the 2026-09-08 code audit. All three are small; none is optional.

**B1. Confirm checks the wrong permission.** `routes.py:35` hardcodes
`_WRITE_PERMISSION = "themes.edit"` for *every* proposal. PR #411 adds `update_product` with
`REQUIRED_PERMISSION = "product.update"` at propose time and does not touch `_require_write`. Once
#411 lands, a staff member with `themes.edit` and no product rights can confirm a product price
change, and a product manager without `themes.edit` gets a 403 confirming their own.

*Fix:* the tool registry already knows each tool's permission. Look it up from
`proposal.tool_name` at apply time instead of using a constant. Same for the `/audit` route, which
gates on `themes.view` regardless of what the record is about.

**B2. A proposal is not bound to a store.** `apply_proposal` fetches by id (tenant-scoped only) and
applies to the `store_id` in the URL. A tenant with two stores can propose on A and confirm at
`/stores/B/agent/confirm`; for `create_discount` the coupon is created on B.

*Fix:* persist `store_id` on the proposal, and reject at apply time when it differs. One column, one
guard, one test.

**B3. Confirming a theme change publishes it live.** `proposals.py:317` calls `service.publish(...)`.
`OVERVIEW-AGENT.md` §4/§5 states the blast radius is "the store's *draft* only; publish is
merchant-driven". That is not true today.

*Fix:* decide which one is right, then make them agree. Recommendation: **stop publishing.** Apply to
the draft, return the customizer deep link, let the merchant press Update. It matches what the
decision was recorded as, it matches R4 ("the editor reads and edits it"), and it removes the only
path where a model-initiated change reaches shoppers without a human looking at it.

**Verify:** integration tests — a `themes.edit`-only member is refused on a product proposal; a
cross-store confirm is refused; after confirm the draft holds the change and the live storefront does
not until publish.

**Blast radius:** api only. Nothing merchant-visible while Phase A holds.

---

### Phase C — Make the loop survivable (one PR, api + one nginx line)

The loop is correct but unbounded. Each of these is a production incident waiting for the first busy
store.

| # | Problem | Fix |
|---|---|---|
| C1 | `list_for_conversation` has **no limit**; every turn replays the whole conversation. Cost grows linearly forever, then turns hard-fail on context length. #411 does not fix it. | Last N turns (start at 10) or a token budget. One `.limit()` plus ordering. |
| C2 | Tool results are `json.dumps`'d into context with **no cap**. `get_products` on a large catalogue is one very expensive turn. | Truncate per result (e.g. 4 KB) with an explicit `"truncated": true` the model can see. |
| C3 | **No per-tenant rate or spend cap.** Harmless while there is no key; a live financial hole the day there is one. | Turns-per-store-per-day counter in Redis; refuse with a clear message, not a 500. |
| C4 | Provider errors are undifferentiated: 401, 402, 403 and 500 all become one `LLMProviderError`. Ops cannot tell "the key is dead" from "the provider is down" — which is exactly the invisible state prod is in now. | `kind="auth" / "credits" / "upstream"`; log structured; no retry on auth/credits (retrying a 402 is a slower 402). |
| C5 | nginx `proxy_read_timeout 60s` on the apex and api blocks, against a loop that can legitimately run 5 × 30s plus tool time. The SSE is cut mid-turn. | Raise the timeout on the agent path **or** cut the per-turn budget below 60s. `X-Accel-Buffering: no` is already set correctly, so buffering is not the problem. |

**Verify:** a 30-turn conversation still answers and its prompt size is flat; a store with 500 products
returns a bounded prompt; a wrong key produces one structured `auth` log line and a clear merchant
message; a deliberately slow turn survives past 60s.

---

### Phase D — Turn it on

1. **D1 (Yousef):** model + who pays. Default per `MODEL-SELECTION.md` §5: Gemini 3.1 Flash-Lite,
   paid tier. `GOOGLE_AI_API_KEY` already exists on the box, so this is the cheapest path to a working
   agent — the same key already powers `/stores/{id}/ai/*`.
2. Set by hand in `/opt/numu-api/.env` (CD ships images only, it does not write env):
   `AGENT_LLM_BASE_URL`, `AGENT_LLM_API_KEY`, `AGENT_LLM_MODEL`, and the four `AGENT_EMBED_*`.
   Restart api + celery.
   **Without `AGENT_EMBED_*` the RAG silently uses the MD5-bucket fallback** and answers cite
   near-random chunks with total confidence. That is worse than no RAG, because it looks like it works.
3. Seed: `POST /agent/knowledge/seed`, then `tenant-reindex` for vionne and rabbit. Both tables are at
   0 rows today.
4. Run the eval: `AGENT_EVAL=1 ... pytest tests/evals -q` on the #411 branch. Require 12/12.
5. Read ten real Arabic replies. The harness scores tool choice, never prose. Reject MSA or English drift.
6. Delete the dead-droplet env sync in `cd.yml` and close #412 — it edits a machine that no longer exists.

**Verify:** as the vionne owner, `كام أوردر عندي النهاردة؟` streams a `tool_call` for `get_orders`
with `period:"today"` and an Egyptian-Arabic reply; `GET /agent/audit` shows the turn;
`numu_knowledge_chunks` is non-zero. Only then unhide the nav key.

---

### Phase E — Land the open work

Merge **api #411** (with the Phase B fix, not before it) and **hub #173**. #411 also brings
`ACTION_UNDOERS`, which `dev` genuinely lacks — today a created coupon cannot be undone at all.

k6 Load Smoke is known quota noise. Do not "fix" it.

**Verify by content, never by a green promote:** `GET /agent/digest` returns 200 for the vionne owner;
the live hub bundle hash changed and contains the `DigestCard` strings.

---

### Phase F — `create_product` (R1)

Design already settled in `OVERVIEW-AGENT.md` §4 Phase 2 and still correct: mirror #411's
`update_product`, CONFIRM tier, `product.create`, `status: draft` by default (D4), applier →
`CreateProductUseCase`, undoer → delete. Register in `_TOOL_SPECS`, add the hub `ProposalCard`
branch, add two golden eval cases (EN + ar-EG).

Out of scope: bulk, variants, CSV.

---

### Phase G — Photos (R2)

**G1, the URL path — do this first.** The agent never touches bytes: add `"product_image"` to the
`customization/assets` allowlist, add `attachments` to `ChatRequest`, append `[attached image: <url>]`
to the user text, wire the hub's existing `uploadStoreAsset` to the composer.

**G2, the model sees the photo.** Only meaningful with a vision model (D1). Emit OpenAI content
blocks in `_message_to_wire`, gated on one `agent_llm_vision` bool. For "اكتب وصف للمنتج ده من
الصورة", call the **existing** Gemini-backed `/ai/generate-description` rather than writing a second
prompt.

---

### Phase H — Theme section editing (R3, R4)

Keep `add_theme_section` and `update_theme_setting`. Add `update_section_settings` and
`remove_section`, both CONFIRM, both diffed. Reorder is YAGNI until asked.

`generate_custom_section` stays reserved (D5): a new section *type* is a React component, a schema, a
build, an R2 upload and a merchant pressing Update — a fleet release, not a chat action. The
chat-sized version is filling a generic block the theme already declares.

QA from the **customizer page**, not the storefront: the section is listed on that page with a
populated settings panel.

---

### Phase I — Knowledge freshness (the n8n / Celery decision from §1)

Nothing refreshes the corpus today. Pick one:

- **Celery (recommended default):** one task wrapping the existing refresh use case + a beat entry.
  Zero new infrastructure. Watch the known trap — a beat task dies silently on a name mismatch **or**
  on being missing from `imports`; check both when adding it.
- **n8n:** only if §1's conditions apply. The `trigger_workflow` allow-list is the entry point;
  keep the tenant id server-injected as it is today.

Either way retrieval stays in Postgres.

---

### Phase J — Observability and cost

- The provider already returns `prompt_tokens` / `completion_tokens`; the turn row drops them. Persist
  them so "what does this store cost us" is a query, not a guess.
- Alert on the `auth` and `credits` error kinds from C4. The current failure mode is silent.
- Keep `tests/evals` opt-in and run it before every model change and every new tool.

---

## 3. What Yousef still owns

| # | Decision | Blocks | Default if silent |
|---|---|---|---|
| D1 | Model + who pays | Phase D | Gemini 3.1 Flash-Lite, paid tier |
| D2 | May merchant/shopper PII go to a free tier that trains on inputs? | Phase D | No — free tiers for dev/eval only |
| B3 | Does a confirmed theme change publish, or land in the draft? | Phase B | Draft only; merchant publishes |
| §1 | Celery or n8n for corpus refresh | Phase I | Celery |
| D4 | New products draft or active? | Phase F | Draft |

---

## 4. Order, with the reasoning

B → C → D → E → F → G → H → I → J. (A is already satisfied.)

B is before E because #411 is what makes the permission bug dangerous — it adds a product-write tool
while the confirm gate still asks for `themes.edit`. C is before D because switching the key on
without a history cap or a spend cap converts a correctness bug into a bill. D is before E because
there is no point merging features onto an agent nobody has ever seen answer a question.

Nothing here is urgent in the incident sense: the panel is hidden, so this is a launch sequence, not
a fire.

Features (F onwards) are the easy part and the part already designed. The first four phases are what
stands between this and something that can be turned on.
