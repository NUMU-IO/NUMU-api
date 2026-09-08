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
- [ ] **C5** nginx no longer cuts the agent stream at 60s
      · `proxy_read_timeout 60s` on the apex and api server blocks. Deliberately deferred to
        Phase D: it is an ops change on the box, untestable from the repo, and nothing streams
        for long while there is no API key
      · verify: a turn that runs past 60s completes

## Phase D — Turn it on

- [ ] **D1** Model chosen and paid for (Yousef)
- [ ] **D2** `AGENT_*` + `AGENT_EMBED_*` set on the box; api + celery restarted
      · verify: `grep '^AGENT_' /opt/numu-api/.env` is non-empty
- [ ] **D3** Knowledge seeded and tenant-indexed for vionne and rabbit
      · verify: `numu_knowledge_chunks` > 0; `search_knowledge` cites a real corpus file
- [ ] **D4** Eval 12/12 + ten Arabic replies read by a human
      · verify: `AGENT_EVAL=1 pytest tests/evals -q`
- [ ] **D5** Unhide the panel — only after D4
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
