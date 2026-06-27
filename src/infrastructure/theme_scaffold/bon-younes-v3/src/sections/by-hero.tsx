"use client";

import { Link, useLocale, useProducts, useResolvedSettings } from "@numueg/theme-sdk";
import { ArrowUpRight } from "lucide-react";
import { applyImageTransform, asImageTransform, asImageUrl, asString, demoOrPlaceholder, localized, PLACEHOLDER_IMG, productHref, resolveBlocks, useBlockResolveContext, useDemo, type ImageTransform, type SectionRenderProps } from "./_shared";
import { InlineEditable } from "./_inline-editable";

interface DrinkCard {
  label: string;
  image: string;
  href: string;
  // Non-destructive focal/zoom/rotation — only set for MERCHANT-configured
  // drink images (editor `drink` blocks). Undefined for product-derived /
  // demo images so those render exactly as before.
  transform?: ImageTransform;
}

const FALLBACK_DRINKS: DrinkCard[] = [
  {
    label: "Dalgona",
    image: "https://images.unsplash.com/photo-1587080413959-06b859fb107d?auto=format&fit=crop&w=600&q=70",
    href: "/products",
  },
  {
    label: "Caramel Latte",
    image: "https://images.unsplash.com/photo-1572442388796-11668a67e53d?auto=format&fit=crop&w=600&q=70",
    href: "/products",
  },
  {
    label: "Iced Americano",
    image: "https://images.unsplash.com/photo-1517701604599-bb29b565090c?auto=format&fit=crop&w=600&q=70",
    href: "/products",
  },
  {
    label: "Flat White",
    image: "https://images.unsplash.com/photo-1541167760496-1628856ab772?auto=format&fit=crop&w=600&q=70",
    href: "/products",
  },
  {
    label: "Cappuccino",
    image: "https://images.unsplash.com/photo-1509042239860-f550ce710b93?auto=format&fit=crop&w=600&q=70",
    href: "/products",
  },
  {
    label: "Mocha",
    image: "https://images.unsplash.com/photo-1485808191679-5f86510681a2?auto=format&fit=crop&w=600&q=70",
    href: "/products",
  },
];

