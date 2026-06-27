"use client";

import { useMemo } from "react";
import { sanitizeHtml, useLocale, useResolvedSettings } from "@numueg/theme-sdk";
import {
  applyImageTransform,
  asBool,
  asImageAlt,
  asImageTransform,
  asImageUrl,
  asString,
  localized,
  usePageData,
  type SectionRenderProps,
} from "./_shared";
import { InlineEditable } from "./_inline-editable";

/**
 * gilded-rich-text — faithful V3 port of the Gilded policy/about/contact/page
 * body, and the body renderer for the CMS `page` template. It mirrors the V2
 * Gilded "header band over prose" treatment used by GildedAuthPage /
 * GildedPolicyPage (numu-egyptian-bazaar/src/components/store/gilded-glamour-boutique):
 * a pure-black header band (`bg-foreground`) carrying a gold uppercase eyebrow
 * over a Montserrat `gld-heading` title in card-white, with an optional faint
 * background image (`opacity-10 mix-blend-overlay`) and the `gilded-decor-bg`
 * gold radial backdrop as the fallback — then a centered `max-w-3xl` prose body
 * where headings stay uppercase wide-tracked and links pick up the brand gold.
 *
 * On a content-page route the storefront forwards the real CMS Page record via
 * the page context (`usePageData()` → `data.page`, host page.type "page"); this
 * section renders that page's bilingual title (`title_i18n[locale]`) + body
 * (`body_i18n[locale]`) so About / Contact / Policy pages get the Gilded
 * treatment. (Legal `/policies/<handle>` pages route through the same `page`
 * descriptor, so they render here too.) On any other surface (no page context)
 * it falls back to its own `title` + `content` settings, so it also works as a
 * generic prose block placed anywhere in any template.
 *
 * Body is rich-text HTML, sanitized via the SDK's sanitizeHtml before
 * dangerouslySetInnerHTML (defence-in-depth against scripts/handlers; SSR-safe —
 * DOMPurify falls back to identity when window is undefined). Engine-wired:
 * useResolvedSettings (global tokens + dynamic sources + draft preview),
 * bilingual defaults via localized(), and InlineEditable on the section title.
 * Robust: never crashes when page ctx is null.
 */
export default function GildedRichText({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const locale = useLocale();
  const pageCtx = usePageData();

  // Real CMS page route (/pages/<handle>, /policies/<handle>, about, contact →
  // host sets page.type "page" and forwards the record as `data.page`). The
  // page's own bilingual title + body render through this prose section so the
  // Gilded chrome frames them; otherwise fall back to the section's own
  // settings. (The host's page-data contract only carries `data.page` for
  // content pages — legal pages arrive as a normal `page` record.)
  const cmsPage = pageCtx?.type === "page" ? pageCtx.data?.page : null;

  const cmsTitle = cmsPage
    ? cmsPage.title_i18n?.[locale] || cmsPage.title || pageCtx?.title || ""
    : "";
  const cmsBody = cmsPage ? cmsPage.body_i18n?.[locale] || cmsPage.body || "" : "";

  // The section's own title falls back to a bilingual default and is
  // InlineEditable (only meaningful on generic placements — a real CMS page
  // supplies its own title).
  const settingTitle =
    asString(s.title) || localized(locale, "About", "من نحن");

  // CMS page title & body win; generic placements fall back to settings.
  const title = cmsTitle || settingTitle;
  const usingPageTitle = !!cmsTitle;
  const content = cmsBody || asString(s.content);

  const eyebrow =
    asString(s.eyebrow) || localized(locale, "The House", "الدار");

  // Header band is optional (a merchant can hide it when the section is used as
  // an inline prose block under another section).
  const showHeader = asBool(s.show_header, true);

  // Optional faint background art behind the header band; falls back to the
  // gold radial decor (no demo photo leaks — an unset picker stays decor-only).
  const bgImage = asImageUrl(s.background_image) || undefined;
  const bgImageAlt = asImageAlt(s.background_image);
  const bgImageTransform = asImageTransform(s.background_image);

  const safeHtml = useMemo(() => sanitizeHtml(content), [content]);

  // Nothing to show (no CMS page, no settings) → render nothing.
  if (!title && !content) return null;

  return (
    <section className="bg-background" data-testid="storefront-page" data-gilded-section={sectionId}>
      {showHeader && title && (
        <header className="relative overflow-hidden bg-foreground py-16 md:py-24">
          {bgImage ? (
            <img
              src={bgImage}
              alt={bgImageAlt}
              loading="lazy"
              className="absolute inset-0 w-full h-full object-cover opacity-10 mix-blend-overlay pointer-events-none select-none"
              style={applyImageTransform(bgImageTransform, "cover")}
            />
          ) : (
            <div aria-hidden="true" className="absolute inset-0 gilded-decor-bg opacity-30 mix-blend-overlay" />
          )}
          <div className="relative z-10 container mx-auto px-4 text-center">
            <p className="gld-eyebrow text-[var(--gilded-gold)] tracking-[0.3em] mb-4">
              {eyebrow}
            </p>
            <h1 className="gld-heading text-4xl md:text-6xl text-card">
              {usingPageTitle ? (
                title
              ) : (
                <InlineEditable
                  sectionId={sectionId}
                  settingKey="title"
                  value={title}
                />
              )}
            </h1>
          </div>
        </header>
      )}

      <div className="container mx-auto px-4 max-w-3xl py-12 md:py-20">
        {/* When the header band is hidden, the title still leads the body. */}
        {!showHeader && title && (
          <h1 className="gld-heading text-3xl md:text-4xl text-foreground mb-8 text-center">
            {usingPageTitle ? (
              title
            ) : (
              <InlineEditable
                sectionId={sectionId}
                settingKey="title"
                value={title}
              />
            )}
          </h1>
        )}

        {content && (
          <div
            className="prose prose-sm sm:prose-base max-w-none text-muted-foreground prose-headings:gld-heading prose-headings:text-foreground prose-headings:uppercase prose-headings:tracking-[0.05em] prose-h1:text-3xl prose-h2:text-2xl prose-h3:text-xl prose-p:text-[15px] prose-p:leading-relaxed prose-a:text-[var(--gilded-gold)] prose-a:no-underline hover:prose-a:underline prose-strong:text-foreground"
            dangerouslySetInnerHTML={{ __html: safeHtml }}
          />
        )}
      </div>
    </section>
  );
}
