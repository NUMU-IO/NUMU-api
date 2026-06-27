"use client";

import { useState } from "react";
import {
  Link,
  Money,
  useLocale,
  useOrders,
  useResolvedSettings,
} from "@numueg/theme-sdk";
import { Check, Copy, Package, ArrowLeft, MessageCircle } from "lucide-react";
import { motion } from "framer-motion";
import { asBool, asString, localized, type SectionRenderProps } from "./_shared";
import { InlineEditable } from "./_inline-editable";

/**
 * bz-order-confirmation — the order-confirmation page body, re-skinned to
 * Bazar's bold amber/navy aesthetic. V2 rendered this through the shared
 * `BaseOrderConfirmationPage`, which read the just-placed order from router
 * state; neither that base component nor the router state exist in V3, which
 * is why a thin port would render blank.
 *
 * Instead — mirroring the vionne V3 port — we read the customer's most-recent
 * order from the SDK's `useOrders()` (the just-placed order on a real
 * storefront). When no order is available (anonymous visitor, editor preview,
 * or no orders yet) we still render the full static layout with a placeholder
 * order number and gracefully omit the total. We never crash and never
 * redirect away. Editable copy uses `useResolvedSettings` + `<InlineEditable>`.
 */
export default function BzOrderConfirmation({
  instance,
  sectionId,
}: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const locale = useLocale();

  const eyebrow = asString(s.eyebrow) || localized(locale, "ORDER CONFIRMED", "تم تأكيد الطلب");
  const title = asString(s.title) || localized(locale, "THANK YOU!", "شكرًا ليك!");
  const subtitle =
    asString(s.subtitle) ||
    localized(locale, "Your order has been placed successfully. We'll send you the order details via WhatsApp.", "تم تسجيل طلبك بنجاح. هنبعتلك تفاصيل الطلب على واتساب.");
  const continueText = asString(s.continue_shopping_text) || localized(locale, "CONTINUE SHOPPING", "كمّل تسوّق");
  const continueLink = asString(s.continue_shopping_link) || "/products";

  const orderNumberLabel = asString(s.order_number_label) || localized(locale, "ORDER NUMBER", "رقم الطلب");
  const totalLabel = asString(s.total_label) || localized(locale, "TOTAL", "الإجمالي");
  const deliveryLabel = asString(s.delivery_label) || localized(locale, "ESTIMATED DELIVERY", "التوصيل المتوقع");
  const deliveryValue = asString(s.delivery_value) || localized(locale, "3-5 business days", "من ٣ لـ ٥ أيام عمل");
  const statusLabel = asString(s.status_label) || localized(locale, "STATUS", "الحالة");
  const statusValue = asString(s.status_value) || localized(locale, "Processing", "قيد التجهيز");
  const trackOrderText = asString(s.track_order_text) || localized(locale, "TRACK ORDER", "تتبّع الطلب");

  const showProgress = asBool(s.show_progress, true);
  const showWhatsApp = asBool(s.show_whatsapp, true);
  const showTrackOrder = asBool(s.show_track_order, true);

  // Most-recent order = the one the customer just placed. `useOrders()` is
  // gated on the logged-in customer; anonymous visitors / editor preview get
  // an empty list, so `order` is undefined and we fall back to placeholders.
  const { orders } = useOrders();
  const order = orders?.[0];
  const orderNumber = order?.order_number ?? "NUM-000000";
  // order.total is integer CENTS (useOrders doesn't normalize it, unlike
  // useCart); <Money> expects MAJOR units — divide by 100 to avoid a 100× total.
  const total = typeof order?.total === "number" ? order.total / 100 : undefined;
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
    localized(locale, "Order Placed", "تم الطلب"),
    localized(locale, "Processing", "قيد التجهيز"),
    localized(locale, "On the Way", "في الطريق"),
    localized(locale, "Delivered", "تم التوصيل"),
  ];

  return (
    <div
      className="bg-[var(--bz-cream)] min-h-[70vh] flex items-start justify-center px-4 py-10 sm:px-6 sm:py-14 md:py-16 lg:py-20"
      data-bz-section={sectionId}
    >
      <div className="w-full max-w-md sm:max-w-lg md:max-w-xl text-center">
        {/* Success icon */}
        <motion.div
          initial={{ scale: 0, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={{ type: "spring", damping: 14, stiffness: 200 }}
        >
          <div className="w-16 h-16 sm:w-20 sm:h-20 rounded-full flex items-center justify-center mx-auto mb-5 sm:mb-6 bg-[var(--bz-amber)] text-[var(--bz-dark)]">
            <Check size={32} />
          </div>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3 }}
        >
          {/* Title */}
          <span className="bz-label text-[var(--bz-amber-dark)] block mb-2">
            <InlineEditable sectionId={sectionId} settingKey="eyebrow" value={eyebrow} />
          </span>
          <h1 className="bz-heading text-2xl sm:text-3xl md:text-4xl text-[var(--bz-dark)] mb-2 sm:mb-3 leading-tight">
            <InlineEditable sectionId={sectionId} settingKey="title" value={title} />
          </h1>
          <p className="text-sm sm:text-[15px] text-[var(--bz-gray)] mb-7 sm:mb-9 max-w-md mx-auto leading-relaxed">
            <InlineEditable sectionId={sectionId} settingKey="subtitle" value={subtitle} multiline />
          </p>

          {/* Order detail card */}
          <div className="bg-white rounded-2xl p-4 sm:p-5 md:p-6 mb-6 sm:mb-7 border border-[var(--bz-dark)]/10 shadow-sm text-start">
            <div className="space-y-4 text-sm">
              {/* Order number */}
              <div className="flex justify-between items-center">
                <span className="bz-label text-[var(--bz-dark)]/60">{orderNumberLabel}</span>
                <div className="flex items-center gap-2">
                  <span className="font-mono text-sm sm:text-base text-[var(--bz-dark)] font-bold tracking-wider" dir="ltr">
                    {orderNumber}
                  </span>
                  <button
                    onClick={handleCopy}
                    className="p-1.5 rounded-md transition-colors hover:bg-[var(--bz-cream)] active:scale-95"
                    title="Copy"
                    aria-label="Copy order number"
                  >
                    {copied ? (
                      <Check size={14} className="text-[var(--bz-amber-dark)]" />
                    ) : (
                      <Copy size={14} className="text-[var(--bz-gray)]" />
                    )}
                  </button>
                </div>
              </div>

              {/* Total — only when we have a real order total. */}
              {typeof total === "number" && total > 0 && (
                <>
                  <div className="h-px bg-[var(--bz-dark)]/10" />
                  <div className="flex justify-between">
                    <span className="bz-label text-[var(--bz-dark)]/60">{totalLabel}</span>
                    <span className="text-sm font-bold text-[var(--bz-dark)]">
                      <Money amount={total} currency={currency} />
                    </span>
                  </div>
                </>
              )}

              {/* Delivery */}
              <div className="h-px bg-[var(--bz-dark)]/10" />
              <div className="flex justify-between">
                <span className="bz-label text-[var(--bz-dark)]/60">{deliveryLabel}</span>
                <span className="text-sm font-bold text-[var(--bz-dark)]">{deliveryValue}</span>
              </div>

              {/* Status */}
              <div className="flex justify-between">
                <span className="bz-label text-[var(--bz-dark)]/60">{statusLabel}</span>
                <span className="flex items-center gap-1.5 text-sm font-bold text-[var(--bz-dark)]">
                  <Package size={14} /> {statusValue}
                </span>
              </div>
            </div>

            {/* Progress tracker */}
            {showProgress && (
              <div className="mt-5 sm:mt-6 pt-5 sm:pt-6 border-t border-[var(--bz-dark)]/10">
                <div className="flex items-center justify-between">
                  {steps.map((step, i) => (
                    <div key={step} className="flex flex-col items-center flex-1">
                      <div
                        className={
                          i <= 1
                            ? "w-6 h-6 sm:w-7 sm:h-7 rounded-full flex items-center justify-center text-[10px] font-bold mb-1.5 bg-[var(--bz-amber)] text-[var(--bz-dark)]"
                            : "w-6 h-6 sm:w-7 sm:h-7 rounded-full flex items-center justify-center text-[10px] font-bold mb-1.5 bg-[var(--bz-cream)] text-[var(--bz-gray)] border border-[var(--bz-dark)]/15"
                        }
                      >
                        {i <= 1 ? <Check size={10} /> : i + 1}
                      </div>
                      <span className="text-[9px] sm:text-[10px] bz-label text-[var(--bz-gray)] text-center px-1">
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
                          ? "flex-1 h-px mx-1 sm:mx-1.5 bg-[var(--bz-amber)]"
                          : "flex-1 h-px mx-1 sm:mx-1.5 bg-[var(--bz-dark)]/15"
                      }
                    />
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* WhatsApp card */}
          {showWhatsApp && (
            <div className="bg-white border border-[var(--bz-dark)]/10 rounded-2xl p-4 sm:p-5 mb-6 sm:mb-7 text-start shadow-sm">
              <div className="flex items-center gap-2 mb-2.5 sm:mb-3">
                <MessageCircle size={16} className="text-[#25D366]" />
                <span className="bz-label text-sm text-[#25D366]">{localized(locale, "WHATSAPP MESSAGE", "رسالة واتساب")}</span>
              </div>
              <div className="bg-[var(--bz-cream)] rounded-xl p-3 sm:p-4 text-xs sm:text-sm text-[var(--bz-dark)] leading-relaxed">
                <p>{localized(locale, "Hello! 👋", "أهلًا! 👋")}</p>
                <p className="mt-1">
                  {localized(locale, "Your order ", "طلبك ")}
                  <strong className="font-semibold text-[var(--bz-dark)]" dir="ltr">{orderNumber}</strong>
                  {localized(locale, " has been received successfully.", " وصلنا بنجاح.")}
                </p>
                {typeof total === "number" && total > 0 && (
                  <p className="mt-1">
                    {localized(locale, "Total: ", "الإجمالي: ")}<strong><Money amount={total} currency={currency} /></strong>
                  </p>
                )}
                <p className="mt-1">{localized(locale, `We'll deliver it within ${deliveryValue}. If you have any questions, contact us here.`, `هنوصّله ليك خلال ${deliveryValue}. لو عندك أي استفسار، تواصل معانا هنا.`)}</p>
                <p className="mt-1">{localized(locale, "Thank you for shopping with us ❤️", "شكرًا لتسوّقك معانا ❤️")}</p>
              </div>
            </div>
          )}

          {/* CTAs */}
          <div className="flex flex-col sm:flex-row gap-2.5 sm:gap-3">
            {showTrackOrder && (
              <Link
                to={`/track?tn=${orderNumber}`}
                className="bz-btn flex-1 rounded-full inline-flex items-center justify-center gap-2 py-3 sm:py-3.5 text-xs"
              >
                <Package size={16} />{" "}
                <InlineEditable sectionId={sectionId} settingKey="track_order_text" value={trackOrderText} />
              </Link>
            )}
            <Link
              to={continueLink}
              className="bz-btn bz-btn-filled flex-1 rounded-full inline-flex items-center justify-center gap-2 py-3 sm:py-3.5 text-xs rtl:[&>svg]:rotate-180"
            >
              <InlineEditable sectionId={sectionId} settingKey="continue_shopping_text" value={continueText} />{" "}
              <ArrowLeft size={14} />
            </Link>
          </div>
        </motion.div>
      </div>
    </div>
  );
}
