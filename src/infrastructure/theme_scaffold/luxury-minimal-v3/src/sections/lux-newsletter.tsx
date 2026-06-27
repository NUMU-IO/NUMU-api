"use client";

import { useState } from "react";
import { useLocale, useResolvedSettings } from "@numueg/theme-sdk";
import { asString, localized, type SectionRenderProps } from "./_shared";
import { InlineEditable } from "./_inline-editable";

/**
 * lux-newsletter — faithful V3 port of the V2 LuxNewsletter
 * (numu-egyptian-bazaar/src/themes/luxury-minimal/sections/newsletter/LuxNewsletter.tsx).
 *
 * Centered, max-w-md column: a 10px/0.3em uppercase eyebrow (title), an
 * uppercase wide-tracked `lux-heading` (subtitle), then an inline form — a
 * left-to-right `lux-input` email field that flexes to fill, beside a solid
 * black `lux-btn`. All V2 className strings kept verbatim. The V2
 * `useNewsletterSubmit` hook isn't available in the V3 bundle, so submission is
 * local state only (no API persistence — matches the other V3 ports): on submit
 * the button swaps to the localized success confirmation. Engine-wired:
 * useResolvedSettings (global tokens + dynamic sources) and InlineEditable on
 * every editable text field.
 */
export default function LuxNewsletter({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const locale = useLocale();

  const title =
    asString(s.title) || localized(locale, "Subscribe to Our Newsletter", "اشترك في نشرتنا");
  const subtitle =
    asString(s.subtitle) || localized(locale, "Be the first to know", "كن أول من يعرف");
  const buttonText = asString(s.button_text) || localized(locale, "Subscribe", "اشترك");
  const placeholder =
    asString(s.placeholder) || localized(locale, "Email address", "البريد الإلكتروني");
  const successText =
    asString(s.success_text) || localized(locale, "Subscribed", "تم الاشتراك");

  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);

  const handleSubmit = () => {
    if (!email) return;
    setSubmitted(true);
    setEmail("");
  };

  return (
    <section className="py-16" data-lux-section={sectionId}>
      <div className="container mx-auto px-4 text-center max-w-md">
        {title && (
          <p className="text-[10px] uppercase tracking-[0.3em] text-muted-foreground mb-4">
            <InlineEditable sectionId={sectionId} settingKey="title" value={title} />
          </p>
        )}
        {subtitle && (
          <h2 className="lux-heading text-xl mb-4">
            <InlineEditable sectionId={sectionId} settingKey="subtitle" value={subtitle} />
          </h2>
        )}
        <div className="flex gap-2">
          <input
            type="email"
            placeholder={placeholder}
            dir="ltr"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleSubmit();
            }}
            className="flex-1 h-11 px-4 text-xs lux-input"
          />
          {buttonText && (
            <button onClick={handleSubmit} className="px-6 h-11 lux-btn">
              {submitted ? (
                successText
              ) : (
                <InlineEditable sectionId={sectionId} settingKey="button_text" value={buttonText} />
              )}
            </button>
          )}
        </div>
      </div>
    </section>
  );
}
