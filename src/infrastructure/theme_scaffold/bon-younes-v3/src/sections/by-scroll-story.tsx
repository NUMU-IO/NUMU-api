"use client";

import { useEffect, useRef } from "react";
import { useLocale, useResolvedSettings } from "@numueg/theme-sdk";
import {
  applyImageTransform,
  asImageTransform,
  asImageUrl,
  asString,
  demoOrPlaceholder,
  localized,
  PLACEHOLDER_IMG,
  resolveBlocks,
  useBlockResolveContext,
  useDemo,
  type ImageTransform,
  type SectionRenderProps,
} from "./_shared";
import { InlineEditable } from "./_inline-editable";

interface StoryPanel {
  title: string;
  binomial: string;
  body: string;
  image: string;
  // Non-destructive focal/zoom/rotation for the merchant-configured panel
  // image. Undefined when the merchant set no transform → image unchanged.
  transform?: ImageTransform;
}

// Marketplace-preview-only demo panels. Bilingual via localized() so the
// "Try theme" preview reads correctly under both en and ar. On a real store
// (demo=false) these never render — demoOrPlaceholder returns [].
const FALLBACK_PANELS = (locale: string | undefined): StoryPanel[] => [
  {
    title: localized(locale, "Espresso", "إسبريسو"),
    binomial: localized(locale, "the daily ritual", "طقس كل يوم"),
    body: localized(
      locale,
      "A thirty-second pull through freshly ground beans. Dense, sweet, a thick crema on top — the foundation of everything we serve.",
      "تحضير في ثلاثين ثانية من بُن متطحون طازة. تقيل وحلو وفوقه كريمة سميكة — أساس كل حاجة بنقدّمها.",
    ),
    image: "https://images.unsplash.com/photo-1559056199-641a0ac8b55e?auto=format&fit=crop&w=1600&q=70",
  },
  {
    title: localized(locale, "Latte", "لاتيه"),
    binomial: localized(locale, "milk meets craft", "لبن بيقابل صنعة"),
    body: localized(
      locale,
      "Velvet-steamed whole milk folded into a double espresso. Smooth, balanced, finished with a slow rosetta.",
      "لبن كامل الدسم متبخّر زي القطيفة فوق دبل إسبريسو. ناعم ومتوازن، وبنزيّنه برسمة روزيتا على مهل.",
    ),
    image: "https://images.unsplash.com/photo-1517256064527-09c73fc73e38?auto=format&fit=crop&w=1600&q=70",
  },
  {
    title: localized(locale, "Iced Coffee", "قهوة مثلجة"),
    binomial: localized(locale, "long & slow", "على مهل"),
    body: localized(
      locale,
      "Twelve hours of cold brew gives us a coffee that's chocolatey, low-acid, and built for Mansoura summers.",
      "اتنعمل كولد برو اتنعشر ساعة، فطلع طعمه شيكولاتة وحموضته خفيفة — معمول لصيف المنصورة.",
    ),
    image: "https://images.unsplash.com/photo-1517701604599-bb29b565090c?auto=format&fit=crop&w=1600&q=70",
  },
  {
    title: localized(locale, "Fresh Juice", "عصير طازة"),
    binomial: localized(locale, "from the orchard", "من البستان"),
    body: localized(
      locale,
      "Cold-pressed mango, orange, sugarcane, and seasonal blends — vitamins in a glass, no syrup, no shortcuts.",
      "مانجة وبرتقان وقصب وخلطات الموسم، كله معصور على البارد — فيتامينات في كباية، من غير سكر ولا اختصارات.",
    ),
    image: "https://images.unsplash.com/photo-1622597467836-f3285f2131b8?auto=format&fit=crop&w=1600&q=70",
  },
  {
    title: localized(locale, "Desserts", "حلويات"),
    binomial: localized(locale, "the sweet pair", "التوأم الحلو"),
    body: localized(
      locale,
      "Baklava, basbousa, brownies and stracciatella cheesecake — every cup deserves a friend on the side.",
      "بقلاوة وبسبوسة وبراوني وتشيز كيك ستراتشيتيلا — كل فنجان يستاهل صاحب جنبه.",
    ),
    image: "https://images.unsplash.com/photo-1551024601-bec78aea704b?auto=format&fit=crop&w=1600&q=70",
  },
];

