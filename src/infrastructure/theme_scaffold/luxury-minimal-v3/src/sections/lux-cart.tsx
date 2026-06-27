"use client";

import {
  Link,
  Money,
  useCart,
  useLocale,
  useResolvedSettings,
  type CartItem,
} from "@numueg/theme-sdk";
import { Minus, Plus, ShoppingCart, X } from "lucide-react";
import { asNumber, asString, localized, type SectionRenderProps } from "./_shared";
import { InlineEditable } from "./_inline-editable";

/**
 * lux-cart — the cart template body for Luxury Minimal V3. There was NO V2 cart
 * PAGE (V2 only shipped a cart DRAWER), so this is net-new but rendered in the
 * theme's faithful visual language: white canvas, near-black ink, SHARP edges
 * (radius 0), hairline 1px borders, uppercase wide-tracked `lux-heading`, and
 * the solid-black `lux-btn`. The structure mirrors bazar's bz-cart (empty +
 * populated states, qty steppers, sticky summary) but every bazar class is
 * replaced with the Luxury Minimal vocabulary lifted from the V2 cart-drawer
 * spec (`w-20 h-24 object-cover bg-[hsl(var(--lux-gray))]`, the `w-6 h-6 border
 * border-border ... hover:border-foreground` stepper, `lux-separator` divider).
 *
 * Shipping is honest: no fabricated flat fee at the cart — FREE when the
 * merchant's real free-ship threshold is met, otherwise "Calculated at
 * checkout" (the platform checkout computes the real rate from the address).
 * Engine-wired: useResolvedSettings + InlineEditable on every label.
 */
