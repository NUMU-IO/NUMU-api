"use client";

import { useState } from "react";
import {
  Link,
  Money,
  useLocale,
  useOrders,
  useResolvedSettings,
  useShop,
  useThemeSettings,
} from "@numueg/theme-sdk";
import { Check, Copy, Package, MessageCircle, ArrowRight } from "lucide-react";
import { motion } from "framer-motion";
import {
  asBool,
  asNumber,
  asString,
  localized,
  readBlocks,
  type SectionRenderProps,
} from "./_shared";
import { InlineEditable } from "./_inline-editable";

/**
 * gilded-order-confirmation — faithful V3 port of the V2
 * GildedOrderConfirmationPage (numu-egyptian-bazaar/src/components/store/
 * gilded-glamour-boutique/GildedOrderConfirmationPage.tsx, which renders a
 * fully custom layout through BaseOrderConfirmationPage's `renderCustom`).
 *
 * Layout (all V2 className strings kept verbatim, only the brand gold migrated
 * from the fixed `hsl(var(--gold))` to the repaintable `var(--gilded-gold)`
 * token so the editor Accent picker recolours it):
 *   - Success crest: a gold vertical hairline (w-px h-12) + a Check inside a
 *     rounded-full gold-bordered 64px ring (scale-in, duration 0.5).
 *   - Eyebrow "Confirmed" (gold, 0.4em tracking) + Montserrat H1 "Thank you for
 *     your order" (text-3xl md:text-5xl, uppercase, 0.08em) + subtitle.
 *   - Summary panel: gold hairline top+bottom (border-gold/30), md:grid-cols-3 —
 *     Order # (gold, Montserrat, copy-to-clipboard button toggling Copy↔Check),
 *     Total (<Money amount={order.total/100}> — useOrders totals are CENTS), and
 *     the Payment-status label.
 *   - Journey tracker (Placed / Processing / On the Way / Delivered) with a gold
 *     progress line + check/number circles. Steps authorable via `step` blocks.
 *   - Optional WhatsApp callout (gated by `show_whatsapp`; resolves the merchant
 *     WhatsApp number from the global Social settings → wa.me deep link).
 *   - Two CTAs: Track Order (gold outline) + Continue Shopping (black fill).
 *
 * Data: most-recent order from `useOrders()` (the just-placed order). When no
 * order is available (anonymous visitor / editor preview) the layout still
 * renders with a placeholder number and the configured copy — it never blanks
 * out and never redirects (matching the engine "no-404" contract).
 */
