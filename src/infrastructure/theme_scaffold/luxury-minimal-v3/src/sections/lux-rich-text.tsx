"use client";

import { useMemo } from "react";
import { sanitizeHtml, useLocale, useResolvedSettings } from "@numueg/theme-sdk";
import { asString, localized, usePageData, type SectionRenderProps } from "./_shared";
import { InlineEditable } from "./_inline-editable";

/**
 * lux-rich-text — faithful V3 port of the V2 bz-rich-text prose section, restyled
 * to the Luxury Minimal language. It is the body renderer for the CMS `page`
 * template (about / contact / page / policy pages).
 *
 * On a content-page route the storefront forwards the real CMS Page record via
 * the page context (`usePageData()` → `data.page`, host page.type "page"); this
 * section renders that page's bilingual title + body (authored in Online Store →
 * Pages) so About / Contact / Policy pages get the Luxury Minimal treatment — an
 * uppercase wide-tracked `lux-heading` title over a refined `.prose` body. (The
 * host routes legal `/policies/<handle>` pages through the same `page`
 * descriptor, so they render here too.) On any other surface (no page context)
 * it falls back to its own `heading` + `body` settings, so it also works as a
 * generic prose block placed anywhere in any template.
 *
 * Body is rich-text HTML, sanitized via the SDK's sanitizeHtml before
 * dangerouslySetInnerHTML (defence-in-depth against scripts/handlers; SSR-safe —
 * DOMPurify falls back to identity when window is undefined). Engine-wired:
 * useResolvedSettings (so global tokens + dynamic sources resolve) and
 * InlineEditable on the heading. Robust: never crashes when page ctx is null.
 */
export default function LuxRichText({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const locale = useLocale();
  const pageCtx = usePageData();

  // Real CMS page route (/pages/<handle>, /policies/<handle>, about, contact →
  // host sets page.type "page" and forwards the record as `data.page`). The
  // page's own bilingual title + body render through this prose section so the
  // Luxury Minimal chrome frames them; otherwise fall back to the section's own
  // settings. (The host's page-data contract only carries `data.page` for
  // content pages and `data.query` for search — there is no separate policy
  // shape; legal pages arrive as a normal `page` record.)
  const cmsPage = pageCtx?.type === "page" ? pageCtx.data?.page : null;

  const cmsTitle = cmsPage
    ? cmsPage.title_i18n?.[locale] || cmsPage.title || pageCtx?.title || ""
    : "";
  const cmsBody = cmsPage ? cmsPage.body_i18n?.[locale] || cmsPage.body || "" : "";

  // The section's own heading falls back to a bilingual default, and is
  // InlineEditable (only meaningful on generic placements — a real CMS page
  // supplies its own title).
  const settingHeading =
    asString(s.heading) || localized(locale, "About", "من نحن");

  // CMS page title & body win; generic placements fall back to settings.
  const title = cmsTitle || settingHeading;
  const usingPageTitle = !!cmsTitle;
  const content = cmsBody || asString(s.body);

  const safeHtml = useMemo(() => sanitizeHtml(content), [content]);

  // Nothing to show (no CMS page, no settings) → render nothing.
  if (!title && !content) return null;

  return (
    <section className="py-16 md:py-24" data-lux-section={sectionId}>
      <div className="container mx-auto px-4 max-w-3xl">
        {title && (
          <h1 className="lux-heading text-3xl md:text-4xl text-foreground mb-8 text-center">
            {usingPageTitle ? (
              title
            ) : (
              <InlineEditable
                sectionId={sectionId}
                settingKey="heading"
                value={title}
              />
            )}
          </h1>
        )}
        {content && (
          <div
            className="prose prose-sm sm:prose-base max-w-none text-muted-foreground prose-headings:lux-heading prose-headings:text-foreground prose-a:text-foreground"
            dangerouslySetInnerHTML={{ __html: safeHtml }}
          />
        )}
      </div>
    </section>
  );
}
