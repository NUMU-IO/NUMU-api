"use client";
import { useState, type FormEvent } from "react";
import { useLocale } from "@numueg/theme-sdk";
import { asString, localized, type SectionRenderProps } from "./_shared";

/**
 * Kick Game newsletter signup.
 *
 * Faithful port of the V2 KGNewsletter (dark band, centered, inline email +
 * subscribe). V2 used the bazaar `useNewsletterSubmit` hook with
 * `showToast: false`; there's no newsletter hook on the V3 SDK, so we keep the
 * same controlled-input behaviour locally and acknowledge submit inline.
 */
const KGNewsletter = ({ instance }: SectionRenderProps) => {
  const s = instance.settings ?? {};
  const locale = useLocale();
  const headline = asString(s.title) || asString(s.headline) || localized(locale, "STAY IN THE LOOP", "خليك على اطّلاع");
  const subtitle =
    asString(s.subtitle) || localized(locale, "New drops, restocks & exclusive offers.", "وصل جديد، رجوع المنتجات، وعروض حصرية.");
  const buttonText = asString(s.button_text) || localized(locale, "SUBSCRIBE", "اشترك");
  const placeholder = asString(s.placeholder) || "your@email.com";

  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!email) return;
    setSubmitted(true);
  };

  return (
    <section
      className="kg-newsletter"
      style={{
        background: "#121212",
        padding: "48px 0",
      }}
    >
      <div
        style={{
          maxWidth: "560px",
          margin: "0 auto",
          padding: "0 16px",
          textAlign: "center",
        }}
      >
        <h2
          className="kg-heading"
          style={{
            fontSize: "1.5rem",
            fontWeight: 700,
            textTransform: "uppercase",
            letterSpacing: "-0.02em",
            color: "#fcfbf7",
            margin: "0 0 8px",
          }}
        >
          {headline}
        </h2>
        <p
          style={{
            fontSize: "0.8125rem",
            color: "rgba(252,251,247,0.6)",
            margin: "0 0 24px",
          }}
        >
          {subtitle}
        </p>

        {submitted ? (
          <p
            style={{
              fontSize: "0.8125rem",
              fontWeight: 700,
              textTransform: "uppercase",
              letterSpacing: "0.04em",
              color: "#d9cd9a",
            }}
          >
            {localized(locale, "Thanks for subscribing!", "شكراً لاشتراكك!")}
          </p>
        ) : (
          <form
            onSubmit={handleSubmit}
            style={{
              display: "flex",
              gap: "8px",
              maxWidth: "400px",
              margin: "0 auto",
            }}
          >
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder={placeholder}
              required
              style={{
                flex: 1,
                height: "40px",
                padding: "0 12px",
                background: "rgba(252,251,247,0.08)",
                border: "1px solid rgba(252,251,247,0.15)",
                borderRadius: "4px",
                color: "#fcfbf7",
                fontSize: "0.8125rem",
                outline: "none",
              }}
            />
            <button
              type="submit"
              className="kg-btn"
              style={{
                height: "40px",
                padding: "0 24px",
                background: "#fcfbf7",
                color: "#121212",
                fontSize: "0.6875rem",
                fontWeight: 700,
                textTransform: "uppercase",
                letterSpacing: "0.04em",
                borderRadius: "4px",
                border: "none",
                cursor: "pointer",
                whiteSpace: "nowrap",
              }}
            >
              {buttonText}
            </button>
          </form>
        )}
      </div>
    </section>
  );
};

export default KGNewsletter;
