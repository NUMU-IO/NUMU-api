"use client";
import { Link, useLocale } from "@numueg/theme-sdk";
import { applyImageTransform, asImageTransform, asString, localized, type SectionRenderProps } from "./_shared";

/**
 * Kick Game about page. Ported from the proven vionne V3 about section
 * (eyebrow + headline, description + CTAs, optional image, quote, value cards)
 * re-palette'd to Kick Game via the `vn-*`/shadcn tokens in theme.css.
 */
const KGAbout = ({ instance }: SectionRenderProps) => {
  const s = instance.settings ?? {};
  const locale = useLocale();
  const eyebrow = asString(s.eyebrow) || localized(locale, "ABOUT KICK GAME", "عن كيك جيم");
  const headline = asString(s.headline) || localized(locale, "Sneakers as art", "السنيكرز فن");
  const quote = asString(s.quote);
  const description = asString(s.description);
  const image = asString(s.image);
  const imageTransform = asImageTransform(s.image);

  const ctaText = asString(s.cta_text);
  const ctaLink = asString(s.cta_link) || "/products";
  const contactCtaText = asString(s.contact_cta_text);
  const contactCtaLink = asString(s.contact_cta_link) || "/contact";

  const values = [
    { title: asString(s.value_1_title), text: asString(s.value_1_text) },
    { title: asString(s.value_2_title), text: asString(s.value_2_text) },
    { title: asString(s.value_3_title), text: asString(s.value_3_text) },
  ].filter((v) => v.title || v.text);

  return (
    <section className="bg-[var(--vn-band)]">
      <div className="container mx-auto px-4 py-14 md:py-20">
        <div className="text-center mb-10 md:mb-14 max-w-3xl mx-auto">
          {eyebrow && <span className="vn-eyebrow inline-block mb-3">{eyebrow}</span>}
          {headline && (
            <h1 className="kg-heading text-3xl md:text-5xl">{headline}</h1>
          )}
        </div>

        <div className="grid md:grid-cols-2 gap-8 md:gap-12 items-start">
          <div className={image ? "" : "md:col-span-2 max-w-2xl mx-auto"}>
            {description && (
              <p className="text-base md:text-lg text-foreground/85 leading-relaxed">
                {description}
              </p>
            )}
            {(ctaText || contactCtaText) && (
              <div className="flex flex-wrap gap-3 mt-7">
                {ctaText && (
                  <Link to={ctaLink} className="vn-btn vn-btn-filled">
                    {ctaText}
                  </Link>
                )}
                {contactCtaText && (
                  <Link to={contactCtaLink} className="vn-btn vn-btn-outline-dark">
                    {contactCtaText}
                  </Link>
                )}
              </div>
            )}
          </div>
          {image ? (
            <div className="relative aspect-[4/5] overflow-hidden bg-[#f0efe9]">
              <img src={image} alt="" className="absolute inset-0 w-full h-full object-cover" style={applyImageTransform(imageTransform, "cover")} />
            </div>
          ) : null}
        </div>

        {quote && (
          <figure className="mt-12 md:mt-16 max-w-3xl mx-auto bg-[var(--vn-white)] border border-[var(--vn-border)] p-7 md:p-9 text-center">
            <blockquote className="kg-heading text-xl md:text-2xl">"{quote}"</blockquote>
          </figure>
        )}

        {values.length > 0 && (
          <div className="mt-12 md:mt-16 grid md:grid-cols-3 gap-5 md:gap-6">
            {values.map((v, i) => (
              <div
                key={i}
                className="bg-[var(--vn-white)] border border-[var(--vn-border)] p-6 md:p-7"
              >
                <h3 className="kg-heading text-lg md:text-xl mb-2">{v.title}</h3>
                <p className="text-sm text-muted-foreground leading-relaxed">{v.text}</p>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
};

export default KGAbout;
