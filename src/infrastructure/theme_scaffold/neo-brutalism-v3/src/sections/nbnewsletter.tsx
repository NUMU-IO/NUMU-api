"use client";
import { useState } from "react";
import { useLocale } from "@numueg/theme-sdk";
import { asString, localized, type SectionRenderProps } from "./_shared";

const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

const NBNewsletter = ({ instance }: SectionRenderProps) => {
  const s = instance.settings ?? {};
  const locale = useLocale();
  const title = asString(s.title) || localized(locale, "Subscribe to our newsletter 📬", "اشترك في نشرتنا 📬");
  const subtitle = asString(s.subtitle) || localized(locale, "Be the first to know about offers and new products", "اعرف أول واحد عن العروض والمنتجات الجديدة");
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
    <section className="py-10 nb-section-alt">
      <div className="container mx-auto px-4">
        <div className="text-center max-w-md mx-auto">
          <h2 className="text-2xl font-black mb-2">{title}</h2>
          <p className="text-sm text-muted-foreground mb-5 font-medium">
            {subtitle}
          </p>
          {submitted ? (
            <div className="nb-badge inline-block px-4 py-2 rounded text-sm">
              {localized(locale, "✓ Subscribed successfully", "✓ تم الاشتراك بنجاح")}
            </div>
          ) : (
            <div className="flex gap-0">
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleSubmit();
                }}
                placeholder={placeholder}
                dir="ltr"
                className="flex-1 h-12 px-4 rounded-r-lg text-sm nb-input"
              />
              <button
                type="button"
                onClick={() => handleSubmit()}
                className="px-6 h-12 rounded-l-lg nb-btn text-sm -mr-[3px]"
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

export default NBNewsletter;
