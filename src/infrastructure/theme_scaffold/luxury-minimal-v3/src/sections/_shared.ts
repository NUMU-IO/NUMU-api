import { createContext, useContext } from "react";
import type { SectionInstance } from "@numueg/theme-sdk";

export interface SectionRenderProps {
  instance: SectionInstance;
  sectionId: string;
}

/**
 * "Demo mode" â€” true ONLY in the marketplace preview ("try before you use"),
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
 * Host-provided page context. The storefront forwards the resolved route's
 * page descriptor in the mount ctx (`ctx.page`), and `mountTheme`'s render
 * callback hands it back so main.tsx can publish it here. Content/search
 * sections read it via `usePageData()`:
 *   - the `page` template: `data.page` carries the real CMS Page record
 *     (title + body + i18n) the merchant authored in Online Store â†’ Pages.
 *   - the `search` template: `data.query` carries the visitor's query.
 * Null on routes that don't forward a page (sections then fall back to their
 * own settings). The SDK's `usePage()` is a SYNTHESISED record (products /
 * collections only) â€” it does NOT carry the CMS body or query, which is why
 * each theme threads the raw descriptor through its own context.
 */
export interface MountPageData {
  type?: string;
  handle?: string;
  title?: string;
  data?: {
    /** Visitor's search query â€” the storefront /search route stashes it as
     *  `query`; `q` kept as a defensive alias. */
    query?: string;
    q?: string;
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
 * Neutral "add an image" placeholder, on-brand for Luxury Minimal (light gray
 * box + gold camera glyph, sharp edges). Inline data-URI so the bundle ships
 * no asset files.
 */
export const PLACEHOLDER_IMG =
  "data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20viewBox='0%200%20400%20400'%3E%3Crect%20width='400'%20height='400'%20fill='%23f4f4f4'/%3E%3Cg%20fill='none'%20stroke='%23b8860b'%20stroke-width='8'%20stroke-linecap='round'%20stroke-linejoin='round'%3E%3Crect%20x='112'%20y='128'%20width='176'%20height='144'%20rx='2'/%3E%3Ccircle%20cx='160'%20cy='176'%20r='16'/%3E%3Cpath%20d='M124%20256l46-46%2036%2030%2042-52%2056%2068'/%3E%3C/g%3E%3C/svg%3E";

/**
 * Turn a demo fallback array into a NEUTRAL placeholder array: every image-ish
 * field â†’ the placeholder glyph, every text-ish field â†’ empty. Used by sections
 * so that, when NOT in demo mode and the merchant has configured nothing, the
 * section still renders its layout but with blank, on-brand placeholders the
 * merchant then fills in â€” instead of the theme's demo content.
 */
export function placeholderize<T>(items: T[]): T[] {
  return items.map((item) => {
    const out: Record<string, unknown> = { ...(item as Record<string, unknown>) };
    for (const key of Object.keys(out)) {
      if (/(image|img|photo|src|thumbnail|background|avatar)/i.test(key)) {
        out[key] = PLACEHOLDER_IMG;
      } else if (
        typeof out[key] === "string" &&
        /(label|title|name|heading|caption|body|text|subtitle|eyebrow|price|city|quote|description)/i.test(key)
      ) {
        out[key] = "";
      }
    }
    return out as T;
  });
}

/**
 * Canonical fallback gate. demo -> the demo array; real store -> [].
 *
 * On a REAL installed store (demo=false) a section must render the merchant's
 * actual data or a real empty-state — NEVER demo/placeholder shapes. Returning
 * [] makes every `real.length ? real : demoOrPlaceholder(...)` collapse to the
 * real-empty path, so phantom products, "0 EGP" cards and blank demo panels
 * never appear on a live store.
 *
 * This previously returned `placeholderize(items)` here, which rendered
 * skeleton cards to real shoppers. `placeholderize` stays for per-image
 * neutral fallbacks on REAL items — see PLACEHOLDER_IMG.
 */
export function demoOrPlaceholder<T>(demo: boolean, items: T[]): T[] {
  return demo ? items : [];
}

/**
 * ENG-3: pick the locale-appropriate default copy. Merchant-entered values
 * still win because callers do `asString(s.x) || localized(locale, en, ar)`.
 * Only the empty-state DEFAULT is locale-driven; the editable setting is
 * untouched. `locale` comes from the SDK's `useLocale()`.
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
  return v && typeof v === "object" ? (v as Record<string, unknown>) : {};
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
 * Read a section's blocks of a given type, in editor order, skipping disabled
 * ones. The customizer's block CRUD writes `instance.blocks` +
 * `instance.block_order`, so components MUST read from there â€” reading
 * `instance.settings.<list>` silently ignores everything the merchant adds in
 * the editor. Returns each block's `settings` bag (use asString / asImageUrl on
 * the fields). Empty array when the section has no blocks of that type â†’ the
 * caller falls back to its defaults.
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

// Storefront route builders now live in the SDK (>= 0.11.0) rather than being
// hand-copied per theme -- the URL shape is platform knowledge, and every local
// copy was a place the fleet could disagree with the host. Re-exported so
// sections keep importing from "./_shared" unchanged.
export { productHref, collectionHref } from "@numueg/theme-sdk";

// â”€â”€ Non-destructive image transform (focal / zoom / rotation) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
// Now provided by the SDK (@numueg/theme-sdk >= 0.11.0) instead of a local
// copy that had to be hand-synced with the merchant-hub editor and 13 other
// themes. Re-exported from here so every section keeps importing it from
// "./_shared" unchanged. The SDK build is pinned against the previous local
// implementation by a parity suite, so this swap is render-identical.
export {
  applyImageTransform,
  asImageTransform,
  type ImageTransform,
} from "@numueg/theme-sdk";
