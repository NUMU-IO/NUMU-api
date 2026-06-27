"use client";

import { sanitizeHtml, useLocale, useResolvedSettings } from "@numueg/theme-sdk";
import { useCallback, useEffect, useMemo, useState } from "react";
import { asString, localized, usePageData, type SectionRenderProps } from "./_shared";
import { InlineEditable } from "./_inline-editable";

/**
 * Whether to mount editor affordances on the body wrapper. Mirrors
 * `isInsideEditor()` in `_inline-editable.tsx`: either we're inside the
 * customizer iframe (`window.parent !== window`) or the URL carries an
 * `editor=...` flag. SSR / public storefront → false → no frame, no handler.
 */
function isInsideEditor(): boolean {
  if (typeof window === "undefined") return false;
  try {
    const params = new URLSearchParams(window.location.search);
    if (params.get("editor")) return true;
  } catch {
    // ignore — URLSearchParams shouldn't throw, but be defensive
  }
  return window.parent !== window;
}

/**
 * by-rich-text — generic prose section for the `page` template
 * (about / contact / shipping / returns / etc.) AND for adding
 * marketing copy anywhere in any template.
 *
 * Phase 4.4b: when the host is on a content-page route it passes the real
 * Page record via the page context (`usePageData()`). This section then
 * renders that page's bilingual title + body (the merchant authored it in
 * Online Store → Pages) INSTEAD of its own default copy. On any other
 * surface (no page context) it falls back to its `heading`/`body`
 * settings, so it still works as a generic prose block.
 *
 * Body is rich-text HTML, sanitized via the SDK's sanitizeHtml before
 * dangerouslySetInnerHTML (defense-in-depth against scripts/handlers).
 */
export default function ByRichText({
  instance,
  sectionId,
}: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const locale = useLocale();
  const pageCtx = usePageData();

  // Real CMS page (only on /pages/<handle> — host sets page.type "page").
  const cmsPage = pageCtx?.type === "page" ? pageCtx.data?.page : null;
  const cmsTitle = cmsPage
    ? cmsPage.title_i18n?.[locale] || cmsPage.title || pageCtx?.title || ""
    : "";
  const cmsBody = cmsPage
    ? cmsPage.body_i18n?.[locale] || cmsPage.body || ""
    : "";
  const isCms = Boolean(cmsTitle || cmsBody);

  const heading =
    cmsTitle || asString(s.heading) || localized(locale, "About Bon Younes", "عن بون يونس");
  const bodyRaw =
    cmsBody ||
    asString(s.body) ||
    localized(
      locale,
      "<p>Specialty coffee, brewed slowly in Mansoura. Beans roasted on site every Wednesday — drop by, or order online.</p><p>This text was added via the rich-text section. Merchants can edit it in the customizer's Sections panel.</p>",
      "<p>قهوة مختصة بتتحضّر على مهل في المنصورة. بنحمّص البُن في المحل كل أربع — عدّي علينا، أو اطلب أونلاين.</p><p>النص ده اتضاف من سيكشن النص الغني. التاجر يقدر يعدّله من قسم السيكشنز في المحرّر.</p>",
    );

  const safeBody = useMemo(() => sanitizeHtml(bodyRaw), [bodyRaw]);

  // Body hover-to-edit affordance — only inside the customizer iframe, and
  // only when this is a section setting (not a real CMS page body, which
  // isn't addressable as a `body` setting). Mirrors InlineEditable: detect in
  // useEffect so SSR/public first paint stays inert (no frame, no handler).
  const [inEditor, setInEditor] = useState(false);
  useEffect(() => {
    setInEditor(isInsideEditor());
  }, []);
  const bodyArmed = inEditor && !isCms;

  const selectBodyField = useCallback(
    (e: React.MouseEvent) => {
      if (!bodyArmed) return;
      // Don't navigate when the merchant clicks a link inside the prose while
      // arming the field — they meant to edit, not follow the link.
      const target = e.target as HTMLElement | null;
      if (target?.closest("a")) e.preventDefault();
      try {
        window.parent.postMessage(
          {
            type: "numu:editor:select-field",
            payload: { sectionId, settingId: "body" },
          },
          "*",
        );
      } catch (err) {
        console.warn("[bon-younes] select-field postMessage failed", err);
      }
    },
    [bodyArmed, sectionId],
  );

  return (
    <section
      className="by-rich-text"
      data-by-section={sectionId}
      style={{
        padding: "3rem 1rem",
        background: "var(--by-cream, #fdf8ee)",
      }}
    >
      <article style={{ maxWidth: 720, margin: "0 auto" }}>
        <h1
          style={{
            fontFamily: "var(--by-display, 'Playfair Display', serif)",
            fontSize: "clamp(1.75rem, 3vw, 2.5rem)",
            color: "var(--by-espresso, #3a2418)",
            margin: "0 0 1.5rem",
          }}
        >
          {/* CMS title is page-record data (not a section setting), so it's
              plain text; the settings `heading` stays inline-editable when
              this section is used as generic marketing copy. */}
          {isCms ? (
            heading
          ) : (
            <InlineEditable
              sectionId={sectionId}
              settingKey="heading"
              value={heading}
            />
          )}
        </h1>
        <div
          className={
            bodyArmed
              ? "by-rich-text__body by-rich-text__body--armed"
              : "by-rich-text__body"
          }
          style={{
            color: "rgba(58,36,24,0.85)",
            fontSize: "1.05rem",
            lineHeight: 1.7,
            cursor: bodyArmed ? "pointer" : undefined,
          }}
          onClick={bodyArmed ? selectBodyField : undefined}
          dangerouslySetInnerHTML={{ __html: safeBody }}
        />
      </article>
      <style>{`
        .by-rich-text__body p { margin: 0 0 1rem; }
        .by-rich-text__body p:last-child { margin-bottom: 0; }
        .by-rich-text__body a {
          color: var(--by-caramel, #b07a4a);
          text-decoration: underline;
        }
        .by-rich-text__body h2, .by-rich-text__body h3 {
          font-family: var(--by-display, 'Playfair Display', serif);
          color: var(--by-espresso, #3a2418);
          margin: 2rem 0 0.75rem;
        }
        /* Editor-only hover affordance: a dashed accent frame so the merchant
           sees the prose is editable and can click to jump to its Body field.
           Inert on the public storefront (class only applied inside the iframe). */
        .by-rich-text__body--armed {
          position: relative;
          border-radius: 6px;
          outline: 1px dashed transparent;
          outline-offset: 4px;
          transition: outline-color 0.12s ease, background-color 0.12s ease;
        }
        .by-rich-text__body--armed:hover {
          outline: 1px dashed var(--by-caramel, #b07a4a);
          background-color: rgba(176,122,74,0.06);
        }
      `}</style>
    </section>
  );
}
