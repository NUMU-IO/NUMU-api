"use client";

import {
  Link,
  Money,
  useCart,
  useLocale,
  useResolvedSettings,
  type CartItem,
} from "@numueg/theme-sdk";
import { motion } from "framer-motion";
import { ArrowRight, Lock, Minus, Plus, ShoppingBag, Trash2 } from "lucide-react";
import { asNumber, asString, localized, productHref, type SectionRenderProps } from "./_shared";
import { InlineEditable } from "./_inline-editable";

/**
 * gilded-cart — faithful V3 port of the V2 GildedCartPage
 * (numu-egyptian-bazaar/src/components/store/gilded-glamour-boutique/GildedCartPage.tsx).
 *
 * Two states, kept VERBATIM from V2:
 *  - EMPTY: `min-h-[60vh] flex items-center justify-center`, a ShoppingBag glyph
 *    inside a `rounded-full border border-[var(--gilded-gold)]/30` ring, an
 *    uppercase tracked eyebrow, an uppercase `gld-heading` headline, and the gold
 *    `gld-btn` "Continue shopping" → /products (ArrowRight, RTL-mirrored).
 *  - FULL: breadcrumb "Home / Cart"; Montserrat title `text-3xl md:text-5xl`
 *    `gld-heading` with an item-count eyebrow; a `grid lg:grid-cols-3 gap-10`
 *    layout — line items (`lg:col-span-2 divide-y`, each row `flex gap-5 py-6`,
 *    `w-28 h-36 object-cover bg-muted` image, uppercase name, variant eyebrow,
 *    gold price, a `border w-9 h-9` qty stepper, and a text "Remove" link) plus a
 *    sticky `border p-6 bg-card sticky top-24` summary (subtotal, shipping
 *    "Calculated at checkout", optional free-ship hint, a `border-t font-bold`
 *    Total, the gold `gld-btn` checkout with a Lock icon → /checkout, and a
 *    "Continue shopping" link).
 *
 * Shipping is HONEST: no fabricated flat fee at the cart. Shipping shows as FREE
 * only when the merchant's real free-ship threshold (0 = none) is met; otherwise
 * "Calculated at checkout" (the platform checkout computes the real, address-
 * derived rate). The cart total therefore reflects the items subtotal.
 *
 * Engine-wired: `useResolvedSettings` (NEVER raw instance.settings) for resolved
 * globals/dynamic-sources/draft preview, `useCart` (cart money is MAJOR units —
 * rendered via SDK `<Money>`, NEVER /100), `useLocale` for bilingual defaults,
 * `InlineEditable` on every section-level text. The brand gold reads
 * `--gilded-gold` via arbitrary utilities so the merchant's Accent picker
 * repaints it; default colours are byte-identical to V2.
 */
