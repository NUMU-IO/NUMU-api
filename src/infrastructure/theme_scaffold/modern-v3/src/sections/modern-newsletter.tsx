"use client";
import { useState } from "react";
import { useLocale } from "@numueg/theme-sdk";
import { Check, Mail } from "lucide-react";
import { localized, type SectionRenderProps } from "./_shared";

/**
 * Modern newsletter — faithful port of the V2 in-tree ModernNewsletter
 * (numu-egyptian-bazaar/src/themes/modern/sections/newsletter/…).
 *
 * V2 used the shared `useNewsletterSubmit` hook (which fires a Meta pixel Lead
 * event — not available to a federated V3 theme). Here the submit logic is
 * inlined as local email/submitted state with the same email regex, so the
 * inline "subscribed" success state behaves identically. Markup classNames are
 * kept verbatim from V2.
 */

const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

const ModernNewsletter = ({ instance }: SectionRenderProps) => {
  const s = instance.settings ?? {};
  const locale = useLocale();
  const title = s.title ?? localized(locale, "Subscribe to our newsletter", "اشترك في نشرتنا البريدية");
  const subtitle =
    s.subtitle ?? localized(locale, "Be the first to know about offers and new products", "اعرف أول واحد عن العروض والمنتجات الجديدة");
  const buttonText = s.button_text ?? localized(locale, "Subscribe", "اشترك");
  const placeholder = s.placeholder ?? localized(locale, "Email address", "البريد الإلكتروني");

  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);

  const handleSubmit = () => {
    const trimmed = email.trim();
    if (!trimmed || !EMAIL_REGEX.test(trimmed)) return;
    setSubmitted(true);
    setEmail("");
  };

  return (
    <section className="py-12 md:py-16">
      <div className="container mx-auto px-4">
        <div
          className="relative max-w-2xl mx-auto rounded-3xl p-8 md:p-12 text-center overflow-hidden"
          style={{
            background:
              "linear-gradient(135deg, hsl(var(--primary) / 0.06) 0%, hsl(var(--primary) / 0.12) 50%, hsl(var(--primary) / 0.04) 100%)",
          }}
        >
          {/* Decorative circle */}
          <div className="absolute -top-16 -right-16 w-48 h-48 rounded-full bg-primary/5 blur-2xl pointer-events-none" />
          <div className="absolute -bottom-12 -left-12 w-36 h-36 rounded-full bg-primary/8 blur-2xl pointer-events-none" />

          <div className="relative">
            <div className="inline-flex items-center justify-center w-12 h-12 rounded-2xl bg-primary/15 mb-5">
              <Mail className="w-6 h-6 text-primary" />
            </div>

            <h2 className="text-2xl md:text-3xl font-bold text-foreground mb-3">
              {title}
            </h2>
            <p className="text-sm md:text-base text-muted-foreground mb-8 max-w-md mx-auto leading-relaxed">
              {subtitle}
            </p>

            {submitted ? (
              <div className="flex items-center justify-center gap-2.5 h-14 text-base font-bold text-green-600">
                <div className="flex items-center justify-center w-8 h-8 rounded-full bg-green-100">
                  <Check className="h-5 w-5" />
                </div>
                <span>{localized(locale, "Subscribed successfully", "تم الاشتراك بنجاح")}</span>
              </div>
            ) : (
              <div className="flex gap-3 max-w-md mx-auto">
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") handleSubmit();
                  }}
                  placeholder={placeholder}
                  dir="ltr"
                  className="flex-1 h-14 px-5 rounded-2xl border border-border bg-background text-sm focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all shadow-sm"
                />
                <button
                  onClick={handleSubmit}
                  className="px-7 h-14 rounded-2xl store-gradient text-white font-bold text-sm hover:opacity-90 transition-all duration-300 shadow-md hover:shadow-lg hover:-translate-y-0.5"
                >
                  {buttonText}
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    </section>
  );
};

export default ModernNewsletter;
