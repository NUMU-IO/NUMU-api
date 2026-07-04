# Template overrides + global sections — design & build plan

Status: **in progress** (increment 1 landing in NUMU-api). Owner: theme-engine.
Goal: close the two biggest "any layout, any UI" gaps from the 2026-07-04 audit —
per-product/collection/page **template overrides** and **global sections shared
across pages for BYOT** — to reach Shopify OS 2.0 parity on layout flexibility.

## Current state (what exists)

- `customization_v3.templates: Record<string, PageTemplate>` — one template per
  route type (`home`, `product`, `products`/`collection`, `cart`, `profile`, …).
  A specific product/collection/page **cannot** use an alternate template.
- `customization_v3.section_groups: { header, footer }` — the host renders these
  only for non-BYOT themes; BYOT bundles render their own chrome (10/16 ship
  none → the Phase 0 `ByotChromeFallback` is a stopgap). No merchant-defined
  global groups beyond header/footer.
- Storefront `resolve-theme.ts` resolves + sanitizes the template map and passes
  the whole `themeSettings` to the bundle; the bundle picks the template by the
  route via `BUILTIN_TEMPLATES[currentTemplate]`.

## Target model (Shopify OS 2.0-aligned)

### 1. Template variants
Template variants live in `customization_v3.templates` keyed as
`"<base>.<suffix>"`, e.g. `product`, `product.wholesale`, `collection`,
`collection.sale`, `page`, `page.about`. The base key (`product`) is the default.

A resource carries a nullable **`template_suffix`** (e.g. `"wholesale"`):
- `products.template_suffix`, `collections.template_suffix`, `pages.template_suffix`.
- `null` → the base template.

### 2. Resolution (storefront, pure + unit-testable)
```
resolveTemplateKey(routeType, resource, templates):
  if resource?.template_suffix and templates["{routeType}.{suffix}"] exists
     → "{routeType}.{suffix}"
  else → "{routeType}"    # fallback; never 404 on a missing variant
```
The resolved key is passed to the bundle in the existing `page` prop
(`page.template`), so the bundle renders the right section list. Sanitization
(`sanitizeAgainstSchemas`) runs per-variant, same as today.

### 3. Global sections for BYOT
Generalize `section_groups` from the fixed `{header, footer}` to an ordered map of
merchant-defined groups (`header`, `footer`, plus custom e.g. `announcement`,
`aside`). Two render paths, chosen by a theme-manifest capability flag:
- `renders_global_sections: false` (default for the chrome-less themes) → the
  **host** renders the groups around the bundle (extends the Phase 0
  `ByotChromeFallback` seam into a full group renderer).
- `renders_global_sections: true` → the bundle renders them itself via a new SDK
  `<GlobalSections group="header" />` component that reads the group from
  `themeSettings.section_groups` — so a designer theme keeps full control.

This subsumes the Phase 0 chrome stopgap: a theme with no header group +
`renders_global_sections:false` gets host-rendered global chrome.

## SDK contract changes (→ minor bump, forces theme rebuild)
- `page.template: string` — the resolved template key (`"product.wholesale"`).
- `<GlobalSections group>` component + `useSectionGroup(group)` hook.
- `SectionProps` already gains `id`/`type` in Phase 2 — variants reuse it.
- `theme.json` gains `renders_global_sections?: boolean` (plugin passes it into
  the emitted manifest; host reads it like `byotProvidesOwnChrome`).

## Hub UX
- **Customizer**: "Create template" → duplicate a base template, name the suffix;
  the template picker lists base + variants. Manage custom section groups next to
  header/footer.
- **Resource editors** (product / collection / page): a **Template** dropdown
  populated from that resource type's available variants → sets `template_suffix`.

## Build increments (sequenced; each its own verifiable PR)

| # | Layer | Scope | Blocked by |
|---|-------|-------|-----------|
| **I1** | **backend (NUMU-api)** | `template_suffix` columns + migration on products/collections/pages; expose in storefront payloads; validate suffix `^[a-z0-9-]{1,32}$` | — (doing now) |
| I2 | storefront | `resolveTemplateKey` + render resolved variant; generalize `section_groups` render (host path) for BYOT | Phase 2 storefront branch |
| I3 | SDK + plugin | `page.template`, `<GlobalSections>`/`useSectionGroup`, `renders_global_sections` manifest flag; minor bump | Phase 2 SDK branch |
| I4 | hub | template-variant creation in customizer + Template dropdown on product/collection/page editors + custom-group management | I1–I3 |
| I5 | scaffold + fleet | scaffold consumes `page.template` + renders `<GlobalSections>`; docs | I3 |

## Risks / notes
- **SDK ripple**: I3 is a contract change → all themes must rebuild+republish to
  use variants/global sections (self-contained bundles freeze the SDK). Base
  behavior is unchanged, so un-rebuilt themes keep working (graceful).
- **Migration**: I1 adds nullable columns (additive, no backfill) — safe on the
  hot `products` table. Alembic revision id ≤32 chars; single linear head.
- **No new routes**: this covers template *variants* of existing route types, not
  net-new routes (that's a separate epic).
- **Verification**: I1 ruff/mypy/migration-history + a resolver unit test in I2;
  full flow needs a staging pass (create variant → assign → render).
