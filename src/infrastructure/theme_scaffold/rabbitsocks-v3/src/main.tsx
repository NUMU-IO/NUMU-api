/**
 * Rabbitsocks (V3) — V3 entry point.
 * Dual mount-context shape, sanitised template selection.
 *
 * Section components live in src/sections/<type>.tsx and are lazy-loaded so
 * only sections the merchant actually uses pay the bundle cost.
 */

import { Suspense, lazy } from "react";
import {
  Section, useThemeSettings, mountTheme,
  type Cart, type Customer, type SectionInstance, type Store, type ThemeSettingsV3,
} from "@numueg/theme-sdk";
import themeManifest from "../theme.json";
// Tailwind-in-bundle: compiles @tailwind directives + ported V2 rabbitsocks
// styles into dist/theme.css (see vite.config.ts / tailwind.config.js).
import "./theme.css";
import {
  selectTemplateSections, type MaybeOrderedTemplate,
} from "./sections/_template-utils";

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
  // ── Home sections (faithful V2 ports) ──────────────────────────────
  "rs-hero": lazy(() => import("./sections/rs-hero")),
  "rs-marquee": lazy(() => import("./sections/rs-marquee")),
  "rs-featured": lazy(() => import("./sections/rs-featured")),
  "rs-categories": lazy(() => import("./sections/rs-categories")),
  "rs-image-with-text": lazy(() => import("./sections/rs-image-with-text")),
  "rs-promo-banner": lazy(() => import("./sections/rs-promo-banner")),
  "rs-newsletter": lazy(() => import("./sections/rs-newsletter")),
  "rs-editorial": lazy(() => import("./sections/rs-editorial")),
  // ── Page-level sections (full V2 parity) ───────────────────────────
  "rs-product-detail": lazy(() => import("./sections/rs-product-detail")),
  "rs-products-page": lazy(() => import("./sections/rs-products-page")),
  "rs-profile": lazy(() => import("./sections/rs-profile")),
  "rs-order-confirmation-section": lazy(() => import("./sections/rs-order-confirmation-section")),
};

const isKnownType = (t: string) => Boolean(SECTION_REGISTRY[t]);

const BUILTIN_TEMPLATES = (
  themeManifest as unknown as { presets?: { templates?: Record<string, MaybeOrderedTemplate> } }
).presets?.templates ?? {};

function RenderSection({ instance, sectionId, groupId }: {
  instance: SectionInstance; sectionId: string; groupId?: string;
}) {
  if (instance.disabled) return null;
  const Component = SECTION_REGISTRY[instance.type];
  if (!Component) {
    return (
      <Section id={sectionId} type={instance.type} groupId={groupId}>
        <section style={{ padding: "1rem", border: "1px dashed #fb923c", color: "#666" }}>
          Unknown section: <strong>{instance.type}</strong>
        </section>
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
  const hostTemplate = settings.templates?.[currentTemplate] as MaybeOrderedTemplate | undefined;
  const builtinTemplate = BUILTIN_TEMPLATES[currentTemplate];
  const sections = selectTemplateSections(hostTemplate, builtinTemplate, isKnownType);
  return (
    <div data-rabbitsocks-v3-app data-theme="rabbitsocks-v3">
      {sections.map(({ id, instance }) => (
        <RenderSection key={id} sectionId={id} instance={instance} />
      ))}
    </div>
  );
}

// ── Host contract: mount(el, ctx) returns a MountResult ────────────────────

/**
 * The host (numu-storefront ByotThemeBoundary) passes mount context as:
 *   { themeSettings, storeData, page, locale }
 *
 * Older drafts of the contract used { store, currentTemplate, ... }.
 * We accept both shapes and normalise via pickStore() so bundles
 * published to the marketplace stay defensive as the host evolves.
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
  locale?: string;
  translations?: Record<string, string>;
  [extra: string]: unknown;
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
  if (typeof ctx.currentTemplate === "string" && ctx.currentTemplate) return ctx.currentTemplate;
  const pt = ctx.page?.type;
  if (typeof pt === "string" && pt) return pt;
  return "home";
}

export function mount(el: HTMLElement, ctx: MountContext): MountResult {
  return mountTheme(el, ctx, ({ currentTemplate }) => (
    <ThemeApp currentTemplate={currentTemplate} />
  ));
}

const v3Handle = {
  kind: "v3-mount" as const,
  numu_theme_version: 3 as const,
  mount_returns: "MountResult" as const,
  manifest: { id: "rabbitsocks-v3", name: "Rabbitsocks (V3)", version: "0.3.3" },
  mount,
};
export default v3Handle;

// Dev-only auto-mount
if (import.meta.env.DEV && typeof document !== "undefined") {
  const rootEl = document.getElementById("root");
  if (rootEl && !rootEl.dataset.numuMounted) {
    rootEl.dataset.numuMounted = "1";
    const params = new URLSearchParams(window.location.search);
    const path = window.location.pathname;
    const tmpl = path.startsWith("/product/") ? "product"
      : path === "/cart" ? "cart"
      : path === "/checkout" ? "checkout"
      : path === "/products" ? "products"
      : path === "/profile" ? "profile"
      : "home";
    mount(rootEl, {
      store: { id: "dev", name: "Rabbitsocks (V3)", slug: "rabbitsocks-v3", currency: "EGP", default_language: "en", use_nextjs_storefront: true } as Store,
      themeSettings: { schema_version: 3, theme_id: "rabbitsocks-v3", global_settings: {}, templates: {}, section_groups: {} },
      currentTemplate: params.get("template") ?? tmpl,
    });
  }
}
