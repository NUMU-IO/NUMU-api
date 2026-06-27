"use client";

import { Link, useLocale, useResolvedSettings } from "@numueg/theme-sdk";
import { ArrowLeft, ShoppingBag } from "lucide-react";
import {
  applyImageTransform,
  asImageTransform,
  asImageUrl,
  asString,
  localized,
  type SectionRenderProps,
} from "./_shared";
import { InlineEditable } from "./_inline-editable";

/**
 * lux-promo-banner — faithful V3 port of the V2 LuxPromoBanner
 * (numu-egyptian-bazaar/src/themes/luxury-minimal/sections/promo-banner/LuxPromoBanner.tsx).
 *
 * Bordered split panel: a 40%-wide image on the LEFT (4:3 on mobile, full height
 * on desktop, with a ShoppingBag fallback over a muted box) and centered/
 * right-aligned content on the RIGHT — a 10px/0.3em eyebrow badge, a light
 * tracking-tight headline, a muted subtitle, and a solid-black CTA with a
 * trailing ArrowLeft. All V2 className strings kept VERBATIM. Engine-wired:
 * useResolvedSettings (so global tokens + dynamic sources resolve) and
 * InlineEditable on every editable text node.
 */
export default function LuxPromoBanner({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const locale = useLocale();

  const badge = asString(s.badge_text) || localized(locale, "Limited Offer", "عرض محدود");
  const headline =
    asString(s.headline) ||
    localized(locale, "25% Off All Accessories", "خصم ٢٥٪ على كل الإكسسوارات");
  const subtitle =
    asString(s.subtitle) ||
    localized(
      locale,
      "Offer ends this month. Don't miss out!",
      "العرض ساري لنهاية الشهر. متفوتش الفرصة!",
    );
  const ctaText = asString(s.cta_text) || localized(locale, "Shop Now", "تسوق الآن");
  // V2 default targets the accessories category, matching the promo copy.
  const ctaLink = asString(s.cta_link) || "/products?category=accessories";
  const imageUrl = asImageUrl(s.image_url);
  // Non-destructive focal/zoom/rotation. Undefined → image renders unchanged.
  const imageTransform = asImageTransform(s.image_url);

  return (
    <section className="py-12" data-lux-section={sectionId}>
      <div className="container mx-auto px-4">
        <div className="border border-foreground/10 overflow-hidden">
          <div className="flex flex-col md:flex-row items-stretch">
            {/* Image */}
            <div className="w-full md:w-2/5 aspect-[4/3] md:aspect-auto bg-[hsl(var(--muted))]">
              {imageUrl ? (
                <img
                  src={imageUrl}
                  alt=""
                  className="w-full h-full object-cover"
                  style={applyImageTransform(imageTransform, "cover")}
                />
              ) : (
                <div className="w-full h-full flex items-center justify-center bg-foreground/5">
                  <ShoppingBag className="h-16 w-16 text-foreground/20" aria-hidden="true" />
                </div>
              )}
            </div>
            {/* Content */}
            <div className="flex-1 flex flex-col justify-center p-8 md:p-12 text-center md:text-right">
              {badge && (
                <span className="text-[10px] uppercase tracking-[0.3em] text-muted-foreground mb-4 block">
                  <InlineEditable sectionId={sectionId} settingKey="badge_text" value={badge} />
                </span>
              )}
              <h3 className="text-2xl md:text-3xl font-light tracking-tight mb-3">
                <InlineEditable sectionId={sectionId} settingKey="headline" value={headline} />
              </h3>
              <p className="text-sm text-muted-foreground mb-6 max-w-md md:me-0 md:ms-auto">
                <InlineEditable
                  sectionId={sectionId}
                  settingKey="subtitle"
                  value={subtitle}
                  multiline
                />
              </p>
              <div>
                <Link
                  to={ctaLink}
                  className="inline-flex items-center gap-2 px-8 py-3 bg-foreground text-background text-xs uppercase tracking-[0.2em] font-medium hover:opacity-80 transition-opacity"
                >
                  <InlineEditable sectionId={sectionId} settingKey="cta_text" value={ctaText} />
                  <ArrowLeft size={12} aria-hidden="true" className="rtl:-scale-x-100" />
                </Link>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
