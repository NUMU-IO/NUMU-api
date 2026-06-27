"use client";

import { useState } from "react";
import { Link, useLocale, useOrders, useResolvedSettings } from "@numueg/theme-sdk";
import { ArrowLeft, Check, Copy, Package, ShieldCheck } from "lucide-react";
import { motion } from "framer-motion";
import {
  asBool,
  asString,
  localized,
  readBlocks,
  type SectionRenderProps,
} from "./_shared";
import { InlineEditable } from "./_inline-editable";

/**
 * lux-order-confirmation — faithful V3 port of the V2
 * LuxOrderConfirmationSection (numu-egyptian-bazaar/src/themes/luxury-minimal/
 * sections/order-confirmation/LuxOrderConfirmationSection.tsx via
 * BaseOrderConfirmationPage).
 *
 * Centered max-w-md confirmation: a bordered 64px success box with a Check
 * (opacity 0→1, duration 0.8), a 10px/0.3em eyebrow ("Thank you for your
 * order"), an uppercase `lux-heading` title, a hairline-bordered order card
 * (mono order # + copy button, lux-separator, date + status rows), and a solid
 * `lux-btn` continue-shopping CTA with a trailing ArrowLeft. All V2 className
 * strings kept verbatim. Engine-wired: useResolvedSettings + InlineEditable on
 * every static text node.
 *
 * Data: most-recent order from `useOrders()` (the just-placed order). When no
 * order is available (anonymous / editor preview) the layout still renders with
 * a placeholder number — never blank, never redirects.
 */
