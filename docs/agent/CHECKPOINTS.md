# NUMU Agent — checkpoints

Working tracker for `BUILD-PLAN.md`. One line per checkpoint, ticked only when its stated
verification passed — not when the code was written.

**Standing rule:** the `assistant` nav flag stays `visible: false` until D4 passes.

---

## Phase A — Keep it hidden

- [x] **A1** `assistant` is `visible: false` in the live nav config
      · verified 2026-09-08 via `GET /api/v1/public/merchant-hub-nav`
- [ ] **A2** `useNavConfig` defaults unknown keys to **hidden**, not visible (hub)
      · verify: remove a key from the config in dev → its feature disappears rather than appears

## Phase B — Security fixes (branch: `fix/agent-confirm-authz`, PR #550)

- [x] **B1** Confirm and audit gate on the tool's own `required_permission`, not `themes.edit`
      · verify: a `themes.edit`-only member is refused on a product proposal; a member with only
        `discounts` can confirm their own discount proposal
- [x] **B2** Proposals carry `store_id`; a confirm from another store is refused
      · verify: propose on store A, confirm at `/stores/B/agent/confirm` → 409, nothing written
- [x] **B3** Confirming a theme change lands in the **draft**, not the live storefront
      · verify: after confirm the draft holds the change and the storefront does not, until publish

### Found on the way

- [x] **B0** `set_tenant_context` no longer issues the Postgres-only `set_config` on the
      SQLite test engine · this was breaking every agent integration test that reached an
      apply path, on `dev`, before any of this work — confirmed by stashing and re-running

## Phase C — Survivability (branch: `fix/agent-loop-bounds`, PR #551, stacked on #550)

- [x] **C1** Conversation history is capped
      · verify: a 30-turn conversation still answers and its prompt size is flat
- [x] **C2** Tool results are truncated with a visible `truncated` marker
      · verify: a store with 500 products produces a bounded prompt
- [x] **C3** Per-tenant turn cap, refused with a merchant-readable message
      · verify: the cap returns a clean SSE error, never a 500
- [x] **C4** Provider errors split into `auth` / `credits` / `upstream`; no retry on the first two
      · verify: a deliberately wrong key logs one structured `auth` line
- [x] **C5** nginx no longer cuts the agent stream at 60s · applied 2026-09-08
      · a regex location for `^/api/v1/stores/[^/]+/agent/` in both the apex and
        `api.numueg.app` server blocks: `proxy_read_timeout`/`proxy_send_timeout` 300s,
        `proxy_buffering off`, HTTP/1.1 upstream, `gzip off`
      · the apex copy repeats `limit_req`/`limit_conn` from `/api` on purpose — a regex
        location REPLACES the prefix one, so leaving them out would have made the agent the
        one unthrottled API path on the domain
      · verified: the route answers 401 (routed, auth required) rather than 404/502; apex,
        api health and the vionne storefront unaffected
      · **not yet proven end to end** — a stream that actually runs past 60s needs an API
        key and a real turn, so the final proof belongs to D4

## Phase D — Turn it on

- [ ] **D1** Model chosen and paid for (Yousef)
      · the shortest path is already proven: prod runs `gemini-3.1-flash-lite-preview` on
        Google's OpenAI-compatible endpoint, `GOOGLE_AI_API_KEY` is set, and a live call
        from the box returned 200 **and accepted a tools array** — the agent's hard
        requirement. Setting `AGENT_LLM_*` to the same three values is all D2 needs.
      · open question is the tier, not the key: the free tier trains on inputs, which is
        what D2 has to rule on before merchant and shopper data goes through it
- [x] **D2** `AGENT_*` + `AGENT_EMBED_*` set on the box; api + celery **recreated** · 2026-09-08
      · reuses the platform's existing `GOOGLE_AI_API_KEY` — no second vendor, no HF token
      · `docker restart` does NOT re-read `env_file`; `docker compose up -d` is required
      · verified in the running container: model, base url and both keys resolve
- [x] **D3a** Platform corpus seeded on production · 2026-09-08
      · 6 docs / 6 chunks, every one with a real pgvector embedding through the Google
        key — #552 confirmed working live
      · retrieval checked end to end: **5/5** queries returned the intended document,
        including Arabic questions against English docs (`ازاي أربط بوسطة` → `numu-docs/shipping/bosta`)
- [ ] **D3b** Tenant layer (catalog + policies) for vionne and rabbit
      · **blocked on #556.** `reindex_tenant` reported 15 catalog docs and wrote none:
        `reindex_policies` queried `public.store_settings`, which does not exist, and a
        failed statement aborts the Postgres transaction — so the catalog work was
        discarded at commit while the caller was told it succeeded
      · vionne additionally hit `429` on embeddings; #556 batches them
- [x] **D4** Eval **12/12**, and the Arabic replies re-read against the seeded corpus · 2026-09-08
      · with knowledge in place the agent answers with real NUMU steps
        (`الإعدادات → الشحن → بوسطة → API key`), calls the right tools, and says
        "no abandoned carts" rather than inventing any. The competitor
        recommendations and the "I am an AI model" opener are gone.
      · earlier reading, kept for the record:
      · the ten Arabic replies were read, and they failed the first time: with no tools
        attached the model introduced itself as a general AI, wrote Python on request, and
        recommended Salla/Zid/Shopify to a NUMU merchant. With tools attached and the
        guard fixed it is grounded and stays on NUMU
      · **still open:** the register is MSA rather than Egyptian colloquial. Not a blocker
        for correctness, but it is not the voice the system prompt asks for
      · **re-read the replies after D3** — the corpus is empty, so every how-to answer is
        currently the model improvising
- [ ] **D5** Unhide the panel — **held on D2, not on D4**
      · every technical gate now passes. What is unresolved is the billing tier: the
        agent runs on the platform's existing Google key, and a modest embedding burst
        returned `429`, which is what a free tier does. Google's free tier trains on
        inputs, and agent prompts carry orders, customer contacts and abandoned carts.
      · D2's recorded default is "No — free tiers for dev/eval only". Confirm the key's
        project has billing enabled, or issue a paid key, before this flag flips.
- [ ] **D6** Dead-droplet env sync deleted from `cd.yml`; #412 closed

## Phase E — Land the open work

- [ ] **E1** api #411 merged (after B)
- [ ] **E2** hub #173 merged
- [ ] **E3** Promoted; verified by content, not by a green pipeline

## Phase F–J — Features and hardening

- [ ] **F** `create_product`
- [ ] **G** Photos (URL path, then vision)
- [ ] **H** Theme section edit + remove
- [ ] **I** Knowledge freshness (Celery by default; n8n only if §1's conditions apply)
- [ ] **J** Token counts persisted; alerts on `auth` / `credits`
