"use client";
import { useState } from "react";
import { useLocale } from "@numueg/theme-sdk";
import { asString, localized, type SectionRenderProps } from "./_shared";

/**
 * Editorial newsletter — faithful port of V2
 * themes/editorial/sections/newsletter/EdNewsletter.tsx.
 *
 * Green full-bleed band, centered white copy, inline email input + button.
 * V2 used the shared `useNewsletterSubmit` hook; here we keep equivalent local
 * email state + submit so the section is self-contained in the bundle. All
 * className strings are verbatim.
 */
export default function EdNewsletter({ instance }: SectionRenderProps) {
  const s = instance.settings ?? {};
  const locale = useLocale();
  const title = asString(s.title) || localized(locale, "Subscribe to our newsletter", "اشترك في نشرتنا");
  const subtitle =
    asString(s.subtitle) ||
    localized(locale, "Be the first to hear about new products and exclusive offers", "اعرف أول واحد عن العروض والمنتجات الجديدة");
  const buttonText = asString(s.button_text) || localized(locale, "Subscribe", "اشترك");
  const placeholder = asString(s.placeholder) || localized(locale, "Your email address", "البريد الإلكتروني");

  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);

  const handleSubmit = () => {
    if (!email) return;
    setSubmitted(true);
    setEmail("");
  };

  return (
    <section className="py-12 bg-[hsl(var(--ed-green))]">
      <div className="container mx-auto px-4 text-center">
        <h2 className="text-white font-black text-2xl md:text-3xl mb-3">
          {title}
        </h2>
        <p className="text-white/60 text-sm mb-6 max-w-sm mx-auto">
          {subtitle}
        </p>
        <div className="flex gap-2 max-w-md mx-auto">
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleSubmit();
            }}
            placeholder={placeholder}
            dir="ltr"
            className="flex-1 h-12 px-4 bg-white/10 text-white placeholder:text-white/40 text-sm border border-white/20 focus:outline-none focus:border-white/50"
          />
          <button
            type="button"
            onClick={handleSubmit}
            className="px-6 h-12 bg-white text-[hsl(var(--ed-dark))] font-bold text-xs uppercase tracking-[0.15em] hover:bg-white/90 transition-colors"
          >
            {submitted ? localized(locale, "Subscribed", "تم الاشتراك") : buttonText}
          </button>
        </div>
      </div>
    </section>
  );
}
