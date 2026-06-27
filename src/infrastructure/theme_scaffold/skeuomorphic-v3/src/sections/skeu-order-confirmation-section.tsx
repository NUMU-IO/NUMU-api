"use client";
import { useState } from "react";
import { Link, Money, useOrders, useLocale } from "@numueg/theme-sdk";
import { Check, Copy, Package, ArrowLeft, MessageCircle } from "lucide-react";
import { motion } from "framer-motion";
import { asString, localized, type SectionRenderProps } from "./_shared";

const TITLE_SHADOW = "0 2px 0 hsl(35 30% 100% / 0.5), 0 -1px 0 hsl(25 20% 50% / 0.1)";

/**
 * Skeuomorphic order-confirmation section.
 *
 * Ported from the proven Vionne V3 order-confirmation structure (success icon,
 * order detail card, progress tracker, WhatsApp card, CTAs), re-skinned with the
 * V2 skeuomorphic cues (tactile button icon container, elevated detail card,
 * skeu-divider progress, inset WhatsApp bubble).
 *
 * Data: reads the customer's most-recent order from `useOrders()` (the
 * just-placed order on a real storefront). With no order (anonymous / editor)
 * it renders the full static layout with placeholders and omits the total —
 * never crashes, never redirects away.
 */
export default function SkeuOrderConfirmationSection({ instance }: SectionRenderProps) {
  const s = instance.settings ?? {};

  const showProgress = s.show_progress ?? true;
  const showWhatsApp = s.show_whatsapp ?? true;
  const showTrackOrder = s.show_track_order ?? true;
  const showEmoji = s.show_emoji ?? false;

  const locale = useLocale();
  const title = asString(s.title) || localized(locale, "Order confirmed", "تم تأكيد الطلب");
  const subtitle =
    asString(s.subtitle) ||
    localized(locale, "Thanks for your order. We'll send the order details over WhatsApp.", "شكراً لطلبك. سنرسل لك تفاصيل الطلب عبر واتساب.");
  const continueText = asString(s.continue_shopping_text) || localized(locale, "Continue shopping", "متابعة التسوق");
  const continueLink = asString(s.continue_shopping_link) || "/";

  const { orders } = useOrders();
  const order = orders?.[0];
  const orderNumber = order?.order_number ?? "NUM-000000";
  const total = order?.total;
  const currency = order?.currency;

  const [copied, setCopied] = useState(false);
  const handleCopy = () => {
    if (typeof navigator !== "undefined" && navigator.clipboard) {
      navigator.clipboard.writeText(orderNumber).catch(() => {});
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const steps = [
    localized(locale, "Ordered", "تم الطلب"),
    localized(locale, "Processing", "قيد المعالجة"),
    localized(locale, "On the way", "في الطريق"),
    localized(locale, "Delivered", "تم التوصيل"),
  ];

  return (
    <div className="bg-background min-h-[60vh] flex items-start justify-center px-4 py-10 sm:px-6 sm:py-14 md:py-16 lg:py-20">
      <div className="w-full max-w-md sm:max-w-lg md:max-w-xl text-center">
        {/* Success icon */}
        <motion.div
          initial={{ scale: 0, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={{ type: "spring", damping: 14, stiffness: 200 }}
        >
          <div className="w-20 h-20 md:w-24 md:h-24 rounded-full flex items-center justify-center mx-auto mb-6 skeu-btn">
            <Check size={44} className="text-white relative z-[1]" />
          </div>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3 }}
        >
          {/* Title */}
          <span className="vn-eyebrow block mb-2 text-[10px] sm:text-[11px]">
            {localized(locale, "Order confirmed", "تم تأكيد الطلب")}
          </span>
          <h1
            className="vn-heading text-2xl sm:text-3xl md:text-4xl text-[var(--vn-ink)] mb-2 sm:mb-3 leading-tight"
            style={{ textShadow: TITLE_SHADOW }}
          >
            {title}{showEmoji ? " 🎉" : ""}
          </h1>
          <p className="text-sm sm:text-[15px] text-[var(--vn-muted)] mb-7 sm:mb-9 max-w-md mx-auto leading-relaxed">
            {subtitle}
          </p>

          {/* Order detail card */}
          <div className="skeu-card skeu-elevated rounded-2xl p-4 sm:p-5 md:p-6 mb-6 sm:mb-7 text-start">
            <div className="relative z-[1] space-y-4 text-sm">
              {/* Order number */}
              <div className="flex justify-between items-center">
                <span className="text-xs sm:text-sm text-[var(--vn-muted)]">{localized(locale, "Order number", "رقم الطلب")}</span>
                <div className="flex items-center gap-2">
                  <span
                    className="font-mono text-sm sm:text-base text-primary font-black tracking-wider"
                    dir="ltr"
                  >
                    {orderNumber}
                  </span>
                  <button
                    onClick={handleCopy}
                    className="p-1.5 rounded-md skeu-chip transition-colors active:scale-95"
                    title={localized(locale, "Copy", "نسخ")}
                  >
                    {copied ? (
                      <Check size={14} className="text-primary" />
                    ) : (
                      <Copy size={14} className="text-[var(--vn-muted)]" />
                    )}
                  </button>
                </div>
              </div>

              {/* Total — only when we have a real order total. */}
              {typeof total === "number" && total > 0 && (
                <>
                  <div className="skeu-divider" />
                  <div className="flex justify-between">
                    <span className="text-xs sm:text-sm text-[var(--vn-muted)]">{localized(locale, "Total", "الإجمالي")}</span>
                    <span className="text-xs sm:text-sm font-bold text-[var(--vn-ink)]">
                      <Money amount={total} currency={currency} />
                    </span>
                  </div>
                </>
              )}

              {/* Delivery */}
              <div className="skeu-divider" />
              <div className="flex justify-between">
                <span className="text-xs sm:text-sm text-[var(--vn-muted)]">{localized(locale, "Estimated delivery", "التوصيل المتوقع")}</span>
                <span className="text-xs sm:text-sm font-bold text-[var(--vn-ink)]">{localized(locale, "3–5 business days", "٣-٥ أيام عمل")}</span>
              </div>

              {/* Status */}
              <div className="flex justify-between">
                <span className="text-xs sm:text-sm text-[var(--vn-muted)]">{localized(locale, "Status", "الحالة")}</span>
                <span className="flex items-center gap-1.5 text-xs sm:text-sm font-bold text-primary">
                  <Package size={14} /> {localized(locale, "Processing", "قيد المعالجة")}
                </span>
              </div>
            </div>

            {/* Progress tracker */}
            {showProgress && (
              <div className="relative z-[1] mt-5 sm:mt-6 pt-5 sm:pt-6 skeu-divider">
                <div className="flex items-center justify-between pt-4">
                  {steps.map((step, i) => (
                    <div key={i} className="flex flex-col items-center flex-1">
                      <div
                        className={
                          i <= 1
                            ? "w-7 h-7 rounded-full flex items-center justify-center text-[10px] font-bold mb-1.5 skeu-btn text-white"
                            : "w-7 h-7 rounded-full flex items-center justify-center text-[10px] font-bold mb-1.5 skeu-inset text-[var(--vn-muted)]"
                        }
                      >
                        {i <= 1 ? <Check size={10} className="relative z-[1]" /> : i + 1}
                      </div>
                      <span className="text-[9px] sm:text-[10px] vn-label text-[var(--vn-muted)] text-center px-1">
                        {step}
                      </span>
                    </div>
                  ))}
                </div>
                <div className="flex mt-1">
                  {[0, 1, 2].map((i) => (
                    <div
                      key={i}
                      className={
                        i < 1
                          ? "flex-1 h-1 rounded-full mx-1 bg-primary"
                          : "flex-1 h-1 rounded-full mx-1 skeu-inset"
                      }
                    />
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* WhatsApp card */}
          {showWhatsApp && (
            <div className="skeu-card rounded-2xl p-4 sm:p-5 mb-6 sm:mb-7 text-start">
              <div className="relative z-[1]">
                <div className="flex items-center gap-2 mb-2.5 sm:mb-3 vn-eyebrow text-[10px] sm:text-[11px]">
                  <MessageCircle size={16} className="text-whatsapp" />
                  <span className="text-sm font-bold text-whatsapp">{localized(locale, "WhatsApp message", "رسالة واتساب")}</span>
                </div>
                <div className="skeu-inset rounded-xl p-3 sm:p-4 text-xs sm:text-sm text-foreground leading-relaxed">
                  <p>{localized(locale, "Hello! 👋", "مرحباً! 👋")}</p>
                  <p className="mt-1">
                    {localized(locale, "We've received your order", "تم استلام طلبك")}{" "}
                    <strong className="font-bold text-[var(--vn-ink)]">{orderNumber}</strong> {localized(locale, "successfully.", "بنجاح.")}
                  </p>
                  {typeof total === "number" && total > 0 && (
                    <p className="mt-1">
                      {localized(locale, "Total:", "الإجمالي:")} <strong><Money amount={total} currency={currency} /></strong>
                    </p>
                  )}
                  <p className="mt-1">{localized(locale, "We'll deliver it within 3–5 business days. For any questions, reach out to us here.", "سنوصله خلال ٣-٥ أيام عمل. لأي استفسار، تواصل معنا هنا.")}</p>
                  <p className="mt-1">{localized(locale, "Thanks for shopping with us ❤️", "شكراً لتسوقك معنا ❤️")}</p>
                </div>
              </div>
            </div>
          )}

          {/* CTAs */}
          <div className="flex flex-col sm:flex-row gap-2.5 sm:gap-3">
            {showTrackOrder && (
              <Link
                to={`/track?tn=${orderNumber}`}
                className="vn-btn vn-btn-outline-dark flex-1 inline-flex items-center justify-center gap-2 py-3 sm:py-3.5 text-xs sm:text-sm"
              >
                <Package size={18} /> {localized(locale, "Track order", "تتبّع الطلب")}
              </Link>
            )}
            <Link
              to={continueLink}
              className="vn-btn vn-btn-filled flex-1 inline-flex items-center justify-center gap-2 py-3 sm:py-3.5 text-xs sm:text-sm rtl:[&>svg]:rotate-180"
            >
              {continueText} <ArrowLeft size={14} />
            </Link>
          </div>
        </motion.div>
      </div>
    </div>
  );
}