export default function GildedOrderConfirmation({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const locale = useLocale();
  const shop = useShop();
  const themeSettings = useThemeSettings();

  // ── Editable copy (merchant value wins; bilingual empty-state default). ──
  const eyebrowText =
    asString(s.eyebrow_text) || localized(locale, "Confirmed", "تم التأكيد");
  const titleText =
    asString(s.title_text) ||
    localized(locale, "Thank you for your order", "شكراً لطلبك");
  const subtitleText =
    asString(s.subtitle_text) ||
    localized(
      locale,
      "We have received your order and will send the details to you shortly. Your curated pieces are being prepared with care.",
      "لقد استلمنا طلبك وسنرسل لك التفاصيل قريباً. يتم تجهيز قطعك المنتقاة بعناية فائقة.",
    );

  const trackerTitle =
    asString(s.tracker_title) || localized(locale, "Journey Status", "حالة الرحلة");

  const whatsAppTitle =
    asString(s.whatsapp_title) || localized(locale, "Need Assistance?", "تحتاج مساعدة؟");
  const whatsAppBody =
    asString(s.whatsapp_text) ||
    localized(
      locale,
      "Our team is available via WhatsApp for any questions about your order",
      "فريقنا متاح عبر واتساب للإجابة على أي استفسار بخصوص طلبك",
    );

  const trackText =
    asString(s.track_text) || localized(locale, "Track Order", "تتبع الطلب");
  const trackLink = asString(s.track_link) || "/track";
  const ctaText =
    asString(s.cta_text) || localized(locale, "Continue Shopping", "متابعة التسوق");
  const ctaLink = asString(s.cta_link) || "/products";

  const showWhatsApp = asBool(s.show_whatsapp, true);

  // ── Order data (graceful fallback — never crashes / redirects). ──
  const { orders } = useOrders();
  const order = orders?.[0];
  const orderNumber = asString(order?.order_number) || "NUM-000001";
  const orderTotal = asNumber(order?.total, 0); // CENTS
  const orderCurrency = asString(order?.currency) || "EGP";

  // Payment-status label, mirroring the V2 BaseOrderConfirmationPage mapping.
  // V2 defaults to "paid" on the query-param landing path.
  const paymentStatus = asString(order?.payment_status) || "paid";
  const paymentLabel = (() => {
    switch (paymentStatus) {
      case "paid":
        return localized(locale, "Paid", "مدفوع");
      case "awaiting_review":
        return localized(locale, "Under Verification", "قيد المراجعة");
      case "rejected":
        return localized(locale, "Payment Rejected", "تم رفض الدفع");
      case "pending":
        return localized(locale, "Awaiting Payment", "بانتظار الدفع");
      default:
        return paymentStatus;
    }
  })();

  // WhatsApp number — read from the global Social settings (Online Store →
  // social), with a legacy fallback to the store's `social_links`.
  const globals = themeSettings.global_settings ?? {};
  const storeSocial = shop?.social_links ?? {};
  const rawWhatsApp =
    asString(globals.social_whatsapp) || asString(storeSocial.whatsapp);
  const whatsAppHref = rawWhatsApp
    ? rawWhatsApp.startsWith("http")
      ? rawWhatsApp
      : `https://wa.me/${rawWhatsApp.replace(/\D/g, "")}`
    : "";

  // ── Copy-to-clipboard. ──
  const [copied, setCopied] = useState(false);
  const handleCopy = () => {
    if (typeof navigator !== "undefined" && navigator.clipboard) {
      navigator.clipboard.writeText(orderNumber).catch(() => {});
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  // ── Journey steps (repeatable `step` blocks; else the V2 4-step default). ──
  interface JourneyStep {
    label: string;
    active: boolean;
  }
  const stepBlocks = readBlocks(instance, "step");
  const steps: JourneyStep[] =
    stepBlocks.length > 0
      ? stepBlocks.map((b) => ({
          label: asString(b.label),
          active: asBool(b.active, false),
        }))
      : [
          { label: localized(locale, "Order Placed", "تم الطلب"), active: true },
          { label: localized(locale, "Processing", "قيد التجهيز"), active: true },
          { label: localized(locale, "On the Way", "في الطريق"), active: false },
          { label: localized(locale, "Delivered", "تم التوصيل"), active: false },
        ];
  // Gold progress line fill — spans from the first to the last *reached* step.
  // V2 hard-codes w-1/3 (2 of 4 steps reached); the dynamic calc reproduces
  // that exactly for the 4-step default and adapts to merchant-authored steps.
  const activeCount = steps.filter((st) => st.active).length;
  const progressWidth =
    steps.length > 1
      ? `${(Math.max(0, activeCount - 1) / (steps.length - 1)) * 100}%`
      : "0%";

  return (
    <div className="min-h-screen bg-background py-16 md:py-24" data-gilded-section={sectionId}>
      <div className="container mx-auto px-4 max-w-3xl">
        {/* Top crest */}
        <motion.div
          initial={{ opacity: 0, scale: 0.7 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.5 }}
          className="flex flex-col items-center mb-10"
        >
          <div className="w-px h-12 bg-[var(--gilded-gold)] mb-6" />
          <div className="w-16 h-16 border border-[var(--gilded-gold)] rounded-full flex items-center justify-center">
            <Check
              size={22}
              className="text-[var(--gilded-gold)]"
              strokeWidth={1.5}
              aria-hidden="true"
            />
          </div>
        </motion.div>

        {/* Heading */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.2 }}
          className="text-center mb-12"
        >
          <p className="text-[11px] tracking-[0.4em] uppercase text-[var(--gilded-gold)] mb-4">
            <InlineEditable sectionId={sectionId} settingKey="eyebrow_text" value={eyebrowText} />
          </p>
          <h1
            className="text-3xl md:text-5xl font-bold tracking-[0.08em] uppercase text-foreground leading-tight"
            style={{ fontFamily: "'Montserrat', sans-serif" }}
          >
            <InlineEditable sectionId={sectionId} settingKey="title_text" value={titleText} />
          </h1>
          <p className="mt-4 text-sm text-muted-foreground max-w-md mx-auto">
            <InlineEditable
              sectionId={sectionId}
              settingKey="subtitle_text"
              value={subtitleText}
              multiline
            />
          </p>
        </motion.div>

        {/* Order summary panel */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.4 }}
          className="border-t border-b border-[var(--gilded-gold)]/30 py-8 px-4 grid grid-cols-1 md:grid-cols-3 gap-8 mb-10"
        >
          {/* Order number */}
          <div className="text-center md:text-start">
            <p className="text-[10px] tracking-[0.3em] uppercase text-muted-foreground mb-2">
              {localized(locale, "Order Number", "رقم الطلب")}
            </p>
            <div className="flex items-center gap-2 justify-center md:justify-start">
              <span
                className="text-lg font-bold tracking-wider text-[var(--gilded-gold)]"
                style={{ fontFamily: "'Montserrat', sans-serif" }}
                dir="ltr"
              >
                {orderNumber}
              </span>
              <button
                type="button"
                onClick={handleCopy}
                aria-label={localized(locale, "Copy order number", "نسخ رقم الطلب")}
                title={localized(locale, "Copy", "نسخ")}
                className="text-muted-foreground hover:text-[var(--gilded-gold)] transition-colors"
              >
                {copied ? (
                  <Check size={14} aria-hidden="true" />
                ) : (
                  <Copy size={14} aria-hidden="true" />
                )}
              </button>
            </div>
          </div>

          {/* Total — useOrders totals are CENTS → /100 for <Money> (major units). */}
          {orderTotal > 0 && (
            <div className="text-center">
              <p className="text-[10px] tracking-[0.3em] uppercase text-muted-foreground mb-2">
                {localized(locale, "Total", "الإجمالي")}
              </p>
              <p
                className="text-lg font-bold tracking-wider text-foreground"
                style={{ fontFamily: "'Montserrat', sans-serif" }}
              >
                <Money amount={orderTotal / 100} currency={orderCurrency} />
              </p>
            </div>
          )}

          {/* Payment */}
          <div className="text-center md:text-end">
            <p className="text-[10px] tracking-[0.3em] uppercase text-muted-foreground mb-2">
              {localized(locale, "Payment", "الدفع")}
            </p>
            <p className="text-sm font-semibold uppercase tracking-wider text-foreground">
              {paymentLabel}
            </p>
          </div>
        </motion.div>

        {/* Progress tracker */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.6, delay: 0.6 }}
          className="mb-12"
        >
          <p className="text-center text-[10px] tracking-[0.3em] uppercase text-muted-foreground mb-6">
            <InlineEditable sectionId={sectionId} settingKey="tracker_title" value={trackerTitle} />
          </p>
          <div className="flex items-center justify-between max-w-2xl mx-auto relative">
            <div className="absolute top-3 inset-x-0 h-px bg-border -z-10" />
            <div
              className="absolute top-3 start-0 h-px bg-[var(--gilded-gold)] -z-10"
              style={{ width: progressWidth }}
            />
            {steps.map((step, i) => (
              <div key={i} className="flex flex-col items-center gap-3 text-center">
                <div
                  className={`w-6 h-6 rounded-full flex items-center justify-center transition-colors ${
                    step.active
                      ? "bg-[var(--gilded-gold)]"
                      : "bg-background border border-border"
                  }`}
                >
                  {step.active ? (
                    <Check
                      size={12}
                      className="text-foreground"
                      strokeWidth={2.5}
                      aria-hidden="true"
                    />
                  ) : (
                    <span className="text-[10px] text-muted-foreground">{i + 1}</span>
                  )}
                </div>
                <span
                  className={`text-[10px] tracking-[0.2em] uppercase ${
                    step.active ? "text-foreground font-semibold" : "text-muted-foreground"
                  }`}
                >
                  {step.label || `${i + 1}`}
                </span>
              </div>
            ))}
          </div>
        </motion.div>

        {/* WhatsApp callout */}
        {showWhatsApp && (
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.8 }}
            className="bg-card border border-border p-6 mb-10 max-w-xl mx-auto"
          >
            <div className="flex items-start gap-4">
              <div className="shrink-0 w-10 h-10 bg-[var(--gilded-gold)]/10 flex items-center justify-center">
                <MessageCircle size={18} className="text-[var(--gilded-gold)]" aria-hidden="true" />
              </div>
              <div>
                <p className="text-xs tracking-[0.2em] uppercase font-semibold text-foreground mb-2">
                  <InlineEditable
                    sectionId={sectionId}
                    settingKey="whatsapp_title"
                    value={whatsAppTitle}
                  />
                </p>
                <p className="text-sm text-muted-foreground leading-relaxed">
                  <InlineEditable
                    sectionId={sectionId}
                    settingKey="whatsapp_text"
                    value={whatsAppBody}
                    multiline
                  />
                  <span className="text-[var(--gilded-gold)] font-semibold" dir="ltr">
                    {" "}
                    {orderNumber}
                  </span>
                  .
                </p>
                {whatsAppHref && (
                  <a
                    href={whatsAppHref}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="gld-btn mt-4 inline-flex items-center gap-2 px-5 py-2.5 text-[11px] tracking-[0.2em] uppercase font-semibold"
                  >
                    <MessageCircle size={14} aria-hidden="true" />
                    {localized(locale, "Chat on WhatsApp", "تواصل عبر واتساب")}
                  </a>
                )}
              </div>
            </div>
          </motion.div>
        )}

        {/* Actions */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 1 }}
          className="flex flex-col sm:flex-row gap-4 justify-center max-w-xl mx-auto"
        >
          <Link
            to={`${trackLink}?order=${encodeURIComponent(orderNumber)}`}
            className="flex-1 h-12 flex items-center justify-center gap-2 border border-[var(--gilded-gold)] text-foreground text-[11px] font-semibold tracking-[0.2em] uppercase hover:bg-[var(--gilded-gold)] transition-colors"
          >
            <Package size={14} aria-hidden="true" />
            <InlineEditable sectionId={sectionId} settingKey="track_text" value={trackText} />
          </Link>
          <Link
            to={ctaLink}
            className="flex-1 h-12 flex items-center justify-center gap-2 bg-foreground text-card text-[11px] font-semibold tracking-[0.2em] uppercase hover:bg-[var(--gilded-gold-dark)] transition-colors"
          >
            <InlineEditable sectionId={sectionId} settingKey="cta_text" value={ctaText} />
            <ArrowRight size={14} aria-hidden="true" className="rtl:-scale-x-100" />
          </Link>
        </motion.div>
      </div>
    </div>
  );
}
