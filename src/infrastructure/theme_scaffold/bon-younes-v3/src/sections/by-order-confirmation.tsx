"use client";

import { useState, type CSSProperties } from "react";
import { Link, useOrders, useLocale, useResolvedSettings } from "@numueg/theme-sdk";
import { Check, Copy, MessageCircle, Package } from "lucide-react";
import { asString, localized, type SectionRenderProps } from "./_shared";
import { InlineEditable } from "./_inline-editable";

/**
 * by-order-confirmation — the post-checkout thank-you page body, in the
 * Bon Younes cafe idiom (cream paper, espresso ink, warm caramel accents,
 * organic rounded shapes, serif headings, pill `.by-btn` CTAs).
 *
 * Ported from the proven luxury-minimal order-confirmation (success icon,
 * order detail card, optional progress tracker / WhatsApp card / track +
 * continue CTAs) and re-skinned to Bon Younes. All four display toggles
 * (show_emoji / show_progress / show_whatsapp / show_track_order) default
 * OFF, matching the reference.
 *
 * Data: most-recent order from `useOrders()` (the just-placed order). When
 * no order is available (anonymous visitor, editor preview, or a direct
 * visit outside the checkout flow) the full static layout still renders
 * with a placeholder order number, gracefully omitting the total — it
 * NEVER blanks out and NEVER redirects away. RTL-safe via logical CSS
 * properties (textAlign:"start"/"end", marginInline*).
 *
 * Money: order totals from useOrders() come back in integer CENTS (the
 * SDK does not normalize them like it does the cart), so we divide by 100
 * and append the EGP/ج.م label — the same convention as by-profile-section.
 */

// ── Shared inline-style fragments (Bon Younes tokens) ─────────────────────
const cream = "var(--by-cream, #f7f1e8)";
const paper = "var(--by-paper, #fffaf2)";
const espresso = "var(--by-espresso, #3a2418)";
const caramel = "var(--by-caramel, #b07a4a)";
const caramelDeep = "var(--by-caramel-deep, #8c5a30)";
const inkMuted = "var(--by-ink-muted, #6b4a36)";
const line = "var(--by-line, rgba(58,36,24,0.14))";
const foam = "var(--by-foam, #f3e5cf)";
const radiusMd = "var(--by-radius-md, 22px)";
const radiusSm = "var(--by-radius-sm, 14px)";
const radiusPill = "var(--by-radius-pill, 9999px)";
const fontSerif = "var(--by-font-serif, 'Cormorant Garamond', 'Playfair Display', serif)";
const fontSans = "var(--by-font-sans, 'DM Sans', system-ui, sans-serif)";

const cardStyle: CSSProperties = {
  background: paper,
  border: `1px solid ${line}`,
  borderRadius: radiusMd,
  boxShadow: "var(--by-shadow-sm, 0 1px 2px rgba(58,36,24,0.06))",
};

const eyebrowStyle: CSSProperties = {
  fontFamily: fontSans,
  fontSize: "0.7rem",
  letterSpacing: "0.28em",
  textTransform: "uppercase",
  color: caramelDeep,
  margin: 0,
};

const rowLabel: CSSProperties = {
  fontSize: "0.78rem",
  color: inkMuted,
  fontFamily: fontSans,
};

const rowValue: CSSProperties = {
  fontSize: "0.9rem",
  color: espresso,
  fontFamily: fontSans,
  fontWeight: 500,
};

