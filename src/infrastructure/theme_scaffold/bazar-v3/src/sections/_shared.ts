// import + re-export: local binding for helpers below AND public re-export.
import { asArray, asBool, asImageAlt, asImageUrl, asNumber, asRecord, asString, localized, pickItems, readBlocks } from "@numueg/theme-kit";
export { asArray, asBool, asImageAlt, asImageUrl, asNumber, asRecord, asString, localized, pickItems, readBlocks };

﻿/**
 * Shared helpers for Bazar V3 sections.
 *
 * All sections receive `{ instance, sectionId }` from main.tsx; the
 * registry signature matches what `<RenderSection>` passes in.
 *
 * Ported from the Bon Younes V3 structural template (same helper surface):
 * only the CSS prefix (`by-` â†’ `bz-`) and the placeholder palette differ.
 */

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
 * Host-provided page context (Phase 4.4b parity with bon-younes). The
 * storefront's /pages/[handle] route passes the resolved CMS page record as
 * `ctx.page = { type:"page", handle, title, data:{ page:{...} } }`. Bazar
 * mirrors it into this context so content sections (bz-rich-text) render the
 * real page title + body instead of their own default copy. Null on non-page
 * routes â€” sections then fall back to their own settings.
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
 * field â†’ the placeholder glyph, every text-ish field â†’ empty. Used by sections
 * so that, when NOT in demo mode and the merchant has configured nothing, the
 * section still renders its layout but with blank, on-brand placeholders the
 * merchant then fills in â€” instead of the theme's demo content.
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

interface RawBlock {
  type?: string;
  disabled?: boolean;
  settings?: Record<string, unknown>;
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
