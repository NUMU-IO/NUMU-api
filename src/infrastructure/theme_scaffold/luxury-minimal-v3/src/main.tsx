/**
 * Luxury Minimal (V3) — entry point.
 *
 * A FAITHFUL V3 port of the V2 luxury-minimal theme: same refined monochrome
 * look, now driven entirely by the V3 theme engine (schema-driven editor,
 * inline editing, global-settings tokens, no-404 catch-all templates).
 *
 * Renders sections from `themeSettings.templates.<currentTemplate>`
 * (host-provided) or falls back to the manifest's built-in preset template.
 * `pickTemplate(ctx.page.type)` routes between the multipage set; each
 * template's section list lives in theme.json's `presets.templates`.
 */

import { Suspense, lazy } from "react";
import {
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
// Tailwind-in-bundle: compiles @tailwind directives + the ported V2
// luxury-minimal styles into dist/theme.css (see vite.config.ts).
import "./theme.css";
import {
  resolveSections,
  selectTemplateSections,
  type MaybeOrderedTemplate,
} from "./sections/_template-utils";
import { DemoContext, PageDataContext, type MountPageData } from "./sections/_shared";

interface MountResult {
  cleanup: () => void;
  applyDraft: (next: ThemeSettingsV3) => void;
}

const SECTION_REGISTRY: Record<string, ReturnType<typeof lazy>> = {
  // Chrome — header / footer (on every template). Aliased to the GENERIC
  // "header"/"footer" types too, so chrome delivered via section_groups (which
  // can carry either the theme-prefixed or the generic type) always resolves.
  "lux-header": lazy(() => import("./sections/lux-header")),
  "lux-footer": lazy(() => import("./sections/lux-footer")),
  header: lazy(() => import("./sections/lux-header")),
  footer: lazy(() => import("./sections/lux-footer")),
  // Home / content sections.
  "lux-hero": lazy(() => import("./sections/lux-hero")),
  "lux-categories": lazy(() => import("./sections/lux-categories")),
  "lux-featured-collection": lazy(() => import("./sections/lux-featured-collection")),
  "lux-promo-banner": lazy(() => import("./sections/lux-promo-banner")),
  "lux-testimonials": lazy(() => import("./sections/lux-testimonials")),
  "lux-newsletter": lazy(() => import("./sections/lux-newsletter")),
  "lux-rich-text": lazy(() => import("./sections/lux-rich-text")),
  // Commerce templates.
  "lux-product-detail": lazy(() => import("./sections/lux-product-detail")),
  "lux-product-grid": lazy(() => import("./sections/lux-product-grid")),
  "lux-cart": lazy(() => import("./sections/lux-cart")),
  "lux-search-results": lazy(() => import("./sections/lux-search-results")),
  "lux-not-found": lazy(() => import("./sections/lux-not-found")),
  // Account templates.
  "lux-profile": lazy(() => import("./sections/lux-profile")),
  "lux-order-confirmation": lazy(() => import("./sections/lux-order-confirmation")),
};

const isKnownType = (t: string) => Boolean(SECTION_REGISTRY[t]);

function UnknownSection({ type }: { type: string }) {
  return (
    <section
      style={{
        padding: "1rem",
        border: "1px dashed #b8860b",
        fontFamily: "'Helvetica Neue', Helvetica, Arial, sans-serif",
        color: "#1a1a1a",
        background: "#fafafa",
      }}
    >
      Unknown section: <strong>{type}</strong>
    </section>
  );
}

const BUILTIN_TEMPLATES = (
  themeManifest as unknown as {
    presets?: { templates?: Record<string, MaybeOrderedTemplate> };
  }
).presets?.templates ?? {};

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

const HEADER_TYPES = new Set(["lux-header", "header"]);
const FOOTER_TYPES = new Set(["lux-footer", "footer"]);

function ThemeApp({ currentTemplate }: { currentTemplate: string }) {
  const settings = useThemeSettings();
  const hostTemplate = settings.templates?.[currentTemplate] as
    | MaybeOrderedTemplate
    | undefined;
  const builtinTemplate = BUILTIN_TEMPLATES[currentTemplate];

  // selectTemplateSections handles: (1) host customisation with known sections,
  // (2) host customisation with only UNKNOWN sections (stale, just-switched
  // theme) → bundled preset, (3) no host customisation → bundled preset.
  const templateSections = selectTemplateSections(
    hostTemplate,
    builtinTemplate,
    isKnownType,
  );

  // Chrome (header/footer) reaches us in TWO possible places:
  //   (1) themeSettings.section_groups.header / .footer — the canonical
  //       Shopify-style home for cross-template chrome (what the V3 customizer
  //       writes), OR
  //   (2) inline in the template's own section list (the theme.json builtin
  //       preset + a fresh activation seed).
  // Rendering only (2) meant that once a saved customization moved chrome into
  // section_groups — or shipped a body-only template — the header + footer
  // silently disappeared. Read BOTH, prefer section_groups, and GUARANTEE chrome
  // always renders: this theme ships an editable header + footer on every page.
  const groups = settings.section_groups as
    | Record<string, MaybeOrderedTemplate>
    | undefined;
  const groupHeader = resolveSections(groups?.header).filter(({ instance }) =>
    isKnownType(instance.type),
  );
  const groupFooter = resolveSections(groups?.footer).filter(({ instance }) =>
    isKnownType(instance.type),
  );

  const inlineHeader = templateSections.filter(({ instance }) =>
    HEADER_TYPES.has(instance.type),
  );
  const inlineFooter = templateSections.filter(({ instance }) =>
    FOOTER_TYPES.has(instance.type),
  );
  // a11y: a single <main> landmark. Chrome renders its own <header>/<footer>
  // banners OUTSIDE <main>; body sections wrap inside it.
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
    <div data-luxury-minimal-v3-app data-theme="luxury-minimal">
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
    </div>
  );
}

// ── Host contract: mount(el, ctx) returns a MountResult ────────────────────
//
// The host (numu-storefront ByotThemeBoundary) passes mount context as
// `{ themeSettings, storeData, page, locale }`. Older drafts used
// `{ store, currentTemplate, ... }`. mountTheme accepts both shapes and
// normalises; we just hand it our render callback.
export interface MountContext {
  storeData?: Store;
  store?: Store;
  page?: { type?: string; handle?: string; data?: Record<string, unknown> };
  currentTemplate?: string;
  themeSettings: ThemeSettingsV3;
  initialCart?: Cart;
  customer?: Customer | null;
  demo?: boolean;
  locale?: string;
  translations?: Record<string, string>;
  [extra: string]: unknown;
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
  manifest: {
    id: "luxury-minimal-v3",
    name: "Luxury Minimal (V3)",
    version: "0.6.1",
  },
  mount,
};
export default v3Handle;

// ── Dev-only auto-mount ────────────────────────────────────────────────────
// In production the host calls `mount(el, ctx)`. In `vite dev` nothing does,
// so bootstrap a minimal context against #root for local iteration. The URL
// path → template mapping lets clicking links swap the rendered template.
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
        name: "Luxury Minimal",
        slug: "luxury-minimal",
        currency: "EGP",
        default_language: "en",
        use_nextjs_storefront: true,
      } as Store,
      themeSettings: {
        schema_version: 3,
        theme_id: "luxury-minimal-v3",
        global_settings: {},
        templates: {},
        section_groups: {},
      },
      currentTemplate: resolveTemplate(),
    };
    mount(rootEl, devCtx);
  }
}
