import { useCart, useLocalization } from "@numueg/theme-sdk";
import { EditableText } from "../lib/EditableText";
import type { EmpSectionProps } from "../lib/section";
import { CouponForm } from "../lib/CouponForm";
import { useT } from "../lib/i18n";

interface CartSettings {
  title?: string;
  checkout_label?: string;
  empty_title?: string;
  empty_cta_label?: string;
  empty_cta_link?: string;
}

/** Full-page cart (`/cart`) — mirrors the drawer with more room. */
export default function CartSummary({ id, settings }: EmpSectionProps) {
  const s = settings as CartSettings;
  const t = useT();
  const { cart, updateQuantity, removeItem, loading } = useCart();
  const { formatMoney } = useLocalization();

  const items = cart?.items ?? [];
  const currency = cart?.currency;

  if (items.length === 0) {
    return (
      <section
        className="nt-container"
        style={{ paddingBlock: "5rem", textAlign: "center" }}
      >
        <EditableText
          as="h1"
          className="nt-display-sm"
          sectionId={id}
          settingId="empty_title"
          value={s.empty_title || "السلة فاضية"}
          style={{ marginBottom: "1.5rem" }}
        />
        <a className="nt-btn-outline" href={s.empty_cta_link || "/products"}>
          <EditableText
            as="span"
            sectionId={id}
            settingId="empty_cta_label"
            value={s.empty_cta_label || "متابعة التسوق"}
          />
        </a>
      </section>
    );
  }

  return (
    <section
      className="nt-container"
      style={{ paddingBlock: "2.5rem", maxWidth: "48rem" }}
    >
      <EditableText
        as="h1"
        className="nt-display-sm"
        sectionId={id}
        settingId="title"
        value={s.title || "سلة التسوق"}
        style={{ marginBottom: "1.5rem" }}
      />

      <div
        style={{
          border: "1px solid var(--nt-border)",
          borderRadius: "var(--nt-radius)",
          overflow: "hidden",
          background: "var(--nt-card)",
        }}
      >
        {items.map((line) => (
          <div className="nt-line" key={line.id}>
            <div className="nt-line__img">
              {line.image_url ? (
                <img src={line.image_url} alt={line.name} />
              ) : null}
            </div>
            <div className="nt-line__body">
              <div className="nt-line__top">
                <div>
                  <p className="nt-line__name">{line.name}</p>
                  {line.variant_name ? (
                    <p className="nt-line__variant">{line.variant_name}</p>
                  ) : null}
                </div>
                <p>{formatMoney(line.price * line.quantity, currency)}</p>
              </div>
              <div className="nt-line__controls">
                <div className="nt-qty">
                  <button
                    type="button"
                    aria-label="تقليل"
                    disabled={loading}
                    onClick={() => updateQuantity(line.id, line.quantity - 1)}
                  >
                    −
                  </button>
                  <span>{line.quantity}</span>
                  <button
                    type="button"
                    aria-label="زيادة"
                    disabled={loading}
                    onClick={() => updateQuantity(line.id, line.quantity + 1)}
                  >
                    +
                  </button>
                </div>
                <button
                  className="nt-line__remove"
                  type="button"
                  disabled={loading}
                  onClick={() => removeItem(line.id)}
                >
                  {t("Remove", "حذف")}
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>

      <div className="nt-cart__totals">
        <CouponForm />
        <div className="nt-subtotal">
          <span>{t("Subtotal", "المجموع الفرعي")}</span>
          <span>{formatMoney(cart?.subtotal ?? 0, currency)}</span>
        </div>
        {cart?.discount_amount && cart.discount_amount > 0 ? (
          <div className="nt-subtotal nt-discount">
            <span>
              {t("Discount", "الخصم")}
              {cart.discount_code ? ` (${cart.discount_code})` : ""}
            </span>
            <span>−{formatMoney(cart.discount_amount, currency)}</span>
          </div>
        ) : null}
        <div className="nt-subtotal nt-total">
          <span>{t("Total", "الإجمالي")}</span>
          <span>
            {formatMoney(
              cart?.total ??
                (cart?.subtotal ?? 0) - (cart?.discount_amount ?? 0),
              currency,
            )}
          </span>
        </div>
      </div>

      <div
        style={{
          display: "flex",
          justifyContent: "flex-end",
          marginTop: "1.5rem",
        }}
      >
        <a className="nt-btn" href="/checkout">
          <EditableText
            as="span"
            sectionId={id}
            settingId="checkout_label"
            value={s.checkout_label || t("Checkout", "إتمام الطلب")}
          />
        </a>
      </div>
    </section>
  );
}
