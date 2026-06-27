"use client";

import { useRef } from "react";
import { Link, useLocale, useResolvedSettings } from "@numueg/theme-sdk";
import { ArrowRight } from "lucide-react";
import {
  applyImageTransform,
  asImageTransform,
  asImageUrl,
  asString,
  localized,
  type SectionRenderProps,
} from "./_shared";
import { InlineEditable } from "./_inline-editable";

const BzHero = ({ instance, sectionId }: SectionRenderProps) => {
  const s = useResolvedSettings(instance);
  const locale = useLocale();

  // Drop-letter is now bilingual: Arabic stores can set a separate Arabic
  // glyph (`letter_ar`) — used when the active locale is Arabic, falling back
  // to the English letter. Inline-edit targets the locale-correct key so a
  // merchant editing in ar edits `letter_ar`.
  const isArabic = (locale ?? "").toLowerCase().startsWith("ar");
  const letterEn = asString(s.letter);
  const letterAr = asString(s.letter_ar);
  const letter = isArabic ? letterAr || letterEn : letterEn;
  const letterKey = isArabic ? "letter_ar" : "letter";
  const headline = asString(s.headline) || localized(locale, "BAZAR DREAMS", "أحلام بازار");
  const subline = asString(s.subline) || localized(locale, "COME TO LIFE", "بتتحقق");
  const tagline =
    asString(s.tagline) ||
    localized(locale, "Handpicked summer essentials, made in Egypt.", "مختارات الصيف الأساسية، صناعة مصرية.");
  const ctaText = asString(s.cta_text) || localized(locale, "SHOP NEW ARRIVALS", "تسوّق وصل حديثًا");
  const ctaLink = asString(s.cta_link) || "/products";
  const secondaryCtaText = asString(s.secondary_cta_text);
  const secondaryCtaLink = asString(s.secondary_cta_link) || "/collections";
  const trustText = asString(s.trust_text);
  // Field key includes "hero" so the merchant hub's ImageUploadField picks
  // the 16:9 crop aspect instead of the default 1:1 square. Falls back to
  // the legacy `image_url` key for any merchant who set it before the rename.
  const imageUrl = asImageUrl(s.hero_image_url) || asImageUrl(s.image_url);
  // Non-destructive focal/zoom/rotation the merchant set on whichever key
  // supplied the URL (hero_image_url is primary, image_url is the legacy
  // fallback). Undefined → image renders exactly as before.
  const imageTransform =
    asImageTransform(s.hero_image_url) || asImageTransform(s.image_url);
  const colorScheme = asString(s.color_scheme) || "auto";
  // Decoration toggles (gap #8) — the morphing blob behind the drop-letter and
  // the SVG wave divider at the hero's base. Default ON so an un-customised
  // store keeps the exact V2 look; a merchant can switch either off.
  const showBlob = s.blob_decoration !== false;
  const showWave = s.show_wave_divider !== false;

  const sectionRef = useRef<HTMLElement | null>(null);

  // Without a hero image we collapse to a single full-width yellow hero —
  // the empty cream half + an orphaned wave looked broken. Merchant uploads
  // an image, the split unlocks.
  const hasImage = Boolean(imageUrl);

  const content = (
    <div className="max-w-md mx-auto md:mx-0">
      {letter && (
        <div className={`bz-heading text-[60px] md:text-[80px] lg:text-[100px] text-[var(--bz-dark)] leading-none mb-2 ${showBlob ? "bz-blob " : ""}inline-block px-4 py-2 bg-[var(--bz-cream)]/80`}>
          <InlineEditable
            sectionId={sectionId}
            settingKey={letterKey}
            value={letter}
          />
        </div>
      )}

      <h1 className="bz-heading text-3xl sm:text-4xl md:text-5xl lg:text-6xl text-[var(--bz-dark)] leading-[1.05]">
        <InlineEditable
          sectionId={sectionId}
          settingKey="headline"
          value={headline}
        />
        <br />
        <span className="text-[var(--bz-navy)]">
          <InlineEditable
            sectionId={sectionId}
            settingKey="subline"
            value={subline}
          />
        </span>
      </h1>

      {tagline && (
        <p className="mt-5 text-sm sm:text-base text-[var(--bz-dark)]/75 leading-relaxed max-w-sm">
          <InlineEditable
            sectionId={sectionId}
            settingKey="tagline"
            value={tagline}
            multiline
          />
        </p>
      )}

      <div className="mt-7 flex flex-wrap items-center gap-3">
        <Link
          to={ctaLink}
          className="bz-btn bz-btn-filled rounded-full px-6 py-3.5 text-[12px] inline-flex items-center gap-2"
        >
          <InlineEditable
            sectionId={sectionId}
            settingKey="cta_text"
            value={ctaText}
          />
          <ArrowRight size={14} aria-hidden="true" className="rtl:-scale-x-100" />
        </Link>
        {secondaryCtaText && (
          <Link
            to={secondaryCtaLink}
            className="bz-btn rounded-full px-6 py-3.5 text-[12px] inline-flex items-center gap-2 border-2 border-[var(--bz-dark)] text-[var(--bz-dark)] hover:bg-[var(--bz-dark)] hover:text-[var(--bz-amber)] transition-colors"
          >
            <InlineEditable
              sectionId={sectionId}
              settingKey="secondary_cta_text"
              value={secondaryCtaText}
            />
          </Link>
        )}
      </div>

      {trustText && (
        <div className="mt-8 pt-6 border-t-2 border-[var(--bz-dark)]/15">
          <p className="bz-label text-[11px] text-[var(--bz-dark)]/70 tracking-wider">
            <InlineEditable
              sectionId={sectionId}
              settingKey="trust_text"
              value={trustText}
            />
          </p>
        </div>
      )}
    </div>
  );

  return (
    <section
      ref={sectionRef}
      className="relative overflow-hidden"
      data-color-scheme={colorScheme}
    >
      {hasImage ? (
        // Split layout: image one side, content the other. Under dir="rtl"
        // CSS grid flips visually so the photo lands on the right (where
        // Arabic readers' eyes land first).
        <div className="grid md:grid-cols-2 min-h-[80vh] md:min-h-[88vh]">
          <div className="relative bg-[var(--bz-cream)] min-h-[40vh] md:min-h-0 overflow-hidden">
            {/* Shopify-style framed image: the photo COVERS its column (fills
                the frame, cropping to the merchant's focal point) instead of
                letterboxing inside it. `cover` is the default fit; the merchant
                fine-tunes framing via the editor's Adjust (focal/zoom) modal. */}
            <img
              src={imageUrl}
              alt=""
              className="absolute inset-0 w-full h-full object-cover"
              style={applyImageTransform(imageTransform, "cover")}
              loading="eager"
            />
          </div>
          <div className="relative bz-wavy-bg flex flex-col justify-center px-6 sm:px-10 md:px-12 lg:px-16 py-12 md:py-16">
            {content}
          </div>
        </div>
      ) : (
        // No image yet → centered single-column hero so the page doesn't
        // ship with a giant empty half-screen.
        <div className="relative bz-wavy-bg flex flex-col justify-center items-center text-center px-6 sm:px-10 md:px-12 py-20 md:py-28 min-h-[70vh]">
          <div className="[&_*]:text-center [&_h1]:max-w-2xl [&_p]:max-w-md [&_p]:mx-auto [&_.flex]:justify-center">
            {content}
          </div>
          {showWave && (
            <svg viewBox="0 0 1440 80" className="absolute bottom-0 left-0 w-full pointer-events-none" preserveAspectRatio="none" aria-hidden="true">
              <path d="M0,80 C300,20 600,60 900,30 C1100,10 1300,50 1440,25 L1440,80 Z" fill="var(--bz-navy)" />
            </svg>
          )}
        </div>
      )}
    </section>
  );
};

export default BzHero;
