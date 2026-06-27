"use client";

import { useEffect, useState } from "react";
import {
  Link,
  Money,
  useCustomer,
  useOrders,
  useCustomerActions,
  useCustomerAddresses,
  useLocale,
  useResolvedSettings,
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
  Eye,
  EyeOff,
  X,
} from "lucide-react";
import { asBool, asString, localized, type SectionRenderProps } from "./_shared";
import { InlineEditable } from "./_inline-editable";

/**
 * gilded-profile — Gilded Glamour Boutique account / profile section.
 *
 * Faithful V3 port of the gilded account page, re-plumbed on the V3 SDK and
 * re-skinned from the prior Vionne port to the gilded design language. There is
 * NO dedicated V2 `GildedProfile` component (the V2 gilded theme styles the
 * SHARED bazaar profile page with the gilded tokens), so the faithful target is
 * the gilded brand: warm beige canvas, white sharp-edged cards, BLACK uppercase
 * Montserrat headings (`gld-heading`), `gld-eyebrow` section labels with a gold
 * hairline rule, muted-GOLD accent via `--gilded-gold` (repaints through the
 * Accent picker), hairline `border-border` cards, and `gld-btn` / `gld-btn-outline`
 * / `gld-input` / `gld-label` controls. NO vionne `vn-*` classes remain.
 *
 * Structure mirrors the lux-profile exemplar: breadcrumb, sidebar (avatar,
 * name/email, orders/spent stats, Orders/Addresses/Settings nav, logout) and a
 * content area with the three tabs — orders list, address book with
 * add/edit/delete/set-default form, and settings with profile + password forms.
 *
 * Data/actions are SDK-native:
 *  - useCustomer()          → identity (null ⇒ logged-out auth guard + /auth link)
 *  - useOrders()            → order history (gated on the customer); totals are
 *                             CENTS → divide by 100 for <Money amount={…/100}/>.
 *  - useCustomerAddresses() → address book + CRUD mutations
 *  - useCustomerActions()   → logout + updateProfile + changePassword
 *
 * Never blank / never crashes: logged-out renders the auth guard; logged-in
 * but empty renders the empty states; loading shows spinners. Engine-wired:
 * useResolvedSettings (global tokens + dynamic sources + draft preview) and
 * InlineEditable on the STATIC section headings only — never on live user data.
 */

type Tab = "orders" | "addresses" | "settings";

const statusLabels = (locale: string | undefined): Record<string, string> => ({
  pending: localized(locale, "Pending", "قيد الانتظار"),
  confirmed: localized(locale, "Confirmed", "تم التأكيد"),
  processing: localized(locale, "Processing", "قيد التجهيز"),
  shipped: localized(locale, "Shipped", "تم الشحن"),
  delivered: localized(locale, "Delivered", "تم التوصيل"),
  cancelled: localized(locale, "Cancelled", "ملغي"),
  refunded: localized(locale, "Refunded", "تم الاسترجاع"),
});

const paymentLabels = (locale: string | undefined): Record<string, string> => ({
  pending: localized(locale, "Payment pending", "الدفع قيد الانتظار"),
  paid: localized(locale, "Paid", "مدفوع"),
  partially_paid: localized(locale, "Partially paid", "مدفوع جزئياً"),
  refunded: localized(locale, "Refunded", "مسترد"),
  partially_refunded: localized(locale, "Partially refunded", "مسترد جزئياً"),
  failed: localized(locale, "Payment failed", "فشل الدفع"),
  voided: localized(locale, "Voided", "ملغي"),
});

const LABEL_ICON: Record<string, typeof Home> = { home: Home, work: Briefcase, other: MapPin };
const labelName = (locale: string | undefined): Record<string, string> => ({
  home: localized(locale, "Home", "المنزل"),
  work: localized(locale, "Work", "العمل"),
  other: localized(locale, "Other", "آخر"),
});

const EMPTY_ADDRESS: Partial<CustomerAddress> = {
  first_name: "",
  last_name: "",
  address_line1: "",
  city: "",
  country: "EG",
  label: "home",
};

/** Gilded section heading: a gold hairline rule above the uppercase Montserrat
 *  eyebrow — the recurring "gold rule" motif from the V2 gilded sections. */
