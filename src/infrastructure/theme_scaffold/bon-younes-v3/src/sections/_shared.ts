import type { CSSProperties } from "react";
/**
 * Shared helpers for Bon Younes V3 sections.
 *
 * All sections receive `{ instance, sectionId }` from main.tsx; the
 * registry signature matches what `<RenderSection>` passes in.
 */

import { createContext, useContext, useMemo } from "react";
import {
  CollectionContext,
  ProductContext,
  resolveSettingsMap,
  ShopContext,
  type DynamicResolveContext,
  type SectionInstance,
} from "@numueg/theme-sdk";

export interface SectionRenderProps {
  instance: SectionInstance;
  sectionId: string;
}

/**
 * "Demo mode" — true ONLY in the marketplace preview ("try before you use"),
 * where the host ships an EMPTY `templates` and the bundle renders its built-in
 * preset to showcase the theme with demo (coffee) imagery. In the editor and on
 * an installed store the host ships a populated customization, so demo is false
 * and unconfigured images fall back to a NEUTRAL PLACEHOLDER instead of demo
 * coffee photos (a clothing merchant must not inherit coffee pictures). Every
 * image stays editable via its image_picker; this only governs the *fallback*
 * when the merchant hasn't set one. Provided by main.tsx, read by sections.
 */
export const DemoContext = createContext<boolean>(false);
export const useDemo = (): boolean => useContext(DemoContext);

/**
 * Host-provided page context (Phase 4.4b). The storefront's
 * /pages/[handle] route passes the resolved CMS page record as
 * `ctx.page = { type:"page", handle, title, data:{ page:{...} } }`.
 * bon-younes mirrors it into this context so content sections (by-rich-text)
 * can render the real page body instead of their own default copy. Null on
 * non-page routes — sections then use their own settings.
 */
export interface MountPageData {
  type?: string;
  handle?: string;
  title?: string;
  data?: {
    page?: {
      handle?: string;
      title?: string | null;
      body?: string | null;
      title_i18n?: Record<string, string> | null;
      body_i18n?: Record<string, string> | null;
      seo?: unknown;
    };
  };
}
export const PageDataContext = createContext<MountPageData | null>(null);
export const usePageData = (): MountPageData | null => useContext(PageDataContext);

/**
 * Neutral "add an image" placeholder (cream box + muted glyph, matches the
 * Bon Younes palette). Inline data-URI so the bundle ships no asset files.
 */
export const PLACEHOLDER_IMG =
  "data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20viewBox='0%200%20400%20400'%3E%3Crect%20width='400'%20height='400'%20fill='%23efe9df'/%3E%3Cg%20fill='none'%20stroke='%23bcae97'%20stroke-width='9'%20stroke-linecap='round'%20stroke-linejoin='round'%3E%3Crect%20x='112'%20y='126'%20width='176'%20height='148'%20rx='12'/%3E%3Ccircle%20cx='160'%20cy='172'%20r='17'/%3E%3Cpath%20d='M124%20256l46-46%2036%2031%2042-52%2052%2067'/%3E%3C/g%3E%3C/svg%3E";

/**
 * Turn a demo fallback array into a NEUTRAL placeholder array: every image-ish
 * field → the placeholder glyph, every text-ish field → empty. Used by sections
 * so that, when NOT in demo mode and the merchant has configured nothing, the
 * section still renders its layout but with blank, on-brand placeholders the
 * merchant then fills in — instead of the theme's demo coffee content.
 */
export function placeholderize<T extends Record<string, unknown>>(items: T[]): T[] {
  return items.map((item) => {
    const out: Record<string, unknown> = { ...item };
    for (const key of Object.keys(out)) {
      if (/(image|img|photo|src|thumbnail|background)/i.test(key)) {
        out[key] = PLACEHOLDER_IMG;
      } else if (/(price|compare_at|compareat|mrp)/i.test(key)) {
        // Prices may be NUMERIC or string. The numeric case previously
        // survived the string-only guard below, leaking a demo price onto
        // placeholder cards on a no-product install — zero/blank both.
        out[key] = typeof out[key] === "number" ? 0 : "";
      } else if (
        typeof out[key] === "string" &&
        /(label|title|name|heading|caption|body|text|binomial|subtitle|eyebrow|badge|tag)/i.test(key)
      ) {
        out[key] = "";
      }
    }
    return out as T;
  });
}

/**
 * Canonical fallback gate. demo → the demo array; real store → [].
 *
 * On a REAL installed store (demo=false) a section must render the
 * merchant's actual data or a real empty-state — NEVER demo/placeholder
 * shapes. Returning [] makes every `real.length ? real : demoOrPlaceholder(...)`
 * collapse to the real-empty path, so phantom products, "0 EGP" cards and
 * demo addons/panels never appear on a live store. (`placeholderize` stays
 * for per-image neutral fallbacks on REAL items — see PLACEHOLDER_IMG.)
 */
