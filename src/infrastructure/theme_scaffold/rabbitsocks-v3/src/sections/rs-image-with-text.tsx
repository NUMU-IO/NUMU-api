"use client";
import { Link, useLocale } from "@numueg/theme-sdk";
import { applyImageTransform, asImageTransform, asString, localized, type SectionRenderProps } from "./_shared";

const RsImageWithText = ({ instance }: SectionRenderProps) => {
  const locale = useLocale();
  const s = instance.settings ?? {};
  const label = asString(s.label) || localized(locale, "THE PHILOSOPHY", "الفلسفة");
  // Support both V2 home variants: (headline + headline_accent + text) and
  // (title + body). Whichever the merchant set wins, falling back to the
  // quiet-luxury default copy.
  const headline = asString(s.headline) || asString(s.title) || localized(locale, "The luxury of things that", "رفاهية الحاجات اللي");
  const headlineAccent = asString(s.headline_accent) || localized(locale, "last.", "بتفضل.");
  const text =
    asString(s.text) ||
    asString(s.body) ||
    localized(
      locale,
      '"Rabbitsocks was born from a pursuit of the perfect tactile experience. We don\'t chase trends; we refine the invisible details of the daily ritual."',
      '"رابيت سوكس اتولدت من السعي ورا إحساس اللمس المثالي. احنا مبنجريش ورا الموضة؛ احنا بنهذّب تفاصيل طقوس اليوم الخفية."',
    );
  const ctaText = asString(s.cta_text);
  const ctaLink = asString(s.cta_link) || asString(s.cta_url) || "/products";
  const imageUrl = asString(s.image_url);
  const imageTransform = asImageTransform(s.image_url);
  const imageAlt = asString(s.image_alt, label);
  // V2 used both "left/right" and "start/end"; treat start/left as image-first.
  const rawPos = asString(s.image_position, "right");
  const imageFirst = rawPos === "left" || rawPos === "start";

  const textPanel = (
    <div className="rs-split-copy-panel flex flex-col justify-center h-full">
      <p className="rs-label mb-5">{label}</p>
      <h2 className="rs-headline-md text-[hsl(var(--rs-primary))] mb-6 leading-[0.94]">
        {headline}{" "}
        {headlineAccent && (
          <span className="text-[hsl(var(--rs-primary)/0.28)]">{headlineAccent}</span>
        )}
      </h2>
      {text && (
        <p className="rs-body text-[hsl(var(--rs-primary)/0.65)] max-w-sm mb-8 italic">
          {text}
        </p>
      )}
      {ctaText && (
        <Link to={ctaLink} className="rs-btn-ghost inline-block self-start">
          {ctaText}
        </Link>
      )}
    </div>
  );

  const imagePanel = (
    <div className="rs-split-art-panel flex items-center justify-center overflow-hidden">
      {imageUrl ? (
        <img
          src={imageUrl}
          alt={imageAlt}
          className={`w-full h-full object-cover ${imageTransform ? "" : "rs-img-zoom"}`}
          style={applyImageTransform(imageTransform, "cover")}
          loading="lazy"
        />
      ) : (
        <div className="w-full h-full bg-[hsl(var(--rs-surface-high))]" />
      )}
    </div>
  );

  return (
    <section className="rs-split-section">
      <div className="grid grid-cols-1 md:grid-cols-2 min-h-[520px]">
        {imageFirst ? (
          <>
            {imagePanel}
            {textPanel}
          </>
        ) : (
          <>
            {textPanel}
            {imagePanel}
          </>
        )}
      </div>
    </section>
  );
};

export default RsImageWithText;
