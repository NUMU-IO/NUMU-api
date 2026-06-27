"use client";
import { useState } from "react";
import { useLocale } from "@numueg/theme-sdk";
import { asString, localized, type SectionRenderProps } from "./_shared";

const HEADING_SHADOW = "0 1px 0 hsl(35 30% 100% / 0.6)";

const SkeuNewsletter = ({ instance }: SectionRenderProps) => {
  const s = instance.settings ?? {};
  const locale = useLocale();
  const title = asString(s.title) || localized(locale, "Join our newsletter 📬", "اشترك في نشرتنا 📬");
  const subtitle =
    asString(s.subtitle) || localized(locale, "Be the first to know about new drops and offers", "اعرف أول واحد عن العروض والمنتجات الجديدة");
  const buttonText = asString(s.button_text) || localized(locale, "Subscribe", "اشترك");
  const placeholder = asString(s.placeholder) || localized(locale, "Email address", "البريد الإلكتروني");

  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);

  const handleSubmit = () => {
    if (!email.trim()) return;
    // Newsletter capture is wired by the host on the real storefront; here we
    // just acknowledge the submission so the section behaves end-to-end.
    setSubmitted(true);
  };

  return (
    <section className="py-10">
      <div className="container mx-auto px-4">
        <div className="text-center max-w-md mx-auto">
          <h2
            className="text-xl font-bold mb-2"
            style={{ textShadow: HEADING_SHADOW }}
          >
            {title}
          </h2>
          <p className="text-sm text-muted-foreground mb-4">{subtitle}</p>
          {submitted ? (
            <div className="skeu-card rounded-xl p-5">
              <p className="text-sm font-bold text-foreground relative z-[1]">
                {localized(locale, "Thanks for subscribing! ✅", "شكراً لاشتراكك! ✅")}
              </p>
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
                className="flex-1 h-12 px-4 rounded-xl skeu-inset text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              />
              <button
                type="button"
                onClick={handleSubmit}
                className="px-6 h-12 rounded-xl skeu-btn text-sm"
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

export default SkeuNewsletter;
