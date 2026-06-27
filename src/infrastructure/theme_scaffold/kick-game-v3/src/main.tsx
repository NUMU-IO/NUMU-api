/**
 * Kick game (V3) — V3 entry point.
 * Dual mount-context shape, sanitised template selection, Tailwind-in-bundle.
 *
 * Section components live in src/sections/<type>.tsx and are lazy-loaded so
 * only sections the merchant actually uses pay the bundle cost.
 */

import { Suspense, lazy } from "react";
import {
  NuMuProvider,
  Section,
  useThemeSettings,
  mountTheme,
  type Cart,
  type Customer,
  type SectionInstance,
  type Store,
  type ThemeSettingsV3,
} from "@numueg/theme-sdk";
import themeManifest from "../theme.json";
// Tailwind-in-bundle: compiles @tailwind directives + the ported V2 kick-game
// styles into dist/theme.css (see vite.config.ts / tailwind.config.js).
import "./theme.css";
import {
  selectTemplateSections,
  type MaybeOrderedTemplate,
} from "./sections/_template-utils";

/**
 * MountResult shape. The published @numueg/theme-sdk@0.1.0 doesn't re-export
 * this type yet, so we declare it inline. Matches the host contract documented
 * in ByotThemeBoundary.tsx on the storefront.
 */
interface MountResult {
  cleanup: () => void;
  applyDraft: (next: ThemeSettingsV3) => void;
}

const SECTION_REGISTRY: Record<string, ReturnType<typeof lazy>> = {
  // Home sections (faithful V2 ports)
  "kghero": lazy(() => import("./sections/kghero")),
  "kgcategories": lazy(() => import("./sections/kgcategories")),
  "kgfeatured": lazy(() => import("./sections/kgfeatured")),
  "kgnewsletter": lazy(() => import("./sections/kgnewsletter")),
  "kgpromo-banner": lazy(() => import("./sections/kgpromo-banner")),
  "kgtestimonials": lazy(() => import("./sections/kgtestimonials")),
  // Page-level sections (ported from the proven vionne V3, re-palette'd)
  "kg-product-detail": lazy(() => import("./sections/kg-product-detail")),
  "kg-products-page": lazy(() => import("./sections/kg-products-page")),
  "kg-profile": lazy(() => import("./sections/kg-profile")),
  "kg-order-confirmation": lazy(() => import("./sections/kg-order-confirmation")),
  "kg-about": lazy(() => import("./sections/kg-about")),
  "kg-contact": lazy(() => import("./sections/kg-contact")),
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
    <div data-kick-game-v3-app data-theme="kick-game-v3">
      {sections.map(({ id, instance }) => (
        <RenderSection key={id} sectionId={id} instance={instance} />
      ))}
    </div>
  );
}

export interface MountContext {
  storeData?: Store; store?: Store;
  page?: { type?: string; handle?: string; data?: Record<string, unknown> };
  currentTemplate?: string;
  themeSettings: ThemeSettingsV3;
  initialCart?: Cart; customer?: Customer | null;
  locale?: string; translations?: Record<string, string>;
  [extra: string]: unknown;
}

export function mount(el: HTMLElement, ctx: MountContext) {
  return mountTheme(el, ctx, ({ currentTemplate }) => (
    <ThemeApp currentTemplate={currentTemplate} />
  ));
}

const v3Handle = {
  kind: "v3-mount" as const,
  numu_theme_version: 3 as const,
  mount_returns: "MountResult" as const,
  manifest: { id: "kick-game-v3", name: "Kick game (V3)", version: "0.3.5" },
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
      : path === "/about" ? "about"
      : path === "/contact" ? "contact"
      : path === "/profile" ? "profile"
      : "home";
    mount(rootEl, {
      store: { id: "dev", name: "Kick game (V3)", slug: "kick-game-v3", currency: "EGP", default_language: "en", use_nextjs_storefront: true },
      themeSettings: { schema_version: 3, theme_id: "kick-game-v3", global_settings: {}, templates: {}, section_groups: {} },
      currentTemplate: params.get("template") ?? tmpl,
    });
  }
}
