"use client";
import { useState } from "react";
import { useLocale } from "@numueg/theme-sdk";
import { Check } from "lucide-react";
import { asString, localized, type SectionRenderProps } from "./_shared";

const BoutiqueNewsletter = ({ instance }: SectionRenderProps) => {
  const s = instance.settings ?? {};
  const locale = useLocale();

  const title = asString(s.title) || localized(locale, "Join Our Newsletter", "اشتركي في نشرتنا");
  const subtitle =
    asString(s.subtitle) ||
    localized(
      locale,
      "Be the first to know about new arrivals and exclusive offers.",
      "كوني أول من يعرف عن أحدث التشكيلات والعروض الحصرية",
    );
  const buttonText = asString(s.button_text) || localized(locale, "Subscribe", "اشتركي");
  const placeholder = asString(s.placeholder) || localized(locale, "Email address", "البريد الإلكتروني");

  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);

  const handleSubmit = () => {
    if (!email || !email.includes("@")) return;
    setSubmitted(true);
    setEmail("");
  };

  return (
    <section className="py-14">
      <div className="container mx-auto px-4">
        <div
          className="max-w-xl mx-auto rounded-3xl p-8 md:p-12 text-center border border-border/50"
          style={{
            background:
              "linear-gradient(135deg, hsl(var(--accent)), hsl(var(--hero-bg)))",
          }}
        >
          <h2 className="text-2xl md:text-3xl font-bold text-foreground mb-3">
            {title}
          </h2>

          {/* Decorative divider */}
          <div className="flex items-center justify-center gap-3 mb-4">
            <span className="block w-8 h-px bg-primary/30" />
            <span className="block w-1.5 h-1.5 rounded-full bg-primary/50" />
            <span className="block w-8 h-px bg-primary/30" />
          </div>

          <p className="text-sm text-muted-foreground leading-relaxed mb-6 max-w-sm mx-auto">
            {subtitle}
          </p>

          {submitted ? (
            <div className="flex items-center justify-center gap-2 h-12 text-sm font-semibold text-green-600">
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
                className="flex-1 h-12 px-5 rounded-full border border-border bg-card text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary/50 transition-all"
              />
              <button
                onClick={handleSubmit}
                className="px-7 h-12 rounded-full font-semibold text-sm text-primary-foreground transition-all duration-300 hover:shadow-[0_0_16px_hsl(var(--primary)/0.35)] hover:scale-[1.03]"
                style={{ background: "hsl(var(--primary))" }}
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

export default BoutiqueNewsletter;
