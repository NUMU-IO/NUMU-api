"use client";
import { useState } from "react";
import { Link, useShop, useLocale } from "@numueg/theme-sdk";
import { ChevronRight, MessageCircle, Phone, Mail, Instagram, Clock } from "lucide-react";
import { asString, localized, type SectionRenderProps } from "./_shared";

/**
 * Normalise a merchant-provided Instagram value into a { handle, url } pair.
 * Accepts a bare handle, "@handle", or a full profile URL and returns a
 * canonical handle (no "@") plus a working URL, never a broken double-prefixed
 * link.
 */
function normalizeInstagram(raw: string | null | undefined): { handle: string; url: string } {
  const trimmed = (raw || "").trim();
  if (!trimmed) return { handle: "", url: "" };
  if (/^https?:\/\//i.test(trimmed)) {
    try {
      const u = new URL(trimmed);
      const handle = u.pathname.replace(/^\/+|\/+$/g, "").split("/")[0] || "";
      return { handle, url: trimmed };
    } catch {
      return { handle: trimmed, url: trimmed };
    }
  }
  const handle = trimmed.replace(/^@/, "");
  return { handle, url: handle ? `https://instagram.com/${handle}` : "" };
}

const NBContact = ({ instance }: SectionRenderProps) => {
  const s = instance.settings ?? {};
  const locale = useLocale();
  // Pull channels from the merchant-configured contact info. Never invent
  // placeholder numbers, emails, or handles — if a merchant didn't configure a
  // channel, hide it. In V3 the configured channels reach the theme via the
  // store's `social_links` map.
  const shop = useShop();
  const socials = (shop?.social_links ?? {}) as Record<string, string>;
  const pick = (...keys: string[]): string => {
    for (const k of keys) {
      const v = socials[k];
      if (typeof v === "string" && v.trim()) return v.trim();
    }
    return "";
  };
  const contact = {
    whatsapp: pick("whatsapp", "contact_whatsapp"),
    phone: pick("phone", "contact_phone"),
    email: pick("email", "contact_email"),
    instagram: pick("instagram", "contact_instagram"),
  };

  const whatsappDigits = (contact.whatsapp || "").replace(/\D/g, "");
  const phoneDigits = (contact.phone || contact.whatsapp || "").replace(/\D/g, "");
  // Display Egyptian numbers as 0XXXXXXXXXX for readability, leave others as-is.
  const formatPhoneForDisplay = (digits: string) =>
    digits.startsWith("20") ? `0${digits.slice(2)}` : digits;
  const { handle: instagramHandle, url: instagramUrl } = normalizeInstagram(
    contact.instagram,
  );
  const email = contact.email || "";

  const eyebrow = asString(s.eyebrow) || localized(locale, "GET IN TOUCH", "تواصل معنا");
  const title = asString(s.title) || localized(locale, "Contact us", "اتصل بنا");
  const subtitle = asString(s.subtitle, "");
  const nameLabel = asString(s.name_label) || localized(locale, "Name", "الاسم");
  const phoneLabel = asString(s.phone_label) || localized(locale, "Phone number", "رقم الهاتف");
  const messageLabel = asString(s.message_label) || localized(locale, "Your message", "رسالتك");
  const submitText = asString(s.submit_text) || localized(locale, "Send", "إرسال");
  const successTitle = asString(s.success_title) || localized(locale, "Thanks for reaching out", "شكراً لتواصلك معنا");
  const successMessage = asString(s.success_message) || localized(locale, "We'll get back to you as soon as possible.", "سنرد عليك في أقرب وقت ممكن.");
  const showWorkingHours = s.show_working_hours !== false;
  const weekdaysLabel = asString(s.hours_weekdays_label) || localized(locale, "Saturday – Thursday", "السبت – الخميس");
  const weekdaysValue = asString(s.hours_weekdays_value) || localized(locale, "9 AM – 9 PM", "9 ص – 9 م");
  const fridayLabel = asString(s.hours_friday_label) || localized(locale, "Friday", "الجمعة");
  const fridayValue = asString(s.hours_friday_value) || localized(locale, "2 PM – 9 PM", "2 م – 9 م");
  const showMap = s.show_map === true;
  const mapEmbedUrl = asString(s.map_embed_url, "");

  const [form, setForm] = useState({ name: "", phone: "", message: "" });
  const [submitted, setSubmitted] = useState(false);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitted(true);
  };

  const methods: { icon: any; label: string; value: string; href: string }[] = [];
  if (whatsappDigits) {
    methods.push({
      icon: MessageCircle,
      label: localized(locale, "WhatsApp", "واتساب"),
      value: formatPhoneForDisplay(whatsappDigits),
      href: `https://wa.me/${whatsappDigits}`,
    });
  }
  if (phoneDigits) {
    methods.push({
      icon: Phone,
      label: localized(locale, "Call us", "اتصل بنا"),
      value: formatPhoneForDisplay(phoneDigits),
      href: `tel:+${phoneDigits}`,
    });
  }
  if (email) {
    methods.push({ icon: Mail, label: localized(locale, "Email", "البريد"), value: email, href: `mailto:${email}` });
  }
  if (instagramHandle) {
    methods.push({
      icon: Instagram,
      label: localized(locale, "Instagram", "إنستجرام"),
      value: `@${instagramHandle}`,
      href: instagramUrl,
    });
  }

  return (
    <div className="bg-background min-h-[60vh]">
      <div className="container mx-auto px-4 py-10 md:py-14 max-w-5xl">
        <nav className="flex items-center gap-1.5 text-xs vn-label text-[var(--vn-muted)] mb-6">
          <Link to="/" className="hover:text-[var(--vn-ink)] transition-colors">
            {localized(locale, "Home", "الرئيسية")}
          </Link>
          <ChevronRight size={12} className="rtl:rotate-180" />
          <span className="text-[var(--vn-ink)]">{title}</span>
        </nav>

        {eyebrow && <span className="nb-badge inline-block mb-3 px-3 py-1 rounded text-xs">{eyebrow}</span>}
        <h1 className="vn-heading text-3xl md:text-4xl mb-3">{title}</h1>
        {subtitle && <p className="text-sm md:text-base text-[var(--vn-muted)] mb-8 md:mb-10 max-w-xl font-medium">{subtitle}</p>}

        <div className="grid md:grid-cols-2 gap-8 md:gap-12 mt-8 md:mt-12">
          {/* Form */}
          <div>
            {submitted ? (
              <div className="nb-card rounded-lg p-8 text-center">
                <h3 className="vn-heading text-lg mb-1.5">{successTitle}</h3>
                <p className="text-sm text-[var(--vn-muted)] font-medium">{successMessage}</p>
              </div>
            ) : (
              <form onSubmit={handleSubmit} className="space-y-5">
                <div>
                  <label className="vn-label block mb-1.5">{nameLabel}</label>
                  <input
                    required
                    value={form.name}
                    onChange={(e) => setForm({ ...form, name: e.target.value })}
                    className="w-full h-11 px-3 nb-input rounded-lg focus:outline-none text-base md:text-sm"
                  />
                </div>
                <div>
                  <label className="vn-label block mb-1.5">{phoneLabel}</label>
                  <input
                    type="tel"
                    value={form.phone}
                    onChange={(e) => setForm({ ...form, phone: e.target.value })}
                    dir="ltr"
                    className="w-full h-11 px-3 nb-input rounded-lg focus:outline-none text-base md:text-sm"
                  />
                </div>
                <div>
                  <label className="vn-label block mb-1.5">{messageLabel}</label>
                  <textarea
                    required
                    value={form.message}
                    onChange={(e) => setForm({ ...form, message: e.target.value })}
                    rows={4}
                    className="w-full nb-input rounded-lg focus:outline-none text-base md:text-sm resize-none p-3"
                  />
                </div>
                <button type="submit" className="vn-btn vn-btn-filled mt-2">
                  {submitText}
                </button>
              </form>
            )}
          </div>

          {/* Contact methods */}
          <div className="space-y-2">
            {methods.map((m, i) => (
              <a
                key={i}
                href={m.href}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-4 py-3.5 px-3 nb-chip rounded-lg transition-all group"
              >
                <m.icon size={18} className="shrink-0 text-[var(--vn-muted)] group-hover:text-[var(--vn-ink)] transition-colors" />
                <div className="flex-1 min-w-0">
                  <p className="vn-label text-[10px]">{m.label}</p>
                  <p className="text-sm text-[var(--vn-ink)] truncate font-bold" dir="ltr">{m.value}</p>
                </div>
              </a>
            ))}

            {showWorkingHours && (
              <div className="pt-6">
                <div className="flex items-center gap-2 mb-3">
                  <Clock size={14} className="text-[var(--vn-muted)]" />
                  <span className="vn-label text-[10px]">{localized(locale, "Business hours", "ساعات العمل")}</span>
                </div>
                <div className="grid grid-cols-1 xs:grid-cols-2 gap-3 xs:gap-4 text-sm text-[var(--vn-muted)]">
                  <div>
                    <p className="font-black text-[var(--vn-ink)] mb-0.5">{weekdaysLabel}</p>
                    <p className="font-medium">{weekdaysValue}</p>
                  </div>
                  <div>
                    <p className="font-black text-[var(--vn-ink)] mb-0.5">{fridayLabel}</p>
                    <p className="font-medium">{fridayValue}</p>
                  </div>
                </div>
              </div>
            )}

            {showMap && mapEmbedUrl && (
              <div className="pt-6">
                <iframe
                  src={mapEmbedUrl}
                  title={localized(locale, "Store location map", "خريطة موقع المتجر")}
                  className="w-full h-64 nb-img-frame rounded-lg"
                  loading="lazy"
                  referrerPolicy="no-referrer-when-downgrade"
                />
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default NBContact;
