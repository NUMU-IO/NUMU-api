"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocale, useResolvedSettings } from "@numueg/theme-sdk";
import { applyImageTransform, asImageTransform, asImageUrl, asString, localized, useDemo, type ImageTransform, type SectionRenderProps } from "./_shared";
import { InlineEditable } from "./_inline-editable";

const HEIGHT_DESKTOP: Record<string, string> = {
  small: "min-h-[400px] md:min-h-[450px]",
  medium: "min-h-[500px] md:min-h-[550px]",
  large: "min-h-[550px] md:min-h-[600px]",
  full: "min-h-screen",
};

interface Slide {
  /** 1-based slide number (real slides only) → maps inline-edits to
   *  `slide_<n>_*` settings. Undefined for demo slides (not editable). */
  n?: number;
  image: string;
  imageTransform?: ImageTransform;
  headline: string;
  subtitle: string;
  ctaText: string;
  ctaLink: string;
}

const SWIPE_THRESHOLD = 60;

/**
 * Marketplace-preview-only demo slides (bilingual). Shown ONLY in demo mode
 * (the catalog "Try theme" preview) when the merchant has configured no slides,
 * so the hero showcases the theme instead of an empty "add slides" prompt. On a
 * real installed store (demo=false) the merchant's slides win and an empty
 * slideshow keeps the editor prompt — these never render there.
 */
const DEMO_SLIDES = (locale: string | undefined): Slide[] => [
  {
    image: "https://images.unsplash.com/photo-1490481651871-ab68de25d43d?auto=format&fit=crop&w=1600&q=70",
    headline: localized(locale, "The Spring Edit", "تشكيلة الربيع"),
    subtitle: localized(locale, "Quietly tailored, made to be lived in.", "تفصيل هادئ، معمول علشان تعيشي فيه."),
    ctaText: localized(locale, "Shop Now", "تسوّقي دلوقتي"),
    ctaLink: "/products",
  },
  {
    image: "https://images.unsplash.com/photo-1485462537746-965f33f7f6a7?auto=format&fit=crop&w=1600&q=70",
    headline: localized(locale, "Modal Series", "مجموعة المودال"),
    subtitle: localized(locale, "Featherweight, breathable, in eight tones.", "خفيف وناعم وبيتنفّس، بثماني درجات لونية."),
    ctaText: localized(locale, "Discover", "اكتشفي"),
    ctaLink: "/products",
  },
  {
    image: "https://images.unsplash.com/photo-1483985988355-763728e1935b?auto=format&fit=crop&w=1600&q=70",
    headline: localized(locale, "Worldwide Delivery", "توصيل لكل العالم"),
    subtitle: localized(locale, "Now shipping to over forty countries.", "بنشحن دلوقتي لأكتر من أربعين دولة."),
    ctaText: localized(locale, "Learn More", "اعرفي أكتر"),
    ctaLink: "/products",
  },
];

