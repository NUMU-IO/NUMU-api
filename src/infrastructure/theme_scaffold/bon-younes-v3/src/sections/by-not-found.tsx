"use client";

import { Link, useLocale, useResolvedSettings } from "@numueg/theme-sdk";
import { asString, localized, type SectionRenderProps } from "./_shared";
import { InlineEditable } from "./_inline-editable";

/**
 * by-not-found — body for the `404` template. Renders a friendly
 * "this page wasn't found" message + a CTA back to the menu, in
 * the Bon Younes voice (so a 404 still feels like the cafe, not
 * a hosting provider).
 */
export default function ByNotFound({
  instance,
  sectionId,
}: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const locale = useLocale();
  const headline = asString(s.headline) || localized(locale, "Spilled the coffee.", "القهوة وقعت.");
  const subhead =
    asString(s.subhead) ||
    localized(
      locale,
      "The page you were after isn't on the menu. Let's get you back to something brewing.",
      "الصفحة اللي بتدوّر عليها مش موجودة في المنيو. خلّينا نرجّعك لحاجة بتتحضّر على نار هادية.",
    );
  const ctaLabel = asString(s.cta_label) || localized(locale, "Back to the menu", "ارجع للمنيو");
  const ctaHref = asString(s.cta_href) || "/products";
  const status = asString(s.status_label) || "404";

  return (
    <section
      className="by-not-found"
      data-by-section={sectionId}
      style={{
        padding: "5rem 1rem",
        background: "var(--by-cream, #fdf8ee)",
        minHeight: "60vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <div style={{ maxWidth: 560, textAlign: "center" }}>
        <div
          style={{
            fontFamily: "var(--by-mono, 'JetBrains Mono', monospace)",
            fontSize: "0.85rem",
            color: "var(--by-caramel, #b07a4a)",
            letterSpacing: "0.2em",
            textTransform: "uppercase",
            marginBottom: "1rem",
          }}
        >
          <InlineEditable
            sectionId={sectionId}
            settingKey="status_label"
            value={status}
          />
        </div>
        <h1
          style={{
            fontFamily: "var(--by-display, 'Playfair Display', serif)",
            fontSize: "clamp(2rem, 4vw, 3rem)",
            color: "var(--by-espresso, #3a2418)",
            margin: 0,
          }}
        >
          <InlineEditable
            sectionId={sectionId}
            settingKey="headline"
            value={headline}
          />
        </h1>
        <p
          style={{
            color: "rgba(58,36,24,0.7)",
            fontSize: "1rem",
            lineHeight: 1.55,
            marginTop: "1rem",
          }}
        >
          <InlineEditable
            sectionId={sectionId}
            settingKey="subhead"
            value={subhead}
            multiline
          />
        </p>
        <Link
          to={ctaHref}
          style={{
            display: "inline-block",
            marginTop: "2rem",
            padding: "0.85rem 1.75rem",
            background: "var(--by-espresso, #3a2418)",
            color: "var(--by-cream, #fdf8ee)",
            textDecoration: "none",
            borderRadius: 999,
            fontSize: "0.9rem",
            fontWeight: 600,
            letterSpacing: "0.04em",
            textTransform: "uppercase",
          }}
        >
          <InlineEditable
            sectionId={sectionId}
            settingKey="cta_label"
            value={ctaLabel}
          />
        </Link>
      </div>
    </section>
  );
}
