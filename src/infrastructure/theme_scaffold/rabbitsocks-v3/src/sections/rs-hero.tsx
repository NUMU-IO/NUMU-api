"use client";
import { Link, useProducts, useLocale } from "@numueg/theme-sdk";
import { asString, localized, type SectionRenderProps } from "./_shared";

const DiamondFrame = ({ image, name }: { image?: string; name?: string }) => (
  <div className="rs-hero-frame relative" style={{ aspectRatio: "1 / 0.9" }}>
    <svg
      viewBox="0 0 100 90"
      className="rs-hero-triangle-outline"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.2"
    >
      <polygon points="50,2 98,45 50,88 2,45" />
    </svg>
    <div className="rs-hero-frame-inner">
      {image ? (
        <img
          src={image}
          alt={name ?? ""}
          className="w-[62%] h-[62%] object-contain"
          style={{ clipPath: "polygon(50% 0%, 100% 50%, 50% 100%, 0% 50%)" }}
        />
      ) : (
        <div
          className="w-[62%] h-[62%] bg-[hsl(var(--rs-surface-high))]"
          style={{ clipPath: "polygon(50% 0%, 100% 50%, 50% 100%, 0% 50%)" }}
        />
      )}
    </div>
  </div>
);

const SquareFrame = ({ image, name }: { image?: string; name?: string }) => (
  <div className="rs-hero-frame rs-hero-frame-square">
    <div className="rs-hero-frame-inner rs-hero-frame-inner-square">
      {image ? (
        <img
          src={image}
          alt={name ?? ""}
          className="w-full h-full object-contain"
        />
      ) : (
        <div className="w-full h-full bg-[hsl(var(--rs-surface-high))]" />
      )}
    </div>
  </div>
);

const TriangleFrame = ({ image, name }: { image?: string; name?: string }) => (
  <div className="rs-hero-frame rs-hero-frame-triangle">
    <svg
      viewBox="0 0 100 95"
      className="rs-hero-triangle-outline"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.2"
    >
      <polygon points="50,2 98,93 2,93" />
    </svg>
    <div className="rs-hero-frame-inner rs-hero-frame-inner-triangle">
      {image ? (
        <img
          src={image}
          alt={name ?? ""}
          className="w-full h-full object-contain"
        />
      ) : (
        <div className="w-full h-full bg-[hsl(var(--rs-surface-high))]" />
      )}
    </div>
  </div>
);

const RsHero = ({ instance }: SectionRenderProps) => {
  const { products } = useProducts();
  const locale = useLocale();
  const s = instance.settings ?? {};
  const badgeText = asString(s.badge_text) || localized(locale, "AUTUMN/WINTER COLLECTION", "تشكيلة خريف/شتاء");
  const headline = asString(s.headline) || localized(locale, "Elegance\nwithout limits.", "أناقة\nمن غير حدود.");
  const subtitle = asString(s.subtitle) || localized(
    locale,
    "We believe in quiet luxury for everyday essentials. Crafted with technical precision and selected for the modern wardrobe.",
    "بنؤمن بالرفاهية الهادئة في تفاصيل يومك. مصنوعة بدقة واتختارت بعناية لخزانة العصري.",
  );
  const ctaLink = asString(s.cta_link, "/products");
  const ctaText = asString(s.cta_text) || localized(locale, "SHOP NOW", "تسوّق دلوقتي");

  const getImg = (i: number) => products[i]?.images?.[0]?.url;
  const getName = (i: number) => products[i]?.name;

  return (
    <section className="rs-hero-stage bg-[hsl(var(--rs-background))] relative overflow-hidden">
      {/* Decorative circles */}
      <div className="absolute top-16 left-[7%] w-8 h-8 rounded-full border border-[hsl(var(--rs-primary)/0.16)] pointer-events-none" />
      <div className="absolute top-28 left-[21%] w-4 h-4 rounded-full border border-[hsl(var(--rs-primary)/0.1)] pointer-events-none" />
      <div className="absolute top-14 left-[35%] w-3 h-3 rounded-full border border-[hsl(var(--rs-primary)/0.12)] pointer-events-none" />
      <div className="absolute top-20 right-[34%] w-5 h-5 rounded-full border border-[hsl(var(--rs-primary)/0.1)] pointer-events-none" />
      <div className="absolute top-10 right-[19%] w-7 h-7 rounded-full border border-[hsl(var(--rs-primary)/0.14)] pointer-events-none" />
      <div className="absolute top-24 right-[7%] w-3 h-3 rounded-full border border-[hsl(var(--rs-primary)/0.18)] pointer-events-none" />
      <div className="absolute top-36 right-[12%] w-2 h-2 rounded-full bg-[hsl(var(--rs-primary)/0.08)] pointer-events-none" />

      {/* Sky / breathing room */}
      <div className="rs-hero-sky" />

      {/* Hero text */}
      <div className="px-6 md:px-10 max-w-[1440px] mx-auto mb-6">
        <h1 className="rs-headline-md whitespace-pre-line text-[hsl(var(--rs-primary))]">
          {headline}
        </h1>
        <p className="mt-3 rs-body max-w-md text-[hsl(var(--rs-primary)/0.65)]">
          {subtitle}
        </p>
      </div>

      {/* Geometric product grid */}
      <div className="px-6 md:px-10 max-w-[1440px] mx-auto">
        <div className="rs-hero-grid">
          <DiamondFrame image={getImg(0)} name={getName(0)} />
          <SquareFrame image={getImg(1)} name={getName(1)} />
          <TriangleFrame image={getImg(2)} name={getName(2)} />
          <DiamondFrame image={getImg(3)} name={getName(3)} />
        </div>
      </div>

      {/* Bottom bar: collection label + CTA */}
      <div className="px-6 md:px-10 py-8 max-w-[1440px] mx-auto flex items-center justify-between">
        <p className="rs-label">{badgeText}</p>
        <Link to={ctaLink} className="rs-btn-ghost inline-block">
          {ctaText}
        </Link>
      </div>
    </section>
  );
};

export default RsHero;
