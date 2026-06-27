import {
  useEffect,
  useState } from "react";
import { EditableText } from "../lib/EditableText";
import type { EmpSectionProps } from "../lib/section";
import { useT } from "../lib/i18n";

interface HeroSettings {
  headline?: string;
  subtitle?: string;
  cta_text?: string;
  cta_link?: string;
  image_1?: string;
  image_2?: string;
  image_3?: string;
  autoplay?: boolean;
}

/**
 * Full-bleed hero slideshow — black canvas, cross-fading cover images with a
 * dark bottom gradient, centered headline + pill CTA at the bottom, dots and
 * prev/next arrows. Slides auto-advance on a timer (client-only effect, so the
 * SSR render is deterministic — slide 0 paints first).
 */
export default function Hero({ id, settings }: EmpSectionProps) {
  const s = settings as HeroSettings;
  const t = useT();
  const headline = s.headline ?? t("Discover the new collection", "اكتشف التشكيلة الجديدة");
  const subtitle = s.subtitle ?? t("Shop now", "تسوق الآن");
  const ctaText = s.cta_text ?? t("Shop", "تسوق");
  const ctaLink = s.cta_link ?? "/products";

  const slides = [s.image_1, s.image_2, s.image_3].filter(
    (x): x is string => Boolean(x),
  );
  if (slides.length === 0) {
    slides.push(
      "https://images.unsplash.com/photo-1441986300917-64674bd600d8?w=1600",
    );
  }

  const [current, setCurrent] = useState(0);
  const count = slides.length;

  useEffect(() => {
    if (s.autoplay === false || count <= 1) return;
    const t = setInterval(() => setCurrent((p) => (p + 1) % count), 6000);
    return () => clearInterval(t);
  }, [count, s.autoplay]);

  const go = (i: number) => setCurrent(((i % count) + count) % count);

  return (
    <section className="nt-hero">
      {slides.map((src, i) => (
        <div
          key={i}
          className={`nt-hero__slide${i === current ? " is-active" : ""}`}
          aria-hidden={i !== current}
        >
          <img src={src} alt="" />
          <div className="nt-hero__overlay" />
        </div>
      ))}

      <div className="nt-hero__content">
        <EditableText
          as="h1"
          className="nt-hero__title"
          sectionId={id}
          settingId="headline"
          value={headline}
        />
        <EditableText
          as="p"
          className="nt-hero__sub"
          sectionId={id}
          settingId="subtitle"
          value={subtitle}
        />
        <a className="nt-hero__cta" href={ctaLink}>
          <EditableText
            as="span"
            sectionId={id}
            settingId="cta_text"
            value={ctaText}
          />
        </a>

        {count > 1 ? (
          <div className="nt-hero__dots">
            {slides.map((_, i) => (
              <button
                key={i}
                className={`nt-hero__dot${i === current ? " is-active" : ""}`}
                type="button"
                aria-label={`شريحة ${i + 1}`}
                onClick={() => go(i)}
              />
            ))}
          </div>
        ) : null}
      </div>

      {count > 1 ? (
        <>
          <button
            className="nt-hero__arrow nt-hero__arrow--prev"
            type="button"
            aria-label="السابق"
            onClick={() => go(current - 1)}
          >
            <Chevron dir="start" />
          </button>
          <button
            className="nt-hero__arrow nt-hero__arrow--next"
            type="button"
            aria-label="التالي"
            onClick={() => go(current + 1)}
          >
            <Chevron dir="end" />
          </button>
        </>
      ) : null}
    </section>
  );
}

const Chevron = ({ dir }: { dir: "start" | "end" }) => (
  <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
    {dir === "start" ? <path d="m15 18-6-6 6-6" /> : <path d="m9 18 6-6-6-6" />}
  </svg>
);