export default function ByHero({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const { products } = useProducts();
  const blkCtx = useBlockResolveContext();
  const locale = useLocale();

  const eyebrow = asString(s.eyebrow);
  const headline =
    asString(s.headline) ||
    localized(locale, "Enjoy Your Coffee with Bon Younes", "اشرب قهوتك مع بون يونس");
  const subtitle =
    asString(s.subtitle) ||
    localized(
      locale,
      "Discover the daily roast — beans pulled fresh, milk steamed slow, and every cup poured with care from our Mansoura roastery.",
      "اكتشف تحميصة اليوم — بُن طازة، لبن بيتبخّر على مهل، وكل فنجان بيتحضّر بحب من محمصتنا في المنصورة.",
    );
  const ctaText = asString(s.cta_text) || localized(locale, "Explore product", "اكتشف المنتجات");
  const ctaLink = asString(s.cta_link) || "/products";

  // Pull merchant-configured drink cards first (editor `drink` blocks); if
  // none, surface real store products; if neither, fall back to curated demo
  // cards so the theme always looks intentional in the customizer.
  const configured: DrinkCard[] = resolveBlocks(instance, "drink", blkCtx)
    .map((r) => ({
      label: asString(r.label),
      image: asImageUrl(r.image),
      href: asString(r.href) || "/products",
      transform: asImageTransform(r.image),
    }))
    // Keep a drink if it has a label OR an image; default a missing image to
    // the neutral placeholder so a real (product-derived or merchant) drink is
    // never silently dropped just because its photo isn't set yet.
    .filter((d) => d.label || d.image)
    .map((d) => ({ ...d, image: d.image || PLACEHOLDER_IMG }));

  const fromProducts: DrinkCard[] = products
    .slice(0, 8)
    .map((p) => ({
      label: p.name,
      image: p.images?.[0]?.url ?? "",
      href: productHref(p.slug || p.id),
    }))
    .filter((d) => d.label || d.image)
    .map((d) => ({ ...d, image: d.image || PLACEHOLDER_IMG }));

  const demo = useDemo();
  const source: DrinkCard[] =
    configured.length > 0
      ? configured
      : fromProducts.length > 0
        ? fromProducts
        : demoOrPlaceholder(demo, FALLBACK_DRINKS);

  // Duplicate the strip so the keyframe loop reads as a seamless cycle.
  const loop = [...source, ...source];

  // Split the headline into two visual lines on the comma/connector so
  // the typography mirrors the reference: "Enjoy Your Coffee" / "with X".
  const [line1, line2] = splitHeadline(headline);

  const beanSrc =
    "data:image/svg+xml;utf8,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 120 120'%3E%3Cg fill='%233A2418'%3E%3Cellipse cx='42' cy='44' rx='17' ry='25' transform='rotate(-22 42 44)'/%3E%3Cellipse cx='70' cy='62' rx='14' ry='22' transform='rotate(-18 70 62)'/%3E%3Cellipse cx='52' cy='80' rx='15' ry='22' transform='rotate(18 52 80)'/%3E%3C/g%3E%3Cg fill='%23F7F1E8' opacity='.9'%3E%3Cpath d='M30 38 q12 6 24 12' stroke='%23F7F1E8' stroke-width='2.5' fill='none' transform='rotate(-22 42 44)'/%3E%3Cpath d='M58 56 q12 6 24 12' stroke='%23F7F1E8' stroke-width='2.5' fill='none' transform='rotate(-18 70 62)'/%3E%3Cpath d='M40 74 q12 6 24 12' stroke='%23F7F1E8' stroke-width='2.5' fill='none' transform='rotate(18 52 80)'/%3E%3C/g%3E%3C/svg%3E";

  return (
    <section className="by-hero" data-by-section={sectionId}>
      <img
        className="by-hero-bean by-hero-bean-left"
        src={beanSrc}
        alt=""
        aria-hidden="true"
        width="120"
        height="120"
      />
      <img
        className="by-hero-bean by-hero-bean-right"
        src={beanSrc}
        alt=""
        aria-hidden="true"
        width="120"
        height="120"
      />
      <div className="by-hero-inner">
        {eyebrow && (
          <InlineEditable
            sectionId={sectionId}
            settingKey="eyebrow"
            value={eyebrow}
            className="by-eyebrow"
          />
        )}
        <h1 className="by-hero-headline">
          <InlineEditable
            sectionId={sectionId}
            settingKey="headline"
            value={headline}
            multiline
          >
            {line1}
            {line2 && (
              <>
                {" "}
                <br />
                <em>{line2}</em>
              </>
            )}
          </InlineEditable>
        </h1>
        <p className="by-hero-subtitle">
          <InlineEditable
            sectionId={sectionId}
            settingKey="subtitle"
            value={subtitle}
            multiline
          />
        </p>
        <Link to={ctaLink} className="by-hero-cta">
          <InlineEditable
            sectionId={sectionId}
            settingKey="cta_text"
            value={ctaText}
          />
          <ArrowUpRight size={16} />
        </Link>
      </div>

      <div className="by-hero-strip" role="group" aria-label="Featured drinks">
        <div className="by-hero-strip-track">
          {loop.map((d, i) => (
            <Link
              key={`${d.label}-${i}`}
              to={d.href}
              className="by-hero-card"
              aria-label={d.label || "Featured product"}
            >
              <div className="by-hero-card-frame">
                <img
                  src={d.image}
                  alt={d.label}
                  loading={i < 5 ? "eager" : "lazy"}
                  decoding="async"
                  style={applyImageTransform(d.transform, "cover")}
                />
              </div>
              <p className="by-hero-card-label">{d.label}</p>
            </Link>
          ))}
        </div>
      </div>
    </section>
  );
}

/**
 * Split a headline into two visual lines for the hero layout.
 *
 * Pattern: "X with Y" → ["X", "with Y"], otherwise split on the
 * last space if the headline has 4+ words, otherwise put the whole
 * thing on line 1.
 */
function splitHeadline(text: string): [string, string] {
  const trimmed = text.trim();
  const withMatch = trimmed.match(/^(.*?)(\s+with\s+.*)$/i);
  if (withMatch) return [withMatch[1].trim(), withMatch[2].trim()];
  const words = trimmed.split(/\s+/);
  if (words.length >= 4) {
    const half = Math.ceil(words.length / 2);
    return [words.slice(0, half).join(" "), words.slice(half).join(" ")];
  }
  return [trimmed, ""];
}
