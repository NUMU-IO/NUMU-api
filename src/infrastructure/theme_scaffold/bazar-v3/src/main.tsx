/**
 * Bazar (V3) — entry point.
 *
 * Renders sections from `themeSettings.templates.<currentTemplate>`
 * (host-provided) or falls back to the manifest's preset home template.
 * Sections lazy-load so each page only pays for what it shows.
 *
 * Phase 3 adds the full multipage set — chrome (header/footer) plus the
 * commerce templates (products / product / collection / cart / checkout /
 * search / page / 404). `pickTemplate(ctx.page.type)` routes between them;
 * each template's section list lives in theme.json's `presets.templates`.
 */

import {
  StrictMode,
  Suspense,
  forwardRef,
  lazy,
  useImperativeHandle,
  useState,
} from "react";
import { createRoot, type Root } from "react-dom/client";
import {
  NuMuProvider,
  Section,
  mountTheme,
  useThemeSettings,
  type Cart,
  type Customer,
  type SectionInstance,
  type Store,
  type ThemeSettingsV3,
} from "@numueg/theme-sdk";
import themeManifest from "../theme.json";
import { DemoContext, PageDataContext, type MountPageData } from "./sections/_shared";
import BzWhatsAppFab from "./sections/_whatsapp-fab";
import "./theme.css";

/**
 * MountResult shape. The published @numueg/theme-sdk@0.1.0 doesn't
 * re-export this type yet, so we declare it inline. Matches the host
 * contract documented in ByotThemeBoundary.tsx on the storefront.
 */
interface MountResult {
  cleanup: () => void;
  applyDraft: (next: ThemeSettingsV3) => void;
}

const SECTION_REGISTRY: Record<string, ReturnType<typeof lazy>> = {
  // Home / content sections (Phase 0–2)
  "bz-about-section": lazy(() => import("./sections/bz-about-section")),
  "bz-hero": lazy(() => import("./sections/bz-hero")),
  "bz-image-with-text": lazy(() => import("./sections/bz-image-with-text")),
  "bz-marquee": lazy(() => import("./sections/bz-marquee")),
  "bz-newsletter": lazy(() => import("./sections/bz-newsletter")),
  "bz-promo-banner": lazy(() => import("./sections/bz-promo-banner")),
  "bz-rich-text": lazy(() => import("./sections/bz-rich-text")),
  "bz-testimonials": lazy(() => import("./sections/bz-testimonials")),
  // Chrome + commerce sections (Phase 3 — multipage templates). Header/footer
  // aliased to the GENERIC "header"/"footer" types too, so chrome delivered via
  // section_groups (prefixed OR generic type) always resolves.
  "bz-header": lazy(() => import("./sections/bz-header")),
  "bz-footer": lazy(() => import("./sections/bz-footer")),
  header: lazy(() => import("./sections/bz-header")),
  footer: lazy(() => import("./sections/bz-footer")),
  "bz-featured-collection": lazy(() => import("./sections/bz-featured-collection")),
  "bz-product-grid": lazy(() => import("./sections/bz-product-grid")),
  "bz-product-detail": lazy(() => import("./sections/bz-product-detail")),
  "bz-related-products": lazy(() => import("./sections/bz-related-products")),
  "bz-cart": lazy(() => import("./sections/bz-cart")),
  "bz-search-results": lazy(() => import("./sections/bz-search-results")),
  "bz-not-found": lazy(() => import("./sections/bz-not-found")),
  // Standalone page sections (Phase 4 — V2 parity: about/contact/profile/order)
  "bz-contact": lazy(() => import("./sections/bz-contact")),
  "bz-profile": lazy(() => import("./sections/bz-profile")),
  "bz-order-confirmation": lazy(() => import("./sections/bz-order-confirmation")),
};

function UnknownSection({ type }: { type: string }) {
  return (
    <section
      style={{
        padding: "1rem",
        border: "1px dashed #FFB300",
        fontFamily: "system-ui",
        color: "#0a0a14",
        background: "#FFF5E1",
      }}
    >
      Unknown section: <strong>{type}</strong>
    </section>
  );
}

import {
  resolveSections,
  selectTemplateSections,
  type MaybeOrderedTemplate,
} from "./sections/_template-utils";

const BUILTIN_TEMPLATES = (
  themeManifest as unknown as {
    presets?: { templates?: Record<string, MaybeOrderedTemplate> };
  }
).presets?.templates ?? {};