export function demoOrPlaceholder<T extends Record<string, unknown>>(
  demo: boolean,
  items: T[],
): T[] {
  return demo ? items : [];
}

/**
 * ENG-3: pick the locale-appropriate DEFAULT copy. Merchant-entered values
 * still win because callers do `asString(s.x) || localized(locale, en, ar)`.
 * Only widens the hardcoded empty-state default a section renders when the
 * merchant hasn't typed a value — under `?locale=ar` (RTL) Arabic shows, else
 * English. (Strings already wired through `useTranslation(...)` keep using the
 * locale catalog; this is for the remaining HARDCODED literals.)
 */
export function localized(locale: string | undefined, en: string, ar: string): string {
  return (locale || "").toLowerCase().startsWith("ar") ? ar : en;
}

export function asString(v: unknown, fallback = ""): string {
  return typeof v === "string" ? v : fallback;
}

export function asNumber(v: unknown, fallback = 0): number {
  return typeof v === "number" && Number.isFinite(v) ? v : fallback;
}

export function asBool(v: unknown, fallback = false): boolean {
  return typeof v === "boolean" ? v : fallback;
}

export function asArray<T = unknown>(v: unknown): T[] {
  return Array.isArray(v) ? (v as T[]) : [];
}

/** Treat an unknown value as a property bag for reading editor blocks. */
export function asRecord(v: unknown): Record<string, unknown> {
  return v && typeof v === "object"
    ? (v as Record<string, unknown>)
    : {};
}

export function pickItems<T = Record<string, unknown>>(
  s: Record<string, unknown>,
  key: string,
  fallback: T[],
): T[] {
  const raw = asArray<T>(s[key]);
  return raw.length > 0 ? raw : fallback;
}

/**
 * Read an image-picker value. The editor stores image_picker settings as
 * either a plain URL string (legacy) or an `{ url, alt }` object (current).
 * Always returns a usable URL string.
 */
export function asImageUrl(v: unknown, fallback = ""): string {
  if (typeof v === "string") return v;
  if (v && typeof v === "object") {
    const r = v as Record<string, unknown>;
    if (typeof r.url === "string") return r.url;
    if (typeof r.src === "string") return r.src;
  }
  return fallback;
}

/** Alt text for an image-picker value (only present on the object shape). */
export function asImageAlt(v: unknown, fallback = ""): string {
  if (v && typeof v === "object") {
    const r = v as Record<string, unknown>;
    if (typeof r.alt === "string") return r.alt;
  }
  return fallback;
}

interface RawBlock {
  type?: string;
  disabled?: boolean;
  settings?: Record<string, unknown>;
  // Nested children (blocks-in-blocks): a block may hold its own block
  // container, e.g. a footer "column" holding "link" blocks.
  blocks?: Record<string, RawBlock>;
  block_order?: string[];
}

/** A block node incl. its nested children — for components that recurse
 *  (footer columns → link blocks, mega-menus, multi-column layouts). */
export interface BlockNode {
  type: string;
  settings: Record<string, unknown>;
  blocks: Record<string, RawBlock>;
  block_order: string[];
}

/**
 * Read a section's blocks of a given type, in editor order, skipping
 * disabled ones. This is the canonical way to render repeatable content
 * (drink cards, story panels, nav items, footer columns, addons): the
 * customizer's block CRUD writes `instance.blocks` + `instance.block_order`,
 * so components MUST read from there — reading `instance.settings.<list>`
 * silently ignores everything the merchant adds in the editor.
 *
 * Returns each block's `settings` bag (use asString/asImageUrl on the
 * fields). Empty array when the section has no blocks of that type → the
 * caller falls back to its demo defaults.
 */
export function readBlocks(
  instance: SectionInstance,
  type: string,
): Record<string, unknown>[] {
  const inst = instance as unknown as {
    blocks?: Record<string, RawBlock>;
    block_order?: string[];
  };
  const blocks = inst.blocks ?? {};
  const order =
    inst.block_order && inst.block_order.length > 0
      ? inst.block_order
      : Object.keys(blocks);
  return order
    .map((id) => blocks[id])
    .filter((b): b is RawBlock => !!b && b.type === type && !b.disabled)
    .map((b) => b.settings ?? {});
}

/**
 * Like {@link readBlocks} but returns each block's FULL node (settings +
 * nested children), so a component can recurse into blocks-in-blocks. The
 * `container` is either a section instance or a parent block node — both
 * expose `blocks` + `block_order`.
 */
