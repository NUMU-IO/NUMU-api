import {
  useState } from "react";
import { EditableText } from "../lib/EditableText";
import type { EmpSectionProps } from "../lib/section";
import { useT } from "../lib/i18n";

interface NewsletterSettings {
  title?: string;
  subtitle?: string;
  button_text?: string;
  placeholder?: string;
}

/** Black newsletter block — centered uppercase headline, muted subtitle and a
 *  pill email field + light submit button. Submission is handled client-side
 *  (swap in the host's marketing endpoint when wiring a real list). */
export default function Newsletter({ id, settings }: EmpSectionProps) {
  const s = settings as NewsletterSettings;
  const t = useT();
  const title = s.title ?? t("Join our newsletter", "اشترك في نشرتنا");
  const subtitle = s.subtitle ?? t("Be first to hear about offers and new arrivals", "اعرف أول واحد عن العروض والمنتجات الجديدة");
  const buttonText = s.button_text ?? t("Subscribe", "اشترك");
  const placeholder = s.placeholder ?? t("Email address", "البريد الإلكتروني");

  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (email) setSubmitted(true);
  };

  return (
    <section className="nt-news">
      <div className="nt-container">
        <div className="nt-news__inner">
          <EditableText
            as="h2"
            className="nt-news__title"
            sectionId={id}
            settingId="title"
            value={title}
          />
          <EditableText
            as="p"
            className="nt-news__sub"
            sectionId={id}
            settingId="subtitle"
            value={subtitle}
          />

          {submitted ? (
            <p style={{ fontWeight: 600 }}>شكراً لاشتراكك! 🎉</p>
          ) : (
            <form className="nt-news__form" onSubmit={onSubmit}>
              <input
                className="nt-news__input"
                type="email"
                dir="ltr"
                required
                value={email}
                placeholder={placeholder}
                onChange={(e) => setEmail(e.target.value)}
              />
              <button className="nt-btn-light" type="submit">
                <EditableText
                  as="span"
                  sectionId={id}
                  settingId="button_text"
                  value={buttonText}
                />
              </button>
            </form>
          )}
        </div>
      </div>
    </section>
  );
}
