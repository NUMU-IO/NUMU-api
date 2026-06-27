/**
 * Bon Younes (V3) — entry point.
 *
 * Renders sections from `themeSettings.templates.<currentTemplate>`
 * (host-provided) or falls back to the manifest's preset home template.
 * Sections lazy-load so each page only pays for what it shows.
 */

import {
  StrictMode,
  Suspense,
  forwardRef,
  lazy,
  useEffect,
  useImperativeHandle,
  useState,
} from "react";
import { createRoot, type Root } from "react-dom/client";
import {
  applyGlobalStyleTokens,
  resolveFontStack,
  NuMuProvider,
  ProductProvider,
  Section,
  useThemeSettings,
  type Cart,
  type Collection,
  type Customer,
  type Product,
  type SectionInstance,
  type Store,
  type ThemeSettingsV3,
} from "@numueg/theme-sdk";
import themeManifest from "../theme.json";
import { DemoContext, PageDataContext, type MountPageData } from "./sections/_shared";

/**
 * MountResult shape. The published @numueg/theme-sdk@0.1.0 doesn't
 * re-export this type yet, so we declare it inline. Matches the host
 * contract documented in `ByotThemeBoundary.tsx` on the storefront.
 */
interface MountResult {
  cleanup: () => void;
  applyDraft: (next: ThemeSettingsV3) => void;
}

const SECTION_REGISTRY: Record<string, ReturnType<typeof lazy>> = {
  "by-header": lazy(() => import("./sections/by-header")),
  "by-hero": lazy(() => import("./sections/by-hero")),
  "by-scroll-story": lazy(() => import("./sections/by-scroll-story")),
  "by-menu": lazy(() => import("./sections/by-menu")),
  "by-product-detail": lazy(() => import("./sections/by-product-detail")),
  "by-footer": lazy(() => import("./sections/by-footer")),
  // Phase 2 — multipage templates
  "by-product-grid": lazy(() => import("./sections/by-product-grid")),
  "by-related-products": lazy(() => import("./sections/by-related-products")),
  "by-cart": lazy(() => import("./sections/by-cart")),
  "by-order-confirmation": lazy(() => import("./sections/by-order-confirmation")),
  "by-rich-text": lazy(() => import("./sections/by-rich-text")),
  "by-not-found": lazy(() => import("./sections/by-not-found")),
  "by-search-results": lazy(() => import("./sections/by-search-results")),
  "by-profile-section": lazy(() => import("./sections/by-profile-section")),
};

function UnknownSection({ type }: { type: string }) {
  return (
    <section
      style={{
        padding: "1rem",
        border: "1px dashed #b07a4a",
        fontFamily: "system-ui",
        color: "#3a2418",
        background: "#fdf8ee",
      }}
    >
      Unknown section: <strong>{type}</strong>
    </section>
  );
}

import { selectTemplateSections, type MaybeOrderedTemplate } from "./sections/_template-utils";

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
  const sections = selectTemplateSections(
    hostTemplate,
    builtinTemplate,
    isKnownType,
  );

  return (
    <div data-bon-younes-v3-app data-theme="bon-younes">
      {sections.map(({ id, instance }) => (
        <RenderSection key={id} sectionId={id} instance={instance} />
      ))}
    </div>
  );
}

/**
 * The host (numu-storefront ByotThemeBoundary) passes mount context as:
 *   { themeSettings, storeData, page, locale }
 *
 * Older drafts of the contract used `{ store, currentTemplate, ... }`.
 * We accept both shapes and normalise — bundles published to the
 * marketplace need to be defensive because the host evolves
 * independently. Anything we don't recognise still flows through via
 * the index signature.
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
  /** Phase 2.4 — store navigation menus keyed by handle (`main-menu`,
   *  `footer`, …), resolved server-side by the host. Forwarded to
   *  NuMuProvider so header/footer `useNavigation(handle)` resolves with
   *  no client round-trip. The `navigation` prop resolves against the
   *  federated host SDK at runtime (like MountResult, it post-dates the
   *  pinned @numueg/theme-sdk@0.1.0 types). */
  navigation?: Record<string, unknown[]>;
  [extra: string]: unknown;
}

interface DraftHandle {
  applyDraft: (next: ThemeSettingsV3) => void;
}

