"use client";

import { useState } from "react";
import { Check } from "lucide-react";
import { motion } from "framer-motion";
import { useLocale, useResolvedSettings } from "@numueg/theme-sdk";
import { asString, localized, type SectionRenderProps } from "./_shared";
import { InlineEditable } from "./_inline-editable";

const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

const BzNewsletter = ({ instance, sectionId }: SectionRenderProps) => {
  const s = useResolvedSettings(instance);
  const locale = useLocale();
  const title = asString(s.title) || localized(locale, "JOIN THE CIRCLE", "انضم لدائرتنا");
  const subtitle =
    asString(s.subtitle) ||
    localized(locale, "Early access to limited drops and exclusive content.", "وصول مبكر للإصدارات المحدودة ومحتوى حصري.");
  const buttonText = asString(s.button_text) || localized(locale, "SUBSCRIBE", "اشترك");
  const placeholder = asString(s.placeholder) || localized(locale, "YOUR EMAIL", "بريدك الإلكتروني");

  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = email.trim();
    if (!trimmed || !EMAIL_REGEX.test(trimmed)) return;
    setSubmitted(true);
  };

  return (
    <section className="py-12 md:py-16 lg:py-24 bg-[var(--bz-cream)]">
      <div className="container mx-auto px-4 text-center">
        <h2 className="bz-heading text-2xl sm:text-3xl md:text-4xl text-[var(--bz-dark)] mb-3 md:mb-4">
          <InlineEditable sectionId={sectionId} settingKey="title" value={title} />
        </h2>
        <p className="text-sm md:text-base text-[var(--bz-gray)] mb-6 md:mb-8 max-w-md mx-auto">
          <InlineEditable sectionId={sectionId} settingKey="subtitle" value={subtitle} multiline />
        </p>
        {submitted ? (
          <motion.div
            initial={{ opacity: 0, scale: 0.85 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ type: "spring", stiffness: 220, damping: 18 }}
            role="status"
            aria-live="polite"
            className="inline-flex items-center justify-center gap-2 px-6 py-3 rounded-full border-2 border-[var(--bz-dark)] bg-[var(--bz-amber)] text-[var(--bz-dark)] bz-label shadow-[3px_3px_0_var(--bz-dark)]"
          >
            <motion.span
              initial={{ scale: 0, rotate: -45 }}
              animate={{ scale: 1, rotate: 0 }}
              transition={{ delay: 0.1, type: "spring", stiffness: 300, damping: 14 }}
              className="inline-flex"
            >
              <Check size={16} aria-hidden="true" strokeWidth={3} />
            </motion.span>
            <span>{localized(locale, "SUBSCRIBED", "تم الاشتراك")}</span>
          </motion.div>
        ) : (
          <form
            className="flex flex-col sm:flex-row gap-2 sm:gap-3 max-w-md mx-auto"
            onSubmit={handleSubmit}
          >
            <label htmlFor="bz-newsletter-email" className="sr-only">
              {placeholder}
            </label>
            <input
              id="bz-newsletter-email"
              type="email"
              value={email}
              onChange={(ev) => setEmail(ev.target.value)}
              placeholder={placeholder}
              dir="ltr"
              autoComplete="email"
              className="flex-1 px-4 py-3 rounded-full border-2 border-[var(--bz-dark)] bg-white text-[var(--bz-dark)] placeholder:text-[var(--bz-gray)] focus:outline-none focus:ring-2 focus:ring-[var(--bz-amber)]"
            />
            <button type="submit" className="bz-btn bz-btn-filled px-6 py-3">
              {buttonText}
            </button>
          </form>
        )}
      </div>
    </section>
  );
};

export default BzNewsletter;
