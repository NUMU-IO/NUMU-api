"use client";
import { useState } from "react";
import { Link, Money, useOrders, useLocale } from "@numueg/theme-sdk";
import { Check, Copy, Package, ArrowLeft, MessageCircle } from "lucide-react";
import { motion } from "framer-motion";
import { asString, localized, type SectionRenderProps } from "./_shared";

/**
 * Boutique order-confirmation section.
 *
 * Ported from the proven Vionne V3 order-confirmation (success icon, order
 * detail card, progress tracker, WhatsApp message card, CTAs) with the
 * grayscale `vn-*` tokens translated to Boutique's pink palette. Reads the
 * most-recent order from useOrders(); never blank / never redirects away.
 */
export default function BoutiqueOrderConfirmationSection({ instance }: SectionRenderProps) {
  const s = instance.settings ?? {};
  const locale = useLocale();

  const showProgress = s.show_progress ?? true;
  const showWhatsApp = s.show_whatsapp ?? true;
  const showTrackOrder = s.show_track_order ?? true;
  const showEmoji = s.show_emoji ?? true;

  const title = asString(s.title) || localized(locale, "Your order is confirmed", "تم تأكيد طلبك");
  const subtitle =
    asString(s.subtitle) ||
    localized(
      locale,
      "Thank you for your order. We'll send the order details over WhatsApp.",
      "شكراً لطلبك. هنبعتلك تفاصيل الطلب على الواتساب.",
    );
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
    localized(locale, "Processing", "قيد التجهيز"),
    localized(locale, "On the way", "في الطريق"),
    localized(locale, "Delivered", "تم التوصيل"),
  ];

  return (
    <div
      className={
        "bg-background min-h-[60vh] flex items-start justify-center " +
        "px-4 py-10 sm:px-6 sm:py-14 md:py-16 lg:py-20"
      }
    >
      <div className="w-full max-w-md sm:max-w-lg md:max-w-xl text-center">
        {/* Success icon */}
        <motion.div
          initial={{ scale: 0, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={{ type: "spring", damping: 14, stiffness: 200 }}
        >
          <div
            className={
              "w-14 h-14 sm:w-16 sm:h-16 md:w-20 md:h-20 rounded-full " +
              "flex items-center justify-center mx-auto mb-5 sm:mb-6 md:mb-7 " +
              "text-primary-foreground"
            }
            style={{ background: "hsl(var(--primary))" }}
          >
            <Check size={28} />
          </div>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3 }}
        >
          {/* Title */}
          <span className="block mb-2 text-[10px] sm:text-[11px] uppercase tracking-widest text-primary">
            {localized(locale, "Order confirmed", "تم تأكيد الطلب")}
          </span>
          <h1 className="text-2xl sm:text-3xl md:text-4xl font-bold text-foreground mb-2 sm:mb-3 leading-tight">
            {title}{showEmoji ? " 🎉" : ""}
          </h1>
          <p className="text-sm sm:text-[15px] text-muted-foreground mb-7 sm:mb-9 max-w-md mx-auto leading-relaxed">
            {subtitle}
          </p>

          {/* Order detail card */}
          <div className="bg-accent/30 rounded-[var(--radius)] p-4 sm:p-5 md:p-6 mb-6 sm:mb-7 border border-border text-start">
            <div className="space-y-4 text-sm">
              {/* Order number */}
              <div className="flex justify-between items-center">
                <span className="text-xs sm:text-sm text-muted-foreground">{localized(locale, "Order number", "رقم الطلب")}</span>
                <div className="flex items-center gap-2">
                  <span
                    className="font-mono text-sm sm:text-base text-foreground font-semibold tracking-wider"
                    dir="ltr"
                  >
                    {orderNumber}
                  </span>
                  <button
                    onClick={handleCopy}
                    className="p-1.5 rounded-md transition-colors hover:bg-white/60 active:scale-95"
                    title={localized(locale, "Copy", "نسخ")}
                  >
                    {copied ? (
                      <Check size={14} className="text-primary" />
                    ) : (
                      <Copy size={14} className="text-muted-foreground" />
                    )}
                  </button>
                </div>
              </div>

              {/* Total — only when we have a real order total. */}
              {typeof total === "number" && total > 0 && (
                <>
                  <div className="h-px bg-border" />
                  <div className="flex justify-between">
                    <span className="text-xs sm:text-sm text-muted-foreground">{localized(locale, "Total", "الإجمالي")}</span>
                    <span className="text-xs sm:text-sm font-medium text-foreground">
                      <Money amount={total} currency={currency} />
                    </span>
                  </div>
                </>
              )}

              {/* Delivery */}
              <div className="h-px bg-border" />
              <div className="flex justify-between">
                <span className="text-xs sm:text-sm text-muted-foreground">{localized(locale, "Estimated delivery", "موعد التوصيل المتوقع")}</span>
                <span className="text-xs sm:text-sm font-medium text-foreground">{localized(locale, "3-5 business days", "3-5 أيام عمل")}</span>
              </div>

              {/* Status */}
              <div className="flex justify-between">
                <span className="text-xs sm:text-sm text-muted-foreground">{localized(locale, "Status", "الحالة")}</span>
                <span className="flex items-center gap-1.5 text-xs sm:text-sm font-medium text-foreground">
                  <Package size={14} /> {localized(locale, "Processing", "قيد التجهيز")}
                </span>
              </div>
            </div>

            {/* Progress tracker */}
            {showProgress && (
              <div className="mt-5 sm:mt-6 pt-5 sm:pt-6 border-t border-border">
                <div className="flex items-center justify-between">
                  {steps.map((step, i) => (
                    <div key={i} className="flex flex-col items-center flex-1">
                      <div
                        className={
                          i <= 1
                            ? "w-6 h-6 sm:w-7 sm:h-7 rounded-full flex items-center justify-center text-[10px] font-medium mb-1.5 text-primary-foreground"
                            : "w-6 h-6 sm:w-7 sm:h-7 rounded-full flex items-center justify-center text-[10px] font-medium mb-1.5 bg-white text-muted-foreground border border-border"
                        }
                        style={i <= 1 ? { background: "hsl(var(--primary))" } : undefined}
                      >
                        {i <= 1 ? <Check size={10} /> : i + 1}
                      </div>
                      <span className="text-[9px] sm:text-[10px] uppercase tracking-widest text-muted-foreground text-center px-1">
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
                          ? "flex-1 h-px mx-1 sm:mx-1.5 bg-primary"
                          : "flex-1 h-px mx-1 sm:mx-1.5 bg-border"
                      }
                    />
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* WhatsApp card */}
          {showWhatsApp && (
            <div className="bg-card border border-border rounded-[var(--radius)] p-4 sm:p-5 mb-6 sm:mb-7 text-start">
              <div className="flex items-center gap-2 mb-2.5 sm:mb-3 text-[10px] sm:text-[11px] uppercase tracking-widest">
                <MessageCircle size={16} className="text-whatsapp" />
                <span className="text-sm font-bold text-whatsapp">{localized(locale, "WhatsApp message", "رسالة واتساب")}</span>
              </div>
              <div className="bg-accent/30 rounded-md p-3 sm:p-4 text-xs sm:text-sm text-foreground leading-relaxed">
                <p>{localized(locale, "Hi there! 👋", "أهلاً! 👋")}</p>
                <p className="mt-1">
                  {localized(locale, "Your order ", "طلبك ")}
                  <strong className="font-semibold text-foreground">{orderNumber}</strong>
                  {localized(locale, " has been placed successfully.", " اتسجّل بنجاح.")}
                </p>
                {typeof total === "number" && total > 0 && (
                  <p className="mt-1">
                    {localized(locale, "Total: ", "الإجمالي: ")}<strong><Money amount={total} currency={currency} /></strong>
                  </p>
                )}
                <p className="mt-1">
                  {localized(
                    locale,
                    "We'll deliver it within 3-5 business days. If you have any questions, reach out to us here.",
                    "هنوصّله خلال 3-5 أيام عمل. لو عندك أي سؤال، تواصلي معنا هنا.",
                  )}
                </p>
                <p className="mt-1">{localized(locale, "Thank you for shopping with us ❤️", "شكراً لتسوقك معنا ❤️")}</p>
              </div>
            </div>
          )}

          {/* CTAs */}
          <div className="flex flex-col sm:flex-row gap-2.5 sm:gap-3">
            {showTrackOrder && (
              <Link
                to={`/track?tn=${orderNumber}`}
                className={
                  "flex-1 inline-flex items-center justify-center gap-2 h-11 rounded-full font-semibold border border-primary text-foreground transition-all hover:bg-primary hover:text-primary-foreground " +
                  "py-3 sm:py-3.5 text-xs sm:text-sm"
                }
              >
                <Package size={18} /> {localized(locale, "Track order", "تتبّعي الطلب")}
              </Link>
            )}
            <Link
              to={continueLink}
              className={
                "flex-1 inline-flex items-center justify-center gap-2 h-11 rounded-full font-semibold text-primary-foreground transition-all hover:scale-[1.02] " +
                "py-3 sm:py-3.5 text-xs sm:text-sm rtl:[&>svg]:rotate-180"
              }
              style={{ background: "hsl(var(--primary))" }}
            >
              {continueText} <ArrowLeft size={14} />
            </Link>
          </div>
        </motion.div>
      </div>
    </div>
  );
}
