"use client";
import { useState } from "react";
import { useShop, useLocale } from "@numueg/theme-sdk";
import { asString, localized, type SectionRenderProps } from "./_shared";

const RsNewsletter = ({ instance }: SectionRenderProps) => {
  const shop = useShop();
  const locale = useLocale();
  const s = instance.settings ?? {};
  const storeName = shop?.name || "RabbitSocks";
  const subtitle = asString(s.subtitle) || localized(
    locale,
    "Subscribe to our newsletter for the latest collections and exclusive offers.",
    "اشترك في نشرتنا عشان توصلك أحدث التشكيلات والعروض الحصرية.",
  );
  const placeholder = asString(s.placeholder) || localized(locale, "Email address", "البريد الإلكتروني");
  const successText = asString(s.success_text) || localized(
    locale,
    "Thank you for subscribing! We'll keep you posted.",
    "شكراً لاشتراكك! هنطمنك على كل جديد.",
  );

  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.trim()) return;
    setSubmitted(true);
  };

  return (
    <section className="bg-[hsl(var(--rs-surface-low))] w-full">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-12 px-6 md:px-12 py-20 border-t border-[hsl(var(--rs-outline-variant)/0.15)]">
        <div>
          <span className="font-[var(--heading-font,'Cormorant_Garamond')] italic text-3xl mb-8 block text-[hsl(var(--rs-primary))]">
            {storeName}
          </span>
          <p className="rs-body mb-12 max-w-xs text-[hsl(var(--rs-primary)/0.65)]">{subtitle}</p>

          {submitted ? (
            <p className="rs-body text-[hsl(var(--rs-primary))]">
              {successText}
            </p>
          ) : (
            <form onSubmit={handleSubmit} className="max-w-md">
              <div className="relative">
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder={placeholder}
                  className="w-full rs-input"
                  dir="ltr"
                  required
                />
                <button type="submit" className="absolute end-0 bottom-4" aria-label="Subscribe">
                  <span className="material-symbols-outlined text-[hsl(var(--rs-primary))]">
                    arrow_forward
                  </span>
                </button>
              </div>
            </form>
          )}
        </div>

        <div className="flex flex-col md:flex-row gap-12 md:gap-16">
          <div className="flex flex-col gap-4">
            <span className="rs-label mb-4 text-[hsl(var(--rs-primary))]">
              {localized(locale, "Navigation", "روابط")}
            </span>
            <a href="/shipping" className="rs-footer-link">{localized(locale, "Shipping & Delivery", "الشحن والتوصيل")}</a>
            <a href="/returns" className="rs-footer-link">{localized(locale, "Returns & Exchanges", "الاسترجاع والاستبدال")}</a>
            <a href="/contact" className="rs-footer-link">{localized(locale, "Contact us", "اتصل بينا")}</a>
          </div>
          <div className="flex flex-col gap-4">
            <span className="rs-label mb-4 text-[hsl(var(--rs-primary))]">
              {localized(locale, "Follow us", "تابعنا")}
            </span>
            <a href="#" className="rs-footer-link">Instagram</a>
            <a href="#" className="rs-footer-link">Pinterest</a>
          </div>
        </div>
      </div>
    </section>
  );
};

export default RsNewsletter;
