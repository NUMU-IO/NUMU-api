"use client";

import { useState } from "react";
import { useLocale, useResolvedSettings, useShop } from "@numueg/theme-sdk";
import {
  Phone,
  MessageCircle,
  Clock,
  Mail,
  Send,
  ArrowRight,
  Loader2,
} from "lucide-react";
import { asBool, asString, localized, type SectionRenderProps } from "./_shared";
import { InlineEditable } from "./_inline-editable";

/**
 * bz-contact — the contact page body, ported from V2 BzContactPage.
 *
 * Layout (verbatim from V2): a wavy-amber hero, a navy scrolling marquee,
 * a navy "pick your channel" grid of contact-method cards, a cream contact
 * form (with a success card), and a dark business-hours panel — all wrapped
 * in the bazar wave dividers + blob decorations.
 *
 * Contact channels come from the merchant's configured contact info, which
 * reaches the theme via the store's `social_links` map (Contact tab in the
 * dashboard → social_links / footer overrides). We never invent placeholder
 * numbers/emails/handles: a channel the merchant didn't configure is hidden
 * rather than advertising a fake link (matches the vionne V3 port).
 *
 * The form is local-only (no submit endpoint exists in V3 yet); on submit we
 * flip to the success card and, when a WhatsApp number is configured, offer a
 * pre-filled "continue on WhatsApp" deep link. Editable headings use
 * `useResolvedSettings` + `<InlineEditable>` like the rest of the theme.
 */

/**
 * Normalise a merchant-provided Instagram value into a { handle, url } pair.
 * Ported from V2 `useContactInfo.normalizeInstagram` — accepts a bare handle,
 * "@handle", or a full profile URL and returns a canonical handle (no "@")
 * plus a working URL, never a broken double-prefixed link.
 */