export default function ByScrollStory({
  instance,
  sectionId,
}: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const locale = useLocale();

  const eyebrow = asString(s.eyebrow) || localized(locale, "the menu, told slowly", "المنيو، على مهل");
  const title = asString(s.title) || localized(locale, "Five reasons to come back tomorrow", "خمس أسباب تخلّيك ترجع بكرة");

  // Resolve block settings so a panel field bound to a dynamic source
  // (editor "Store → Name", product.title, …) yields its real value instead
  // of an empty string — otherwise such panels look "empty" and get dropped.
  const blkCtx = useBlockResolveContext();
  const raw: StoryPanel[] = resolveBlocks(instance, "panel", blkCtx)
    .map((r) => ({
      title: asString(r.title),
      binomial: asString(r.binomial),
      body: asString(r.body),
      // A real panel that has text but no image still renders, falling back
      // to the neutral on-brand placeholder rather than being filtered out.
      image: asImageUrl(r.image) || PLACEHOLDER_IMG,
      transform: asImageTransform(r.image),
    }))
    // Keep a panel that has EITHER a title or an image (previously required
    // both, which dropped text-only or dynamic-title panels to FALLBACK).
    .filter((p) => p.title || p.image);

  const demo = useDemo();
  const panels: StoryPanel[] = raw.length > 0 ? raw : demoOrPlaceholder(demo, FALLBACK_PANELS(locale));
  const n = panels.length;

  const stageRef = useRef<HTMLDivElement | null>(null);

  // Browsers without scroll-driven animation support (Safari <19, older
  // mobile WebKit) won't run the @supports block in CSS — but we still
  // want to set inline --by-n so anything that DOES support it gets the
  // right scroll length. Cheap and harmless when unsupported.
  useEffect(() => {
    if (stageRef.current) {
      stageRef.current.style.setProperty("--by-n", String(n));
    }
  }, [n]);

  // On a real installed store (demo=false) with no configured panels,
  // demoOrPlaceholder returns [] → hide the section entirely instead of
  // rendering an empty stage/container. Placed after all hooks so the
  // Rules of Hooks hold (hooks must run on every render).
  if (n === 0) return null;

  return (
    <section
      className="by-story"
      data-by-section={sectionId}
      aria-label="Bon Younes story"
    >
      <span className="by-story-eyebrow by-eyebrow">
        <InlineEditable
          sectionId={sectionId}
          settingKey="eyebrow"
          value={eyebrow}
        />
      </span>
      <h2 className="by-story-title">
        <InlineEditable
          sectionId={sectionId}
          settingKey="title"
          value={title}
        />
      </h2>

      {/* Mobile/fallback layout — vertical card stack. */}
      <div className="by-story-track-fallback" aria-hidden={false}>
        {panels.map((p, i) => (
          <article
            key={`${p.title}-${i}-fallback`}
            className="by-story-card-fallback"
          >
            <figure>
              <img src={p.image} alt={p.title} loading="lazy" decoding="async" style={applyImageTransform(p.transform, "cover")} />
            </figure>
            <header>
              <h3>{p.title}</h3>
              {p.binomial && <em>{p.binomial}</em>}
              {p.body && <p>{p.body}</p>}
            </header>
          </article>
        ))}
      </div>

      {/* Desktop scroll-driven stage — only painted when @supports passes. */}
      <div
        className="by-story-stage"
        ref={stageRef}
        style={{ ["--by-n" as string]: String(n) } as React.CSSProperties}
      >
        <main className="by-story-main">
          {panels.map((p, i) => (
            <article
              key={`${p.title}-${i}`}
              className="by-story-panel"
              style={{ ["--by-i" as string]: String(i) } as React.CSSProperties}
            >
              <header className="by-story-panel-half by-story-panel-header">
                <h3>{p.title}</h3>
                {p.binomial && <em>{p.binomial}</em>}
                {p.body && <p>{p.body}</p>}
              </header>
              <figure className="by-story-panel-half by-story-panel-figure">
                <img
                  src={p.image}
                  alt={p.title}
                  loading={i === 0 ? "eager" : "lazy"}
                  decoding="async"
                  style={applyImageTransform(p.transform, "cover")}
                />
              </figure>
            </article>
          ))}
        </main>
        {panels.map((p, i) => (
          <div
            key={`spacer-${p.title}-${i}`}
            className="by-story-spacer"
            aria-hidden="true"
          />
        ))}
      </div>
    </section>
  );
}
