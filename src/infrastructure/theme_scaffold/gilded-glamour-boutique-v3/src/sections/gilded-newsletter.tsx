"use client";

import { useState } from "react";
import { Check } from "lucide-react";
import { useLocale, useResolvedSettings } from "@numueg/theme-sdk";
import { asString, localized, type SectionRenderProps } from "./_shared";
import { InlineEditable } from "./_inline-editable";

/**
 * gilded-newsletter — faithful V3 port of the V2 GildedNewsletter
 * (numu-egyptian-bazaar/src/themes/gilded-glamour-boutique/sections/newsletter/GildedNewsletter.tsx).
 *
 * Centered max-w-xl column: a 10px/0.3em uppercase muted eyebrow (title), a
 * muted-foreground subtitle, then an inline form — a left-to-right hairline
 * email input (gold focus border) that flexes to fill, beside a gold-fill
 * uppercase wide-tracked button. On submit the form swaps to an olive check +
 * success message. All V2 className strings kept verbatim, only the brand gold
 * pieces use the global-wired `border-gold` / `bg-gold` / `text-olive` helper
 * classes so the merchant's Accent picker repaints them. The V2
 * `useNewsletterSubmit` hook isn't available in the V3 bundle, so submission is
 * local state only (no API persistence — matches the other V3 ports). Engine-
 * wired: useResolvedSettings (global tokens + dynamic sources + draft preview)
 * and InlineEditable on every editable text field. Bilingual EN/AR defaults.
 */
const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export default function GildedNewsletter({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const locale = useLocale();

  const title = asString(s.title) || localized(locale, "Newsletter", "النشرة البريدية");
  const subtitle =
    asString(s.subtitle) ||
    localized(locale, "Join the empire. Be first to know.", "انضم إلى الإمبراطورية. كن أول من يعرف.");
  const buttonText = asString(s.button_text) || localized(locale, "Join", "اشترك");
  const placeholder = asString(s.placeholder) || localized(locale, "Your email", "بريدك الإلكتروني");
  const successText = localized(locale, "Successfully subscribed", "تم الاشتراك بنجاح");

  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);

  const handleSubmit = () => {
    const trimmed = email.trim();
    if (!trimmed || !EMAIL_REGEX.test(trimmed)) return;
    setSubmitted(true);
    setEmail("");
  };

  return (
    <section className="py-12 md:py-20 lg:py-32" data-gilded-section={sectionId}>
      <div className="container mx-auto px-4">
        <div className="max-w-xl mx-auto text-center">
          <h5 className="text-[10px] sm:text-xs tracking-[0.2em] sm:tracking-[0.3em] uppercase text-muted-foreground mb-3 md:mb-4">
            <InlineEditable sectionId={sectionId} settingKey="title" value={title} />
          </h5>
          <p className="text-sm text-muted-foreground mb-6">
            <InlineEditable sectionId={sectionId} settingKey="subtitle" value={subtitle} multiline />
          </p>

          {submitted ? (
            <div className="flex items-center justify-center gap-2 h-12 text-sm font-semibold text-olive">
              <Check className="h-5 w-5" />
              <span>{successText}</span>
            </div>
          ) : (
            <div className="flex flex-col sm:flex-row max-w-md mx-auto gap-2 sm:gap-0">
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleSubmit();
                }}
                placeholder={placeholder}
                dir="ltr"
                className="flex-1 bg-transparent border border-border px-4 py-3 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-gold transition-colors"
              />
              <button
                onClick={handleSubmit}
                className="bg-gold text-foreground px-6 py-3 text-xs font-semibold tracking-[0.15em] uppercase hover:bg-gold-dark transition-colors"
              >
                <InlineEditable sectionId={sectionId} settingKey="button_text" value={buttonText} />
              </button>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
