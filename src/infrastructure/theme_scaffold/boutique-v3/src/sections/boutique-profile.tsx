"use client";
import { useEffect, useState } from "react";
import {
  Link,
  useCustomer,
  useOrders,
  useCustomerActions,
  useCustomerAddresses,
  useLocale,
  type CustomerAddress,
} from "@numueg/theme-sdk";
import {
  ArrowLeft,
  LogOut,
  User,
  Loader2,
  MapPin,
  Package,
  Settings,
  Plus,
  Trash2,
  Home,
  Briefcase,
  X,
} from "lucide-react";
import { asString, localized, type SectionRenderProps } from "./_shared";

/**
 * Boutique account / profile section.
 *
 * Ported from the proven Vionne V3 profile (sidebar with avatar/stats/nav +
 * logout; orders / addresses CRUD / settings tabs; logged-out auth guard) with
 * the grayscale `vn-*` tokens translated to Boutique's pink palette via shadcn
 * semantic Tailwind classes.
 *
 * Data/actions are SDK-native: useCustomer() (null ⇒ auth guard), useOrders(),
 * useCustomerAddresses() (+CRUD), useCustomerActions() (logout/updateProfile/
 * changePassword). Never blank / never crashes.
 */

type Tab = "orders" | "addresses" | "settings";

function statusLabel(locale: string | undefined, status: string): string {
  const map: Record<string, [string, string]> = {
    pending: ["Pending", "قيد الانتظار"],
    confirmed: ["Confirmed", "مؤكّد"],
    processing: ["Processing", "قيد التجهيز"],
    shipped: ["Shipped", "تم الشحن"],
    delivered: ["Delivered", "تم التوصيل"],
    cancelled: ["Cancelled", "ملغي"],
    refunded: ["Refunded", "مُسترجع"],
  };
  const pair = map[status];
  return pair ? localized(locale, pair[0], pair[1]) : status;
}

const LABEL_ICON: Record<string, typeof Home> = { home: Home, work: Briefcase, other: MapPin };
function labelName(locale: string | undefined, key: string): string {
  const map: Record<string, [string, string]> = {
    home: ["Home", "المنزل"],
    work: ["Work", "العمل"],
    other: ["Other", "أخرى"],
  };
  const pair = map[key];
  return pair ? localized(locale, pair[0], pair[1]) : key;
}

const EMPTY_ADDRESS: Partial<CustomerAddress> = {
  first_name: "",
  last_name: "",
  address_line1: "",
  city: "",
  country: "EG",
  label: "home",
};

const BTN_FILLED =
  "inline-flex items-center justify-center gap-2 h-11 px-6 rounded-full font-semibold text-sm text-primary-foreground transition-all hover:scale-[1.02]";

