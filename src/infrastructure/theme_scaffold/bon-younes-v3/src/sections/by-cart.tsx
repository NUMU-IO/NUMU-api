"use client";

import {
  Link,
  useCart,
  useLocale,
  useResolvedSettings,
  useTranslation,
  type CartItem,
} from "@numueg/theme-sdk";
import { asString, localized, type SectionRenderProps } from "./_shared";
import { InlineEditable } from "./_inline-editable";

/**
 * by-cart — the cart template's body. Handles both the empty state
 * (Shopify pattern: no line items → "Your basket is empty / browse the
 * menu") and the populated state (line items list + subtotal +
 * checkout CTA) in one component. We deviate from the Phase 2 brief's
 * "cart-empty-state + cart-items as two sections" suggestion here
 * because Shopify renders them as a single section that switches
 * internally — two separate sections would render in sequence and
 * either both show (broken) or require a sibling-aware conditional
 * (worse).
 */
export default function ByCart({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const cart = useCart();
  // System strings (not section settings) flow through the locale
  // catalog so a merchant can rebrand them in Edit theme content. Keys
  // match locales/<lang>.json (see the theme's locale catalog).
  const { t } = useTranslation();
  const locale = useLocale();

  const headline = asString(s.empty_headline) || localized(locale, "Your basket is empty", "سلتك فاضية");
  const subhead =
    asString(s.empty_subhead) ||
    localized(
      locale,
      "Browse the menu and add a drink — we'll have it ready when you check out.",
      "اتفرّج على المنيو وضيف مشروبك — هيكون جاهز أول ما تكمّل الطلب.",
    );
  const emptyCta = asString(s.empty_cta_label) || localized(locale, "Browse the menu", "اتفرّج على المنيو");
  const emptyCtaHref = asString(s.empty_cta_href) || "/products";
  const populatedTitle = asString(s.populated_title) || localized(locale, "Your basket", "سلتك");
  const checkoutCta = asString(s.checkout_cta_label) || localized(locale, "Continue to checkout", "كمّل للدفع");

  const items: CartItem[] = cart?.items ?? [];
  const isEmpty = items.length === 0;

  if (isEmpty) {
    return (
      <section
        className="by-cart by-cart--empty"
        data-by-section={sectionId}
        style={{
          padding: "4rem 1rem",
          background: "var(--by-cream, #fdf8ee)",
          minHeight: "60vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <div style={{ maxWidth: 480, textAlign: "center" }}>
          <h1
            style={{
              fontFamily: "var(--by-display, 'Playfair Display', serif)",
              fontSize: "clamp(1.75rem, 3vw, 2.5rem)",
              color: "var(--by-espresso, #3a2418)",
              margin: 0,
            }}
          >
            <InlineEditable
              sectionId={sectionId}
              settingKey="empty_headline"
              value={headline}
            />
          </h1>
          <p
            style={{
              color: "rgba(58,36,24,0.7)",
              fontSize: "1rem",
              lineHeight: 1.55,
              marginTop: "0.75rem",
            }}
          >
            <InlineEditable
              sectionId={sectionId}
              settingKey="empty_subhead"
              value={subhead}
              multiline
            />
          </p>
          <Link
            to={emptyCtaHref}
            style={{
              display: "inline-block",
              marginTop: "1.5rem",
              padding: "0.75rem 1.5rem",
              background: "var(--by-espresso, #3a2418)",
              color: "var(--by-cream, #fdf8ee)",
              textDecoration: "none",
              borderRadius: 999,
              fontSize: "0.9rem",
              fontWeight: 600,
              letterSpacing: "0.04em",
              textTransform: "uppercase",
            }}
          >
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
  const currency = cart?.currency ?? "EGP";

  return (
    <section
      className="by-cart by-cart--populated"
      data-by-section={sectionId}
      style={{
        padding: "3rem 1rem",
        background: "var(--by-cream, #fdf8ee)",
        minHeight: "60vh",
      }}
    >
      <div style={{ maxWidth: 800, margin: "0 auto" }}>
        <h1
          style={{
            fontFamily: "var(--by-display, 'Playfair Display', serif)",
            fontSize: "clamp(1.75rem, 3vw, 2.25rem)",
            color: "var(--by-espresso, #3a2418)",
            marginBottom: "1.5rem",
          }}
        >
          <InlineEditable
            sectionId={sectionId}
            settingKey="populated_title"
            value={populatedTitle}
          />
        </h1>
        <ul
          style={{
            listStyle: "none",
            padding: 0,
            margin: 0,
            display: "flex",
            flexDirection: "column",
            gap: "1rem",
          }}
        >
          {items.map((it) => (
            <li
              key={it.id}
              style={{
                display: "flex",
                gap: "1rem",
                padding: "1rem",
                background: "white",
                borderRadius: 12,
                boxShadow: "0 1px 2px rgba(58,36,24,0.05)",
                alignItems: "center",
              }}
            >
              {it.image_url && (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={it.image_url}
                  alt={it.name}
                  style={{
                    width: 72,
                    height: 72,
                    objectFit: "cover",
                    borderRadius: 8,
                    flex: "0 0 auto",
                  }}
                />
              )}
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 600, fontSize: "0.95rem" }}>
                  {it.name}
                </div>
                {it.variant_name && (
                  <div
                    style={{
                      fontSize: "0.8rem",
                      color: "rgba(58,36,24,0.6)",
                    }}
                  >
                    {it.variant_name}
                  </div>
                )}
                <div
                  style={{
                    fontSize: "0.85rem",
                    color: "rgba(58,36,24,0.7)",
                    marginTop: "0.25rem",
                  }}
                >
                  {t("cart.quantity_short", "Qty")} {it.quantity} ·{" "}
                  {it.price * it.quantity} {currency}
                </div>
              </div>
            </li>
          ))}
        </ul>
        <div
          style={{
            marginTop: "1.5rem",
            padding: "1rem 1.25rem",
            background: "white",
            borderRadius: 12,
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            fontSize: "1rem",
            fontWeight: 600,
          }}
        >
          <span>{t("cart.subtotal", "Subtotal")}</span>
          <span>
            {subtotal} {currency}
          </span>
        </div>
        <Link
          to="/checkout"
          style={{
            display: "block",
            marginTop: "1rem",
            padding: "1rem 1.5rem",
            background: "var(--by-espresso, #3a2418)",
            color: "var(--by-cream, #fdf8ee)",
            textDecoration: "none",
            borderRadius: 999,
            textAlign: "center",
            fontSize: "0.95rem",
            fontWeight: 600,
            letterSpacing: "0.04em",
            textTransform: "uppercase",
          }}
        >
          <InlineEditable
            sectionId={sectionId}
            settingKey="checkout_cta_label"
            value={checkoutCta}
          />
        </Link>
      </div>
    </section>
  );
}