/** Normalize the two ctx shapes into one. */
function pickStore(ctx: MountContext): Store {
  const s = ctx.storeData ?? ctx.store;
  if (s) return s;
  // Last-resort fallback so undefined never reaches NuMuProvider —
  // SDK code reads `store.currency` without optional chaining and
  // would throw. Keeps the bundle alive long enough to render.
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

const ThemeSettingsBridge = forwardRef<
  DraftHandle,
  { ctx: MountContext; mountEl: HTMLElement }
>(function ThemeSettingsBridge({ ctx, mountEl }, ref) {
    const [themeSettings, setThemeSettings] = useState<ThemeSettingsV3>(
      ctx.themeSettings,
    );
    useImperativeHandle(
      ref,
      () => ({
        applyDraft: (next) =>
          setThemeSettings((prev) => (prev === next ? prev : next)),
      }),
      [],
    );
    // Phase 3.5 — bridge global settings (colors/fonts/layout) to CSS custom
    // properties on the mount root so editing them re-paints the storefront.
    // Re-runs on every applyDraft, so the customizer's live preview recolors
    // as the merchant drags a color.
    useEffect(() => {
      const gs = (themeSettings.global_settings ?? {}) as Record<string, unknown>;
      applyGlobalStyleTokens(gs, mountEl);
      // Fonts (#4 → font_picker): the typography fields now use the platform's
      // ~55-font catalog, but SDK applyGlobalStyleTokens only writes
      // `--theme-<id>` for the ~10 fonts in its built-in FONT_REGISTRY (its
      // isFontToken gate). Any catalog font outside that set falls through to
      // the raw string branch — wrong var value (bare name, no font stack) and,
      // critically, no Google Fonts <link> injected, so "fonts don't change
      // anything". resolveFontStack() turns the chosen name into a usable
      // stack AND injects the @font-face link for known faces. We write the
      // EXACT vars styles.css reads (--theme-heading_font / --theme-body_font),
      // overriding whatever the generic pass set. Colors are already handled
      // correctly by applyGlobalStyleTokens (--theme-primary_color etc.), so we
      // don't touch them here.
      const headingFont = gs.heading_font;
      if (typeof headingFont === "string" && headingFont.trim()) {
        mountEl.style.setProperty(
          "--theme-heading_font",
          resolveFontStack(headingFont),
        );
      }
      const bodyFont = gs.body_font;
      if (typeof bodyFont === "string" && bodyFont.trim()) {
        mountEl.style.setProperty(
          "--theme-body_font",
          resolveFontStack(bodyFont),
        );
      }
    }, [themeSettings, mountEl]);
    const store = pickStore(ctx);
    const template = pickTemplate(ctx);
    // Real catalog the host forwards in the mount ctx: home/products routes
    // ship page.data.{products,collections}; the PDP ships page.data.product.
    // Feeding these into NuMuProvider (+ ProductProvider) is what makes
    // useProducts()/useProduct() return the merchant's ACTUAL catalog — without
    // it every data-driven section saw empty real data and fell back to demo
    // content (the phantom coffee products on real stores).
    const pageData = (ctx.page?.data ?? {}) as {
      products?: Product[];
      collections?: Collection[];
      product?: Product;
    };
    // Demo mode: an explicit host flag (ctx.demo) wins when the host sets it;
    // otherwise infer from empty templates — the marketplace preview ships none
    // (so the bundle renders its built-in preset with demo imagery), while the
    // editor + an installed store ship a populated customization (→ neutral
    // placeholders for unconfigured images, never demo coffee photos).
    //
    // KNOWN LIMITATION (hardening tracked for the batch — see
    // SESSION-IMGMODEL-COMPLETE.md): the empty-templates inference can misfire
    // for an installed store whose stored customization diverges from the
    // active bundle's schemas (the storefront's sanitizeAgainstSchemas can then
    // empty `templates`). The robust fix is for the host to pass an explicit
    // `ctx.demo` (true only for the marketplace preview); the bundle already
    // honours it above. Land that host wiring before copying this pattern to
    // the other themes.
    const demo =
      typeof ctx.demo === "boolean"
        ? ctx.demo
        : !themeSettings.templates ||
          Object.keys(themeSettings.templates).length === 0;
    return (
      <DemoContext.Provider value={demo}>
        <PageDataContext.Provider value={(ctx.page as MountPageData | undefined) ?? null}>
          <NuMuProvider
            store={store}
            themeSettings={themeSettings}
            initialCart={ctx.initialCart}
            customer={ctx.customer}
            locale={ctx.locale}
            translations={ctx.translations}
            navigation={ctx.navigation}
            initialProducts={pageData.products}
            initialCollections={pageData.collections}
            currentTemplate={template}
          >
            {pageData.product ? (
              <ProductProvider product={pageData.product}>
                <ThemeApp currentTemplate={template} />
              </ProductProvider>
            ) : (
              <ThemeApp currentTemplate={template} />
            )}
          </NuMuProvider>
        </PageDataContext.Provider>
      </DemoContext.Provider>
    );
  },
);

let currentRoot: Root | null = null;

export function mount(el: HTMLElement, ctx: MountContext): MountResult {
  if (currentRoot) {
    currentRoot.unmount();
    currentRoot = null;
  }
  const root = createRoot(el);
  currentRoot = root;
  const handleRef = { current: null as DraftHandle | null };
  root.render(
    <StrictMode>
      <ThemeSettingsBridge
        ctx={ctx}
        mountEl={el}
        ref={(h) => {
          handleRef.current = h;
        }}
      />
    </StrictMode>,
  );
  return {
    applyDraft: (next) => handleRef.current?.applyDraft(next),
    cleanup: () => {
      root.unmount();
      if (currentRoot === root) currentRoot = null;
      handleRef.current = null;
    },
  };
}

const v3Handle = {
  kind: "v3-mount" as const,
  numu_theme_version: 3 as const,
  mount_returns: "MountResult" as const,
  manifest: {
    id: "bon-younes-v3",
    name: "Bon Younes (V3)",
    version: "0.2.0",
  },
  mount,
};
export default v3Handle;

// ── Dev-only auto-mount ────────────────────────────────────────────────────
//
// In production the storefront host calls `mount(el, ctx)`. In `vite dev`
// nothing calls it, so we'd see an empty page. Bootstrap a minimal context
// against #root so theme developers can iterate locally with just
// `npm run dev`.
//
// We also map the URL path to a template so that clicking on links inside
// the dev preview (e.g. a product card → `/products/<id>`) actually swaps
// the rendered template — otherwise every URL just remounts `home` and the
// preview looks frozen.
function templateFromPath(pathname: string): string {
  if (pathname.startsWith("/products/") || pathname.startsWith("/product/")) {
    return "product";
  }
  if (pathname === "/cart") return "cart";
  if (pathname === "/checkout") return "checkout";
  if (pathname === "/collections" || pathname.startsWith("/collections/")) {
    return "collection";
  }
  if (pathname === "/products") return "products";
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
        name: "Bon Younes",
        slug: "bon-younes",
        currency: "EGP",
        default_language: "en",
        use_nextjs_storefront: true,
        social_links: {
          facebook: "https://www.facebook.com/younesscafee/",
          instagram: "https://instagram.com/younesscafee",
        },
      },
      themeSettings: {
        schema_version: 3,
        theme_id: "bon-younes-v3",
        global_settings: {
          brand_name: "Bon Younes",
          brand_name_ar: "بن يونس",
          currency: "EGP",
        },
        templates: {},
        section_groups: {},
      },
      currentTemplate: resolveTemplate(),
    };
    mount(rootEl, devCtx);

    // Small dev-preview HUD: shows which template is rendered + lets you
    // jump between them without typing URLs. Only injected in dev.
    const hud = document.createElement("nav");
    hud.setAttribute("aria-label", "Dev preview navigation");
    hud.style.cssText = [
      "position:fixed",
      "left:50%",
      "bottom:14px",
      "transform:translateX(-50%)",
      "z-index:9999",
      "background:rgba(58,36,24,.92)",
      "color:#f7f1e8",
      "border-radius:9999px",
      "padding:6px 10px",
      "display:flex",
      "gap:4px",
      "font:500 11px/1 'DM Sans',system-ui,sans-serif",
      "letter-spacing:.12em",
      "text-transform:uppercase",
      "box-shadow:0 10px 30px rgba(0,0,0,.25)",
    ].join(";");
    const buttons: Array<[string, string]> = [
      ["Home", "/"],
      ["Product", "/products/vanilla-latte"],
      ["Cart", "/cart"],
      ["Checkout", "/checkout"],
    ];
    for (const [label, href] of buttons) {
      const b = document.createElement("a");
      b.textContent = label;
      b.href = href;
      b.style.cssText =
        "color:inherit;text-decoration:none;padding:6px 10px;border-radius:9999px";
      b.addEventListener("mouseenter", () => {
        b.style.background = "rgba(247,241,232,.12)";
      });
      b.addEventListener("mouseleave", () => {
        b.style.background = "transparent";
      });
      hud.appendChild(b);
    }
    document.body.appendChild(hud);
  }
}
