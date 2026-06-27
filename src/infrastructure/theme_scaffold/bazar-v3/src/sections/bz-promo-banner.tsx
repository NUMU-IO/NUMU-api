"use client";

import { Link, useLocale, useResolvedSettings, useShop } from "@numueg/theme-sdk";
import { ShoppingBag } from "lucide-react";
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
    "https://images.unsplash.com/photo-1483985988355-763728e1935b?auto=format&fit=crop&w=1200&q=70",
};

const BzPromoBanner = ({ instance, sectionId }: SectionRenderProps) => {
  const s = useResolvedSettings(instance);
  const demo = useDemo();
  const shop = useShop();
  const locale = useLocale();

  const configuredImage = asImageUrl(s.image_url);
  const imageUrl =
    configuredImage || (demo ? FALLBACK_IMAGE.image : "");
  // Non-destructive focal/zoom/rotation for the large editorial card image.
  // Undefined → the image renders exactly as before (object-cover + grayscale
  // hover transition, which is a filter not a transform so it doesn't clash).
  const imageTransform = asImageTransform(s.image_url);
  const title = asString(s.title) || "BAZAR";
  const subtitle = asString(s.subtitle) || localized(locale, "SEASONAL EDIT", "تشكيلة الموسم");
  const cardLabel = asString(s.card_label) || localized(locale, "CURATED", "مختارة بعناية");
  const ctaText = asString(s.cta_text) || localized(locale, "SHOP NOW", "تسوّق دلوقتي");
  const ctaLink = asString(s.cta_link) || "/products";
  // The large card's overline uses the live store name (uppercased) so the
  // editorial banner always reflects the merchant's brand without extra setup.
  const storeName = (shop?.name || "BAZAR").toUpperCase();
  // Demo placeholderize the store-name overline only when the merchant truly
  // has no store name in context.
  const overline = demoOrPlaceholder(demo || Boolean(shop?.name), [
    { name: storeName },
  ])[0].name || "BAZAR";

  return (
    <section className="py-12 md:py-16 lg:py-24 bg-[var(--bz-cream)]">
      <div className="container mx-auto px-4">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 sm:gap-4 md:gap-6">
          <div className="md:col-span-2 relative rounded-2xl md:rounded-3xl overflow-hidden aspect-[4/3] sm:aspect-[16/10] group bz-card-hover">
            {imageUrl ? (
              <img
                src={imageUrl}
                alt=""
                className="w-full h-full object-cover grayscale group-hover:grayscale-0 transition-all duration-700"
                style={applyImageTransform(imageTransform, "cover")}
                loading="lazy"
              />
            ) : (
              <div
                className="w-full h-full bg-[var(--bz-navy)] flex items-center justify-center relative"
                aria-hidden="true"
              >
                {/* Subtle organic blob to break the flat fill so the
                    fallback reads as designed empty-state, not "image
                    failed". */}
                <div className="absolute -top-10 -end-10 w-40 h-40 bg-[var(--bz-amber)]/20 bz-blob" />
                <div className="absolute -bottom-12 -start-8 w-32 h-32 bg-[var(--bz-cream)]/10 bz-blob" />
                <ShoppingBag size={48} className="text-[var(--bz-amber)]/40 relative" />
              </div>
            )}
            <div className="absolute inset-0 bg-gradient-to-t from-black/70 via-transparent to-transparent" />
            <div className="absolute bottom-4 sm:bottom-6 start-4 sm:start-6">
              <span className="bz-label text-[var(--bz-amber)]">
                <InlineEditable sectionId={sectionId} settingKey="subtitle" value={subtitle} />
              </span>
              <h3 className="bz-heading text-lg sm:text-xl md:text-2xl text-white mt-1 sm:mt-2">
                {overline}
              </h3>
            </div>
          </div>
          <div className="relative rounded-2xl md:rounded-3xl overflow-hidden aspect-[16/9] sm:aspect-[3/2] md:aspect-auto group bz-card-hover bg-[var(--bz-amber)]">
            <div className="absolute inset-0 flex flex-col items-center justify-center text-center p-5 sm:p-6">
              <span className="bz-label text-[var(--bz-dark)]/60">
                <InlineEditable sectionId={sectionId} settingKey="card_label" value={cardLabel} />
              </span>
              <h3 className="bz-heading text-xl sm:text-2xl md:text-3xl text-[var(--bz-dark)] mt-2 sm:mt-3">
                <InlineEditable sectionId={sectionId} settingKey="title" value={title} />
              </h3>
              <Link to={ctaLink} className="bz-btn mt-4 sm:mt-6 text-[10px] sm:text-[11px]">
                <InlineEditable sectionId={sectionId} settingKey="cta_text" value={ctaText} />
              </Link>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
};

export default BzPromoBanner;