export default function GildedCart({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const { cart, updateQuantity, removeItem, loading } = useCart();
  const locale = useLocale();

  // ── Editable copy (merchant value wins; bilingual locale-aware defaults) ──
  const homeLabel = asString(s.home_label) || localized(locale, "Home", "الرئيسية");
  const cartLabel = asString(s.cart_label) || localized(locale, "Cart", "السلة");
  const title = asString(s.title) || localized(locale, "Your Cart", "سلة التسوق");

  const emptyEyebrow =
    asString(s.empty_eyebrow) ||
    localized(locale, "Your cart is empty", "سلتك فارغة");
  const emptyHeadline =
    asString(s.empty_headline) || localized(locale, "Nothing Here Yet", "لا يوجد شيء بعد");
  const emptyCtaLabel =
    asString(s.empty_cta_label) ||
    localized(locale, "Explore the Collection", "استكشف المجموعة");
  const continueLabel =
    asString(s.continue_label) || localized(locale, "Continue Shopping", "متابعة التسوق");
  const continueHref = asString(s.continue_href) || "/products";

  const itemSingularLabel =
    asString(s.item_singular_label) || localized(locale, "Curated Piece", "قطعة مختارة");
  const itemPluralLabel =
    asString(s.item_plural_label) || localized(locale, "Curated Pieces", "قطع مختارة");

  const summaryLabel =
    asString(s.summary_label) || localized(locale, "Order Summary", "ملخص الطلب");
  const subtotalLabel =
    asString(s.subtotal_label) || localized(locale, "Subtotal", "الإجمالي الفرعي");
  const shippingLabel = asString(s.shipping_label) || localized(locale, "Shipping", "الشحن");
  const shippingCalcLabel =
    asString(s.shipping_calc_label) ||
    localized(locale, "Calculated at checkout", "يُحسب عند الدفع");
  const freeLabel = asString(s.free_label) || localized(locale, "Free", "مجاني");
  const totalLabel = asString(s.total_label) || localized(locale, "Total", "الإجمالي");
  const checkoutLabel =
    asString(s.checkout_label) || localized(locale, "Secure Checkout", "دفع آمن");
  const removeLabel = asString(s.remove_label) || localized(locale, "Remove", "إزالة");

  // No fabricated flat shipping fee at the cart. FREE only when the merchant's
  // real free-ship threshold (0 = none) is met; otherwise "calculated at
  // checkout". The grand total reflects the items subtotal — the platform
  // checkout adds the real, address-derived rate.
  const freeThreshold = asNumber(s.free_shipping_threshold, 0);

  const items: CartItem[] = cart?.items ?? [];
  const currency = cart?.currency;
  const isEmpty = items.length === 0;
  const totalItems = items.reduce((n, it) => n + it.quantity, 0);

  // ── EMPTY STATE ──────────────────────────────────────────────────────────
  if (isEmpty) {
    return (
      <section
        className="min-h-[60vh] bg-background flex items-center justify-center"
        data-gilded-section={sectionId}
      >
        <div className="text-center px-6">
          <div className="w-16 h-16 mx-auto mb-6 rounded-full border border-[var(--gilded-gold)]/30 flex items-center justify-center">
            <ShoppingBag
              size={22}
              className="text-[var(--gilded-gold)]"
              aria-hidden="true"
            />
          </div>
          <p className="text-xs font-semibold tracking-[0.25em] uppercase text-muted-foreground mb-2">
            <InlineEditable
              sectionId={sectionId}
              settingKey="empty_eyebrow"
              value={emptyEyebrow}
            />
          </p>
          <h1 className="gld-heading text-3xl md:text-4xl font-bold tracking-[0.08em] uppercase text-foreground mb-6">
            <InlineEditable
              sectionId={sectionId}
              settingKey="empty_headline"
              value={emptyHeadline}
            />
          </h1>
          <Link
            to={continueHref}
            className="gld-btn inline-flex items-center gap-2 px-8 py-3 text-xs tracking-[0.15em]"
          >
            <InlineEditable
              sectionId={sectionId}
              settingKey="empty_cta_label"
              value={emptyCtaLabel}
            />
            <ArrowRight size={12} className="rtl:-scale-x-100" aria-hidden="true" />
          </Link>
        </div>
      </section>
    );
  }

  // ── FULL STATE ───────────────────────────────────────────────────────────
  const subtotal = cart?.subtotal ?? 0;
  const freeShipEarned = freeThreshold > 0 && subtotal >= freeThreshold;
  // Shipping is added in the platform checkout (needs an address); the cart
  // total reflects the items subtotal so we never show a fabricated figure.
  const grandTotal = subtotal;

  return (
    <section className="min-h-screen bg-background" data-gilded-section={sectionId}>
      <div className="container mx-auto px-4 py-12 md:py-16">
        {/* Breadcrumb */}
        <nav className="text-[10px] tracking-[0.25em] uppercase text-muted-foreground mb-6">
          <Link to="/" className="hover:text-[var(--gilded-gold)] transition-colors">
            <InlineEditable
              sectionId={sectionId}
              settingKey="home_label"
              value={homeLabel}
            />
          </Link>
          <span className="mx-3">/</span>
          <span className="text-foreground">
            <InlineEditable
              sectionId={sectionId}
              settingKey="cart_label"
              value={cartLabel}
            />
          </span>
        </nav>

        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
          className="mb-10"
        >
          <h1 className="gld-heading text-3xl md:text-5xl font-bold tracking-[0.08em] uppercase text-foreground">
            <InlineEditable sectionId={sectionId} settingKey="title" value={title} />
          </h1>
          <p className="text-xs tracking-[0.2em] uppercase text-muted-foreground mt-2">
            {totalItems}{" "}
            {totalItems === 1 ? itemSingularLabel : itemPluralLabel}
          </p>
        </motion.div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-10">
          {/* Items */}
          <div className="lg:col-span-2 divide-y divide-border">
            {items.map((item, idx) => (
              <motion.div
                key={item.id}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.4, delay: idx * 0.05 }}
                className="flex gap-5 py-6"
              >
                <Link to={productHref(item.product_id)} className="shrink-0">
                  {item.image_url ? (
                    <img
                      src={item.image_url}
                      alt={item.name}
                      className="w-28 h-36 sm:w-32 sm:h-40 object-cover bg-muted"
                      loading="lazy"
                    />
                  ) : (
                    <div className="w-28 h-36 sm:w-32 sm:h-40 bg-muted flex items-center justify-center">
                      <ShoppingBag
                        size={22}
                        className="text-muted-foreground"
                        aria-hidden="true"
                      />
                    </div>
                  )}
                </Link>
                <div className="flex-1 min-w-0 flex flex-col">
                  <Link to={productHref(item.product_id)}>
                    <h3 className="text-sm md:text-base font-semibold tracking-[0.08em] uppercase truncate hover:text-[var(--gilded-gold)] transition-colors">
                      {item.name}
                    </h3>
                  </Link>
                  {item.variant_name && (
                    <p className="text-[10px] tracking-[0.2em] uppercase text-muted-foreground mt-2">
                      {item.variant_name}
                    </p>
                  )}
                  <p className="text-base font-semibold text-[var(--gilded-gold)] mt-2">
                    <Money amount={item.price * item.quantity} currency={currency} />
                  </p>
                  <div className="flex items-end justify-between mt-auto pt-4">
                    <div className="flex items-center border border-border">
                      <button
                        type="button"
                        onClick={() => updateQuantity(item.id, item.quantity - 1)}
                        disabled={loading}
                        aria-label={localized(locale, "Decrease quantity", "إنقاص الكمية")}
                        className="w-9 h-9 flex items-center justify-center text-muted-foreground hover:text-[var(--gilded-gold)] transition-colors disabled:opacity-40"
                      >
                        <Minus size={13} aria-hidden="true" />
                      </button>
                      <span className="text-xs font-semibold w-10 text-center tracking-widest">
                        {item.quantity}
                      </span>
                      <button
                        type="button"
                        onClick={() => updateQuantity(item.id, item.quantity + 1)}
                        disabled={loading}
                        aria-label={localized(locale, "Increase quantity", "زيادة الكمية")}
                        className="w-9 h-9 flex items-center justify-center text-muted-foreground hover:text-[var(--gilded-gold)] transition-colors disabled:opacity-40"
                      >
                        <Plus size={13} aria-hidden="true" />
                      </button>
                    </div>
                    <button
                      type="button"
                      onClick={() => removeItem(item.id)}
                      disabled={loading}
                      aria-label={removeLabel}
                      className="text-[10px] tracking-[0.2em] uppercase text-muted-foreground hover:text-foreground transition-colors flex items-center gap-1.5 disabled:opacity-40"
                    >
                      <Trash2 size={12} aria-hidden="true" />
                      {removeLabel}
                    </button>
                  </div>
                </div>
              </motion.div>
            ))}
          </div>

          {/* Summary */}
          <div>
            <div className="sticky top-24 border border-border p-6 bg-card">
              <h2 className="text-[10px] tracking-[0.25em] uppercase text-muted-foreground mb-5 pb-3 border-b border-border">
                <InlineEditable
                  sectionId={sectionId}
                  settingKey="summary_label"
                  value={summaryLabel}
                />
              </h2>
              <div className="space-y-3 text-sm">
                <div className="flex justify-between">
                  <span className="text-muted-foreground tracking-wide">
                    <InlineEditable
                      sectionId={sectionId}
                      settingKey="subtotal_label"
                      value={subtotalLabel}
                    />
                  </span>
                  <span className="font-semibold">
                    <Money amount={subtotal} currency={currency} />
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground tracking-wide">
                    <InlineEditable
                      sectionId={sectionId}
                      settingKey="shipping_label"
                      value={shippingLabel}
                    />
                  </span>
                  {freeShipEarned ? (
                    <span className="font-semibold text-[var(--gilded-gold)] uppercase tracking-wide text-xs">
                      <InlineEditable
                        sectionId={sectionId}
                        settingKey="free_label"
                        value={freeLabel}
                      />
                    </span>
                  ) : (
                    <span className="font-semibold text-muted-foreground text-xs">
                      <InlineEditable
                        sectionId={sectionId}
                        settingKey="shipping_calc_label"
                        value={shippingCalcLabel}
                      />
                    </span>
                  )}
                </div>
                <div className="flex justify-between font-bold text-base pt-4 border-t border-border">
                  <span className="tracking-[0.1em] uppercase">
                    <InlineEditable
                      sectionId={sectionId}
                      settingKey="total_label"
                      value={totalLabel}
                    />
                  </span>
                  <span>
                    <Money amount={grandTotal} currency={currency} />
                  </span>
                </div>
              </div>
              <Link
                to="/checkout"
                className="gld-btn mt-6 w-full py-3.5 text-xs tracking-[0.2em] flex items-center justify-center gap-2"
                data-testid="cart-page-checkout"
              >
                <Lock size={12} aria-hidden="true" />
                <InlineEditable
                  sectionId={sectionId}
                  settingKey="checkout_label"
                  value={checkoutLabel}
                />
              </Link>
              <Link
                to={continueHref}
                className="mt-4 w-full text-center text-[10px] tracking-[0.2em] uppercase text-muted-foreground hover:text-[var(--gilded-gold)] block transition-colors"
              >
                <InlineEditable
                  sectionId={sectionId}
                  settingKey="continue_label"
                  value={continueLabel}
                />
              </Link>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
