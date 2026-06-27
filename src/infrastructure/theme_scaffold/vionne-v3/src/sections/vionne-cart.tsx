"use client";

import {
  Link,
  Money,
  useCart,
  useLocale,
  useResolvedSettings,
  type CartItem,
} from "@numueg/theme-sdk";
import { ArrowRight, Minus, Plus, ShoppingBag, X } from "lucide-react";
import { asNumber, asString, localized, type SectionRenderProps } from "./_shared";
import { InlineEditable } from "./_inline-editable";

/**
 * vionne-cart — the cart template body.
 *
 * Vionne shipped only a header+footer stub `cart` template (no body section),
 * so the cart page rendered blank between the chrome. This is the missing body:
 * the grayscale-and-gold editorial cart, in Vionne's `vn-*` design language —
 * an empty state (outlined bag + CTA) and a populated state (line items with
 * qty steppers + remove, plus a sticky summary with subtotal / shipping /
 * total + a checkout CTA). Data + actions are SDK-native via useCart(); copy is
 * merchant-editable (InlineEditable) and bilingual (localized defaults).
 *
 * Shipping mirrors the platform rule (bz-cart parity): the real rate needs an
 * address (only known in checkout), so the cart shows FREE when the merchant's
 * real free-ship threshold is met, otherwise "calculated at checkout" — never a
 * fabricated figure. The cart total is the items subtotal.
 */
