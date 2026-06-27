import {
  useState } from "react";
import {
  useCustomer,
  useCustomerActions,
  useOrders,
  useCustomerAddresses,
  useLocalization,
} from "@numueg/theme-sdk";
import { EditableText } from "../lib/EditableText";
import type { EmpSectionProps } from "../lib/section";

interface AccountSettings {
  heading?: string;
}

type Tab = "orders" | "addresses" | "settings";

const STATUS_AR: Record<string, string> = {
  pending: "قيد الانتظار",
  confirmed: "مؤكد",
  processing: "قيد التجهيز",
  shipped: "تم الشحن",
  delivered: "تم التوصيل",
  cancelled: "ملغي",
  refunded: "مسترد",
};

/**
 * Account / profile dashboard. Logged-out → login/register guard; logged-in →
 * sidebar (name + logout) + tabs for order history, address book (add/remove)
 * and profile settings. All data + mutations are SDK-native (useCustomer /
 * useOrders / useCustomerAddresses / useCustomerActions). Never blank: shows
 * spinners while loading and empty states when there's nothing.
 */
export default function Account({ id, settings }: EmpSectionProps) {
  const s = settings as AccountSettings;
  const customer = useCustomer();

  return (
    <section className="nt-container" style={{ paddingBlock: "2.5rem" }}>
      <EditableText
        as="h1"
        className="nt-display-sm"
        sectionId={id}
        settingId="heading"
        value={s.heading ?? "حسابي"}
        style={{ marginBottom: "2rem" }}
      />
      {customer ? <Dashboard /> : <AuthGuard />}
    </section>
  );
}