export default function LuxOrderConfirmation({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const locale = useLocale();

  const showGuarantees = asBool(s.show_guarantees, false);
  const showProgress = asBool(s.show_progress, false);
  const showTrackOrder = asBool(s.show_track_order, false);

  const prefixText =
    asString(s.prefix_text) || localized(locale, "Thank you for your order", "شكراً لطلبك");
  const titleText =
    asString(s.title_text) || localized(locale, "Order Confirmation", "تأكيد الطلب");
  const ctaText = asString(s.cta_text) || localized(locale, "Continue Shopping", "متابعة التسوق");
  const ctaLink = asString(s.cta_link) || "/products";

  const { orders } = useOrders();
  const order = orders?.[0];
  const orderNumber = order?.order_number ?? "NUM-000000";

  // Order date — formatted for the active locale; falls back to today.
  const rawDate = order?.created_at ?? null;
  let orderDate = "";
  try {
    const d = rawDate ? new Date(rawDate) : new Date();
    if (!Number.isNaN(d.getTime())) {
      orderDate = d.toLocaleDateString(locale?.toLowerCase().startsWith("ar") ? "ar-EG" : "en-US", {
        year: "numeric",
        month: "short",
        day: "numeric",
      });
    }
  } catch {
    orderDate = "";
  }

  const statusLabel =
    asString(order?.status) || localized(locale, "Processing", "قيد التجهيز");

  const [copied, setCopied] = useState(false);
  const handleCopy = () => {
    if (typeof navigator !== "undefined" && navigator.clipboard) {
      navigator.clipboard.writeText(orderNumber).catch(() => {});
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  // Repeatable progress steps + guarantee rows authored in the editor.
  const stepBlocks = readBlocks(instance, "step");
  const steps =
    stepBlocks.length > 0
      ? stepBlocks
      : [
          { label: localized(locale, "Ordered", "تم الطلب") },
          { label: localized(locale, "Processing", "قيد التجهيز") },
          { label: localized(locale, "On the way", "في الطريق") },
          { label: localized(locale, "Delivered", "تم التوصيل") },
        ];

  const guarantees = readBlocks(instance, "guarantee");

  return (
    <div
      className="container mx-auto px-4 py-20 max-w-md text-center"
      data-lux-section={sectionId}
    >
      {/* Success icon */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.8 }}
        className="w-16 h-16 flex items-center justify-center mx-auto mb-8 border border-foreground"
      >
        <Check size={28} aria-hidden="true" />
      </motion.div>

      {/* Prefix eyebrow */}
      <p className="text-[10px] uppercase tracking-[0.3em] text-muted-foreground mb-3">
        <InlineEditable sectionId={sectionId} settingKey="prefix_text" value={prefixText} />
      </p>

      {/* Title */}
      <h1 className="lux-heading text-xl mb-8">
        <InlineEditable sectionId={sectionId} settingKey="title_text" value={titleText} />
      </h1>

      {/* Order detail card */}
      <div className="border border-border p-6 mb-8 text-right">
        {/* Order number */}
        <div className="flex items-center justify-between mb-3">
          <p className="text-xs text-muted-foreground">
            {localized(locale, "Order #", "رقم الطلب")}
          </p>
          <div className="flex items-center gap-2">
            <p className="font-mono text-xs text-foreground" dir="ltr">
              {orderNumber}
            </p>
            <button
              onClick={handleCopy}
              className="p-1 hover:opacity-50 transition-opacity"
              title={localized(locale, "Copy", "نسخ")}
              aria-label={localized(locale, "Copy order number", "نسخ رقم الطلب")}
            >
              {copied ? (
                <Check size={12} className="text-[hsl(var(--success))]" aria-hidden="true" />
              ) : (
                <Copy size={12} className="text-muted-foreground" aria-hidden="true" />
              )}
            </button>
          </div>
        </div>

        <div className="lux-separator mb-3" />

        {/* Date */}
        {orderDate && (
          <div className="flex justify-between text-xs mb-2">
            <span className="text-muted-foreground">{localized(locale, "Date", "التاريخ")}</span>
            <span className="text-foreground">{orderDate}</span>
          </div>
        )}

        {/* Status */}
        <div className="flex justify-between text-xs mb-2">
          <span className="text-muted-foreground">{localized(locale, "Status", "الحالة")}</span>
          <span className="flex items-center gap-1.5 text-xs text-foreground">
            <Package size={14} aria-hidden="true" /> {statusLabel}
          </span>
        </div>

        {/* Progress tracker */}
        {showProgress && (
          <div className="mt-6 pt-6 border-t border-border">
            <div className="flex items-center justify-between">
              {steps.map((step, i) => {
                const label = asString(step.label) || `${i + 1}`;
                return (
                  <div key={i} className="flex flex-col items-center flex-1">
                    <div
                      className={
                        i <= 1
                          ? "w-7 h-7 flex items-center justify-center text-[10px] font-medium mb-1.5 bg-foreground text-background"
                          : "w-7 h-7 flex items-center justify-center text-[10px] font-medium mb-1.5 bg-background text-muted-foreground border border-border"
                      }
                    >
                      {i <= 1 ? <Check size={10} aria-hidden="true" /> : i + 1}
                    </div>
                    <span className="text-[9px] uppercase tracking-[0.15em] text-muted-foreground text-center px-1">
                      {label}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {/* Guarantees */}
      {showGuarantees && guarantees.length > 0 && (
        <div className="border border-border p-6 mb-8 space-y-3 text-start">
          {guarantees.map((g, i) => (
            <div key={i} className="flex items-center gap-3">
              <ShieldCheck
                size={16}
                className="lux-gold flex-shrink-0"
                aria-hidden="true"
              />
              <span className="text-xs text-muted-foreground leading-relaxed">
                {asString(g.label) || localized(locale, "Secure & guaranteed", "آمن ومضمون")}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* CTAs */}
      <div className="flex flex-col sm:flex-row gap-3 justify-center">
        {showTrackOrder && (
          <Link
            to={`/track?tn=${orderNumber}`}
            className="lux-btn-outline flex-1 inline-flex items-center justify-center gap-2"
          >
            <Package size={14} aria-hidden="true" />{" "}
            {localized(locale, "Track Order", "تتبع الطلب")}
          </Link>
        )}
        <Link to={ctaLink} className="inline-flex items-center gap-2 lux-btn justify-center">
          <InlineEditable sectionId={sectionId} settingKey="cta_text" value={ctaText} />
          <ArrowLeft size={12} aria-hidden="true" className="rtl:-scale-x-100" />
        </Link>
      </div>
    </div>
  );
}
