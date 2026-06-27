/**
 * Skeuomorphic (V3) — V3 entry point.
 * Dual mount-context shape, sanitised template selection, routes by
 * currentTemplate (home / product / products / about / contact / profile /
 * order-confirmation) exactly like the Vionne V3 exemplar.
 */

import { Suspense, lazy } from "react";
import {
  Section, useThemeSettings, mountTheme,
  type Cart, type Customer, type SectionInstance, type Store, type ThemeSettingsV3,
} from "@numueg/theme-sdk";
import themeManifest from "../theme.json";
// Tailwind-in-bundle: compiles @tailwind directives + the ported V2
// skeuomorphic styles into dist/theme.css (see vite.config.ts /
// tailwind.config.js / postcss.config.js).
import "./theme.css";
import {
  selectTemplateSections, type MaybeOrderedTemplate,
} from "./sections/_template-utils";

interface MountResult {
  cleanup: () => void;
  applyDraft: (next: ThemeSettingsV3) => void;
}

const SECTION_REGISTRY: Record<string, ReturnType<typeof lazy>> = {
  // Home sections
  "skeu-announcement-bar": lazy(() => import("./sections/skeu-announcement-bar")),
  "skeu-hero": lazy(() => import("./sections/skeu-hero")),
  "skeu-categories": lazy(() => import("./sections/skeu-categories")),
  "skeu-featured-collection": lazy(() => import("./sections/skeu-featured-collection")),
  "skeu-promo-banner": lazy(() => import("./sections/skeu-promo-banner")),
  "skeu-testimonials": lazy(() => import("./sections/skeu-testimonials")),
  "skeu-newsletter": lazy(() => import("./sections/skeu-newsletter")),
  // Page-level sections (full V2 parity + Vionne-ported pages)
  "skeu-product-detail": lazy(() => import("./sections/skeu-product-detail")),
  "skeu-products-page": lazy(() => import("./sections/skeu-products-page")),
  "skeu-profile": lazy(() => import("./sections/skeu-profile")),
  "skeu-about": lazy(() => import("./sections/skeu-about")),
  "skeu-contact": lazy(() => import("./sections/skeu-contact")),
  "skeu-order-confirmation-section": lazy(() => import("./sections/skeu-order-confirmation-section")),
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
        <section style={{ padding: "1rem", border: "1px dashed #fb923c", color: "#92400e" }}>
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
    <div data-skeuomorphic-v3-app data-theme="skeuomorphic-v3">
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

export function mount(el: HTMLElement, ctx: MountContext): MountResult {
  return mountTheme(el, ctx, ({ currentTemplate }) => (
    <ThemeApp currentTemplate={currentTemplate} />
  ));
}

const v3Handle = {
  kind: "v3-mount" as const,
  numu_theme_version: 3 as const,
  mount_returns: "MountResult" as const,
  manifest: { id: "skeuomorphic-v3", name: "Skeuomorphic (V3)", version: "0.3.3" },
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
      store: { id: "dev", name: "Skeuomorphic (V3)", slug: "skeuomorphic-v3", currency: "EGP", default_language: "en", use_nextjs_storefront: true },
      themeSettings: { schema_version: 3, theme_id: "skeuomorphic-v3", global_settings: {}, templates: {}, section_groups: {} },
      currentTemplate: params.get("template") ?? tmpl,
    });
  }
}