/* ───────────────────────── Auth guard (logged out) ───────────────────────── */
function AuthGuard() {
  const { login, register } = useCustomerActions();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [form, setForm] = useState({
    email: "",
    password: "",
    first_name: "",
    last_name: "",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const set = (k: keyof typeof form, v: string) =>
    setForm((p) => ({ ...p, [k]: v }));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      if (mode === "login") {
        await login({ email: form.email, password: form.password });
      } else {
        await register({
          email: form.email,
          password: form.password,
          first_name: form.first_name,
          last_name: form.last_name,
        });
      }
    } catch {
      setError("تعذّر إتمام العملية. تأكد من البيانات وحاول مجددًا.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="nt-auth">
      <div className="nt-auth__tabs">
        <button
          type="button"
          className="nt-chip"
          aria-pressed={mode === "login"}
          onClick={() => setMode("login")}
        >
          تسجيل الدخول
        </button>
        <button
          type="button"
          className="nt-chip"
          aria-pressed={mode === "register"}
          onClick={() => setMode("register")}
        >
          حساب جديد
        </button>
      </div>

      <form className="nt-form" onSubmit={submit}>
        {mode === "register" ? (
          <div className="nt-form__row">
            <input
              className="nt-input"
              placeholder="الاسم الأول"
              value={form.first_name}
              onChange={(e) => set("first_name", e.target.value)}
            />
            <input
              className="nt-input"
              placeholder="الاسم الأخير"
              value={form.last_name}
              onChange={(e) => set("last_name", e.target.value)}
            />
          </div>
        ) : null}
        <input
          className="nt-input"
          type="email"
          dir="ltr"
          required
          placeholder="البريد الإلكتروني"
          value={form.email}
          onChange={(e) => set("email", e.target.value)}
        />
        <input
          className="nt-input"
          type="password"
          required
          placeholder="كلمة المرور"
          value={form.password}
          onChange={(e) => set("password", e.target.value)}
        />
        {error ? <p className="nt-coupon__err">{error}</p> : null}
        <button className="nt-btn nt-btn--block" type="submit" disabled={busy}>
          {busy ? "..." : mode === "login" ? "تسجيل الدخول" : "إنشاء الحساب"}
        </button>
      </form>
    </div>
  );
}

/* ───────────────────────── Dashboard (logged in) ───────────────────────── */
function Dashboard() {
  const customer = useCustomer();
  const { logout } = useCustomerActions();
  const [tab, setTab] = useState<Tab>("orders");

  const name =
    [customer?.first_name, customer?.last_name].filter(Boolean).join(" ") ||
    customer?.email ||
    "عميل";

  return (
    <div className="nt-account">
      <aside className="nt-account__side">
        <div className="nt-account__avatar">{name[0]}</div>
        <p className="nt-account__name">{name}</p>
        <p className="nt-account__email">{customer?.email}</p>
        <nav className="nt-account__nav">
          {(
            [
              ["orders", "طلباتي"],
              ["addresses", "العناوين"],
              ["settings", "الإعدادات"],
            ] as Array<[Tab, string]>
          ).map(([key, label]) => (
            <button
              key={key}
              type="button"
              className={`nt-account__navitem${tab === key ? " is-active" : ""}`}
              onClick={() => setTab(key)}
            >
              {label}
            </button>
          ))}
          <button
            type="button"
            className="nt-account__navitem"
            onClick={() => logout()}
          >
            تسجيل الخروج
          </button>
        </nav>
      </aside>

      <div className="nt-account__main">
        {tab === "orders" ? <OrdersTab /> : null}
        {tab === "addresses" ? <AddressesTab /> : null}
        {tab === "settings" ? <SettingsTab /> : null}
      </div>
    </div>
  );
}

function OrdersTab() {
  const { orders, loading } = useOrders();
  const { formatMoney } = useLocalization();

  if (loading) return <p className="nt-placeholder">جارٍ التحميل…</p>;
  if (orders.length === 0)
    return <p className="nt-placeholder">لا توجد طلبات بعد.</p>;

  return (
    <div className="nt-orders">
      {orders.map((o) => (
        <a key={o.id} className="nt-orders__row" href={`/orders/${o.id}`}>
          <div>
            <p className="nt-orders__num">#{o.order_number}</p>
            <p className="nt-orders__meta">
              {STATUS_AR[o.status] || o.status}
              {o.item_count ? ` · ${o.item_count} عنصر` : ""}
            </p>
          </div>
          <span className="nt-orders__total">
            {formatMoney(o.total, o.currency)}
          </span>
        </a>
      ))}
    </div>
  );
}

function AddressesTab() {
  const { addresses, loading, addAddress, deleteAddress } =
    useCustomerAddresses();
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    first_name: "",
    address_line1: "",
    city: "",
    phone: "",
  });
  const set = (k: keyof typeof form, v: string) =>
    setForm((p) => ({ ...p, [k]: v }));

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    try {
      await addAddress(form);
      setForm({ first_name: "", address_line1: "", city: "", phone: "" });
      setAdding(false);
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <p className="nt-placeholder">جارٍ التحميل…</p>;

  return (
    <div>
      {addresses.length === 0 ? (
        <p className="nt-placeholder" style={{ textAlign: "start" }}>
          لا توجد عناوين محفوظة.
        </p>
      ) : (
        <div className="nt-addresses">
          {addresses.map((a) => (
            <div className="nt-address" key={a.id}>
              <div>
                <p className="nt-address__name">
                  {a.first_name} {a.last_name}
                  {a.is_default ? " · افتراضي" : ""}
                </p>
                <p className="nt-address__line">
                  {[a.address_line1, a.city, a.country]
                    .filter(Boolean)
                    .join("، ")}
                </p>
                {a.phone ? (
                  <p className="nt-address__line" dir="ltr">
                    {a.phone}
                  </p>
                ) : null}
              </div>
              <button
                type="button"
                className="nt-line__remove"
                onClick={() => deleteAddress(a.id)}
                aria-label="حذف"
              >
                حذف
              </button>
            </div>
          ))}
        </div>
      )}

      {adding ? (
        <form className="nt-form" onSubmit={save} style={{ marginTop: "1.5rem" }}>
          <input
            className="nt-input"
            placeholder="الاسم"
            value={form.first_name}
            onChange={(e) => set("first_name", e.target.value)}
          />
          <input
            className="nt-input"
            placeholder="العنوان"
            required
            value={form.address_line1}
            onChange={(e) => set("address_line1", e.target.value)}
          />
          <div className="nt-form__row">
            <input
              className="nt-input"
              placeholder="المدينة"
              value={form.city}
              onChange={(e) => set("city", e.target.value)}
            />
            <input
              className="nt-input"
              placeholder="الهاتف"
              dir="ltr"
              value={form.phone}
              onChange={(e) => set("phone", e.target.value)}
            />
          </div>
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <button className="nt-btn" type="submit" disabled={busy}>
              {busy ? "..." : "حفظ"}
            </button>
            <button
              className="nt-btn-outline"
              type="button"
              onClick={() => setAdding(false)}
            >
              إلغاء
            </button>
          </div>
        </form>
      ) : (
        <button
          className="nt-btn-outline"
          type="button"
          style={{ marginTop: "1.5rem" }}
          onClick={() => setAdding(true)}
        >
          + إضافة عنوان
        </button>
      )}
    </div>
  );
}

function SettingsTab() {
  const customer = useCustomer();
  const { updateProfile } = useCustomerActions();
  const [form, setForm] = useState({
    first_name: customer?.first_name ?? "",
    last_name: customer?.last_name ?? "",
    phone: customer?.phone ?? "",
  });
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const set = (k: keyof typeof form, v: string) => {
    setForm((p) => ({ ...p, [k]: v }));
    setSaved(false);
  };

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    try {
      await updateProfile(form);
      setSaved(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="nt-form" onSubmit={save}>
      <div className="nt-form__row">
        <input
          className="nt-input"
          placeholder="الاسم الأول"
          value={form.first_name}
          onChange={(e) => set("first_name", e.target.value)}
        />
        <input
          className="nt-input"
          placeholder="الاسم الأخير"
          value={form.last_name}
          onChange={(e) => set("last_name", e.target.value)}
        />
      </div>
      <input
        className="nt-input"
        placeholder="الهاتف"
        dir="ltr"
        value={form.phone}
        onChange={(e) => set("phone", e.target.value)}
      />
      <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
        <button className="nt-btn" type="submit" disabled={busy}>
          {busy ? "..." : "حفظ التغييرات"}
        </button>
        {saved ? <span className="nt-muted">تم الحفظ ✓</span> : null}
      </div>
    </form>
  );
}