export default function BoutiqueProfile({ instance }: SectionRenderProps) {
  const s = instance.settings ?? {};
  const locale = useLocale();
  const title = asString(s.title) || localized(locale, "My account", "حسابي");
  const ordersTitle = asString(s.orders_title) || localized(locale, "My orders", "طلباتي");
  const addressesTitle = asString(s.addresses_title) || localized(locale, "My addresses", "عناويني");
  const settingsTitle = asString(s.settings_title) || localized(locale, "Settings", "الإعدادات");
  const showStats = s.show_stats ?? true;

  const customer = useCustomer();
  const { orders, loading: loadingOrders } = useOrders();
  const {
    addresses,
    loading: loadingAddresses,
    addAddress,
    updateAddress,
    deleteAddress,
    setDefaultAddress,
  } = useCustomerAddresses();
  const { logout, updateProfile, changePassword } = useCustomerActions();

  const [activeTab, setActiveTab] = useState<Tab>("orders");

  // ── Settings form ───────────────────────────────────────────────
  const [firstName, setFirstName] = useState(customer?.first_name ?? "");
  const [lastName, setLastName] = useState(customer?.last_name ?? "");
  const [phone, setPhone] = useState(customer?.phone ?? "");
  const [savingProfile, setSavingProfile] = useState(false);
  const [currentPw, setCurrentPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [changingPw, setChangingPw] = useState(false);

  useEffect(() => {
    if (customer) {
      setFirstName(customer.first_name ?? "");
      setLastName(customer.last_name ?? "");
      setPhone(customer.phone ?? "");
    }
  }, [customer]);

  // ── Address form ─────────────────────────────────────────────────
  const [showAddressForm, setShowAddressForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<Partial<CustomerAddress>>(EMPTY_ADDRESS);
  const [savingAddress, setSavingAddress] = useState(false);

  // ── Logged-out auth guard (matches V2) ──────────────────────────
  if (!customer) {
    return (
      <div className="bg-background">
        <div className="container mx-auto px-4 py-20 text-center">
          <div className="w-14 h-14 border border-border flex items-center justify-center mx-auto mb-5 rounded-full">
            <User size={22} className="text-muted-foreground" />
          </div>
          <p className="text-lg font-bold text-foreground mb-1">
            {localized(locale, "Login to view your account", "سجّلي الدخول لعرض حسابك")}
          </p>
          <p className="text-xs text-muted-foreground mb-6">
            {localized(locale, "Track orders, manage addresses and settings", "تتبّعي طلباتك وأديري عناوينك وإعداداتك")}
          </p>
          <Link to="/auth?redirect=/profile" className={BTN_FILLED} style={{ background: "hsl(var(--primary))" }}>
            {localized(locale, "Login", "تسجيل الدخول")}
          </Link>
        </div>
      </div>
    );
  }

  const fullName =
    [customer.first_name, customer.last_name].filter(Boolean).join(" ") || customer.email;
  const initial = (customer.first_name?.[0] || customer.email?.[0] || "?").toUpperCase();
  const totalSpent = (customer.total_spent ?? 0) / 100;

  const openNewAddress = () => {
    setEditingId(null);
    setForm({
      ...EMPTY_ADDRESS,
      first_name: customer.first_name ?? "",
      last_name: customer.last_name ?? "",
    });
    setShowAddressForm(true);
  };

  const openEditAddress = (addr: CustomerAddress) => {
    setEditingId(addr.id);
    setForm({ ...addr });
    setShowAddressForm(true);
  };

  const closeAddressForm = () => {
    setShowAddressForm(false);
    setEditingId(null);
  };

  const handleSaveAddress = async () => {
    if (!form.first_name || !form.address_line1 || !form.city) return;
    setSavingAddress(true);
    try {
      const payload = {
        first_name: form.first_name,
        last_name: form.last_name ?? "",
        address_line1: form.address_line1,
        address_line2: form.address_line2 ?? null,
        city: form.city,
        state: form.state ?? null,
        postal_code: form.postal_code ?? null,
        country: form.country ?? "EG",
        phone: form.phone ?? null,
        label: form.label ?? "home",
      };
      if (editingId) {
        await updateAddress(editingId, payload);
      } else {
        await addAddress({ ...payload, is_default: addresses.length === 0 });
      }
      closeAddressForm();
      setForm(EMPTY_ADDRESS);
    } finally {
      setSavingAddress(false);
    }
  };

  const handleSaveProfile = async () => {
    setSavingProfile(true);
    try {
      await updateProfile({ first_name: firstName, last_name: lastName, phone: phone || undefined });
    } finally {
      setSavingProfile(false);
    }
  };

  const handleChangePassword = async () => {
    if (newPw.length < 8) return;
    setChangingPw(true);
    try {
      await changePassword({ current_password: currentPw, new_password: newPw });
      setCurrentPw("");
      setNewPw("");
    } finally {
      setChangingPw(false);
    }
  };

  const tabs: { key: Tab; label: string; icon: typeof Package; count?: number }[] = [
    { key: "orders", label: localized(locale, "Orders", "الطلبات"), icon: Package, count: orders.length },
    { key: "addresses", label: localized(locale, "Addresses", "العناوين"), icon: MapPin, count: addresses.length },
    { key: "settings", label: localized(locale, "Settings", "الإعدادات"), icon: Settings },
  ];

  const inputClass =
    "w-full h-10 px-3 border border-border bg-transparent text-sm rounded-md focus:border-primary outline-none transition-colors";
  const labelClass = "text-[10px] uppercase tracking-widest text-muted-foreground block mb-1";
  const headingClass = "text-xs uppercase tracking-widest text-muted-foreground mb-5";

  return (
    <div className="bg-background" data-testid="storefront-profile">
      <div className="container mx-auto px-4 py-8 md:py-12">
        {/* Breadcrumb */}
        <div className="flex items-center gap-2 text-[10px] uppercase tracking-widest text-muted-foreground mb-8">
          <Link to="/" className="hover:text-foreground transition-colors">
            Home
          </Link>
          <ArrowLeft size={10} className="rtl:rotate-180" />
          <span className="text-foreground">{title}</span>
        </div>

        <div className="flex flex-col lg:flex-row gap-8 lg:gap-12">
          {/* ═══ Sidebar ═══ */}
          <div className="lg:w-64 shrink-0">
            <div className="mb-6">
              <div className="w-12 h-12 rounded-full bg-accent/40 flex items-center justify-center text-foreground text-lg font-medium mb-3">
                {initial}
              </div>
              <h1 className="text-base font-bold text-foreground">{fullName}</h1>
              <p className="text-xs text-muted-foreground">{customer.email}</p>
            </div>

            {showStats && (
              <div className="flex gap-4 mb-6 pb-6 border-b border-border">
                <div>
                  <p className="text-lg font-medium text-foreground">{orders.length}</p>
                  <p className="text-[10px] text-muted-foreground">{localized(locale, "Orders", "الطلبات")}</p>
                </div>
                <div>
                  <p className="text-lg font-medium text-foreground">
                    {totalSpent.toLocaleString("en-US")}
                  </p>
                  <p className="text-[10px] text-muted-foreground">EGP</p>
                </div>
              </div>
            )}

            <nav className="space-y-0.5 mb-6">
              {tabs.map((item) => {
                const active = activeTab === item.key;
                return (
                  <button
                    key={item.key}
                    type="button"
                    onClick={() => setActiveTab(item.key)}
                    className={
                      "w-full flex items-center gap-3 px-3 py-2.5 text-sm rounded-md transition-colors " +
                      (active
                        ? "text-foreground font-medium bg-accent/40"
                        : "text-muted-foreground hover:text-foreground")
                    }
                  >
                    <item.icon size={15} />
                    <span className="flex-1 text-start">{item.label}</span>
                    {item.count !== undefined && (
                      <span className="text-[10px] text-muted-foreground">{item.count}</span>
                    )}
                  </button>
                );
              })}
            </nav>

            <button
              type="button"
              onClick={() => logout()}
              className="flex items-center gap-2 px-3 py-2 text-xs text-muted-foreground hover:text-foreground transition-colors"
            >
              <LogOut size={13} />
              {localized(locale, "Logout", "تسجيل الخروج")}
            </button>
          </div>

          {/* ═══ Content ═══ */}
          <div className="flex-1 min-w-0">
            {/* ─── Orders ─── */}
            {activeTab === "orders" && (
              <div>
                <h2 className={headingClass}>{ordersTitle}</h2>
                {loadingOrders ? (
                  <div className="flex justify-center py-16">
                    <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                  </div>
                ) : orders.length === 0 ? (
                  <div className="text-center py-16 border border-border rounded-md">
                    <div className="w-10 h-px bg-border mx-auto mb-5" />
                    <p className="text-sm text-muted-foreground mb-1">{localized(locale, "No orders yet", "لا توجد طلبات بعد")}</p>
                    <p className="text-xs text-muted-foreground mb-5">
                      {localized(locale, "Your orders will appear here after your first purchase", "هتظهر طلباتك هنا بعد أول عملية شراء")}
                    </p>
                    <Link
                      to="/products"
                      className="text-xs font-medium border-b border-primary pb-0.5 hover:opacity-70 transition-opacity"
                    >
                      {localized(locale, "Browse products", "تصفّحي المنتجات")}
                    </Link>
                  </div>
                ) : (
                  <div className="border border-border rounded-md overflow-hidden">
                    {orders.map((order, i) => (
                      <div
                        key={order.id}
                        className={
                          "px-5 py-4 hover:bg-accent/20 transition-colors " +
                          (i > 0 ? "border-t border-border" : "")
                        }
                      >
                        <div className="flex items-start justify-between mb-2">
                          <div>
                            <span className="text-[13px] font-mono font-medium text-foreground block" dir="ltr">
                              {order.order_number}
                            </span>
                            <span className="text-[11px] text-muted-foreground">
                              {order.created_at
                                ? new Date(order.created_at).toLocaleDateString("en-US", {
                                    day: "numeric",
                                    month: "short",
                                    year: "numeric",
                                  })
                                : ""}
                              {order.item_count
                                ? ` · ${order.item_count} ${localized(
                                    locale,
                                    order.item_count > 1 ? "items" : "item",
                                    "منتج",
                                  )}`
                                : ""}
                            </span>
                          </div>
                          <div className="text-end">
                            <span className="text-[13px] font-medium text-foreground block">
                              {(order.total / 100).toLocaleString("en-US")} EGP
                            </span>
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-accent/40 text-foreground/70">
                              {statusLabel(locale, order.status)}
                            </span>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* ─── Addresses ─── */}
            {activeTab === "addresses" && (
              <div>
                <div className="flex items-center justify-between mb-5">
                  <h2 className={headingClass + " mb-0"}>{addressesTitle}</h2>
                  {!showAddressForm && (
                    <button
                      type="button"
                      onClick={openNewAddress}
                      className="flex items-center gap-1.5 text-xs font-medium text-foreground hover:opacity-70 transition-opacity"
                    >
                      <Plus size={13} />
                      {localized(locale, "Add address", "إضافة عنوان")}
                    </button>
                  )}
                </div>

                {showAddressForm && (
                  <div className="border border-border rounded-md p-5 mb-5">
                    <div className="flex items-center justify-between mb-4">
                      <h3 className="text-sm font-medium text-foreground">
                        {editingId ? localized(locale, "Edit address", "تعديل العنوان") : localized(locale, "New address", "عنوان جديد")}
                      </h3>
                      <button
                        type="button"
                        onClick={closeAddressForm}
                        className="text-muted-foreground hover:text-foreground"
                        aria-label={localized(locale, "Close", "إغلاق")}
                      >
                        <X size={16} />
                      </button>
                    </div>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-3">
                      <div>
                        <label className={labelClass}>{localized(locale, "First name", "الاسم الأول")}</label>
                        <input
                          value={form.first_name ?? ""}
                          onChange={(e) => setForm((p) => ({ ...p, first_name: e.target.value }))}
                          className={inputClass}
                        />
                      </div>
                      <div>
                        <label className={labelClass}>{localized(locale, "Last name", "اسم العائلة")}</label>
                        <input
                          value={form.last_name ?? ""}
                          onChange={(e) => setForm((p) => ({ ...p, last_name: e.target.value }))}
                          className={inputClass}
                        />
                      </div>
                    </div>
                    <div className="mb-3">
                      <label className={labelClass}>{localized(locale, "Address", "العنوان")}</label>
                      <input
                        value={form.address_line1 ?? ""}
                        onChange={(e) => setForm((p) => ({ ...p, address_line1: e.target.value }))}
                        placeholder={localized(locale, "Street, building, apt", "الشارع، المبنى، الشقة")}
                        className={inputClass}
                      />
                    </div>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-3">
                      <div>
                        <label className={labelClass}>{localized(locale, "City", "المدينة")}</label>
                        <input
                          value={form.city ?? ""}
                          onChange={(e) => setForm((p) => ({ ...p, city: e.target.value }))}
                          className={inputClass}
                        />
                      </div>
                      <div>
                        <label className={labelClass}>{localized(locale, "Phone", "الهاتف")}</label>
                        <input
                          value={form.phone ?? ""}
                          onChange={(e) => setForm((p) => ({ ...p, phone: e.target.value }))}
                          placeholder="01xxxxxxxxx"
                          dir="ltr"
                          className={inputClass}
                        />
                      </div>
                    </div>
                    <div className="flex gap-2 mb-4">
                      {(["home", "work", "other"] as const).map((l) => {
                        const Icon = LABEL_ICON[l];
                        const active = form.label === l;
                        return (
                          <button
                            key={l}
                            type="button"
                            onClick={() => setForm((p) => ({ ...p, label: l }))}
                            className={
                              "flex items-center gap-1.5 px-3 py-1.5 text-xs border rounded-full transition-colors " +
                              (active
                                ? "border-primary text-foreground"
                                : "border-border text-muted-foreground")
                            }
                          >
                            <Icon size={12} />
                            {labelName(locale, l)}
                          </button>
                        );
                      })}
                    </div>
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={handleSaveAddress}
                        disabled={savingAddress}
                        className={BTN_FILLED + " disabled:opacity-50"}
                        style={{ background: "hsl(var(--primary))" }}
                      >
                        {savingAddress ? (
                          <Loader2 size={14} className="animate-spin" />
                        ) : editingId ? (
                          localized(locale, "Update", "تحديث")
                        ) : (
                          localized(locale, "Save", "حفظ")
                        )}
                      </button>
                      <button
                        type="button"
                        onClick={closeAddressForm}
                        className="px-5 py-2 text-xs text-muted-foreground hover:text-foreground transition-colors"
                      >
                        {localized(locale, "Cancel", "إلغاء")}
                      </button>
                    </div>
                  </div>
                )}

                {loadingAddresses ? (
                  <div className="flex justify-center py-16">
                    <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                  </div>
                ) : addresses.length === 0 && !showAddressForm ? (
                  <div className="text-center py-16 border border-border rounded-md">
                    <div className="w-10 h-px bg-border mx-auto mb-5" />
                    <p className="text-sm text-muted-foreground mb-1">{localized(locale, "No saved addresses", "لا توجد عناوين محفوظة")}</p>
                    <p className="text-xs text-muted-foreground mb-5">
                      {localized(locale, "Add an address to speed up checkout", "أضيفي عنواناً لتسريع إتمام الطلب")}
                    </p>
                    <button
                      type="button"
                      onClick={openNewAddress}
                      className="text-xs font-medium border-b border-primary pb-0.5 hover:opacity-70 transition-opacity"
                    >
                      {localized(locale, "Add address", "إضافة عنوان")}
                    </button>
                  </div>
                ) : (
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    {addresses.map((addr) => {
                      const LabelIcon = LABEL_ICON[addr.label ?? "other"] || MapPin;
                      return (
                        <div
                          key={addr.id}
                          className={
                            "border rounded-md p-4 transition-colors " +
                            (addr.is_default ? "border-primary/40" : "border-border")
                          }
                        >
                          <div className="flex items-start justify-between mb-2">
                            <div className="flex items-center gap-2">
                              <LabelIcon size={13} className="text-muted-foreground" />
                              <span className="text-xs font-medium text-foreground">
                                {labelName(locale, addr.label ?? "other")}
                              </span>
                              {addr.is_default && (
                                <span className="text-[9px] px-1.5 py-0.5 rounded bg-accent/40 text-foreground/60">
                                  {localized(locale, "Default", "افتراضي")}
                                </span>
                              )}
                            </div>
                            <div className="flex gap-1">
                              <button
                                type="button"
                                onClick={() => openEditAddress(addr)}
                                className="text-[10px] text-muted-foreground hover:text-foreground transition-colors px-1"
                              >
                                {localized(locale, "Edit", "تعديل")}
                              </button>
                              <button
                                type="button"
                                onClick={() => deleteAddress(addr.id)}
                                className="text-muted-foreground hover:text-destructive transition-colors px-1"
                                aria-label={localized(locale, "Delete address", "حذف العنوان")}
                              >
                                <Trash2 size={12} />
                              </button>
                            </div>
                          </div>
                          <p className="text-[13px] text-foreground mb-0.5">
                            {[addr.first_name, addr.last_name].filter(Boolean).join(" ")}
                          </p>
                          <p className="text-xs text-muted-foreground">{addr.address_line1}</p>
                          {addr.address_line2 && (
                            <p className="text-xs text-muted-foreground">{addr.address_line2}</p>
                          )}
                          <p className="text-xs text-muted-foreground">
                            {addr.city}
                            {addr.state ? `, ${addr.state}` : ""}
                          </p>
                          {addr.phone && (
                            <p className="text-xs text-muted-foreground mt-1" dir="ltr">
                              {addr.phone}
                            </p>
                          )}
                          {!addr.is_default && (
                            <button
                              type="button"
                              onClick={() => setDefaultAddress(addr.id)}
                              className="text-[10px] text-muted-foreground hover:text-foreground transition-colors mt-2 border-b border-current pb-px"
                            >
                              {localized(locale, "Set as default", "تعيين كافتراضي")}
                            </button>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            )}

            {/* ─── Settings ─── */}
            {activeTab === "settings" && (
              <div className="space-y-8">
                <div>
                  <h2 className={headingClass}>{settingsTitle}</h2>
                  <div className="border border-border rounded-md p-5 space-y-3">
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      <div>
                        <label className={labelClass}>{localized(locale, "First name", "الاسم الأول")}</label>
                        <input
                          value={firstName}
                          onChange={(e) => setFirstName(e.target.value)}
                          className={inputClass}
                        />
                      </div>
                      <div>
                        <label className={labelClass}>{localized(locale, "Last name", "اسم العائلة")}</label>
                        <input
                          value={lastName}
                          onChange={(e) => setLastName(e.target.value)}
                          className={inputClass}
                        />
                      </div>
                    </div>
                    <div>
                      <label className={labelClass}>{localized(locale, "Email", "البريد الإلكتروني")}</label>
                      <input
                        value={customer.email}
                        disabled
                        dir="ltr"
                        className={inputClass + " opacity-60 cursor-not-allowed"}
                      />
                    </div>
                    <div>
                      <label className={labelClass}>{localized(locale, "Phone", "الهاتف")}</label>
                      <input
                        value={phone}
                        onChange={(e) => setPhone(e.target.value)}
                        placeholder="01xxxxxxxxx"
                        dir="ltr"
                        className={inputClass}
                      />
                    </div>
                    <button
                      type="button"
                      onClick={handleSaveProfile}
                      disabled={savingProfile}
                      className={BTN_FILLED + " disabled:opacity-50"}
                      style={{ background: "hsl(var(--primary))" }}
                    >
                      {savingProfile ? <Loader2 size={14} className="animate-spin" /> : localized(locale, "Save changes", "حفظ التغييرات")}
                    </button>
                  </div>
                </div>

                <div>
                  <h2 className={headingClass}>{localized(locale, "Change password", "تغيير كلمة المرور")}</h2>
                  <div className="border border-border rounded-md p-5 space-y-3">
                    <div>
                      <label className={labelClass}>{localized(locale, "Current password", "كلمة المرور الحالية")}</label>
                      <input
                        type="password"
                        value={currentPw}
                        onChange={(e) => setCurrentPw(e.target.value)}
                        className={inputClass}
                      />
                    </div>
                    <div>
                      <label className={labelClass}>{localized(locale, "New password", "كلمة المرور الجديدة")}</label>
                      <input
                        type="password"
                        value={newPw}
                        onChange={(e) => setNewPw(e.target.value)}
                        placeholder={localized(locale, "Min 8 characters", "8 أحرف على الأقل")}
                        className={inputClass}
                      />
                    </div>
                    <button
                      type="button"
                      onClick={handleChangePassword}
                      disabled={changingPw || !currentPw || !newPw}
                      className={BTN_FILLED + " disabled:opacity-50"}
                      style={{ background: "hsl(var(--primary))" }}
                    >
                      {changingPw ? <Loader2 size={14} className="animate-spin" /> : localized(locale, "Change password", "تغيير كلمة المرور")}
                    </button>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
