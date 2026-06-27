/**
 * Shared helpers for Bazar V3 sections.
 *
 * All sections receive `{ instance, sectionId }` from main.tsx; the
 * registry signature matches what `<RenderSection>` passes in.
 *
 * Ported from the Bon Younes V3 structural template (same helper surface):
 * only the CSS prefix (`by-` → `bz-`) and the placeholder palette differ.
 */

import { createContext, useContext, type CSSProperties } from "react";
import type { SectionInstance } from "@numueg/theme-sdk";

export interface SectionRenderProps {
  instance: SectionInstance;
  sectionId: string;
}

/**
 * "Demo mode" — true ONLY in the marketplace preview ("try before you use"),
 * where the host ships an EMPTY `templates` and the bundle renders its built-in
 * preset to showcase the theme with demo imagery. In the editor and on an
 * installed store the host ships a populated customization, so demo is false
 * and unconfigured images fall back to a NEUTRAL PLACEHOLDER instead of the
 * theme's demo photos (a merchant must not inherit the demo's pictures). Every
 * image stays editable via its image_picker; this only governs the *fallback*
 * when the merchant hasn't set one. Provided by main.tsx, read by sections.
 */
export const DemoContext = createContext<boolean>(false);
export const useDemo = (): boolean => useContext(DemoContext);

/**
 * Host-provided page context (Phase 4.4b parity with bon-younes). The
 * storefront's /pages/[handle] route passes the resolved CMS page record as
 * `ctx.page = { type:"page", handle, title, data:{ page:{...} } }`. Bazar
 * mirrors it into this context so content sections (bz-rich-text) render the
 * real page title + body instead of their own default copy. Null on non-page
 * routes — sections then fall back to their own settings.
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
    /** Legal policy page (/policies/<handle>): host sets page.type "policy"
     *  with data.policy.{title,body}. */
    policy?: unknown;
  };
}
export const PageDataContext = createContext<MountPageData | null>(null);
export const usePageData = (): MountPageData | null =>
  useContext(PageDataContext);

/**
 * Neutral "add an image" placeholder, on-brand for Bazar (cream box + amber
 * glyph). Inline data-URI so the bundle ships no asset files.
 */
export const PLACEHOLDER_IMG =
  "data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20viewBox='0%200%20400%20400'%3E%3Crect%20width='400'%20height='400'%20fill='%23FFF5E1'/%3E%3Cg%20fill='none'%20stroke='%23FFB300'%20stroke-width='10'%20stroke-linecap='round'%20stroke-linejoin='round'%3E%3Crect%20x='110'%20y='124'%20width='180'%20height='152'%20rx='14'/%3E%3Ccircle%20cx='160'%20cy='172'%20r='18'/%3E%3Cpath%20d='M122%20258l48-48%2038%2032%2044-54%2056%2070'/%3E%3C/g%3E%3C/svg%3E";

/**
 * Turn a demo fallback array into a NEUTRAL placeholder array: every image-ish
 * field → the placeholder glyph, every text-ish field → empty. Used by sections
 * so that, when NOT in demo mode and the merchant has configured nothing, the
 * section still renders its layout but with blank, on-brand placeholders the
 * merchant then fills in — instead of the theme's demo content.
 */
export function placeholderize<T>(items: T[]): T[] {
  return items.map((item) => {
    const out: Record<string, unknown> = { ...(item as Record<string, unknown>) };
    for (const key of Object.keys(out)) {
      if (/(image|img|photo|src|thumbnail|background)/i.test(key)) {
        out[key] = PLACEHOLDER_IMG;
      } else if (
        typeof out[key] === "string" &&
        /(label|title|name|heading|caption|body|text|binomial|subtitle|eyebrow|price|city|quote|description)/i.test(key)
      ) {
        out[key] = "";
      }
    }
    return out as T;
  });
}

/** demo ? items : placeholderize(items) — the canonical fallback gate. */
export function demoOrPlaceholder<T>(demo: boolean, items: T[]): T[] {
  return demo ? items : placeholderize(items);
}

/**
 * ENG-3: pick the locale-appropriate default copy. Merchant-entered values
 * still win because callers do `asString(s.x) || localized(locale, en, ar)`.
 * Only the empty-state DEFAULT is locale-driven; the editable setting is
 * untouched. `locale` comes from the SDK's `useLocale()` (active visitor
 * locale, e.g. "en" | "ar").
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
}

/**
 * Read a section's blocks of a given type, in editor order, skipping
 * disabled ones. This is the canonical way to render repeatable content
 * (value cards, testimonial reviews): the customizer's block CRUD writes
 * `instance.blocks` + `instance.block_order`, so components MUST read from
 * there — reading `instance.settings.<list>` silently ignores everything the
 * merchant adds in the editor.
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

/** Compose a product detail URL the SDK's <Link> understands. */
export function productHref(slugOrId: string | undefined | null): string {
  if (!slugOrId) return "/products";
  return `/products/${slugOrId}`;
}

// ── Non-destructive image transform (focal / zoom / rotation) ───────────────
//
// An image setting value may carry optional `transform` metadata
// (`{ url, alt, transform }`). The original asset is never modified — the
// storefront reproduces the framing purely from these numbers via CSS, so the
// SAME uploaded image can be framed differently per placement (hero vs card).
//
// ⚠ This is a MIRROR of the merchant-hub editor's
// `numo-merchant-hub/src/features/theme-editor-v3/components/inputs/imageTransform.ts`
// — `applyImageTransform` MUST stay equivalent so the editor preview matches the
// storefront render exactly. (Phase 2 hoists this into @numueg/theme-sdk so every
// theme imports one copy.)
export interface ImageTransform {
  v: 1;
  focal?: { x: number; y: number }; // 0..1, default center
  zoom?: number;                    // 1..4, default 1
  rotation?: number;                // degrees, default 0
  fit?: "cover" | "contain";
}

const _clampT = (n: number, lo: number, hi: number): number =>
  Math.min(hi, Math.max(lo, Number.isFinite(n) ? n : lo));

/** Read the transform off an image setting value (string | {url,alt,transform}). */
export function asImageTransform(v: unknown): ImageTransform | undefined {
  if (v && typeof v === "object" && "transform" in v) {
    const t = (v as { transform?: unknown }).transform;
    if (t && typeof t === "object") return t as ImageTransform;
  }
  return undefined;
}

/** CSS reproducing the transform on an <img> filling a fixed-aspect,
 *  overflow-hidden container. Empty object when there is no transform → the
 *  image renders exactly as before (full backward compatibility). */
export function applyImageTransform(
  t: ImageTransform | undefined | null,
  fit: "cover" | "contain" = "cover",
): CSSProperties {
  if (!t) return {};
  const fx = Math.round(_clampT(t.focal?.x ?? 0.5, 0, 1) * 1e4) / 100;
  const fy = Math.round(_clampT(t.focal?.y ?? 0.5, 0, 1) * 1e4) / 100;
  const zoom = _clampT(t.zoom ?? 1, 1, 4);
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
