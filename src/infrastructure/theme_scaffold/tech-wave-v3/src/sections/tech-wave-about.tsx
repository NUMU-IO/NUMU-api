"use client";
import { Link, useResolvedSettings, useLocale } from "@numueg/theme-sdk";
import { applyImageTransform, asImageTransform, asImageUrl, asString, localized, type SectionRenderProps } from "./_shared";

/**
 * Tech Wave about section.
 *
 * Ported from the proven Vionne V3 about page (eyebrow + headline, copy +
 * CTAs, optional side image, pull-quote, value cards) and re-skinned to Tech
 * Wave via the `vn-*` utility classes — which this theme's theme.css re-maps
 * onto the dark/neon palette. Uses `useResolvedSettings` so dynamic-source
 * bindings render as primitives (never "[object Object]").
 */
const TechWaveAbout = ({ instance }: SectionRenderProps) => {
  const s = useResolvedSettings(instance);
  const locale = useLocale();
  const eyebrow = asString(s.eyebrow) || localized(locale, "ABOUT US", "من نحن");
  const headline = asString(s.headline) || localized(locale, "Built different", "مختلفين عن الباقي");
  const quote = asString(s.quote);
  const description = asString(s.description);
  const image = asImageUrl(s.image);
  const imageTransform = asImageTransform(s.image);

  const ctaText = asString(s.cta_text);
  const ctaLink = asString(s.cta_link, "/products");
  const contactCtaText = asString(s.contact_cta_text);
  const contactCtaLink = asString(s.contact_cta_link, "/contact");

  const values = [
    { title: asString(s.value_1_title), text: asString(s.value_1_text) },
    { title: asString(s.value_2_title), text: asString(s.value_2_text) },
    { title: asString(s.value_3_title), text: asString(s.value_3_text) },
  ].filter((v) => v.title || v.text);

  return (
    <section className="bg-[var(--vn-band)]">
      <div className="container mx-auto px-4 py-14 md:py-20">
        <div className="text-center mb-10 md:mb-14 max-w-3xl mx-auto">
          {eyebrow && (
            <span className="vn-eyebrow inline-block mb-3">
              {eyebrow}
            </span>
          )}
          {headline && (
            <h1 className="vn-heading text-3xl md:text-5xl">
              {headline}
            </h1>
          )}
        </div>

        <div className="grid md:grid-cols-2 gap-8 md:gap-12 items-start">
          <div className={image ? "" : "md:col-span-2 max-w-2xl mx-auto"}>
            {description && (
              <p className="text-base md:text-lg text-[var(--vn-ink)]/85 leading-relaxed">
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
            <div className="relative aspect-[4/5] overflow-hidden rounded-xl tw-img-frame">
              <img src={image} alt="" className="absolute inset-0 w-full h-full object-cover" style={applyImageTransform(imageTransform, "cover")} />
            </div>
          ) : null}
        </div>

        {quote && (
          <figure className="mt-12 md:mt-16 max-w-3xl mx-auto tw-card rounded-xl p-7 md:p-9 text-center">
            <blockquote className="vn-heading text-xl md:text-2xl">
              "{quote}"
            </blockquote>
          </figure>
        )}

        {values.length > 0 && (
          <div className="mt-12 md:mt-16 grid md:grid-cols-3 gap-5 md:gap-6">
            {values.map((v, i) => (
              <div
                key={i}
                className="tw-card rounded-xl p-6 md:p-7"
              >
                <h3 className="vn-heading text-lg md:text-xl mb-2">{v.title}</h3>
                <p className="text-sm text-[var(--vn-muted)] leading-relaxed">{v.text}</p>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
};

export default TechWaveAbout;
