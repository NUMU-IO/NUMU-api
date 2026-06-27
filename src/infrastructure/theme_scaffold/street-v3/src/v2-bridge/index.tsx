/**
 * v2-bridge — per-theme compat shim for V2 sections ported via
 * `numu-theme migrate`. DELETE this file once each section has been
 * rewritten to use idiomatic V3 SDK hooks + components.
 */

import type { ComponentPropsWithoutRef, ReactNode } from "react";
import {
  useV2Products,
  useV2Categories,
  useV2Auth,
  useV2Language,
  useV2Theme,
} from "@numueg/theme-sdk/v2-compat";
import { ProductCard, useThemeSettings } from "@numueg/theme-sdk";
import type { SectionInstance } from "@numueg/theme-sdk";

// ── Re-shaped V3 hooks under V2 names ─────────────────────────────────────

/** V2's `useProducts` returned `{ products, loading }`. Same shape. */
export function useProducts() {
  return useV2Products();
}

/** V2's `useCategories` returned `{ categories, loading }`. Same shape. */
export function useCategories() {
  return useV2Categories();
}

/** V2's `useAuth` returned `{ user, isAuthenticated }`. */
export function useAuth() {
  return useV2Auth();
}

/** V2's `useLanguage` returned `{ language, direction, setLanguage, t }`. */
export function useLanguage() {
  return useV2Language();
}

/** V2's `useStore` returned the full store config; here we expose
 *  only the parts ported sections use (themeSettings nested). */
export function useStore() {
  return useV2Theme();
}

// ── V2 type re-shaping ────────────────────────────────────────────────────

/**
 * V2 sections expected this prop shape:
 *
 *   interface SectionComponentProps {
 *     section: { id: string; type: string; settings: Record<string, any>; ... };
 *     ...
 *   }
 *
 * V3 mount passes `{ instance: SectionInstance }`. We re-export a
 * compatible interface so existing destructure patterns like
 * `const { section } = props` keep compiling — the wrapper below
 * coerces `instance` → `section` at render time.
 */
export interface SectionComponentProps {
  section: SectionInstance & { id?: string };
}

/**
 * Section adapter. Migrate-generated sections still expect to be
 * called with `{ section }`; the V3 `SECTION_REGISTRY` calls them
 * with `{ instance }`. Wrap each export:
 *
 *   export default v2Section(YourComponent);
 */
export function v2Section<P extends SectionComponentProps>(
  Component: (props: P) => ReactNode,
): (props: { instance: SectionInstance }) => ReactNode {
  return function V2SectionAdapter({ instance }) {
    const props = { section: instance } as unknown as P;
    return Component(props);
  };
}

// ── editable.section() compat ────────────────────────────────────────────

/**
 * V2's editable helper spread DOM props on individual nodes so the
 * old V2 customizer could click-select them. V3 uses `<EditableText>`
 * / `<EditableImage>` for the same job. Returning an empty object
 * here means the spread is a no-op — the section still renders, just
 * without the inline click-to-edit affordance. Replace with the V3
 * components when polishing the port.
 */
export const editable = {
  section: (_sectionId: string, _key: string) => ({}),
  block: (_blockId: string, _key: string) => ({}),
};

// ── Misc re-exports the V2 sections often imported ────────────────────────

export { ProductCard, useThemeSettings };

/**
 * V2's `<Link to="/...">` came from react-router-dom. The V3 storefront
 * is Next.js, where `<a href="/...">` works (Next intercepts internal
 * navigation). We expose a polyfill so existing JSX renders without
 * react-router-dom installed. Pass-through any extra props.
 */
export function Link({
  to,
  children,
  ...rest
}: { to: string; children: ReactNode } & Omit<
  ComponentPropsWithoutRef<"a">,
  "href"
>) {
  return (
    <a href={to} {...rest}>
      {children}
    </a>
  );
}

/** V2's image placeholder constant. */
export const PLACEHOLDER_HERO =
  "https://images.unsplash.com/photo-1483985988355-763728e1935b?auto=format&fit=crop&w=1200&q=60";