export default function VionneCart({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const { cart, updateQuantity, removeItem, loading } = useCart();
  const locale = useLocale();

  const emptyHeadline =
    asString(s.empty_headline) || localized(locale, "YOUR CART IS EMPTY", "سلتك فاضية");
  const emptySubhead =
    asString(s.empty_subhead) ||
    localized(locale, "Nothing here yet — let's find something you'll love.", "لسه مفيش حاجة هنا — تعالي نلاقي حاجة تعجبك.");
  const emptyCta = asString(s.empty_cta_label) || localized(locale, "CONTINUE SHOPPING", "كمّلي تسوّق");
  const emptyCtaHref = asString(s.empty_cta_href) || "/products";

  const populatedTitle = asString(s.populated_title) || localized(locale, "CART", "السلة");
  const checkoutCta = asString(s.checkout_cta_label) || localized(locale, "CHECKOUT", "إتمام الشراء");
  const continueLabel = asString(s.continue_label) || localized(locale, "CONTINUE SHOPPING", "كمّلي تسوّق");
  const subtotalLabel = asString(s.subtotal_label) || localized(locale, "Subtotal", "الإجمالي الفرعي");
  const shippingLabel = asString(s.shipping_label) || localized(locale, "Shipping", "الشحن");
  const totalLabel = asString(s.total_label) || localized(locale, "Total", "الإجمالي");
  const freeLabel = asString(s.free_label) || localized(locale, "Free", "مجاني");
  const shippingCalcLabel =
    asString(s.shipping_calc_label) || localized(locale, "Calculated at checkout", "يُحسب عند الدفع");
  const freeThreshold = asNumber(s.free_shipping_threshold, 0);

  const items: CartItem[] = cart?.items ?? [];
  const currency = cart?.currency;
  const isEmpty = items.length === 0;
  const totalItems = items.reduce((n, it) => n + it.quantity, 0);

  // ── Empty state ──────────────────────────────────────────────────────────
  if (isEmpty) {
    return (
      <section
        className="bg-background min-h-[70vh] flex items-center justify-center"
        data-vn-section={sectionId}
        data-testid="storefront-cart"
      >
        <div className="text-center px-6 py-20">
          <div className="w-16 h-16 mx-auto mb-7 rounded-full border border-[var(--vn-border)] flex items-center justify-center">
            <ShoppingBag size={22} className="text-[var(--vn-muted)]" aria-hidden="true" />
          </div>
          <span className="vn-eyebrow block mb-3 text-[var(--vn-muted)]">
            {localized(locale, "Your bag", "شنطتك")}
          </span>
          <h1 className="vn-heading text-3xl md:text-4xl text-[var(--vn-ink)] mb-4">
            <InlineEditable sectionId={sectionId} settingKey="empty_headline" value={emptyHeadline} />
          </h1>
          <p className="text-sm text-[var(--vn-muted)] mb-8 max-w-sm mx-auto leading-relaxed">
            <InlineEditable sectionId={sectionId} settingKey="empty_subhead" value={emptySubhead} multiline />
          </p>
          <Link to={emptyCtaHref} className="vn-btn vn-btn-filled inline-flex items-center gap-2">
            <InlineEditable sectionId={sectionId} settingKey="empty_cta_label" value={emptyCta} />
            <ArrowRight size={13} aria-hidden="true" className="rtl:rotate-180" />
          </Link>
        </div>
      </section>
    );
  }

  // ── Populated state ──────────────────────────────────────────────────────
  const subtotal = cart?.subtotal ?? 0;
  const freeShipEarned = freeThreshold > 0 && subtotal >= freeThreshold;
  const remainingForFree = freeThreshold > 0 ? Math.max(freeThreshold - subtotal, 0) : 0;
  const grandTotal = subtotal;

  return (
    <section className="bg-background min-h-[70vh]" data-vn-section={sectionId} data-testid="storefront-cart">
      <div className="container mx-auto px-4 py-12 md:py-16">
        <header className="mb-8 md:mb-10">
          <span className="vn-eyebrow block mb-2 text-[var(--vn-muted)]">
            {localized(locale, "Your bag", "شنطتك")}
          </span>
          <h1 className="vn-heading text-3xl md:text-5xl text-[var(--vn-ink)]">
            <InlineEditable sectionId={sectionId} settingKey="populated_title" value={populatedTitle} />
            <span className="text-[var(--vn-muted)]"> ({totalItems})</span>
          </h1>
        </header>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-10 lg:gap-12">
          {/* Items */}
          <ul className="lg:col-span-2 divide-y divide-[var(--vn-border)] border-y border-[var(--vn-border)]">
            {items.map((it) => (
              <li key={it.id} className="flex gap-5 py-5" data-testid="storefront-cart-item">
                <div className="shrink-0">
                  {it.image_url ? (
                    <img
                      src={it.image_url}
                      alt={it.name}
                      className="w-24 h-28 sm:w-28 sm:h-36 object-cover bg-[var(--vn-band)]"
                      loading="lazy"
                    />
                  ) : (
                    <div className="w-24 h-28 sm:w-28 sm:h-36 bg-[var(--vn-band)] flex items-center justify-center">
                      <ShoppingBag size={24} className="text-[var(--vn-muted)]/40" aria-hidden="true" />
                    </div>
                  )}
                </div>
                <div className="flex-1 min-w-0 flex flex-col">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <h3 className="text-sm md:text-base font-medium text-[var(--vn-ink)] line-clamp-2">
                        {it.name}
                      </h3>
                      {it.variant_name && (
                        <p className="vn-label text-[10px] text-[var(--vn-muted)] mt-1.5">
                          {it.variant_name}
                        </p>
                      )}
                    </div>
                    <button
                      type="button"
                      onClick={() => removeItem(it.id)}
                      disabled={loading}
                      aria-label={localized(locale, "Remove item", "إزالة العنصر")}
                      className="shrink-0 p-1 text-[var(--vn-muted)]/60 hover:text-[var(--vn-ink)] transition-colors disabled:opacity-40"
                    >
                      <X size={16} aria-hidden="true" />
                    </button>
                  </div>

                  <div className="flex items-end justify-between mt-auto pt-4">
                    {/* Quantity stepper */}
                    <div className="inline-flex items-center border border-[var(--vn-border)] rounded-full">
                      <button
                        type="button"
                        onClick={() => updateQuantity(it.id, Math.max(1, it.quantity - 1))}
                        disabled={loading}
                        aria-label={localized(locale, "Decrease quantity", "تقليل الكمية")}
                        className="w-9 h-9 flex items-center justify-center text-[var(--vn-muted)] hover:text-[var(--vn-accent)] transition-colors disabled:opacity-40"
                      >
                        <Minus size={13} aria-hidden="true" />
                      </button>
                      <span className="min-w-[2rem] text-center text-sm font-medium tabular-nums text-[var(--vn-ink)]">
                        {it.quantity}
                      </span>
                      <button
                        type="button"
                        onClick={() => updateQuantity(it.id, it.quantity + 1)}
                        disabled={loading}
                        aria-label={localized(locale, "Increase quantity", "زيادة الكمية")}
                        className="w-9 h-9 flex items-center justify-center text-[var(--vn-muted)] hover:text-[var(--vn-accent)] transition-colors disabled:opacity-40"
                      >
                        <Plus size={13} aria-hidden="true" />
                      </button>
                    </div>
                    <span className="text-base font-semibold text-[var(--vn-ink)]">
                      <Money amount={it.price * it.quantity} currency={currency} />
                    </span>
                  </div>
                </div>
              </li>
            ))}
          </ul>

          {/* Summary */}
          <div>
            <div className="lg:sticky lg:top-24 border border-[var(--vn-border)] p-6">
              <h2 className="vn-eyebrow text-[var(--vn-muted)] mb-5">
                {localized(locale, "Order summary", "ملخص الطلب")}
              </h2>
              <div className="space-y-3 text-sm">
                <div className="flex justify-between text-[var(--vn-ink)]">
                  <span className="text-[var(--vn-muted)]">{subtotalLabel}</span>
                  <span className="font-medium">
                    <Money amount={subtotal} currency={currency} />
                  </span>
                </div>
                <div className="flex justify-between text-[var(--vn-ink)]">
                  <span className="text-[var(--vn-muted)]">{shippingLabel}</span>
                  <span className="font-medium">
                    {freeShipEarned ? (
                      <span className="uppercase tracking-wide text-[var(--vn-accent)]">{freeLabel}</span>
                    ) : (
                      <span className="text-[var(--vn-muted)] text-xs">{shippingCalcLabel}</span>
                    )}
                  </span>
                </div>
                {!freeShipEarned && remainingForFree > 0 && (
                  <p className="text-xs text-[var(--vn-accent)]">
                    {localized(locale, "Add ", "ضيفي ")}
                    <Money amount={remainingForFree} currency={currency} />
                    {localized(locale, " more for free shipping", " كمان وتحصلي على شحن مجاني")}
                  </p>
                )}
                <div className="flex justify-between items-baseline pt-4 mt-1 border-t border-[var(--vn-border)]">
                  <span className="vn-heading text-base text-[var(--vn-ink)]">{totalLabel}</span>
                  <span className="vn-heading text-xl text-[var(--vn-ink)]">
                    <Money amount={grandTotal} currency={currency} />
                  </span>
                </div>
              </div>

              <Link
                to="/checkout"
                className="vn-btn vn-btn-filled w-full mt-6 flex items-center justify-center gap-2"
                data-testid="storefront-cart-checkout"
              >
                <InlineEditable sectionId={sectionId} settingKey="checkout_cta_label" value={checkoutCta} />
                <ArrowRight size={13} aria-hidden="true" className="rtl:rotate-180" />
              </Link>
              <Link
                to={emptyCtaHref}
                className="mt-4 block text-center vn-label text-[10px] text-[var(--vn-muted)] hover:text-[var(--vn-accent)] transition-colors"
              >
                <InlineEditable sectionId={sectionId} settingKey="continue_label" value={continueLabel} />
              </Link>
              <p className="mt-4 text-[11px] text-[var(--vn-muted)] leading-relaxed text-center">
                {localized(
                  locale,
                  "Shipping & taxes calculated at checkout.",
                  "الشحن والضرائب بتتحسب عند الدفع.",
                )}
              </p>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