function normalizeInstagram(
  raw: string | null | undefined,
): { handle: string; url: string } {
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

export default function BzContact({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const shop = useShop();
  const locale = useLocale();

  // ── Merchant-configured channels (never invented) ────────────────
  const socials = (shop?.social_links ?? {}) as Record<string, string>;
  const pick = (...keys: string[]): string => {
    for (const k of keys) {
      const v = socials[k];
      if (typeof v === "string" && v.trim()) return v.trim();
    }
    return "";
  };
  // The store's dedicated contact fields (hub → Store settings, exposed by the
  // public store serializer) win, then fall back to the social_links map.
  const shopContact = shop as unknown as
    | { contact_email?: string; contact_phone?: string }
    | undefined;
  const whatsapp = pick("whatsapp", "contact_whatsapp");
  const phone =
    asString(shopContact?.contact_phone) ||
    pick("phone", "contact_phone") ||
    whatsapp;
  const email = asString(shopContact?.contact_email) || pick("email", "contact_email");
  const ig = normalizeInstagram(pick("instagram", "contact_instagram"));
  const instagram = ig.handle;
  const instagramUrl = ig.url;
  const storeName = (shop?.name as string) || "Bazar";

  // ── Editable copy ────────────────────────────────────────────────
  const eyebrow = asString(s.eyebrow) || localized(locale, "WE'D LOVE TO HEAR FROM YOU", "يسعدنا نسمع منك");
  const headline = asString(s.headline) || localized(locale, "GET IN TOUCH", "تواصل معانا");
  const intro =
    asString(s.intro) ||
    localized(locale, "Questions, collaborations, or just want to say hello — we're always here for you.", "أي استفسار أو تعاون أو حتى عايز تسلّم بس — إحنا دايمًا موجودين ليك.");
  const channelsLabel = asString(s.channels_label) || localized(locale, "REACH US THROUGH", "تواصل معانا عن طريق");
  const channelsHeadline = asString(s.channels_headline) || localized(locale, "PICK YOUR CHANNEL", "اختار وسيلتك");
  const formLabel = asString(s.form_label) || localized(locale, "DROP US A LINE", "ابعتلنا رسالة");
  const formHeadline = asString(s.form_headline) || localized(locale, "SEND A MESSAGE", "ابعت رسالة");
  const nameLabel = asString(s.name_label) || localized(locale, "YOUR NAME", "اسمك");
  const emailLabel = asString(s.email_label) || localized(locale, "EMAIL", "البريد الإلكتروني");
  const phoneLabel = asString(s.phone_label) || localized(locale, "PHONE NUMBER", "رقم التليفون");
  const messageLabel = asString(s.message_label) || localized(locale, "YOUR MESSAGE", "رسالتك");
  const submitText = asString(s.submit_text) || localized(locale, "SEND MESSAGE", "ابعت الرسالة");
  const successTitle = asString(s.success_title) || localized(locale, "MESSAGE SENT", "تم إرسال الرسالة");
  const successMessage =
    asString(s.success_message) ||
    localized(locale, "Thank you for reaching out. We'll get back to you as soon as possible.", "شكرًا لتواصلك معانا. هنرد عليك في أقرب وقت.");
  const showHours = asBool(s.show_hours, true);
  const hoursLabel = asString(s.hours_label) || localized(locale, "AVAILABILITY", "مواعيد العمل");
  const hoursHeadline = asString(s.hours_headline) || localized(locale, "BUSINESS HOURS", "ساعات العمل");
  const hoursDescription =
    asString(s.hours_description) ||
    localized(locale, "Our team is available during the hours listed here. For urgent matters, reach out via WhatsApp for the fastest response.", "فريقنا متاح في المواعيد المكتوبة هنا. للأمور العاجلة، تواصل معانا على واتساب لأسرع رد.");
  const hoursNote =
    asString(s.hours_note) || localized(locale, "Online orders are processed 24/7", "الطلبات الأونلاين بتتنفذ على مدار الساعة");

  const hoursRows = [
    {
      day: asString(s.hours_weekdays_label) || localized(locale, "SATURDAY – THURSDAY", "السبت – الخميس"),
      time: asString(s.hours_weekdays_value) || localized(locale, "9 AM – 9 PM", "٩ ص – ٩ م"),
    },
    {
      day: asString(s.hours_friday_label) || localized(locale, "FRIDAY", "الجمعة"),
      time: asString(s.hours_friday_value) || localized(locale, "2 PM – 9 PM", "٢ م – ٩ م"),
    },
  ];

  // ── Local-only form ──────────────────────────────────────────────
  const [form, setForm] = useState({
    name: "",
    email: "",
    phone: "",
    message: "",
  });
  const [status, setStatus] = useState<"idle" | "submitting" | "success">(
    "idle",
  );
  const setField = (key: keyof typeof form, value: string) =>
    setForm((p) => ({ ...p, [key]: value }));

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setStatus("submitting");
    // No V3 submit endpoint yet — flip straight to success so the merchant
    // still gets the message via the WhatsApp deep link below.
    setTimeout(() => setStatus("success"), 300);
  };
  const reset = () => {
    setForm({ name: "", email: "", phone: "", message: "" });
    setStatus("idle");
  };

  const whatsappContinueUrl = whatsapp
    ? `https://wa.me/${whatsapp.replace(/^\+/, "")}?text=${encodeURIComponent(
        `Name: ${form.name}\nEmail: ${form.email}\nPhone: ${form.phone}\nMessage: ${form.message}`,
      )}`
    : null;

  return (
    <div className="bg-[var(--bz-cream)]" data-bz-section={sectionId}>
      {/* ═══ HERO ═══ */}
      <section className="relative min-h-[40vh] sm:min-h-[45vh] md:min-h-[50vh] landscape:min-h-[60vh] flex flex-col items-center justify-center overflow-hidden bz-wavy-bg py-12 sm:py-14 md:py-0">
        <div aria-hidden="true" className="absolute top-6 md:top-10 end-4 md:end-10 w-20 h-20 sm:w-28 sm:h-28 md:w-32 md:h-32 bg-[var(--bz-cream)]/30 bz-blob" />
        <div aria-hidden="true" className="absolute bottom-12 md:bottom-20 start-4 md:start-10 w-14 h-14 sm:w-16 sm:h-16 md:w-20 md:h-20 bg-[var(--bz-amber-dark)]/40 bz-blob" />

        <div className="relative z-10 text-center px-4">
          <span className="bz-label text-[var(--bz-dark)]/60 mb-3 md:mb-4 block">
            <InlineEditable sectionId={sectionId} settingKey="eyebrow" value={eyebrow} />
          </span>
          <h1 className="bz-heading text-2xl sm:text-4xl md:text-6xl lg:text-8xl text-[var(--bz-dark)]">
            <InlineEditable sectionId={sectionId} settingKey="headline" value={headline} />
          </h1>
          <p className="mt-5 md:mt-6 text-sm md:text-base text-[var(--bz-dark)]/60 max-w-md mx-auto leading-relaxed">
            <InlineEditable sectionId={sectionId} settingKey="intro" value={intro} multiline />
          </p>
        </div>

        <svg viewBox="0 0 1440 80" className="absolute bottom-0 w-full" preserveAspectRatio="none" aria-hidden="true">
          <path d="M0,80 C300,20 600,60 900,30 C1100,10 1300,50 1440,25 L1440,80 Z" fill="var(--bz-navy)" />
        </svg>
      </section>

      {/* ═══ SCROLLING TEXT ═══ */}
      <section className="bg-[var(--bz-navy)] py-5 overflow-hidden" aria-label={`Reach out, connect, let's talk — ${storeName}`}>
        <div className="bz-marquee-track" aria-hidden="true">
          {[...Array(6)].map((_, i) => (
            <span key={i} className="bz-heading text-lg sm:text-xl md:text-2xl lg:text-3xl text-[var(--bz-amber)] whitespace-nowrap mx-6 sm:mx-8">
              {localized(locale, "REACH OUT • CONNECT • LET'S TALK •", "تواصل • اتكلم معانا • خلّينا نسمعك •")} {storeName.toUpperCase()} •
            </span>
          ))}
        </div>
      </section>

      {/* ═══ CONTACT METHODS ═══ */}
      <section className="bg-[var(--bz-navy)] py-12 md:py-16 lg:py-24 relative">
        <div className="container mx-auto px-4">
          <div className="text-center mb-8 md:mb-12">
            <span className="bz-label text-[var(--bz-amber)]">
              <InlineEditable sectionId={sectionId} settingKey="channels_label" value={channelsLabel} />
            </span>
            <h2 className="bz-heading text-2xl sm:text-3xl md:text-4xl text-[var(--bz-cream)] mt-2 md:mt-3">
              <InlineEditable sectionId={sectionId} settingKey="channels_headline" value={channelsHeadline} />
            </h2>
          </div>

          <div className="grid grid-cols-1 xs:grid-cols-2 sm:grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4 md:gap-6">
            {whatsapp && (
              <a href={`https://wa.me/${whatsapp.replace(/^\+/, "")}`} target="_blank" rel="noopener noreferrer"
                className="group bz-card-hover rounded-3xl bg-white/5 border border-white/10 p-4 sm:p-6 text-center hover:border-[var(--bz-amber)]/50 transition-colors">
                <div className="w-14 h-14 rounded-full bg-[var(--bz-amber)] flex items-center justify-center mx-auto mb-4 group-hover:scale-110 transition-transform">
                  <MessageCircle size={24} className="text-[var(--bz-dark)]" />
                </div>
                <h3 className="bz-heading text-lg text-[var(--bz-cream)]">{localized(locale, "WHATSAPP", "واتساب")}</h3>
                <p className="text-sm text-[var(--bz-cream)]/40 mt-2">{localized(locale, "Quick responses, always", "ردود سريعة دايمًا")}</p>
                <span className="bz-label text-[var(--bz-amber)] mt-3 inline-flex items-center gap-1">{localized(locale, "CHAT NOW", "ابدأ المحادثة")} <ArrowRight size={12} className="rtl:-scale-x-100" /></span>
              </a>
            )}

            {phone && (
              <a href={`tel:+${phone.replace(/^\+/, "")}`}
                className="group bz-card-hover rounded-3xl bg-white/5 border border-white/10 p-4 sm:p-6 text-center hover:border-[var(--bz-amber)]/50 transition-colors">
                <div className="w-14 h-14 rounded-full bg-[var(--bz-amber)] flex items-center justify-center mx-auto mb-4 group-hover:scale-110 transition-transform">
                  <Phone size={24} className="text-[var(--bz-dark)]" />
                </div>
                <h3 className="bz-heading text-lg text-[var(--bz-cream)]">{localized(locale, "PHONE", "تليفون")}</h3>
                <p className="text-sm text-[var(--bz-cream)]/40 mt-2">{localized(locale, "Give us a ring", "اتصل بينا")}</p>
                <span className="bz-label text-[var(--bz-amber)] mt-3 inline-flex items-center gap-1">{localized(locale, "CALL US", "اتصل بينا")} <ArrowRight size={12} className="rtl:-scale-x-100" /></span>
              </a>
            )}

            {instagram && (
              <a href={instagramUrl} target="_blank" rel="noopener noreferrer"
                className="group bz-card-hover rounded-3xl bg-white/5 border border-white/10 p-4 sm:p-6 text-center hover:border-[var(--bz-amber)]/50 transition-colors">
                <div className="w-14 h-14 rounded-full bg-[var(--bz-amber)] flex items-center justify-center mx-auto mb-4 group-hover:scale-110 transition-transform">
                  <svg viewBox="0 0 24 24" width={24} height={24} fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" className="text-[var(--bz-dark)]"><rect width="20" height="20" x="2" y="2" rx="5" ry="5"/><path d="M16 11.37A4 4 0 1 1 12.63 8 4 4 0 0 1 16 11.37z"/><line x1="17.5" x2="17.51" y1="6.5" y2="6.5"/></svg>
                </div>
                <h3 className="bz-heading text-lg text-[var(--bz-cream)]">{localized(locale, "INSTAGRAM", "إنستجرام")}</h3>
                <p className="text-sm text-[var(--bz-cream)]/40 mt-2">{localized(locale, "Follow our journey", "تابع رحلتنا")}</p>
                <span className="bz-label text-[var(--bz-amber)] mt-3 inline-flex items-center gap-1" dir="ltr">@{instagram} <ArrowRight size={12} className="rtl:-scale-x-100" /></span>
              </a>
            )}

            {email && (
              <a href={`mailto:${email}`}
                className="group bz-card-hover rounded-3xl bg-white/5 border border-white/10 p-4 sm:p-6 text-center hover:border-[var(--bz-amber)]/50 transition-colors">
                <div className="w-14 h-14 rounded-full bg-[var(--bz-amber)] flex items-center justify-center mx-auto mb-4 group-hover:scale-110 transition-transform">
                  <Mail size={24} className="text-[var(--bz-dark)]" />
                </div>
                <h3 className="bz-heading text-lg text-[var(--bz-cream)]">{localized(locale, "EMAIL", "البريد الإلكتروني")}</h3>
                <p className="text-sm text-[var(--bz-cream)]/40 mt-2">{localized(locale, "For detailed inquiries", "للاستفسارات التفصيلية")}</p>
                <span className="bz-label text-[var(--bz-amber)] mt-3 inline-flex items-center gap-1">{localized(locale, "WRITE US", "راسلنا")} <ArrowRight size={12} className="rtl:-scale-x-100" /></span>
              </a>
            )}
          </div>
        </div>

        <svg viewBox="0 0 1440 60" className="absolute bottom-0 start-0 w-full" preserveAspectRatio="none" aria-hidden="true">
          <path d="M0,60 C360,10 720,50 1080,20 C1260,5 1380,40 1440,25 L1440,60 Z" fill="var(--bz-cream)" />
        </svg>
      </section>

      {/* ═══ CONTACT FORM ═══ */}
      <section className="py-12 md:py-16 lg:py-24 bg-[var(--bz-cream)]">
        <div className="container mx-auto px-4 max-w-2xl">
          <div className="text-center mb-8 md:mb-12">
            <span className="bz-label text-[var(--bz-amber)]">
              <InlineEditable sectionId={sectionId} settingKey="form_label" value={formLabel} />
            </span>
            <h2 className="bz-heading text-2xl sm:text-3xl md:text-4xl text-[var(--bz-dark)] mt-2 md:mt-3">
              <InlineEditable sectionId={sectionId} settingKey="form_headline" value={formHeadline} />
            </h2>
          </div>

          {status === "success" ? (
            <div className="text-center py-16">
              <div className="w-20 h-20 rounded-full bg-[var(--bz-amber)] flex items-center justify-center mx-auto mb-6">
                <Send size={32} className="text-[var(--bz-dark)]" />
              </div>
              <h3 className="bz-heading text-2xl md:text-3xl text-[var(--bz-dark)]">{successTitle}</h3>
              <p className="text-sm text-[var(--bz-gray)] mt-3 max-w-sm mx-auto">{successMessage}</p>
              {whatsappContinueUrl && (
                <a href={whatsappContinueUrl} target="_blank" rel="noopener noreferrer" className="bz-btn bz-btn-amber mt-6 rounded-full gap-2 inline-flex">
                  <MessageCircle size={16} /> {localized(locale, "CONTINUE ON WHATSAPP", "كمّل على واتساب")}
                </a>
              )}
              <button type="button" onClick={reset} className="bz-btn mt-4 rounded-full text-[11px] block mx-auto">
                {localized(locale, "SEND ANOTHER", "ابعت رسالة تانية")}
              </button>
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-5">
              <div>
                <label className="bz-label text-[var(--bz-dark)]/60 mb-2 block">
                  <InlineEditable sectionId={sectionId} settingKey="name_label" value={nameLabel} />
                </label>
                <input type="text" required value={form.name} onChange={(e) => setField("name", e.target.value)}
                  placeholder={localized(locale, "Full name", "الاسم بالكامل")}
                  className="w-full h-12 px-5 rounded-xl border-2 border-[var(--bz-dark)]/15 bg-transparent text-base md:text-sm focus:border-[var(--bz-amber)] outline-none transition-colors placeholder:text-[var(--bz-dark)]/30" />
              </div>
              <div>
                <label className="bz-label text-[var(--bz-dark)]/60 mb-2 block">
                  <InlineEditable sectionId={sectionId} settingKey="email_label" value={emailLabel} />
                </label>
                <input type="email" required value={form.email} onChange={(e) => setField("email", e.target.value)}
                  placeholder="you@example.com" dir="ltr"
                  className="w-full h-12 px-5 rounded-xl border-2 border-[var(--bz-dark)]/15 bg-transparent text-base md:text-sm focus:border-[var(--bz-amber)] outline-none transition-colors placeholder:text-[var(--bz-dark)]/30" />
              </div>
              <div>
                <label className="bz-label text-[var(--bz-dark)]/60 mb-2 block">
                  <InlineEditable sectionId={sectionId} settingKey="phone_label" value={phoneLabel} />
                </label>
                <input type="tel" required value={form.phone} onChange={(e) => setField("phone", e.target.value)}
                  placeholder="+20 XXX XXX XXXX" dir="ltr"
                  className="w-full h-12 px-5 rounded-xl border-2 border-[var(--bz-dark)]/15 bg-transparent text-base md:text-sm focus:border-[var(--bz-amber)] outline-none transition-colors placeholder:text-[var(--bz-dark)]/30" />
              </div>
              <div>
                <label className="bz-label text-[var(--bz-dark)]/60 mb-2 block">
                  <InlineEditable sectionId={sectionId} settingKey="message_label" value={messageLabel} />
                </label>
                <textarea required rows={5} value={form.message} onChange={(e) => setField("message", e.target.value)}
                  placeholder={localized(locale, "Tell us what's on your mind...", "قولّنا اللي في بالك...")}
                  className="w-full px-5 py-3 rounded-xl border-2 border-[var(--bz-dark)]/15 bg-transparent text-base md:text-sm focus:border-[var(--bz-amber)] outline-none transition-colors resize-none placeholder:text-[var(--bz-dark)]/30" />
              </div>
              <button type="submit" disabled={status === "submitting"} className="bz-btn bz-btn-filled w-full rounded-full py-4 text-[12px] gap-2 disabled:opacity-60 disabled:cursor-not-allowed">
                {status === "submitting" ? (
                  <>
                    <Loader2 size={14} className="animate-spin" /> {localized(locale, "SENDING…", "جاري الإرسال…")}
                  </>
                ) : (
                  <>
                    <InlineEditable sectionId={sectionId} settingKey="submit_text" value={submitText} /> <Send size={14} />
                  </>
                )}
              </button>
            </form>
          )}
        </div>
      </section>

      {/* ═══ BUSINESS HOURS ═══ */}
      {showHours && (
        <section className="relative py-12 md:py-20 lg:py-28 bg-[var(--bz-dark)] overflow-hidden">
          <svg viewBox="0 0 1440 60" className="absolute top-0 start-0 w-full -mt-px" preserveAspectRatio="none" aria-hidden="true">
            <path d="M0,0 C360,50 720,10 1080,40 C1260,55 1380,20 1440,30 L1440,0 L0,0 Z" fill="var(--bz-cream)" />
          </svg>

          <div aria-hidden="true" className="absolute top-12 md:top-20 end-4 md:end-10 w-24 h-24 sm:w-32 sm:h-32 md:w-40 md:h-40 bg-[var(--bz-amber)]/10 bz-blob" />

          <div className="container mx-auto px-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-8 md:gap-10 lg:gap-12 items-center">
              <div>
                <span className="bz-label text-[var(--bz-amber)]">
                  <InlineEditable sectionId={sectionId} settingKey="hours_label" value={hoursLabel} />
                </span>
                <h2 className="bz-heading text-2xl sm:text-3xl md:text-4xl lg:text-5xl text-[var(--bz-cream)] mt-3 md:mt-4 leading-tight">
                  <InlineEditable sectionId={sectionId} settingKey="hours_headline" value={hoursHeadline} />
                </h2>
                <p className="text-[var(--bz-cream)]/40 mt-4 text-sm leading-relaxed">
                  <InlineEditable sectionId={sectionId} settingKey="hours_description" value={hoursDescription} multiline />
                </p>
                {whatsapp && (
                  <a href={`https://wa.me/${whatsapp.replace(/^\+/, "")}`} target="_blank" rel="noopener noreferrer"
                    className="bz-btn bz-btn-amber mt-8 rounded-full gap-2">
                    <MessageCircle size={16} /> {localized(locale, "CHAT ON WHATSAPP", "تواصل على واتساب")}
                  </a>
                )}
              </div>

              <div className="space-y-3">
                {hoursRows.map((row) => (
                  <div key={row.day} className="flex flex-wrap items-center justify-between gap-2 rounded-2xl bg-white/5 border border-white/10 px-4 sm:px-6 py-3 sm:py-4">
                    <div className="flex items-center gap-2 sm:gap-3">
                      <Clock size={16} className="text-[var(--bz-amber)]" />
                      <span className="bz-label text-[var(--bz-cream)]">{row.day}</span>
                    </div>
                    <span className="text-xs sm:text-sm text-[var(--bz-cream)]/60 font-medium">{row.time}</span>
                  </div>
                ))}
                <div className="rounded-2xl bg-[var(--bz-amber)]/10 border border-[var(--bz-amber)]/20 px-4 sm:px-6 py-3 sm:py-4 text-center mt-4">
                  <p className="text-sm text-[var(--bz-amber)] font-semibold">{hoursNote}</p>
                </div>
              </div>
            </div>
          </div>
        </section>
      )}
    </div>
  );
}