export default function ByOrderConfirmation({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const locale = useLocale();

  const showEmoji = s.show_emoji ?? false;
  const showProgress = s.show_progress ?? false;
  const showWhatsApp = s.show_whatsapp ?? false;
  const showTrackOrder = s.show_track_order ?? false;

  const title = asString(s.title) || localized(locale, "Order confirmed", "تم تأكيد الطلب");
  const subtitle =
    asString(s.subtitle) ||
    localized(
      locale,
      "Thank you for your order — we'll send the details over WhatsApp.",
      "شكراً لطلبك — هنبعتلك التفاصيل على واتساب.",
    );
  const continueText = asString(s.continue_shopping_text) || localized(locale, "Back to the menu", "ارجع للمنيو");
  const continueLink = asString(s.continue_shopping_link) || "/products";

  const { orders } = useOrders();
  const order = orders?.[0];
  const orderNumber = order?.order_number ?? "NUM-000000";
  const total = typeof order?.total === "number" ? order.total / 100 : undefined;
  const currencyLabel = localized(locale, "EGP", "ج.م");

  const [copied, setCopied] = useState(false);
  const handleCopy = () => {
    if (typeof navigator !== "undefined" && navigator.clipboard) {
      navigator.clipboard.writeText(orderNumber).catch(() => {});
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const steps = [
    localized(locale, "Ordered", "اتطلب"),
    localized(locale, "Brewing", "بيتحضّر"),
    localized(locale, "On the way", "في الطريق"),
    localized(locale, "Delivered", "اتسلّم"),
  ];

  return (
    <section
      className="by-order-confirmation"
      data-by-section={sectionId}
      style={{
        background: cream,
        minHeight: "60vh",
        display: "flex",
        alignItems: "flex-start",
        justifyContent: "center",
        padding: "clamp(2rem, 6vw, 4rem) 1rem",
      }}
    >
      <div style={{ width: "100%", maxWidth: 560, textAlign: "center" }}>
        {/* Success icon — espresso ring, caramel check */}
        <div
          className="by-oc-pop"
          style={{
            width: 64,
            height: 64,
            borderRadius: radiusPill,
            background: espresso,
            color: cream,
            display: "grid",
            placeItems: "center",
            margin: "0 auto 1.5rem",
            boxShadow: "var(--by-shadow-md, 0 8px 30px rgba(58,36,24,0.10))",
          }}
        >
          <Check size={28} aria-hidden="true" />
        </div>

        {/* Eyebrow + title */}
        <p style={{ ...eyebrowStyle, marginBottom: "0.6rem" }}>
          {localized(locale, "Thank you for your order", "شكراً لطلبك")}
        </p>
        <h1
          style={{
            fontFamily: fontSerif,
            fontWeight: 500,
            fontSize: "clamp(1.8rem, 5vw, 2.6rem)",
            color: espresso,
            margin: "0 0 0.85rem",
            lineHeight: 1.1,
          }}
        >
          <InlineEditable sectionId={sectionId} settingKey="title" value={title} />
          {showEmoji ? " 🎉" : ""}
        </h1>
        <p
          style={{
            color: inkMuted,
            fontSize: "0.98rem",
            lineHeight: 1.6,
            maxWidth: "42ch",
            margin: "0 auto 2rem",
          }}
        >
          <InlineEditable sectionId={sectionId} settingKey="subtitle" value={subtitle} multiline />
        </p>

        {/* Order detail card */}
        <div style={{ ...cardStyle, padding: "1.5rem 1.4rem", marginBottom: "1.5rem", textAlign: "start" }}>
          <div style={{ display: "flex", flexDirection: "column", gap: "0.9rem" }}>
            {/* Order number */}
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "0.75rem" }}>
              <span style={rowLabel}>{localized(locale, "Order number", "رقم الطلب")}</span>
              <span style={{ display: "inline-flex", alignItems: "center", gap: "0.5rem" }}>
                <span
                  style={{
                    fontFamily: "var(--by-mono, 'JetBrains Mono', monospace)",
                    fontWeight: 600,
                    fontSize: "0.85rem",
                    color: espresso,
                  }}
                  dir="ltr"
                >
                  {orderNumber}
                </span>
                <button
                  type="button"
                  onClick={handleCopy}
                  aria-label={localized(locale, "Copy order number", "انسخ رقم الطلب")}
                  style={{
                    border: "none",
                    background: "transparent",
                    color: copied ? caramelDeep : inkMuted,
                    cursor: "pointer",
                    padding: "0.2rem",
                    display: "grid",
                    placeItems: "center",
                  }}
                >
                  {copied ? <Check size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}
                </button>
              </span>
            </div>

            {/* Total */}
            {typeof total === "number" && total > 0 && (
              <>
                <div style={{ height: 1, background: line }} />
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "0.75rem" }}>
                  <span style={rowLabel}>{localized(locale, "Total", "الإجمالي")}</span>
                  <span style={{ ...rowValue, fontFamily: fontSerif, fontSize: "1.05rem", fontWeight: 600 }}>
                    {total.toLocaleString("en-US")} {currencyLabel}
                  </span>
                </div>
              </>
            )}

            {/* Estimated delivery */}
            <div style={{ height: 1, background: line }} />
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "0.75rem" }}>
              <span style={rowLabel}>{localized(locale, "Estimated delivery", "موعد التوصيل")}</span>
              <span style={rowValue}>{localized(locale, "3-5 business days", "3-5 أيام عمل")}</span>
            </div>

            {/* Status */}
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "0.75rem" }}>
              <span style={rowLabel}>{localized(locale, "Status", "الحالة")}</span>
              <span style={{ ...rowValue, display: "inline-flex", alignItems: "center", gap: "0.4rem" }}>
                <Package size={15} style={{ color: caramelDeep }} aria-hidden="true" />
                {localized(locale, "Brewing", "بيتحضّر")}
              </span>
            </div>
          </div>

          {/* Progress tracker */}
          {showProgress && (
            <div style={{ marginTop: "1.4rem", paddingTop: "1.4rem", borderTop: `1px solid ${line}` }}>
              <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between" }}>
                {steps.map((step, i) => (
                  <div
                    key={i}
                    style={{
                      flex: 1,
                      display: "flex",
                      flexDirection: "column",
                      alignItems: "center",
                      gap: "0.45rem",
                    }}
                  >
                    <span
                      style={{
                        width: 28,
                        height: 28,
                        borderRadius: radiusPill,
                        display: "grid",
                        placeItems: "center",
                        fontSize: "0.7rem",
                        fontWeight: 600,
                        background: i <= 1 ? espresso : "transparent",
                        color: i <= 1 ? cream : inkMuted,
                        border: i <= 1 ? "none" : `1px solid ${line}`,
                      }}
                    >
                      {i <= 1 ? <Check size={12} aria-hidden="true" /> : i + 1}
                    </span>
                    <span
                      style={{
                        fontSize: "0.62rem",
                        letterSpacing: "0.1em",
                        textTransform: "uppercase",
                        color: inkMuted,
                        textAlign: "center",
                        padding: "0 0.2rem",
                      }}
                    >
                      {step}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* WhatsApp preview card */}
        {showWhatsApp && (
          <div style={{ ...cardStyle, padding: "1.25rem 1.4rem", marginBottom: "1.5rem", textAlign: "start" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "0.8rem" }}>
              <MessageCircle size={18} style={{ color: "var(--by-whatsapp, #25d366)" }} aria-hidden="true" />
              <span
                style={{
                  fontFamily: fontSans,
                  fontSize: "0.72rem",
                  letterSpacing: "0.16em",
                  textTransform: "uppercase",
                  color: caramelDeep,
                  fontWeight: 600,
                }}
              >
                {localized(locale, "WhatsApp message", "رسالة واتساب")}
              </span>
            </div>
            <div
              style={{
                background: foam,
                borderRadius: radiusSm,
                padding: "0.95rem 1.1rem",
                fontFamily: fontSans,
                fontSize: "0.85rem",
                color: espresso,
                lineHeight: 1.6,
                display: "flex",
                flexDirection: "column",
                gap: "0.3rem",
              }}
            >
              <span>{localized(locale, "Hello! 👋", "أهلاً! 👋")}</span>
              <span>
                {localized(locale, "Your order", "طلبك")}{" "}
                <strong style={{ fontWeight: 600 }} dir="ltr">
                  {orderNumber}
                </strong>{" "}
                {localized(locale, "was placed successfully.", "اتسجّل بنجاح.")}
              </span>
              {typeof total === "number" && total > 0 && (
                <span>
                  {localized(locale, "Total", "الإجمالي")}:{" "}
                  <strong style={{ fontWeight: 600 }}>
                    {total.toLocaleString("en-US")} {currencyLabel}
                  </strong>
                </span>
              )}
              <span>
                {localized(
                  locale,
                  "We'll deliver it within 3-5 business days. For any questions, reach out to us here.",
                  "هنوصّله خلال 3-5 أيام عمل. لو عندك أي سؤال، كلّمنا هنا.",
                )}
              </span>
              <span>{localized(locale, "Thank you for shopping with us ❤️", "شكراً لتسوّقك معانا ❤️")}</span>
            </div>
          </div>
        )}

        {/* CTAs */}
        <div
          className="by-oc-actions"
          style={{ display: "flex", flexDirection: "column", gap: "0.75rem", alignItems: "stretch" }}
        >
          {showTrackOrder && (
            <Link to={`/track?tn=${orderNumber}`} className="by-btn by-btn-ghost" style={{ justifyContent: "center" }}>
              <Package size={16} aria-hidden="true" />
              {localized(locale, "Track order", "تتبّع الطلب")}
            </Link>
          )}
          <Link to={continueLink} className="by-btn" style={{ justifyContent: "center" }}>
            <InlineEditable sectionId={sectionId} settingKey="continue_shopping_text" value={continueText} />
          </Link>
        </div>
      </div>

      {/* Scoped: success-icon pop-in + desktop CTA row. */}
      <style>{`
        @keyframes by-oc-pop {
          0% { transform: scale(0.6); opacity: 0; }
          60% { transform: scale(1.06); }
          100% { transform: scale(1); opacity: 1; }
        }
        .by-order-confirmation .by-oc-pop {
          animation: by-oc-pop 360ms cubic-bezier(0.16, 1, 0.3, 1) both;
        }
        @media (prefers-reduced-motion: reduce) {
          .by-order-confirmation .by-oc-pop { animation: none; }
        }
        @media (min-width: 520px) {
          .by-order-confirmation .by-oc-actions {
            flex-direction: row;
            justify-content: center;
          }
          .by-order-confirmation .by-oc-actions > a { flex: 1; }
        }
      `}</style>
    </section>
  );
}