const VionneSlideshow = ({ instance, sectionId }: SectionRenderProps) => {
  const locale = useLocale();
  const s = useResolvedSettings(instance);
  const autoplay = s.autoplay !== false;
  const interval = Math.max(2, Number(s.interval ?? 10)) * 1000;
  const heightClass = HEIGHT_DESKTOP[asString(s.height, "large")] ?? HEIGHT_DESKTOP.large;

  const alignment = asString(s.text_alignment, "start") as "start" | "center";

  // Merchant-uploaded slide images arrive as `{ url, alt }` objects; pull the
  // URL via asImageUrl so they render (raw object → broken image, the "green
  // blob" the merchant saw). Text coerced so a bound field can't crash a slide.
  // Subtitle uses one shared class for every slide, so sizing stays uniform.
  const built: Slide[] = [];
  for (let i = 1; i <= 3; i++) {
    const image = asImageUrl(s[`slide_${i}_image`]);
    const imageTransform = asImageTransform(s[`slide_${i}_image`]);
    const headline = asString(s[`slide_${i}_headline`]);
    if (!image && !headline) continue;
    built.push({
      n: i,
      image,
      imageTransform,
      headline,
      subtitle: asString(s[`slide_${i}_subtitle`]),
      ctaText: asString(s[`slide_${i}_cta_text`]),
      ctaLink: asString(s[`slide_${i}_cta_link`], "/products"),
    });
  }

  // Demo-only bilingual fallback: in the marketplace preview the cleaned preset
  // ships no slide copy, so showcase DEMO_SLIDES instead of an empty hero. Real
  // stores (demo=false) keep the merchant's slides / the "add slides" prompt.
  const demo = useDemo();
  const slides: Slide[] = built.length > 0 ? built : demo ? DEMO_SLIDES(locale) : built;

  const [current, setCurrent] = useState(0);
  const count = slides.length || 1;

  const next = useCallback(() => setCurrent((c) => (c + 1) % count), [count]);
  const prev = useCallback(() => setCurrent((c) => (c - 1 + count) % count), [count]);

  useEffect(() => {
    if (!autoplay || count <= 1) return;
    const t = window.setInterval(next, interval);
    return () => window.clearInterval(t);
  }, [autoplay, interval, count, next]);

  const touchStart = useRef<number | null>(null);
  const onTouchStart = (e: React.TouchEvent) => {
    touchStart.current = e.touches[0]?.clientX ?? null;
  };
  const onTouchEnd = (e: React.TouchEvent) => {
    if (touchStart.current == null) return;
    const dx = (e.changedTouches[0]?.clientX ?? 0) - touchStart.current;
    if (Math.abs(dx) > SWIPE_THRESHOLD) {
      if (dx < 0) next();
      else prev();
    }
    touchStart.current = null;
  };

  if (slides.length === 0) {
    return (
      <section className={`relative w-full ${heightClass} bg-muted flex items-center justify-center`}>
        <p className="vn-eyebrow text-muted-foreground">{localized(locale, "Add slides in the theme editor", "أضيفي شرائح من محرّر الثيم")}</p>
      </section>
    );
  }

  return (
    <section
      role="region"
      aria-roledescription="carousel"
      aria-label="Featured slides"
      className={`relative w-full ${heightClass} overflow-hidden bg-[var(--vn-band)]`}
      onTouchStart={onTouchStart}
      onTouchEnd={onTouchEnd}
    >
      {slides.map((sl, i) => {
        const isActive = i === current;
        const isFirst = i === 0;
        return (
          <div
            key={i}
            // `inert` (HTML5) hides the subtree from sequential focus AND
            // assistive tech without needing tabIndex={-1} on every Link.
            // aria-hidden alone leaves CTAs focusable.
            {...(!isActive ? { inert: "" as unknown as undefined } : {})}
            className={`vn-slide ${isActive ? "is-active" : ""}`}
          >
            {sl.image ? (
              <img
                src={sl.image}
                alt={sl.headline || `Slide ${i + 1}`}
                className="absolute inset-0 w-full h-full object-cover"
                style={applyImageTransform(sl.imageTransform, "cover")}
                // First slide is the LCP; eager + high priority. Others
                // are below the fold visually (CSS-stacked) and can wait.
                loading={isFirst ? "eager" : "lazy"}
                fetchPriority={isFirst ? "high" : "low"}
                decoding={isFirst ? "sync" : "async"}
              />
            ) : (
              <div className="absolute inset-0 vn-shimmer" />
            )}
            <div className="vn-slide-overlay" />
            <div
              className={`absolute z-10 flex flex-col px-6 md:px-10 ${
                alignment === "center"
                  ? "inset-x-0 bottom-12 md:bottom-16 items-center text-center"
                  : "start-0 bottom-10 md:bottom-14 max-w-[640px] items-start text-start"
              }`}
            >
              {sl.headline && (
                <h2 className="vn-heading text-white text-3xl md:text-5xl lg:text-6xl">
                  {sl.n ? (
                    <InlineEditable sectionId={sectionId} settingKey={`slide_${sl.n}_headline`} value={sl.headline} />
                  ) : (
                    sl.headline
                  )}
                </h2>
              )}
              {sl.subtitle && (
                <p className="text-white/85 text-sm md:text-base mt-3 max-w-xl">
                  {sl.n ? (
                    <InlineEditable sectionId={sectionId} settingKey={`slide_${sl.n}_subtitle`} value={sl.subtitle} />
                  ) : (
                    sl.subtitle
                  )}
                </p>
              )}
              {sl.ctaText && (
                <Link
                  to={sl.ctaLink || "/products"}
                  className="vn-btn vn-btn-outline-light mt-6"
                >
                  {sl.n ? (
                    <InlineEditable sectionId={sectionId} settingKey={`slide_${sl.n}_cta_text`} value={sl.ctaText} />
                  ) : (
                    sl.ctaText
                  )}
                </Link>
              )}
            </div>
          </div>
        );
      })}

      {count > 1 && (
        <div className="absolute bottom-5 left-1/2 -translate-x-1/2 z-20 flex gap-2.5">
          {slides.map((_, i) => (
            <button
              key={i}
              type="button"
              onClick={() => setCurrent(i)}
              aria-label={`Go to slide ${i + 1}`}
              className={`vn-slide-dot ${i === current ? "is-active" : ""}`}
            />
          ))}
        </div>
      )}
    </section>
  );
};

export default VionneSlideshow;
