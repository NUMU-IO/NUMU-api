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
      <section className="empire-page empire-container">
        <p className="empire-placeholder">جارٍ تحميل طلبك…</p>
      </section>
    );
  }

  if (!order) {
    return (
      <section className="empire-page empire-container">
        <div className="empire-oc">
          <h1 className="empire-display-sm" style={{ marginBottom: "0.5rem" }}>
            لم يتم العثور على الطلب
          </h1>
          <p className="empire-muted" style={{ marginBottom: "1.5rem" }}>
            راجع بريد التأكيد للوصول إلى رابط طلبك.
          </p>
          <a className="empire-btn-outline" href="/">
            العودة للرئيسية
          </a>
        </div>
      </section>
    );
  }

  const items = (order.line_items as Array<Record<string, unknown>>) ?? [];

  return (
    <section className="empire-container" style={{ paddingBlock: "4rem" }}>
      <div className="empire-oc">
        <div className="empire-oc__icon">
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            <path d="M20 6 9 17l-5-5" />
          </svg>
        </div>
        <EditableText
          as="h1"
          className="empire-display-sm"
          sectionId={id}
          settingId="title"
          value={s.title || "شكراً لطلبك!"}
          style={{ marginBottom: "0.5rem" }}
        />
        <EditableText
          as="p"
          className="empire-muted"
          sectionId={id}
          settingId="subtitle"
          value={s.subtitle || "تم استلام طلبك بنجاح. هنبعتلك تأكيد على بريدك."}
        />

        <div className="empire-oc__card">
          <div className="empire-oc__row">
            <span className="empire-muted">رقم الطلب</span>
            <span className="empire-mono" style={{ fontWeight: 800 }}>
              #{order.order_number}
            </span>
          </div>
          <div className="empire-oc__row">
            <span className="empire-muted">الحالة</span>
            <span style={{ fontWeight: 700, textTransform: "capitalize" }}>
              {order.status}
            </span>
          </div>
          <div className="empire-oc__row">
            <span className="empire-muted">الإجمالي</span>
            <span style={{ fontWeight: 800 }}>
              {formatMoney(order.total, order.currency)}
            </span>
          </div>
        </div>

        {items.length > 0 ? (
          <ul className="empire-oc__items">
            {items.map((it, i) => {
              const name =
                (it.name as string) || (it.title as string) || "منتج";
              const qty = (it.quantity as number) ?? 1;
              const price = (it.price as number) ?? 0;
              return (
                <li className="empire-oc__item" key={i}>
                  <span>
                    {name}{" "}
                    <span className="empire-muted" style={{ fontSize: "0.75rem" }}>
                      × {qty}
                    </span>
                  </span>
                  <span>{formatMoney(price * qty, order.currency)}</span>
                </li>
              );
            })}
          </ul>
        ) : null}

        <a className="empire-btn" href="/products">
          متابعة التسوق
        </a>
      </div>
    </section>
  );
}
