"use client";

import { Link, useLocale, useResolvedSettings } from "@numueg/theme-sdk";
import {
  applyImageTransform,
  asImageTransform,
  asImageUrl,
  asString,
  demoOrPlaceholder,
  localized,
  useDemo,
  type SectionRenderProps,
} from "./_shared";
import { InlineEditable } from "./_inline-editable";

const FALLBACK_IMAGE = {
  image:
    "https://images.unsplash.com/photo-1490481651871-ab68de25d43d?auto=format&fit=crop&w=900&q=70",
};

const BzImageWithText = ({ instance, sectionId }: SectionRenderProps) => {
  const s = useResolvedSettings(instance);
  const demo = useDemo();
  const locale = useLocale();

  const label = asString(s.label) || localized(locale, "THE PHILOSOPHY", "الفلسفة");
  const headline = asString(s.headline) || localized(locale, "THE LUXURY OF THINGS THAT LAST.", "رفاهية الأشياء اللي بتدوم.");
  const quote =
    asString(s.quote) ||
    localized(
      locale,
      "Born from a pursuit of the perfect tactile experience. We don't chase trends; we refine the invisible details of the daily ritual.",
      "اتولدنا من السعي لتجربة لمس مثالية. إحنا مبنجريش ورا الموضة؛ بنتقن التفاصيل الخفية في روتين يومك.",
    );
  const body =
    asString(s.body) ||
    localized(
      locale,
      "Our materials are sourced from the finest mills, ensuring every thread meets our standards for thermal regulation and enduring softness.",
      "خاماتنا مختارة من أجود المصانع، عشان كل خيط يوصل لمعاييرنا في توازن الحرارة ونعومة تدوم.",
    );
  const ctaText = asString(s.cta_text) || localized(locale, "LEARN MORE", "اعرف أكتر");
  const ctaLink = asString(s.cta_link) || "/about";
  // Demo-mode fills in a real photo; outside demo an unset image collapses to
  // the branded "B" placeholder block (designed empty-state, below).
  const configuredImage = asImageUrl(s.image_url);
  const imageUrl = configuredImage || demoOrPlaceholder(demo, [FALLBACK_IMAGE])[0].image;
  // Non-destructive focal/zoom/rotation the merchant set in the editor. When
  // absent, the image renders exactly as before (object-cover + hover-zoom).
  const imageTransform = asImageTransform(s.image_url);
  const colorScheme = asString(s.color_scheme) || "dark";

  return (
    <section className="relative py-12 md:py-20 lg:py-32 bg-[var(--bz-dark)] overflow-hidden" data-color-scheme={colorScheme}>
      <svg viewBox="0 0 1440 60" className="absolute top-0 start-0 w-full -mt-px" preserveAspectRatio="none" aria-hidden="true">
        <path d="M0,0 C360,50 720,10 1080,40 C1260,55 1380,20 1440,30 L1440,0 L0,0 Z" fill="var(--bz-cream)" />
      </svg>

      <div className="container mx-auto px-4">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 md:gap-8 lg:gap-12 items-center">
          <div>
            <span className="bz-label text-[var(--bz-amber)]">
              <InlineEditable sectionId={sectionId} settingKey="label" value={label} />
            </span>
            <h2 className="bz-heading text-2xl sm:text-3xl md:text-4xl lg:text-5xl text-[var(--bz-cream)] mt-3 md:mt-4 leading-tight">
              <InlineEditable sectionId={sectionId} settingKey="headline" value={headline} multiline />
            </h2>
            <p className="text-[var(--bz-cream)]/60 mt-6 leading-relaxed text-sm md:text-base italic">
              &ldquo;
              <InlineEditable sectionId={sectionId} settingKey="quote" value={quote} multiline />
              &rdquo;
            </p>
            <p className="text-[var(--bz-cream)]/40 mt-4 text-sm leading-relaxed">
              <InlineEditable sectionId={sectionId} settingKey="body" value={body} multiline />
            </p>
            <Link to={ctaLink} className="bz-btn bz-btn-amber mt-6 md:mt-8 rounded-full">
              <InlineEditable sectionId={sectionId} settingKey="cta_text" value={ctaText} />
            </Link>
          </div>
          {imageUrl ? (
            <div className="relative order-first md:order-none">
              <div className="rounded-3xl overflow-hidden aspect-[4/5] bg-[var(--bz-navy)]">
                <img
                  src={imageUrl}
                  alt=""
                  className={`w-full h-full object-cover opacity-80 ${imageTransform ? "" : "bz-img-zoom"}`}
                  style={applyImageTransform(imageTransform, "cover")}
                  loading="lazy"
                />
              </div>
              <div aria-hidden="true" className="absolute -bottom-6 -start-6 w-24 h-24 bg-[var(--bz-amber)] bz-blob opacity-60" />
            </div>
          ) : (
            <div className="relative order-first md:order-none" aria-hidden="true">
              <div className="rounded-3xl aspect-[4/5] bg-[var(--bz-navy)] flex items-center justify-center">
                <div className="bz-heading text-[80px] sm:text-[100px] md:text-[120px] text-[var(--bz-amber)]/10 bz-blob inline-block">
                  B
                </div>
              </div>
              <div className="absolute -bottom-6 -start-6 w-16 h-16 sm:w-20 sm:h-20 md:w-24 md:h-24 bg-[var(--bz-amber)] bz-blob opacity-60" />
            </div>
          )}
        </div>
      </div>
    </section>
  );
};

export default BzImageWithText;
