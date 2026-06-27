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
  ArrowRight,
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
 * Modern account / profile section.
 *
 * Copied from the proven Vionne V3 profile page and re-identified for Modern.
 * The `vn-*` classes + `--vn-*` tokens are defined in Modern's src/theme.css
 * (re-mapped to Modern's teal palette) so the page renders in Modern's
 * identity: sidebar (avatar, stats, Orders/Addresses/Settings nav, logout) +
 * content area with the three tabs (orders list, address book CRUD, settings
 * profile + password forms) and the logged-out auth guard.
 *
 * Data/actions are SDK-native:
 *  - useCustomer()         → identity (null ⇒ logged-out auth guard)
 *  - useOrders()           → order history (gated on the customer)
 *  - useCustomerAddresses()→ address book + CRUD mutations
 *  - useCustomerActions()  → logout + updateProfile + changePassword
 *
 * Never blank / never crashes: logged-out renders the auth guard; logged-in but
 * empty renders the empty states (orders/addresses); loading shows spinners.
 */

type Tab = "orders" | "addresses" | "settings";

const statusLabel = (status: string, locale: string | undefined): string => {
  const map: Record<string, string> = {
    pending: localized(locale, "Pending", "قيد الانتظار"),
    confirmed: localized(locale, "Confirmed", "مؤكد"),
    processing: localized(locale, "Processing", "قيد المعالجة"),
    shipped: localized(locale, "Shipped", "تم الشحن"),
    delivered: localized(locale, "Delivered", "تم التوصيل"),
    cancelled: localized(locale, "Cancelled", "ملغي"),
    refunded: localized(locale, "Refunded", "مسترد"),
  };
  return map[status] || status;
};

const LABEL_ICON: Record<string, typeof Home> = { home: Home, work: Briefcase, other: MapPin };
const labelName = (key: string, locale: string | undefined): string => {
  const map: Record<string, string> = {
    home: localized(locale, "Home", "المنزل"),
    work: localized(locale, "Work", "العمل"),
    other: localized(locale, "Other", "آخر"),
  };
  return map[key] || map.other;
};

const EMPTY_ADDRESS: Partial<CustomerAddress> = {
  first_name: "",
  last_name: "",
  address_line1: "",
  city: "",
  country: "EG",
  label: "home",
};

export default function ModernProfile({ instance }: SectionRenderProps) {
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

  // ── Logged-out auth guard ───────────────────────────────────────
  if (!customer) {
    return (
      <div className="bg-background">
        <div className="container mx-auto px-4 py-20 text-center">
          <div className="w-14 h-14 border border-[var(--vn-border)] flex items-center justify-center mx-auto mb-5 rounded-full">
            <User size={22} className="text-[var(--vn-muted)]" />
          </div>
          <p className="vn-heading text-lg text-[var(--vn-ink)] mb-1">
            {localized(locale, "Login to view your account", "سجّل الدخول لعرض حسابك")}
          </p>
          <p className="text-xs text-[var(--vn-muted)] mb-6">
            {localized(locale, "Track orders, manage addresses and settings", "تابع الطلبات وأدِر العناوين والإعدادات")}
          </p>
          <Link
            to="/auth?redirect=/profile"
            className="vn-btn vn-btn-filled inline-flex"
          >
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
    "w-full h-10 px-3 border border-[var(--vn-border)] bg-transparent text-sm rounded-md focus:border-[var(--vn-ink)] outline-none transition-colors";
  const labelClass = "vn-label text-[10px] text-[var(--vn-muted)] block mb-1";
  const headingClass = "vn-eyebrow text-[var(--vn-muted)] mb-5";

  return (
    <div className="bg-background" data-testid="storefront-profile">
      <div className="container mx-auto px-4 py-8 md:py-12">
        {/* Breadcrumb */}
        <div className="flex items-center gap-2 vn-label text-[10px] text-[var(--vn-muted)] mb-8">
          <Link to="/" className="hover:text-[var(--vn-ink)] transition-colors">
            {localized(locale, "Home", "الرئيسية")}
          </Link>
          <ArrowRight size={10} className="rtl:rotate-180" />
          <span className="text-[var(--vn-ink)]">{title}</span>
        </div>

        <div className="flex flex-col lg:flex-row gap-8 lg:gap-12">
          {/* ═══ Sidebar ═══ */}
          <div className="lg:w-64 shrink-0">
            <div className="mb-6">
              <div className="w-12 h-12 rounded-full bg-[var(--vn-band)] flex items-center justify-center text-[var(--vn-ink)] text-lg font-medium mb-3">
                {initial}
              </div>
              <h1 className="vn-heading text-base text-[var(--vn-ink)]">{fullName}</h1>
              <p className="text-xs text-[var(--vn-muted)]">{customer.email}</p>
            </div>

            {showStats && (
              <div className="flex gap-4 mb-6 pb-6 border-b border-[var(--vn-border)]">
                <div>
                  <p className="text-lg font-medium text-[var(--vn-ink)]">{orders.length}</p>
                  <p className="text-[10px] text-[var(--vn-muted)]">{localized(locale, "Orders", "طلبات")}</p>
                </div>
                <div>
                  <p className="text-lg font-medium text-[var(--vn-ink)]">
                    {totalSpent.toLocaleString("en-US")}
                  </p>
                  <p className="text-[10px] text-[var(--vn-muted)]">{localized(locale, "EGP", "ج.م")}</p>
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
                        ? "text-[var(--vn-ink)] font-medium bg-[var(--vn-band)]"
                        : "text-[var(--vn-muted)] hover:text-[var(--vn-ink)]")
                    }
                  >
                    <item.icon size={15} />
                    <span className="flex-1 text-start">{item.label}</span>
                    {item.count !== undefined && (
                      <span className="text-[10px] text-[var(--vn-muted)]">{item.count}</span>
                    )}
                  </button>
                );
              })}
            </nav>

            <button
              type="button"
              onClick={() => logout()}
              className="flex items-center gap-2 px-3 py-2 text-xs text-[var(--vn-muted)] hover:text-[var(--vn-ink)] transition-colors"
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
                    <Loader2 className="h-5 w-5 animate-spin text-[var(--vn-muted)]" />
                  </div>
                ) : orders.length === 0 ? (
                  <div className="text-center py-16 border border-[var(--vn-border)] rounded-md">
                    <div className="w-10 h-px bg-[var(--vn-border)] mx-auto mb-5" />
                    <p className="text-sm text-[var(--vn-muted)] mb-1">{localized(locale, "No orders yet", "لا توجد طلبات بعد")}</p>
                    <p className="text-xs text-[var(--vn-muted)] mb-5">
                      {localized(locale, "Your orders will appear here after your first purchase", "ستظهر طلباتك هنا بعد أول عملية شراء")}
                    </p>
                    <Link
                      to="/products"
                      className="text-xs font-medium border-b border-[var(--vn-ink)] pb-0.5 hover:opacity-70 transition-opacity"
                    >
                      {localized(locale, "Browse products", "تصفّح المنتجات")}
                    </Link>
                  </div>
                ) : (
                  <div className="border border-[var(--vn-border)] rounded-md overflow-hidden">
                    {orders.map((order, i) => (
                      <div
                        key={order.id}
                        className={
                          "px-5 py-4 hover:bg-[var(--vn-band)]/60 transition-colors " +
                          (i > 0 ? "border-t border-[var(--vn-border)]" : "")
                        }
                      >
                        <div className="flex items-start justify-between mb-2">
                          <div>
                            <span className="text-[13px] font-mono font-medium text-[var(--vn-ink)] block" dir="ltr">
                              {order.order_number}
                            </span>
                            <span className="text-[11px] text-[var(--vn-muted)]">
                              {order.created_at
                                ? new Date(order.created_at).toLocaleDateString(locale?.toLowerCase().startsWith("ar") ? "ar-EG" : "en-US", {
                                    day: "numeric",
                                    month: "short",
                                    year: "numeric",
                                  })
                                : ""}
                              {order.item_count
                                ? localized(locale, ` · ${order.item_count} item${order.item_count > 1 ? "s" : ""}`, ` · ${order.item_count} منتج`)
                                : ""}
                            </span>
                          </div>
                          <div className="text-end">
                            <span className="text-[13px] font-medium text-[var(--vn-ink)] block">
                              {(order.total / 100).toLocaleString("en-US")} {localized(locale, "EGP", "ج.م")}
                            </span>
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--vn-band)] text-[var(--vn-ink)]/70">
                              {statusLabel(order.status, locale)}
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
                      className="flex items-center gap-1.5 text-xs font-medium text-[var(--vn-ink)] hover:opacity-70 transition-opacity"
                    >
                      <Plus size={13} />
                      {localized(locale, "Add address", "إضافة عنوان")}
                    </button>
                  )}
                </div>

                {showAddressForm && (
                  <div className="border border-[var(--vn-border)] rounded-md p-5 mb-5">
                    <div className="flex items-center justify-between mb-4">
                      <h3 className="text-sm font-medium text-[var(--vn-ink)]">
                        {editingId ? localized(locale, "Edit address", "تعديل العنوان") : localized(locale, "New address", "عنوان جديد")}
                      </h3>
                      <button
                        type="button"
                        onClick={closeAddressForm}
                        className="text-[var(--vn-muted)] hover:text-[var(--vn-ink)]"
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
                                ? "border-[var(--vn-ink)] text-[var(--vn-ink)]"
                                : "border-[var(--vn-border)] text-[var(--vn-muted)]")
                            }
                          >
                            <Icon size={12} />
                            {labelName(l, locale)}
                          </button>
                        );
                      })}
                    </div>
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={handleSaveAddress}
                        disabled={savingAddress}
                        className="vn-btn vn-btn-filled disabled:opacity-50"
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
                        className="px-5 py-2 text-xs text-[var(--vn-muted)] hover:text-[var(--vn-ink)] transition-colors"
                      >
                        {localized(locale, "Cancel", "إلغاء")}
                      </button>
                    </div>
                  </div>
                )}

                {loadingAddresses ? (
                  <div className="flex justify-center py-16">
                    <Loader2 className="h-5 w-5 animate-spin text-[var(--vn-muted)]" />
                  </div>
                ) : addresses.length === 0 && !showAddressForm ? (
                  <div className="text-center py-16 border border-[var(--vn-border)] rounded-md">
                    <div className="w-10 h-px bg-[var(--vn-border)] mx-auto mb-5" />
                    <p className="text-sm text-[var(--vn-muted)] mb-1">{localized(locale, "No saved addresses", "لا توجد عناوين محفوظة")}</p>
                    <p className="text-xs text-[var(--vn-muted)] mb-5">
                      {localized(locale, "Add an address to speed up checkout", "أضف عنواناً لتسريع إتمام الطلب")}
                    </p>
                    <button
                      type="button"
                      onClick={openNewAddress}
                      className="text-xs font-medium border-b border-[var(--vn-ink)] pb-0.5 hover:opacity-70 transition-opacity"
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
                            (addr.is_default ? "border-[var(--vn-ink)]/30" : "border-[var(--vn-border)]")
                          }
                        >
                          <div className="flex items-start justify-between mb-2">
                            <div className="flex items-center gap-2">
                              <LabelIcon size={13} className="text-[var(--vn-muted)]" />
                              <span className="text-xs font-medium text-[var(--vn-ink)]">
                                {labelName(addr.label ?? "other", locale)}
                              </span>
                              {addr.is_default && (
                                <span className="text-[9px] px-1.5 py-0.5 rounded bg-[var(--vn-band)] text-[var(--vn-ink)]/60">
                                  {localized(locale, "Default", "افتراضي")}
                                </span>
                              )}
                            </div>
                            <div className="flex gap-1">
                              <button
                                type="button"
                                onClick={() => openEditAddress(addr)}
                                className="text-[10px] text-[var(--vn-muted)] hover:text-[var(--vn-ink)] transition-colors px-1"
                              >
                                {localized(locale, "Edit", "تعديل")}
                              </button>
                              <button
                                type="button"
                                onClick={() => deleteAddress(addr.id)}
                                className="text-[var(--vn-muted)] hover:text-[var(--vn-sale)] transition-colors px-1"
                                aria-label={localized(locale, "Delete address", "حذف العنوان")}
                              >
                                <Trash2 size={12} />
                              </button>
                            </div>
                          </div>
                          <p className="text-[13px] text-[var(--vn-ink)] mb-0.5">
                            {[addr.first_name, addr.last_name].filter(Boolean).join(" ")}
                          </p>
                          <p className="text-xs text-[var(--vn-muted)]">{addr.address_line1}</p>
                          {addr.address_line2 && (
                            <p className="text-xs text-[var(--vn-muted)]">{addr.address_line2}</p>
                          )}
                          <p className="text-xs text-[var(--vn-muted)]">
                            {addr.city}
                            {addr.state ? `, ${addr.state}` : ""}
                          </p>
                          {addr.phone && (
                            <p className="text-xs text-[var(--vn-muted)] mt-1" dir="ltr">
                              {addr.phone}
                            </p>
                          )}
                          {!addr.is_default && (
                            <button
                              type="button"
                              onClick={() => setDefaultAddress(addr.id)}
                              className="text-[10px] text-[var(--vn-muted)] hover:text-[var(--vn-ink)] transition-colors mt-2 border-b border-current pb-px"
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
                  <div className="border border-[var(--vn-border)] rounded-md p-5 space-y-3">
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
                      className="vn-btn vn-btn-filled disabled:opacity-50"
                    >
                      {savingProfile ? <Loader2 size={14} className="animate-spin" /> : localized(locale, "Save changes", "حفظ التغييرات")}
                    </button>
                  </div>
                </div>

                <div>
                  <h2 className={headingClass}>{localized(locale, "Change password", "تغيير كلمة المرور")}</h2>
                  <div className="border border-[var(--vn-border)] rounded-md p-5 space-y-3">
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
                      className="vn-btn vn-btn-filled disabled:opacity-50"
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