export default function LuxCart({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const { cart, updateQuantity, removeItem, loading } = useCart();
  const locale = useLocale();

  const emptyHeadline =
    asString(s.empty_headline) || localized(locale, "Your cart is empty", "سلتك فاضية");
  const emptySubhead =
    asString(s.empty_subhead) ||
    localized(
      locale,
      "You haven't added anything yet. Explore the collection.",
      "لسه مضفتش حاجة. اكتشف التشكيلة.",
    );
  const emptyCta =
    asString(s.empty_cta_label) || localized(locale, "Continue shopping", "كمّل تسوّق");
  const emptyCtaHref = asString(s.empty_cta_href) || "/products";

  const populatedTitle = asString(s.populated_title) || localized(locale, "Cart", "السلة");
  const checkoutCta =
    asString(s.checkout_cta_label) || localized(locale, "Checkout", "إتمام الشراء");
  const subtotalLabel =
    asString(s.subtotal_label) || localized(locale, "Subtotal", "الإجمالي الفرعي");
  const shippingLabel = asString(s.shipping_label) || localized(locale, "Shipping", "الشحن");
  const totalLabel = asString(s.total_label) || localized(locale, "Total", "الإجمالي");
  const freeLabel = asString(s.free_label) || localized(locale, "Free", "مجاني");
  const shippingCalcLabel =
    asString(s.shipping_calc_label) ||
    localized(locale, "Calculated at checkout", "يُحسب عند الدفع");

  // No fabricated flat shipping fee at the cart. FREE only when the merchant's
  // real free-ship threshold (0 = none) is met; otherwise "calculated at
  // checkout". The grand total reflects the items subtotal — the platform
  // checkout adds the real, address-derived rate.
  const freeThreshold = asNumber(s.free_shipping_threshold, 0);

  const items: CartItem[] = cart?.items ?? [];
  const currency = cart?.currency;
  const isEmpty = items.length === 0;
  const totalItems = items.reduce((n, it) => n + it.quantity, 0);

  if (isEmpty) {
    return (
      <section
        className="min-h-[70vh] bg-background flex items-center justify-center"
        data-lux-section={sectionId}
      >
        <div className="text-center px-6 py-20">
          <ShoppingCart
            size={28}
            className="mx-auto mb-6 text-muted-foreground"
            aria-hidden="true"
          />
          <h1 className="lux-heading text-2xl md:text-3xl text-foreground mb-4">
            <InlineEditable
              sectionId={sectionId}
              settingKey="empty_headline"
              value={emptyHeadline}
            />
          </h1>
          <p className="text-muted-foreground text-sm leading-relaxed mb-8 max-w-sm mx-auto">
            <InlineEditable
              sectionId={sectionId}
              settingKey="empty_subhead"
              value={emptySubhead}
              multiline
            />
          </p>
          <Link to={emptyCtaHref} className="inline-flex items-center lux-btn">
            <InlineEditable
              sectionId={sectionId}
              settingKey="empty_cta_label"
              value={emptyCta}
            />
          </Link>
        </div>
      </section>
    );
  }

  const subtotal = cart?.subtotal ?? 0;
  const freeShipEarned = freeThreshold > 0 && subtotal >= freeThreshold;
  // Shipping is added in the platform checkout (needs an address); the cart
  // total reflects the items subtotal so we never show a fabricated figure.
  const grandTotal = subtotal;

  return (
    <section className="min-h-[70vh] bg-background" data-lux-section={sectionId}>
      <div className="container mx-auto px-4 py-12 md:py-16">
        <h1 className="lux-heading text-2xl md:text-3xl text-foreground mb-10">
          <InlineEditable
            sectionId={sectionId}
            settingKey="populated_title"
            value={populatedTitle}
          />{" "}
          ({totalItems})
        </h1>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-10">
          {/* Line items */}
          <div className="lg:col-span-2 space-y-6">
            {items.map((it) => (
              <div
                key={it.id}
                className="flex gap-4 pb-6 border-b border-border last:border-b-0"
              >
                <div className="shrink-0">
                  {it.image_url ? (
                    <img
                      src={it.image_url}
                      alt={it.name}
                      className="w-20 h-24 object-cover bg-[hsl(var(--lux-gray))]"
                    />
                  ) : (
                    <div className="w-20 h-24 bg-[hsl(var(--lux-gray))] flex items-center justify-center">
                      <ShoppingCart
                        size={20}
                        className="text-muted-foreground"
                        aria-hidden="true"
                      />
                    </div>
                  )}
                </div>
                <div className="flex-1 min-w-0 flex flex-col">
                  <p className="text-sm text-foreground truncate">{it.name}</p>
                  {it.variant_name && (
                    <p className="text-[10px] uppercase tracking-[0.15em] text-muted-foreground mt-1">
                      {it.variant_name}
                    </p>
                  )}
                  <p className="text-sm text-foreground mt-2">
                    <Money amount={it.price} currency={currency} />
                  </p>
                  <div className="flex items-end justify-between mt-auto pt-3">
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => updateQuantity(it.id, it.quantity - 1)}
                        disabled={loading}
                        aria-label="Decrease quantity"
                        className="w-6 h-6 border border-border flex items-center justify-center hover:border-foreground transition-colors disabled:opacity-40"
                      >
                        <Minus size={10} aria-hidden="true" />
                      </button>
                      <span className="text-xs w-5 text-center text-foreground">
                        {it.quantity}
                      </span>
                      <button
                        type="button"
                        onClick={() => updateQuantity(it.id, it.quantity + 1)}
                        disabled={loading}
                        aria-label="Increase quantity"
                        className="w-6 h-6 border border-border flex items-center justify-center hover:border-foreground transition-colors disabled:opacity-40"
                      >
                        <Plus size={10} aria-hidden="true" />
                      </button>
                    </div>
                    <button
                      type="button"
                      onClick={() => removeItem(it.id)}
                      disabled={loading}
                      aria-label="Remove item"
                      className="text-muted-foreground hover:text-foreground transition-colors disabled:opacity-40"
                    >
                      <X size={12} aria-hidden="true" />
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>

          {/* Summary */}
          <div>
            <div className="sticky top-24 border border-border p-6">
              <div className="space-y-3 text-sm">
                <div className="flex justify-between text-foreground">
                  <span className="text-muted-foreground">
                    <InlineEditable
                      sectionId={sectionId}
                      settingKey="subtotal_label"
                      value={subtotalLabel}
                    />
                  </span>
                  <span>
                    <Money amount={subtotal} currency={currency} />
                  </span>
                </div>
                <div className="flex justify-between text-foreground">
                  <span className="text-muted-foreground">
                    <InlineEditable
                      sectionId={sectionId}
                      settingKey="shipping_label"
                      value={shippingLabel}
                    />
                  </span>
                  <span>
                    {freeShipEarned ? (
                      <span className="lux-gold">
                        <InlineEditable
                          sectionId={sectionId}
                          settingKey="free_label"
                          value={freeLabel}
                        />
                      </span>
                    ) : (
                      <span className="text-muted-foreground text-xs">
                        <InlineEditable
                          sectionId={sectionId}
                          settingKey="shipping_calc_label"
                          value={shippingCalcLabel}
                        />
                      </span>
                    )}
                  </span>
                </div>
                <div className="lux-separator my-4" />
                <div className="flex justify-between items-center">
                  <span className="lux-heading text-sm text-foreground">
                    <InlineEditable
                      sectionId={sectionId}
                      settingKey="total_label"
                      value={totalLabel}
                    />
                  </span>
                  <span className="lux-heading text-base text-foreground">
                    <Money amount={grandTotal} currency={currency} />
                  </span>
                </div>
              </div>
              <Link
                to="/checkout"
                className="block w-full mt-6 py-3 text-center lux-btn"
              >
                <InlineEditable
                  sectionId={sectionId}
                  settingKey="checkout_cta_label"
                  value={checkoutCta}
                />
              </Link>
              <Link
                to={emptyCtaHref}
                className="mt-4 block text-center lux-nav-link"
              >
                <InlineEditable
                  sectionId={sectionId}
                  settingKey="empty_cta_label"
                  value={emptyCta}
                />
              </Link>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
