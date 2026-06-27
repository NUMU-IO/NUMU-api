import {
  useOrder,
  usePage,
  useLocalization,
} from "@numueg/theme-sdk";
import { EditableText } from "../lib/EditableText";
import type { EmpSectionProps } from "../lib/section";

interface OcSettings {
  title?: string;
  subtitle?: string;
}

/**
 * Order confirmation / thank-you page. The storefront passes the order id via
 * `page.data.order_id` (or the page handle); `useOrder` fetches the detail.
 */
export default function OrderConfirmation({ id, settings }: EmpSectionProps) {
  const s = settings as OcSettings;
  const page = usePage();
  const orderId =
    (page?.data?.order_id as string | undefined) ?? page?.handle ?? null;
  const { order, loading } = useOrder(orderId);
  const { formatMoney } = useLocalization();

  if (loading) {
    return (
      <section className="nt-page nt-container">
        <p className="nt-placeholder">جارٍ تحميل طلبك…</p>
      </section>
    );
  }

  if (!order) {
    return (
      <section className="nt-page nt-container">
        <div className="nt-oc">
          <h1 className="nt-display-sm" style={{ marginBottom: "0.5rem" }}>
            لم يتم العثور على الطلب
          </h1>
          <p className="nt-muted" style={{ marginBottom: "1.5rem" }}>
            راجع بريد التأكيد للوصول إلى رابط طلبك.
          </p>
          <a className="nt-btn-outline" href="/">
            العودة للرئيسية
          </a>
        </div>
      </section>
    );
  }

  const items = (order.line_items as Array<Record<string, unknown>>) ?? [];

  return (
    <section className="nt-container" style={{ paddingBlock: "4rem" }}>
      <div className="nt-oc">
        <div className="nt-oc__icon">
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            <path d="M20 6 9 17l-5-5" />
          </svg>
        </div>
        <EditableText
          as="h1"
          className="nt-display-sm"
          sectionId={id}
          settingId="title"
          value={s.title || "شكراً لطلبك!"}
          style={{ marginBottom: "0.5rem" }}
        />
        <EditableText
          as="p"
          className="nt-muted"
          sectionId={id}
          settingId="subtitle"
          value={s.subtitle || "تم استلام طلبك بنجاح. هنبعتلك تأكيد على بريدك."}
        />

        <div className="nt-oc__card">
          <div className="nt-oc__row">
            <span className="nt-muted">رقم الطلب</span>
            <span className="nt-mono" style={{ fontWeight: 800 }}>
              #{order.order_number}
            </span>
          </div>
          <div className="nt-oc__row">
            <span className="nt-muted">الحالة</span>
            <span style={{ fontWeight: 700, textTransform: "capitalize" }}>
              {order.status}
            </span>
          </div>
          <div className="nt-oc__row">
            <span className="nt-muted">الإجمالي</span>
            <span style={{ fontWeight: 800 }}>
              {formatMoney(order.total, order.currency)}
            </span>
          </div>
        </div>

        {items.length > 0 ? (
          <ul className="nt-oc__items">
            {items.map((it, i) => {
              const name =
                (it.name as string) || (it.title as string) || "منتج";
              const qty = (it.quantity as number) ?? 1;
              const price = (it.price as number) ?? 0;
              return (
                <li className="nt-oc__item" key={i}>
                  <span>
                    {name}{" "}
                    <span className="nt-muted" style={{ fontSize: "0.75rem" }}>
                      × {qty}
                    </span>
                  </span>
                  <span>{formatMoney(price * qty, order.currency)}</span>
                </li>
              );
            })}
          </ul>
        ) : null}

        <a className="nt-btn" href="/products">
          متابعة التسوق
        </a>
      </div>
    </section>
  );
}