function SectionHeading({
  sectionId,
  settingKey,
  value,
  className = "",
}: {
  sectionId: string;
  settingKey: string;
  value: string;
  className?: string;
}) {
  return (
    <div className={"mb-5 " + className}>
      <div className="w-8 h-px bg-gold mb-3" aria-hidden="true" />
      <h2 className="gld-eyebrow text-foreground">
        <InlineEditable sectionId={sectionId} settingKey={settingKey} value={value} />
      </h2>
    </div>
  );
}

export default function GildedProfile({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const locale = useLocale();
  const STATUS_LABELS = statusLabels(locale);
  const PAYMENT_LABELS = paymentLabels(locale);
  const LABEL_NAME = labelName(locale);
  const title = asString(s.title) || localized(locale, "My Account", "حسابي");
  const ordersTitle = asString(s.orders_title) || localized(locale, "My Orders", "طلباتي");
  const addressesTitle = asString(s.addresses_title) || localized(locale, "My Addresses", "عناويني");
  const settingsTitle = asString(s.settings_title) || localized(locale, "Settings", "الإعدادات");
  const passwordTitle = asString(s.password_title) || localized(locale, "Change password", "تغيير كلمة المرور");
  const showStats = asBool(s.show_stats, true);

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
  const [showPw, setShowPw] = useState(false);
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

  // ── Logged-out auth guard (gilded gold crest + /auth link) ───────
  if (!customer) {
    return (
      <div className="bg-background min-h-[70vh]" data-gilded-section={sectionId} data-testid="storefront-profile">
        <div className="container mx-auto px-4 py-20 text-center">
          <div className="w-14 h-14 border border-gold rounded-full flex items-center justify-center mx-auto mb-5">
            <User size={22} className="text-gold" />
          </div>
          <p className="gld-heading text-lg text-foreground mb-1">
            {localized(locale, "Sign in to view your account", "سجّل الدخول لعرض حسابك")}
          </p>
          <p className="text-xs text-muted-foreground mb-6">
            {localized(locale, "Track orders, manage addresses and account settings", "تتبع طلباتك، وادِر عناوينك وإعدادات حسابك")}
          </p>
          <Link to="/auth?redirect=/profile" className="gld-btn inline-flex">
            {localized(locale, "Sign in", "تسجيل الدخول")}
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

  const inputClass = "w-full h-10 px-3 text-sm gld-input";
  const labelClass = "gld-label text-[10px] text-muted-foreground block mb-1";

  return (
    <div className="bg-background min-h-[70vh]" data-gilded-section={sectionId} data-testid="storefront-profile">
      <div className="container mx-auto px-4 py-8 md:py-12">
        {/* Breadcrumb */}
        <div className="flex items-center gap-2 gld-label text-[10px] text-muted-foreground mb-8">
          <Link to="/" className="hover:text-gold transition-colors">
            {localized(locale, "Home", "الرئيسية")}
          </Link>
          <ArrowRight size={10} className="rtl:-scale-x-100" aria-hidden="true" />
          <span className="text-foreground">
            <InlineEditable sectionId={sectionId} settingKey="title" value={title} />
          </span>
        </div>

        <div className="flex flex-col lg:flex-row gap-8 lg:gap-12">
          {/* ═══ Sidebar ═══ */}
          <div className="lg:w-64 shrink-0">
            <div className="mb-6">
              <div className="w-12 h-12 rounded-full bg-gold flex items-center justify-center text-foreground text-lg font-medium mb-3">
                {initial}
              </div>
              <h1 className="gld-heading text-base text-foreground">{fullName}</h1>
              <p className="text-xs text-muted-foreground" dir="ltr">{customer.email}</p>
            </div>

            {showStats && (
              <div className="flex gap-8 mb-6 pb-6 border-b border-border">
                <div>
                  <p className="text-2xl font-medium text-foreground leading-none mb-1">{orders.length}</p>
                  <p className="gld-label text-[10px] text-muted-foreground">{localized(locale, "Orders", "طلبات")}</p>
                </div>
                <div>
                  <p className="text-2xl font-medium text-gold leading-none mb-1">
                    {totalSpent.toLocaleString("en-US")}
                  </p>
                  <p className="gld-label text-[10px] text-muted-foreground">{localized(locale, "EGP Spent", "ج.م مصروف")}</p>
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
                      "w-full flex items-center gap-3 px-3 py-2.5 text-sm transition-colors " +
                      (active
                        ? "text-foreground font-medium bg-muted border-s-2 border-gold"
                        : "text-muted-foreground hover:text-gold")
                    }
                  >
                    <item.icon size={15} className={active ? "text-gold" : ""} />
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
              className="gld-btn-outline inline-flex items-center gap-2"
            >
              <LogOut size={13} className="rtl:-scale-x-100" aria-hidden="true" />
              {localized(locale, "Sign out", "تسجيل الخروج")}
            </button>
          </div>

          {/* ═══ Content ═══ */}
          <div className="flex-1 min-w-0">
            {/* ─── Orders ─── */}
            {activeTab === "orders" && (
              <div>
                <SectionHeading sectionId={sectionId} settingKey="orders_title" value={ordersTitle} />
                {loadingOrders ? (
                  <div className="flex justify-center py-16">
                    <Loader2 className="h-5 w-5 animate-spin text-gold" />
                  </div>
                ) : orders.length === 0 ? (
                  <div className="text-center py-16 border border-border bg-card">
                    <div className="w-10 h-px bg-gold mx-auto mb-5" />
                    <p className="text-sm text-foreground mb-1">{localized(locale, "No orders yet", "لا توجد طلبات بعد")}</p>
                    <p className="text-xs text-muted-foreground mb-5">
                      {localized(locale, "Your orders will appear here after your first purchase", "ستظهر طلباتك هنا بعد أول عملية شراء")}
                    </p>
                    <Link
                      to="/products"
                      className="text-xs font-medium uppercase tracking-[0.15em] text-gold border-b border-gold pb-0.5 hover:opacity-70 transition-opacity"
                    >
                      {localized(locale, "Browse products", "تصفح المنتجات")}
                    </Link>
                  </div>
                ) : (
                  <div className="border border-border bg-card overflow-hidden">
                    {orders.map((order, i) => (
                      <div
                        key={order.id}
                        className={
                          "px-5 py-4 hover:bg-muted/60 transition-colors " +
                          (i > 0 ? "border-t border-border" : "")
                        }
                      >
                        <div className="flex items-start justify-between mb-2 gap-3">
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
                                ? ` · ${order.item_count} ${localized(locale, order.item_count > 1 ? "items" : "item", "منتج")}`
                                : ""}
                            </span>
                          </div>
                          <div className="text-end shrink-0">
                            <span className="text-[13px] font-medium text-gold block">
                              {/* useOrders total is CENTS → /100 for <Money> (major units) */}
                              <Money amount={order.total / 100} currency={order.currency} />
                            </span>
                            <span className="text-[10px] uppercase tracking-[0.1em] px-1.5 py-0.5 bg-muted text-foreground/70">
                              {STATUS_LABELS[order.status] || order.status}
                            </span>
                          </div>
                        </div>
                        {order.payment_status && (
                          <span className="text-[10px] text-muted-foreground">
                            {PAYMENT_LABELS[order.payment_status] || order.payment_status}
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* ─── Addresses ─── */}
            {activeTab === "addresses" && (
              <div>
                <div className="flex items-end justify-between mb-5">
                  <SectionHeading
                    sectionId={sectionId}
                    settingKey="addresses_title"
                    value={addressesTitle}
                    className="mb-0"
                  />
                  {!showAddressForm && (
                    <button
                      type="button"
                      onClick={openNewAddress}
                      className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-[0.1em] text-gold hover:opacity-70 transition-opacity"
                    >
                      <Plus size={13} />
                      {localized(locale, "Add address", "إضافة عنوان")}
                    </button>
                  )}
                </div>

                {showAddressForm && (
                  <div className="border border-border bg-card p-5 mb-5">
                    <div className="flex items-center justify-between mb-4">
                      <h3 className="gld-label text-sm text-foreground">
                        {editingId ? localized(locale, "Edit address", "تعديل العنوان") : localized(locale, "New address", "عنوان جديد")}
                      </h3>
                      <button
                        type="button"
                        onClick={closeAddressForm}
                        className="text-muted-foreground hover:text-gold transition-colors"
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
                        placeholder={localized(locale, "Street, building, apartment", "الشارع، المبنى، الشقة")}
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
                        <label className={labelClass}>{localized(locale, "Phone", "رقم الموبايل")}</label>
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
                              "flex items-center gap-1.5 px-3 py-1.5 text-xs uppercase tracking-[0.1em] border transition-colors " +
                              (active
                                ? "border-gold text-gold bg-gold/10"
                                : "border-border text-muted-foreground hover:border-gold")
                            }
                          >
                            <Icon size={12} />
                            {LABEL_NAME[l]}
                          </button>
                        );
                      })}
                    </div>
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={handleSaveAddress}
                        disabled={savingAddress}
                        className="gld-btn disabled:opacity-50"
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
                        className="px-5 py-2 text-xs uppercase tracking-[0.1em] text-muted-foreground hover:text-gold transition-colors"
                      >
                        {localized(locale, "Cancel", "إلغاء")}
                      </button>
                    </div>
                  </div>
                )}

                {loadingAddresses ? (
                  <div className="flex justify-center py-16">
                    <Loader2 className="h-5 w-5 animate-spin text-gold" />
                  </div>
                ) : addresses.length === 0 && !showAddressForm ? (
                  <div className="text-center py-16 border border-border bg-card">
                    <div className="w-10 h-px bg-gold mx-auto mb-5" />
                    <p className="text-sm text-foreground mb-1">{localized(locale, "No saved addresses", "لا توجد عناوين محفوظة")}</p>
                    <p className="text-xs text-muted-foreground mb-5">
                      {localized(locale, "Add an address to speed up checkout", "أضف عنواناً لتسريع عملية الدفع")}
                    </p>
                    <button
                      type="button"
                      onClick={openNewAddress}
                      className="text-xs font-medium uppercase tracking-[0.15em] text-gold border-b border-gold pb-0.5 hover:opacity-70 transition-opacity"
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
                            "border p-4 bg-card transition-colors " +
                            (addr.is_default ? "border-gold" : "border-border")
                          }
                        >
                          <div className="flex items-start justify-between mb-2">
                            <div className="flex items-center gap-2">
                              <LabelIcon size={13} className="text-gold" />
                              <span className="text-xs font-medium uppercase tracking-[0.1em] text-foreground">
                                {LABEL_NAME[addr.label ?? "other"] || localized(locale, "Other", "آخر")}
                              </span>
                              {addr.is_default && (
                                <span className="text-[9px] uppercase tracking-[0.1em] px-1.5 py-0.5 bg-gold text-foreground">
                                  {localized(locale, "Default", "افتراضي")}
                                </span>
                              )}
                            </div>
                            <div className="flex gap-1">
                              <button
                                type="button"
                                onClick={() => openEditAddress(addr)}
                                className="text-[10px] uppercase tracking-[0.1em] text-muted-foreground hover:text-gold transition-colors px-1"
                              >
                                {localized(locale, "Edit", "تعديل")}
                              </button>
                              <button
                                type="button"
                                onClick={() => deleteAddress(addr.id)}
                                className="text-muted-foreground hover:text-sale transition-colors px-1"
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
                              className="text-[10px] uppercase tracking-[0.1em] text-muted-foreground hover:text-gold transition-colors mt-2 border-b border-current pb-px"
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
                  <SectionHeading sectionId={sectionId} settingKey="settings_title" value={settingsTitle} />
                  <div className="border border-border bg-card p-5 space-y-3">
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
                      <label className={labelClass}>{localized(locale, "Phone", "رقم الموبايل")}</label>
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
                      className="gld-btn disabled:opacity-50"
                    >
                      {savingProfile ? <Loader2 size={14} className="animate-spin" /> : localized(locale, "Save changes", "حفظ التغييرات")}
                    </button>
                  </div>
                </div>

                <div>
                  <SectionHeading sectionId={sectionId} settingKey="password_title" value={passwordTitle} />
                  <div className="border border-border bg-card p-5 space-y-3">
                    <div>
                      <label className={labelClass}>{localized(locale, "Current password", "كلمة المرور الحالية")}</label>
                      <div className="relative">
                        <input
                          type={showPw ? "text" : "password"}
                          value={currentPw}
                          onChange={(e) => setCurrentPw(e.target.value)}
                          className={inputClass + " pe-10"}
                        />
                        <button
                          type="button"
                          onClick={() => setShowPw((v) => !v)}
                          className="absolute end-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-gold transition-colors"
                          aria-label={showPw ? localized(locale, "Hide password", "إخفاء كلمة المرور") : localized(locale, "Show password", "إظهار كلمة المرور")}
                        >
                          {showPw ? <EyeOff size={14} /> : <Eye size={14} />}
                        </button>
                      </div>
                    </div>
                    <div>
                      <label className={labelClass}>{localized(locale, "New password", "كلمة المرور الجديدة")}</label>
                      <input
                        type={showPw ? "text" : "password"}
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
                      className="gld-btn disabled:opacity-50"
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