export function readBlockNodes(
  container:
    | SectionInstance
    | { blocks?: Record<string, RawBlock>; block_order?: string[] },
  type: string,
): BlockNode[] {
  const inst = container as {
    blocks?: Record<string, RawBlock>;
    block_order?: string[];
  };
  const blocks = inst.blocks ?? {};
  const order =
    inst.block_order && inst.block_order.length > 0
      ? inst.block_order
      : Object.keys(blocks);
  return order
    .map((id) => blocks[id])
    .filter((b): b is RawBlock => !!b && b.type === type && !b.disabled)
    .map((b) => ({
      type: b.type as string,
      settings: b.settings ?? {},
      blocks: b.blocks ?? {},
      block_order: b.block_order ?? [],
    }));
}

/**
 * Dynamic-source resolution for BLOCK settings — parity with the SDK's
 * useResolvedSettings for SECTION settings.
 *
 * readBlocks/readBlockNodes return RAW block settings, so a block field
 * the merchant bound to a dynamic source (`{ __numu_source: "store.name" }`,
 * "product.title", …) arrives as that object and asString() yields "" —
 * silently dropping the configured content (footer columns/links, story
 * panels, nav items, menu tabs, addons). Sections resolve their OWN
 * settings via useResolvedSettings; blocks never got that pass. These
 * helpers close the gap WITHOUT an SDK change (the dist already exports
 * resolveSettingsMap + the three resource contexts).
 *
 * Usage: call useBlockResolveContext() ONCE at the top of the section
 * component (it reads React context → must be a hook), then pass the ctx
 * into the pure resolveBlocks/resolveBlockNodes, which are safe inside
 * .map()/loops (the footer recurses into nested link blocks).
 */
export function useBlockResolveContext(): DynamicResolveContext {
  const product = useContext(ProductContext) as DynamicResolveContext["product"];
  const collection = useContext(
    CollectionContext,
  ) as DynamicResolveContext["collection"];
  const store = useContext(ShopContext) as DynamicResolveContext["store"];
  return useMemo(
    () => ({ product, collection, store }),
    [product, collection, store],
  );
}

/** {@link readBlocks} + dynamic-source resolution of each block's settings. */
export function resolveBlocks(
  instance: SectionInstance,
  type: string,
  ctx: DynamicResolveContext,
): Record<string, unknown>[] {
  return readBlocks(instance, type).map((s) => resolveSettingsMap(s, ctx));
}

/**
 * {@link readBlockNodes} + resolution of each node's OWN settings. Nested
 * children stay raw — recurse `resolveBlockNodes(node, childType, ctx)` at
 * the call site (e.g. footer column → link blocks) with the same ctx.
 */
export function resolveBlockNodes(
  container:
    | SectionInstance
    | { blocks?: Record<string, RawBlock>; block_order?: string[] },
  type: string,
  ctx: DynamicResolveContext,
): BlockNode[] {
  return readBlockNodes(container, type).map((n) => ({
    ...n,
    settings: resolveSettingsMap(n.settings, ctx),
  }));
}

/** Compose a product detail URL the SDK's <Link> understands. */
export function productHref(slugOrId: string | undefined | null): string {
  if (!slugOrId) return "/products";
  return `/products/${slugOrId}`;
}


// ── Non-destructive image transform (focal / zoom / rotation) — Phase 2 ──────
// Mirror of merchant-hub imageTransform.ts (and bazar _shared). Keep
// applyImageTransform in sync so the editor preview == the storefront render.
// Identity-safe: with no transform it returns {}, so images render unchanged.
export interface ImageTransform {
  v: 1;
  focal?: { x: number; y: number };
  zoom?: number;
  rotation?: number;
  fit?: "cover" | "contain";
}
const _clampImgT = (n: number, lo: number, hi: number): number =>
  Math.min(hi, Math.max(lo, Number.isFinite(n) ? n : lo));
export function asImageTransform(v: unknown): ImageTransform | undefined {
  if (v && typeof v === "object" && "transform" in v) {
    const t = (v as { transform?: unknown }).transform;
    if (t && typeof t === "object") return t as ImageTransform;
  }
  return undefined;
}
export function applyImageTransform(
  t: ImageTransform | undefined | null,
  fit: "cover" | "contain" = "cover",
): CSSProperties {
  if (!t) return {};
  const fx = Math.round(_clampImgT(t.focal?.x ?? 0.5, 0, 1) * 1e4) / 100;
  const fy = Math.round(_clampImgT(t.focal?.y ?? 0.5, 0, 1) * 1e4) / 100;
  const zoom = _clampImgT(t.zoom ?? 1, 1, 4);
  const rot = ((t.rotation ?? 0) % 360 + 360) % 360;
  const effFit = t.fit ?? fit;
  const style: CSSProperties = {
    transform: `scale(${zoom}) rotate(${rot}deg)`,
    transformOrigin: `${fx}% ${fy}%`,
    objectFit: effFit,
  };
  if (effFit === "cover") style.objectPosition = `${fx}% ${fy}%`;
  return style;
}