const isKnownType = (t: string) => Boolean(SECTION_REGISTRY[t]);

function RenderSection({
  instance,
  sectionId,
  groupId,
}: {
  instance: SectionInstance;
  sectionId: string;
  groupId?: string;
}) {
  if (instance.disabled) return null;
  const Component = SECTION_REGISTRY[instance.type];
  if (!Component) {
    return (
      <Section id={sectionId} type={instance.type} groupId={groupId}>
        <UnknownSection type={instance.type} />
      </Section>
    );
  }
  return (
    <Section id={sectionId} type={instance.type} groupId={groupId}>
      <Suspense fallback={<div style={{ minHeight: "20vh" }} />}>
        <Component instance={instance} sectionId={sectionId} />
      </Suspense>
    </Section>
  );
}

function ThemeApp({ currentTemplate }: { currentTemplate: string }) {
  const settings = useThemeSettings();
  const hostTemplate = settings.templates?.[currentTemplate] as
    | MaybeOrderedTemplate
    | undefined;
  const builtinTemplate = BUILTIN_TEMPLATES[currentTemplate];

  // selectTemplateSections handles three cases:
  //   1. host customisation has known sections → render it (filtered).
  //   2. host customisation has only UNKNOWN sections (theme just got
  //      switched, stale data) → fall back to bundled preset.
  //   3. no host customisation at all → use bundled preset.
  const templateSections = selectTemplateSections(
    hostTemplate,
    builtinTemplate,
    isKnownType,
  );

  // Chrome (header/footer) reaches us in TWO possible places: (1) the engine's
  // section_groups.header / .footer (what the V3 customizer writes), or (2)
  // inline in the template's section list (the theme.json builtin preset + a
  // fresh activation seed). Rendering only (2) meant chrome silently vanished
  // once a saved customization moved header/footer into section_groups or
  // shipped a body-only template. Read BOTH, prefer section_groups, and
  // GUARANTEE chrome always renders.
  const groups = settings.section_groups as
    | Record<string, MaybeOrderedTemplate>
    | undefined;
  const HEADER_TYPES = new Set(["bz-header", "header"]);
  const FOOTER_TYPES = new Set(["bz-footer", "footer"]);
  const groupHeader = resolveSections(groups?.header).filter(({ instance }) =>
    isKnownType(instance.type),
  );
  const groupFooter = resolveSections(groups?.footer).filter(({ instance }) =>
    isKnownType(instance.type),
  );

  // a11y: a single <main> landmark. Chrome renders its own <header>/<footer>
  // banners OUTSIDE main (avoids banner/contentinfo nested-in-main warnings);
  // body sections wrap inside it.
  const inlineHeader = templateSections.filter(({ instance }) =>
    HEADER_TYPES.has(instance.type),
  );
  const inlineFooter = templateSections.filter(({ instance }) =>
    FOOTER_TYPES.has(instance.type),
  );
  const body = templateSections.filter(
    ({ instance }) =>
      !HEADER_TYPES.has(instance.type) && !FOOTER_TYPES.has(instance.type),
  );
  // Chrome renders ONLY from real editor data (section_groups preferred, else the
  // in-template header/footer sections). NO synthetic fallback — the preview must
  // never show chrome that isn't an editable section in the customizer.
  const header = groupHeader.length > 0 ? groupHeader : inlineHeader;
  const footer = groupFooter.length > 0 ? groupFooter : inlineFooter;

  return (
    // pb on mobile clears the fixed bottom tab bar (h-14 + safe-area) so the
    // footer / page end is never hidden behind it; no padding on md+ where the
    // tab bar is hidden.
    <div
      data-bazar-v3-app
      data-theme="bazar"
      className="pb-[calc(3.5rem+env(safe-area-inset-bottom,0px))] md:pb-0"
    >
      {/* The separate, editable announcement strip is rendered by bz-header
          (above its nav), driven by the header's announcement message blocks. */}
      {header.map(({ id, instance }) => (
        <RenderSection key={id} sectionId={id} instance={instance} />
      ))}
      <main>
        {body.map(({ id, instance }) => (
          <RenderSection key={id} sectionId={id} instance={instance} />
        ))}
      </main>
      {footer.map(({ id, instance }) => (
        <RenderSection key={id} sectionId={id} instance={instance} />
      ))}
      {/* Floating WhatsApp button (real glyph) — renders only when the merchant
          set a number; raised clear of the mobile tab bar. */}
      <BzWhatsAppFab />
    </div>
  );
}

