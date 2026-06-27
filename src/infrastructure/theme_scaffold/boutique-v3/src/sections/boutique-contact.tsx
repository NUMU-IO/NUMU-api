"use client";
import { useState } from "react";
import { Link, useShop, useLocale } from "@numueg/theme-sdk";
import { ChevronRight, MessageCircle, Phone, Mail, Instagram, Clock } from "lucide-react";
import { asString, localized, type SectionRenderProps } from "./_shared";

const BTN_FILLED =
  "inline-flex items-center justify-center gap-2 h-11 px-6 rounded-full font-semibold text-sm text-primary-foreground transition-all hover:scale-[1.02]";

/** Normalise a merchant-provided Instagram value into a { handle, url } pair. */
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

const BoutiqueContact = ({ instance }: SectionRenderProps) => {
  const s = instance.settings ?? {};
  const locale = useLocale();
  // Pull channels from the merchant-configured contact info. Never invent
  // placeholder numbers/emails/handles — hide a channel if not configured.
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
  const formatPhoneForDisplay = (digits: string) =>
    digits.startsWith("20") ? `0${digits.slice(2)}` : digits;
  const { handle: instagramHandle, url: instagramUrl } = normalizeInstagram(
    contact.instagram,
  );
  const email = contact.email || "";

  const eyebrow = asString(s.eyebrow) || localized(locale, "Get in Touch", "تواصلي معنا");
  const title = asString(s.title) || localized(locale, "Contact Us", "اتصلي بنا");
  const subtitle = asString(s.subtitle);
  const nameLabel = asString(s.name_label) || localized(locale, "Name", "الاسم");
  const phoneLabel = asString(s.phone_label) || localized(locale, "Phone number", "رقم الهاتف");
  const messageLabel = asString(s.message_label) || localized(locale, "Your message", "رسالتك");
  const submitText = asString(s.submit_text) || localized(locale, "Send", "إرسال");
  const successTitle = asString(s.success_title) || localized(locale, "Thank you for reaching out", "شكراً لتواصلك معنا");
  const successMessage =
    asString(s.success_message) ||
    localized(locale, "We'll get back to you as soon as possible.", "سنعاود التواصل معك في أقرب وقت ممكن.");
  const showWorkingHours = s.show_working_hours !== false;
  const weekdaysLabel = asString(s.hours_weekdays_label) || localized(locale, "Sat – Thu", "السبت – الخميس");
  const weekdaysValue = asString(s.hours_weekdays_value) || localized(locale, "9 AM – 9 PM", "9 ص – 9 م");
  const fridayLabel = asString(s.hours_friday_label) || localized(locale, "Friday", "الجمعة");
  const fridayValue = asString(s.hours_friday_value) || localized(locale, "2 PM – 9 PM", "2 م – 9 م");
  const showMap = s.show_map === true;
  const mapEmbedUrl = asString(s.map_embed_url);

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
      label: "WhatsApp",
      value: formatPhoneForDisplay(whatsappDigits),
      href: `https://wa.me/${whatsappDigits}`,
    });
  }
  if (phoneDigits) {
    methods.push({
      icon: Phone,
      label: localized(locale, "Call us", "اتصلي بنا"),
      value: formatPhoneForDisplay(phoneDigits),
      href: `tel:+${phoneDigits}`,
    });
  }
  if (email) {
    methods.push({ icon: Mail, label: localized(locale, "Email", "البريد الإلكتروني"), value: email, href: `mailto:${email}` });
  }
  if (instagramHandle) {
    methods.push({
      icon: Instagram,
      label: "Instagram",
      value: `@${instagramHandle}`,
      href: instagramUrl,
    });
  }

  return (
    <div className="bg-background min-h-[60vh]">
      <div className="container mx-auto px-4 py-10 md:py-14 max-w-5xl">
        <nav className="flex items-center gap-1.5 text-xs uppercase tracking-widest text-muted-foreground mb-6">
          <Link to="/" className="hover:text-foreground transition-colors">
            {localized(locale, "Home", "الرئيسية")}
          </Link>
          <ChevronRight size={12} className="rtl:rotate-180" />
          <span className="text-foreground">{title}</span>
        </nav>

        {eyebrow && <span className="block mb-2 text-xs uppercase tracking-widest text-primary">{eyebrow}</span>}
        <h1 className="text-3xl md:text-4xl font-bold text-foreground mb-3">{title}</h1>
        {subtitle && <p className="text-sm md:text-base text-muted-foreground mb-8 md:mb-10 max-w-xl">{subtitle}</p>}

        <div className="grid md:grid-cols-2 gap-8 md:gap-12 mt-8 md:mt-12">
          {/* Form */}
          <div>
            {submitted ? (
              <div className="bg-accent/30 rounded-md p-8 text-center">
                <h3 className="text-lg font-bold text-foreground mb-1.5">{successTitle}</h3>
                <p className="text-sm text-muted-foreground">{successMessage}</p>
              </div>
            ) : (
              <form onSubmit={handleSubmit} className="space-y-5">
                <div>
                  <label className="block mb-1.5 text-xs uppercase tracking-widest text-muted-foreground">{nameLabel}</label>
                  <input
                    required
                    value={form.name}
                    onChange={(e) => setForm({ ...form, name: e.target.value })}
                    className="w-full h-11 bg-transparent border-b border-border focus:border-primary focus:outline-none transition-colors text-base md:text-sm"
                  />
                </div>
                <div>
                  <label className="block mb-1.5 text-xs uppercase tracking-widest text-muted-foreground">{phoneLabel}</label>
                  <input
                    type="tel"
                    value={form.phone}
                    onChange={(e) => setForm({ ...form, phone: e.target.value })}
                    dir="ltr"
                    className="w-full h-11 bg-transparent border-b border-border focus:border-primary focus:outline-none transition-colors text-base md:text-sm"
                  />
                </div>
                <div>
                  <label className="block mb-1.5 text-xs uppercase tracking-widest text-muted-foreground">{messageLabel}</label>
                  <textarea
                    required
                    value={form.message}
                    onChange={(e) => setForm({ ...form, message: e.target.value })}
                    rows={4}
                    className="w-full bg-transparent border-b border-border focus:border-primary focus:outline-none transition-colors text-base md:text-sm resize-none py-2"
                  />
                </div>
                <button type="submit" className={BTN_FILLED + " mt-2"} style={{ background: "hsl(var(--primary))" }}>
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
                className="flex items-center gap-4 py-3.5 border-b border-border hover:border-primary transition-colors group"
              >
                <m.icon size={18} className="shrink-0 text-muted-foreground group-hover:text-primary transition-colors" />
                <div className="flex-1 min-w-0">
                  <p className="text-[10px] uppercase tracking-widest text-muted-foreground">{m.label}</p>
                  <p className="text-sm text-foreground truncate" dir="ltr">{m.value}</p>
                </div>
              </a>
            ))}

            {showWorkingHours && (
              <div className="pt-6">
                <div className="flex items-center gap-2 mb-3">
                  <Clock size={14} className="text-muted-foreground" />
                  <span className="text-[10px] uppercase tracking-widest text-muted-foreground">{localized(locale, "Working hours", "ساعات العمل")}</span>
                </div>
                <div className="grid grid-cols-1 xs:grid-cols-2 gap-3 xs:gap-4 text-sm text-muted-foreground">
                  <div>
                    <p className="font-medium text-foreground mb-0.5">{weekdaysLabel}</p>
                    <p>{weekdaysValue}</p>
                  </div>
                  <div>
                    <p className="font-medium text-foreground mb-0.5">{fridayLabel}</p>
                    <p>{fridayValue}</p>
                  </div>
                </div>
              </div>
            )}

            {showMap && mapEmbedUrl && (
              <div className="pt-6">
                <iframe
                  src={mapEmbedUrl}
                  title="Store location map"
                  className="w-full h-64 border border-border rounded-md"
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

export default BoutiqueContact;
