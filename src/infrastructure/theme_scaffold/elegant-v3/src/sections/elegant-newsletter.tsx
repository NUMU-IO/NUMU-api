"use client";
import { useState } from "react";
import { Check } from "lucide-react";
import { useLocale } from "@numueg/theme-sdk";
import { asString, localized, type SectionRenderProps } from "./_shared";

const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

const ElegantNewsletter = ({ instance }: SectionRenderProps) => {
  const s = instance.settings ?? {};
  const locale = useLocale();

  const title = asString(s.title) || localized(locale, "Join Our World", "انضم لعالمنا");
  const subtitle =
    asString(s.subtitle) ||
    localized(
      locale,
      "Subscribe for the latest offers and exclusive collections.",
      "اشترك ليصلك كل جديد من العروض والتشكيلات الحصرية",
    );
  const buttonText = asString(s.button_text) || localized(locale, "Subscribe", "اشترك");
  const placeholder = asString(s.placeholder) || localized(locale, "Email address", "البريد الإلكتروني");

  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);

  const handleSubmit = () => {
    const trimmed = email.trim();
    if (!trimmed || !EMAIL_REGEX.test(trimmed)) return;
    setSubmitted(true);
  };

  return (
    <section className="py-12 md:py-16">
      <div className="container mx-auto px-4">
        <div className="max-w-lg mx-auto bg-[hsl(var(--hero-bg))] border border-border/60 p-8 md:p-12 text-center">
          {/* Heading */}
          <h2
            className="text-xl md:text-2xl font-semibold text-foreground mb-2"
            style={{ fontFamily: "var(--font-heading, serif)" }}
          >
            {title}
          </h2>

          {/* Decorative line */}
          <div className="w-10 h-px bg-primary/40 mx-auto mb-4" />

          {/* Subtitle */}
          <p className="text-sm text-muted-foreground mb-8 leading-relaxed">
            {subtitle}
          </p>

          {submitted ? (
            <div className="flex items-center justify-center gap-2 h-12 text-sm font-medium text-primary">
              <Check className="h-5 w-5" />
              <span>{localized(locale, "Subscribed successfully", "تم الاشتراك بنجاح")}</span>
            </div>
          ) : (
            <div className="flex gap-2">
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleSubmit();
                }}
                placeholder={placeholder}
                dir="ltr"
                className="flex-1 h-12 px-4 bg-background border border-border text-sm focus:outline-none focus:ring-1 focus:ring-primary/40 transition-shadow"
              />
              <button
                onClick={handleSubmit}
                className="px-6 h-12 border border-primary bg-primary text-primary-foreground text-sm font-semibold tracking-wide hover:bg-primary/90 transition-colors"
              >
                {buttonText}
              </button>
            </div>
          )}
        </div>
      </div>
    </section>
  );
};

export default ElegantNewsletter;