// ── Host contract: mount(el, ctx) returns a MountResult ────────────────────

/**
 * The host (numu-storefront ByotThemeBoundary) passes mount context as:
 *   { themeSettings, storeData, page, locale }
 *
 * Older drafts of the contract used { store, currentTemplate, ... }.
 * We accept both shapes and normalise so bundles published to the
 * marketplace stay defensive as the host evolves.
 */
export interface MountContext {
  // V3 storefront contract (current host)
  storeData?: Store;
  page?: { type?: string; handle?: string; data?: Record<string, unknown> };
  // Legacy / dev contract
  store?: Store;
  currentTemplate?: string;
  initialCart?: Cart;
  customer?: Customer | null;
  // Common to both
  themeSettings: ThemeSettingsV3;
  /** Optional explicit demo/marketplace-preview flag from the host. When
   *  omitted, the bundle infers it from empty templates (preview ships none). */
  demo?: boolean;
  locale?: string;
  translations?: Record<string, string>;
  [extra: string]: unknown;
}

interface DraftHandle {
  applyDraft: (next: ThemeSettingsV3) => void;
}

/** Normalize the two ctx shapes into one. SDK reads store.currency without
 *  optional chaining and would throw if undefined slipped through. */
function pickStore(ctx: MountContext): Store {
  const s = ctx.storeData ?? ctx.store;
  if (s) return s;
  return {
    id: "unknown",
    name: "Store",
    slug: "store",
    currency: "EGP",
    default_language: "en",
    use_nextjs_storefront: true,
  } as Store;
}

function pickTemplate(ctx: MountContext): string {
  if (typeof ctx.currentTemplate === "string" && ctx.currentTemplate) {
    return ctx.currentTemplate;
  }
  const pageType = ctx.page?.type;
  if (typeof pageType === "string" && pageType) return pageType;
  return "home";
}

export function mount(el: HTMLElement, ctx: MountContext): MountResult {
  return mountTheme(el, ctx, ({ currentTemplate, demo, page }) => (
    <DemoContext.Provider value={demo}>
      <PageDataContext.Provider value={(page as MountPageData | null) ?? null}>
        <ThemeApp currentTemplate={currentTemplate} />
      </PageDataContext.Provider>
    </DemoContext.Provider>
  ));
}

const v3Handle = {
  kind: "v3-mount" as const,
  numu_theme_version: 3 as const,
  mount_returns: "MountResult" as const,
  manifest: { id: "bazar-v3", name: "Bazar (V3)", version: "0.4.5" },
  mount,
};
export default v3Handle;

// ── Dev-only auto-mount ────────────────────────────────────────────────────
//
// In production the storefront host calls `mount(el, ctx)`. In `vite dev`
// nothing calls it, so we'd see an empty page. Bootstrap a minimal context
// against #root so theme developers can iterate locally with `npm run dev`.
//
// We map the URL path → template so clicking links inside the dev preview
// (a product card → `/products/<id>`, the cart icon → `/cart`, …) swaps the
// rendered template instead of always remounting `home`.
function templateFromPath(pathname: string): string {
  if (pathname.startsWith("/products/") || pathname.startsWith("/product/")) {
    return "product";
  }
  if (pathname === "/products") return "products";
  if (pathname === "/cart") return "cart";
  if (pathname === "/checkout") return "checkout";
  if (pathname === "/search") return "search";
  if (pathname === "/collections" || pathname.startsWith("/collections/")) {
    return "collection";
  }
  return "home";
}

if (import.meta.env.DEV && typeof document !== "undefined") {
  const rootEl = document.getElementById("root");
  if (rootEl && !rootEl.dataset.numuMounted) {
    rootEl.dataset.numuMounted = "1";
    const resolveTemplate = () =>
      new URLSearchParams(window.location.search).get("template") ??
      templateFromPath(window.location.pathname);
    const devCtx: MountContext = {
      store: {
        id: "dev-store",
        name: "Bazar",
        slug: "bazar",
        currency: "EGP",
        default_language: "en",
        use_nextjs_storefront: true,
      },
      themeSettings: {
        schema_version: 3,
        theme_id: "bazar-v3",
        global_settings: {},
        templates: {},
        section_groups: {},
      },
      currentTemplate: resolveTemplate(),
    };
    mount(rootEl, devCtx);
  }
}
