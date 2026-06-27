"use client";

import { useEffect, useState, type CSSProperties } from "react";
import {
  Link,
  useCustomer,
  useOrders,
  useCustomerActions,
  useCustomerAddresses,
  useLocale,
  useResolvedSettings,
  type CustomerAddress,
} from "@numueg/theme-sdk";
import {
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
 * by-profile-section — Bon Younes account / profile dashboard.
 *
 * Ported from luxury-minimal's lux-profile-section and re-skinned to the
 * Bon Younes cafe idiom: cream paper + espresso ink + warm caramel accents,
 * rounded organic shapes (--by-radius-*), pill `.by-btn` controls, serif
 * headings, clamp()-based responsive type. Renders the body of the `profile`
 * template (the storefront /account route now ships `page.type:"profile"`).
 *
 * Data/actions are SDK-native (useCustomer / useOrders / useCustomerAddresses
 * / useCustomerActions). Never blank / never crashes: logged-out → auth guard,
 * logged-in but empty → empty states, loading → spinners. RTL-safe via logical
 * CSS properties (marginInline*, textAlign:"start"/"end").
 */

type Tab = "orders" | "addresses" | "settings";

const statusLabels = (locale: string): Record<string, string> => ({
  pending: localized(locale, "Pending", "قيد الانتظار"),
  confirmed: localized(locale, "Confirmed", "مؤكد"),
  processing: localized(locale, "Processing", "قيد التجهيز"),
  shipped: localized(locale, "Shipped", "تم الشحن"),
  delivered: localized(locale, "Delivered", "تم التوصيل"),
  cancelled: localized(locale, "Cancelled", "ملغي"),
  refunded: localized(locale, "Refunded", "مسترد"),
});

const LABEL_ICON: Record<string, typeof Home> = { home: Home, work: Briefcase, other: MapPin };
const labelName = (locale: string): Record<string, string> => ({
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

// ── Shared inline-style fragments (Bon Younes tokens) ─────────────────────
const cream = "var(--by-cream, #f7f1e8)";
const paper = "var(--by-paper, #fffaf2)";
const espresso = "var(--by-espresso, #3a2418)";
const caramelDeep = "var(--by-caramel-deep, #8c5a30)";
const inkMuted = "var(--by-ink-muted, #6b4a36)";
const line = "var(--by-line, rgba(58,36,24,0.14))";
const foam = "var(--by-foam, #f3e5cf)";
const radiusMd = "var(--by-radius-md, 22px)";
const radiusSm = "var(--by-radius-sm, 14px)";
const radiusPill = "var(--by-radius-pill, 9999px)";
const fontSerif = "var(--by-font-serif, 'Cormorant Garamond', 'Playfair Display', serif)";

const fieldStyle: CSSProperties = {
  width: "100%",
  minHeight: 44,
  padding: "0.65rem 0.9rem",
  background: paper,
  border: `1px solid ${line}`,
  borderRadius: radiusPill,
  color: espresso,
  fontFamily: "var(--by-font-sans, 'DM Sans', system-ui, sans-serif)",
  fontSize: "0.92rem",
};

const eyebrowStyle: CSSProperties = {
  fontFamily: "var(--by-font-sans, 'DM Sans', system-ui, sans-serif)",
  fontSize: "0.7rem",
  letterSpacing: "0.28em",
  textTransform: "uppercase",
  color: caramelDeep,
  margin: 0,
};

const cardStyle: CSSProperties = {
  background: paper,
  border: `1px solid ${line}`,
  borderRadius: radiusMd,
  boxShadow: "var(--by-shadow-sm, 0 1px 2px rgba(58,36,24,0.06))",
};

export default function ByProfileSection({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const locale = useLocale();
  const STATUS_LABELS = statusLabels(locale);
  const LABEL_NAME = labelName(locale);
  const title = asString(s.title) || localized(locale, "My Account", "حسابي");
  const ordersTitle = asString(s.orders_title) || localized(locale, "My Orders", "طلباتي");
  const addressesTitle = asString(s.addresses_title) || localized(locale, "My Addresses", "عناويني");
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

  // ── Logged-out auth guard ────────────────────────────────────────
  if (!customer) {
    return (
      <section
        className="by-profile by-profile--guest"
        data-by-section={sectionId}
        data-testid="storefront-profile"
        style={{
          background: cream,
          minHeight: "60vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: "4rem 1rem",
        }}
      >
        <div style={{ maxWidth: 440, textAlign: "center" }}>
          <div
            style={{
              width: 64,
              height: 64,
              borderRadius: radiusPill,
              background: foam,
              display: "grid",
              placeItems: "center",
              margin: "0 auto 1.25rem",
              color: espresso,
            }}
          >
            <User size={26} />
          </div>
          <h1
            style={{
              fontFamily: fontSerif,
              fontWeight: 500,
              fontSize: "clamp(1.5rem, 4vw, 2rem)",
              color: espresso,
              margin: "0 0 0.5rem",
            }}
          >
            {localized(locale, "Sign in to view your account", "سجّل الدخول لعرض حسابك")}
          </h1>
          <p style={{ color: inkMuted, fontSize: "0.92rem", lineHeight: 1.6, margin: "0 0 1.75rem" }}>
            {localized(
              locale,
              "Track orders and manage your addresses and settings.",
              "تتبّع طلباتك وادِر عناوينك وإعداداتك.",
            )}
          </p>
          <Link to="/auth?redirect=/account" className="by-btn">
            {localized(locale, "Sign in", "تسجيل الدخول")}
          </Link>
        </div>
      </section>
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

  const fieldLabel = (text: string) => (
    <span
      style={{
        display: "block",
        fontSize: "0.68rem",
        letterSpacing: "0.16em",
        textTransform: "uppercase",
        color: inkMuted,
        marginBottom: "0.35rem",
      }}
    >
      {text}
    </span>
  );

  const sectionHeading = (text: string) => (
    <h2
      style={{
        fontFamily: fontSerif,
        fontWeight: 500,
        fontSize: "clamp(1.4rem, 3.4vw, 1.9rem)",
        color: espresso,
        margin: "0 0 1.25rem",
        lineHeight: 1.1,
      }}
    >
      {text}
    </h2>
  );

  return (
    <section
      className="by-profile"
      data-by-section={sectionId}
      data-testid="storefront-profile"
      style={{ background: cream, padding: "clamp(1.75rem, 5vw, 3rem) 1rem", minHeight: "60vh" }}
    >
      <div style={{ maxWidth: "var(--by-shell-max, 1240px)", margin: "0 auto" }}>
        {/* Breadcrumb */}
        <nav
          style={{
            display: "flex",
            alignItems: "center",
            gap: "0.5rem",
            fontSize: "0.68rem",
            letterSpacing: "0.16em",
            textTransform: "uppercase",
            color: inkMuted,
            marginBottom: "1.75rem",
          }}
          aria-label="Breadcrumb"
        >
          <Link to="/" style={{ color: inkMuted }}>
            {localized(locale, "Home", "الرئيسية")}
          </Link>
          <span aria-hidden>/</span>
          <span style={{ color: espresso }}>{title}</span>
        </nav>

        <div
          className="by-profile-layout"
          style={{ display: "flex", flexDirection: "column", gap: "clamp(1.5rem, 4vw, 2.5rem)" }}
        >
          {/* ═══ Sidebar ═══ */}
          <aside className="by-profile-sidebar" style={{ flex: "0 0 auto" }}>
            <div
              style={{
                ...cardStyle,
                padding: "1.5rem 1.4rem",
                display: "flex",
                flexDirection: "column",
                gap: "1.25rem",
              }}
            >
              {/* Identity */}
              <div style={{ display: "flex", alignItems: "center", gap: "0.85rem" }}>
                <div
                  style={{
                    width: 52,
                    height: 52,
                    borderRadius: radiusPill,
                    background: espresso,
                    color: cream,
                    display: "grid",
                    placeItems: "center",
                    fontFamily: fontSerif,
                    fontSize: "1.35rem",
                    flexShrink: 0,
                    boxShadow: "var(--by-shadow-sm, 0 1px 2px rgba(58,36,24,0.06))",
                  }}
                >
                  {initial}
                </div>
                <div style={{ minWidth: 0 }}>
                  <h1
                    style={{
                      fontFamily: fontSerif,
                      fontWeight: 500,
                      fontSize: "1.2rem",
                      color: espresso,
                      margin: 0,
                      lineHeight: 1.2,
                      wordBreak: "break-word",
                    }}
                  >
                    {fullName}
                  </h1>
                  <p style={{ fontSize: "0.78rem", color: inkMuted, margin: 0 }} dir="ltr">
                    {customer.email}
                  </p>
                </div>
              </div>

              {showStats && (
                <div
                  style={{
                    display: "flex",
                    gap: "0.75rem",
                    paddingBottom: "1.25rem",
                    borderBottom: `1px solid ${line}`,
                  }}
                >
                  <div
                    style={{
                      flex: 1,
                      background: foam,
                      borderRadius: radiusSm,
                      padding: "0.75rem 0.85rem",
                    }}
                  >
                    <p style={{ fontFamily: fontSerif, fontSize: "1.4rem", color: espresso, margin: 0 }}>
                      {orders.length}
                    </p>
                    <p style={{ fontSize: "0.66rem", letterSpacing: "0.12em", textTransform: "uppercase", color: inkMuted, margin: 0 }}>
                      {localized(locale, "Orders", "طلبات")}
                    </p>
                  </div>
                  <div
                    style={{
                      flex: 1,
                      background: foam,
                      borderRadius: radiusSm,
                      padding: "0.75rem 0.85rem",
                    }}
                  >
                    <p style={{ fontFamily: fontSerif, fontSize: "1.4rem", color: espresso, margin: 0 }}>
                      {totalSpent.toLocaleString("en-US")}
                    </p>
                    <p style={{ fontSize: "0.66rem", letterSpacing: "0.12em", textTransform: "uppercase", color: inkMuted, margin: 0 }}>
                      {localized(locale, "EGP", "ج.م")}
                    </p>
                  </div>
                </div>
              )}

              {/* Nav */}
              <nav style={{ display: "flex", flexDirection: "column", gap: "0.3rem" }}>
                {tabs.map((item) => {
                  const active = activeTab === item.key;
                  const Icon = item.icon;
                  return (
                    <button
                      key={item.key}
                      type="button"
                      onClick={() => setActiveTab(item.key)}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: "0.7rem",
                        width: "100%",
                        padding: "0.7rem 0.85rem",
                        borderRadius: radiusPill,
                        border: "none",
                        cursor: "pointer",
                        fontFamily: "var(--by-font-sans, 'DM Sans', system-ui, sans-serif)",
                        fontSize: "0.88rem",
                        textAlign: "start",
                        background: active ? espresso : "transparent",
                        color: active ? cream : espresso,
                        transition: "background 220ms ease, color 220ms ease",
                      }}
                    >
                      <Icon size={16} style={{ flexShrink: 0 }} />
                      <span style={{ flex: 1, textAlign: "start" }}>{item.label}</span>
                      {item.count !== undefined && (
                        <span
                          style={{
                            fontSize: "0.72rem",
                            color: active ? cream : inkMuted,
                            opacity: 0.85,
                          }}
                        >
                          {item.count}
                        </span>
                      )}
                    </button>
                  );
                })}
              </nav>

              <button
                type="button"
                onClick={() => logout()}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "0.5rem",
                  padding: "0.5rem 0.85rem",
                  border: "none",
                  background: "transparent",
                  color: inkMuted,
                  fontSize: "0.8rem",
                  cursor: "pointer",
                  alignSelf: "flex-start",
                }}
              >
                <LogOut size={14} />
                {localized(locale, "Sign out", "تسجيل الخروج")}
              </button>
            </div>
          </aside>

          {/* ═══ Content ═══ */}
          <div style={{ flex: 1, minWidth: 0 }}>
            {/* ─── Orders ─── */}
            {activeTab === "orders" && (
              <div>
                {sectionHeading(ordersTitle)}
                {loadingOrders ? (
                  <div style={{ display: "flex", justifyContent: "center", padding: "4rem 0" }}>
                    <Loader2 size={22} style={{ animation: "spin 1s linear infinite", color: inkMuted }} />
                  </div>
                ) : orders.length === 0 ? (
                  <div
                    style={{
                      ...cardStyle,
                      textAlign: "center",
                      padding: "3.5rem 1.5rem",
                    }}
                  >
                    <p style={{ fontFamily: fontSerif, fontSize: "1.2rem", color: espresso, margin: "0 0 0.4rem" }}>
                      {localized(locale, "No orders yet", "لا توجد طلبات بعد")}
                    </p>
                    <p style={{ fontSize: "0.88rem", color: inkMuted, margin: "0 0 1.5rem", lineHeight: 1.6 }}>
                      {localized(
                        locale,
                        "Your orders will appear here after your first purchase.",
                        "هتظهر طلباتك هنا بعد أول عملية شراء.",
                      )}
                    </p>
                    <Link to="/products" className="by-btn">
                      {localized(locale, "Browse products", "تصفّح المنتجات")}
                    </Link>
                  </div>
                ) : (
                  <div style={{ display: "flex", flexDirection: "column", gap: "0.85rem" }}>
                    {orders.map((order) => (
                      <div
                        key={order.id}
                        style={{
                          ...cardStyle,
                          padding: "1rem 1.2rem",
                          display: "flex",
                          alignItems: "flex-start",
                          justifyContent: "space-between",
                          gap: "1rem",
                          flexWrap: "wrap",
                        }}
                      >
                        <div style={{ minWidth: 0 }}>
                          <span
                            style={{
                              display: "block",
                              fontFamily: "var(--by-mono, 'JetBrains Mono', monospace)",
                              fontWeight: 600,
                              fontSize: "0.9rem",
                              color: espresso,
                            }}
                            dir="ltr"
                          >
                            {order.order_number}
                          </span>
                          <span style={{ fontSize: "0.78rem", color: inkMuted }}>
                            {order.created_at
                              ? new Date(order.created_at).toLocaleDateString("en-US", {
                                  day: "numeric",
                                  month: "short",
                                  year: "numeric",
                                })
                              : ""}
                            {order.item_count
                              ? ` · ${order.item_count} ${localized(locale, "items", "منتج")}`
                              : ""}
                          </span>
                        </div>
                        <div style={{ textAlign: "end" }}>
                          <span
                            style={{
                              display: "block",
                              fontFamily: fontSerif,
                              fontWeight: 600,
                              fontSize: "1.05rem",
                              color: espresso,
                            }}
                          >
                            {(order.total / 100).toLocaleString("en-US")}{" "}
                            {localized(locale, "EGP", "ج.م")}
                          </span>
                          <span
                            style={{
                              display: "inline-block",
                              marginTop: "0.25rem",
                              padding: "0.2rem 0.65rem",
                              borderRadius: radiusPill,
                              background: foam,
                              color: caramelDeep,
                              fontSize: "0.68rem",
                              letterSpacing: "0.1em",
                              textTransform: "uppercase",
                            }}
                          >
                            {STATUS_LABELS[order.status] || order.status}
                          </span>
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
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    gap: "1rem",
                    marginBottom: "1.25rem",
                    flexWrap: "wrap",
                  }}
                >
                  <h2
                    style={{
                      fontFamily: fontSerif,
                      fontWeight: 500,
                      fontSize: "clamp(1.4rem, 3.4vw, 1.9rem)",
                      color: espresso,
                      margin: 0,
                      lineHeight: 1.1,
                    }}
                  >
                    {addressesTitle}
                  </h2>
                  {!showAddressForm && (
                    <button type="button" onClick={openNewAddress} className="by-btn by-btn-ghost">
                      <Plus size={15} />
                      {localized(locale, "Add address", "إضافة عنوان")}
                    </button>
                  )}
                </div>

                {showAddressForm && (
                  <div style={{ ...cardStyle, padding: "1.4rem", marginBottom: "1.25rem" }}>
                    <div
                      style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                        marginBottom: "1rem",
                      }}
                    >
                      <h3 style={{ fontFamily: fontSerif, fontSize: "1.15rem", color: espresso, margin: 0 }}>
                        {editingId
                          ? localized(locale, "Edit address", "تعديل العنوان")
                          : localized(locale, "New address", "عنوان جديد")}
                      </h3>
                      <button
                        type="button"
                        onClick={closeAddressForm}
                        aria-label={localized(locale, "Close", "إغلاق")}
                        style={{
                          width: 36,
                          height: 36,
                          borderRadius: radiusPill,
                          border: "none",
                          background: "transparent",
                          color: inkMuted,
                          cursor: "pointer",
                          display: "grid",
                          placeItems: "center",
                        }}
                      >
                        <X size={18} />
                      </button>
                    </div>
                    <div
                      style={{
                        display: "grid",
                        gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
                        gap: "0.85rem",
                        marginBottom: "0.85rem",
                      }}
                    >
                      <label>
                        {fieldLabel(localized(locale, "First name", "الاسم الأول"))}
                        <input
                          value={form.first_name ?? ""}
                          onChange={(e) => setForm((p) => ({ ...p, first_name: e.target.value }))}
                          style={fieldStyle}
                        />
                      </label>
                      <label>
                        {fieldLabel(localized(locale, "Last name", "اسم العائلة"))}
                        <input
                          value={form.last_name ?? ""}
                          onChange={(e) => setForm((p) => ({ ...p, last_name: e.target.value }))}
                          style={fieldStyle}
                        />
                      </label>
                    </div>
                    <label style={{ display: "block", marginBottom: "0.85rem" }}>
                      {fieldLabel(localized(locale, "Address", "العنوان"))}
                      <input
                        value={form.address_line1 ?? ""}
                        onChange={(e) => setForm((p) => ({ ...p, address_line1: e.target.value }))}
                        placeholder={localized(locale, "Street, building, apartment", "الشارع، المبنى، الشقة")}
                        style={fieldStyle}
                      />
                    </label>
                    <div
                      style={{
                        display: "grid",
                        gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
                        gap: "0.85rem",
                        marginBottom: "0.85rem",
                      }}
                    >
                      <label>
                        {fieldLabel(localized(locale, "City", "المدينة"))}
                        <input
                          value={form.city ?? ""}
                          onChange={(e) => setForm((p) => ({ ...p, city: e.target.value }))}
                          style={fieldStyle}
                        />
                      </label>
                      <label>
                        {fieldLabel(localized(locale, "Phone", "الهاتف"))}
                        <input
                          value={form.phone ?? ""}
                          onChange={(e) => setForm((p) => ({ ...p, phone: e.target.value }))}
                          placeholder="01xxxxxxxxx"
                          dir="ltr"
                          style={fieldStyle}
                        />
                      </label>
                    </div>
                    <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", marginBottom: "1.1rem" }}>
                      {(["home", "work", "other"] as const).map((l) => {
                        const Icon = LABEL_ICON[l];
                        const active = form.label === l;
                        return (
                          <button
                            key={l}
                            type="button"
                            onClick={() => setForm((p) => ({ ...p, label: l }))}
                            style={{
                              display: "inline-flex",
                              alignItems: "center",
                              gap: "0.4rem",
                              padding: "0.45rem 0.85rem",
                              borderRadius: radiusPill,
                              border: `1px solid ${active ? espresso : line}`,
                              background: active ? espresso : paper,
                              color: active ? cream : inkMuted,
                              fontSize: "0.78rem",
                              cursor: "pointer",
                              minHeight: 38,
                              transition: "background 220ms ease, color 220ms ease, border-color 220ms ease",
                            }}
                          >
                            <Icon size={13} />
                            {LABEL_NAME[l]}
                          </button>
                        );
                      })}
                    </div>
                    <div style={{ display: "flex", gap: "0.6rem", flexWrap: "wrap" }}>
                      <button
                        type="button"
                        onClick={handleSaveAddress}
                        disabled={savingAddress}
                        className="by-btn"
                        style={savingAddress ? { opacity: 0.55, cursor: "not-allowed" } : undefined}
                      >
                        {savingAddress ? (
                          <Loader2 size={15} style={{ animation: "spin 1s linear infinite" }} />
                        ) : editingId ? (
                          localized(locale, "Update", "تحديث")
                        ) : (
                          localized(locale, "Save", "حفظ")
                        )}
                      </button>
                      <button type="button" onClick={closeAddressForm} className="by-btn by-btn-ghost">
                        {localized(locale, "Cancel", "إلغاء")}
                      </button>
                    </div>
                  </div>
                )}

                {loadingAddresses ? (
                  <div style={{ display: "flex", justifyContent: "center", padding: "4rem 0" }}>
                    <Loader2 size={22} style={{ animation: "spin 1s linear infinite", color: inkMuted }} />
                  </div>
                ) : addresses.length === 0 && !showAddressForm ? (
                  <div style={{ ...cardStyle, textAlign: "center", padding: "3.5rem 1.5rem" }}>
                    <p style={{ fontFamily: fontSerif, fontSize: "1.2rem", color: espresso, margin: "0 0 0.4rem" }}>
                      {localized(locale, "No saved addresses", "لا توجد عناوين محفوظة")}
                    </p>
                    <p style={{ fontSize: "0.88rem", color: inkMuted, margin: "0 0 1.5rem", lineHeight: 1.6 }}>
                      {localized(locale, "Add an address to speed up checkout.", "ضيف عنوان عشان تسرّع الدفع.")}
                    </p>
                    <button type="button" onClick={openNewAddress} className="by-btn">
                      <Plus size={15} />
                      {localized(locale, "Add address", "إضافة عنوان")}
                    </button>
                  </div>
                ) : (
                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
                      gap: "0.85rem",
                    }}
                  >
                    {addresses.map((addr) => {
                      const LabelIcon = LABEL_ICON[addr.label ?? "other"] || MapPin;
                      return (
                        <div
                          key={addr.id}
                          style={{
                            ...cardStyle,
                            padding: "1.1rem 1.2rem",
                            border: `1px solid ${addr.is_default ? espresso : line}`,
                          }}
                        >
                          <div
                            style={{
                              display: "flex",
                              alignItems: "flex-start",
                              justifyContent: "space-between",
                              gap: "0.5rem",
                              marginBottom: "0.6rem",
                            }}
                          >
                            <div style={{ display: "flex", alignItems: "center", gap: "0.45rem" }}>
                              <LabelIcon size={14} style={{ color: caramelDeep }} />
                              <span style={{ fontSize: "0.82rem", fontWeight: 600, color: espresso }}>
                                {LABEL_NAME[addr.label ?? "other"] || localized(locale, "Other", "آخر")}
                              </span>
                              {addr.is_default && (
                                <span
                                  style={{
                                    padding: "0.12rem 0.5rem",
                                    borderRadius: radiusPill,
                                    background: foam,
                                    color: caramelDeep,
                                    fontSize: "0.62rem",
                                    letterSpacing: "0.08em",
                                    textTransform: "uppercase",
                                  }}
                                >
                                  {localized(locale, "Default", "افتراضي")}
                                </span>
                              )}
                            </div>
                            <div style={{ display: "flex", gap: "0.25rem", flexShrink: 0 }}>
                              <button
                                type="button"
                                onClick={() => openEditAddress(addr)}
                                style={{
                                  border: "none",
                                  background: "transparent",
                                  color: inkMuted,
                                  fontSize: "0.74rem",
                                  cursor: "pointer",
                                  padding: "0.2rem 0.35rem",
                                }}
                              >
                                {localized(locale, "Edit", "تعديل")}
                              </button>
                              <button
                                type="button"
                                onClick={() => deleteAddress(addr.id)}
                                aria-label={localized(locale, "Delete address", "حذف العنوان")}
                                style={{
                                  border: "none",
                                  background: "transparent",
                                  color: inkMuted,
                                  cursor: "pointer",
                                  padding: "0.2rem 0.35rem",
                                  display: "grid",
                                  placeItems: "center",
                                }}
                              >
                                <Trash2 size={13} />
                              </button>
                            </div>
                          </div>
                          <p style={{ fontSize: "0.9rem", color: espresso, margin: "0 0 0.15rem" }}>
                            {[addr.first_name, addr.last_name].filter(Boolean).join(" ")}
                          </p>
                          <p style={{ fontSize: "0.82rem", color: inkMuted, margin: 0 }}>{addr.address_line1}</p>
                          {addr.address_line2 && (
                            <p style={{ fontSize: "0.82rem", color: inkMuted, margin: 0 }}>{addr.address_line2}</p>
                          )}
                          <p style={{ fontSize: "0.82rem", color: inkMuted, margin: 0 }}>
                            {addr.city}
                            {addr.state ? `, ${addr.state}` : ""}
                          </p>
                          {addr.phone && (
                            <p style={{ fontSize: "0.82rem", color: inkMuted, margin: "0.25rem 0 0" }} dir="ltr">
                              {addr.phone}
                            </p>
                          )}
                          {!addr.is_default && (
                            <button
                              type="button"
                              onClick={() => setDefaultAddress(addr.id)}
                              style={{
                                marginTop: "0.7rem",
                                border: "none",
                                background: "transparent",
                                color: caramelDeep,
                                fontSize: "0.74rem",
                                cursor: "pointer",
                                padding: 0,
                                borderBottom: `1px solid ${caramelDeep}`,
                              }}
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
              <div style={{ display: "flex", flexDirection: "column", gap: "1.75rem" }}>
                <div>
                  {sectionHeading(settingsTitle)}
                  <div style={{ ...cardStyle, padding: "1.4rem", display: "flex", flexDirection: "column", gap: "0.85rem" }}>
                    <div
                      style={{
                        display: "grid",
                        gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
                        gap: "0.85rem",
                      }}
                    >
                      <label>
                        {fieldLabel(localized(locale, "First name", "الاسم الأول"))}
                        <input value={firstName} onChange={(e) => setFirstName(e.target.value)} style={fieldStyle} />
                      </label>
                      <label>
                        {fieldLabel(localized(locale, "Last name", "اسم العائلة"))}
                        <input value={lastName} onChange={(e) => setLastName(e.target.value)} style={fieldStyle} />
                      </label>
                    </div>
                    <label>
                      {fieldLabel(localized(locale, "Email", "البريد الإلكتروني"))}
                      <input
                        value={customer.email}
                        disabled
                        dir="ltr"
                        style={{ ...fieldStyle, opacity: 0.6, cursor: "not-allowed" }}
                      />
                    </label>
                    <label>
                      {fieldLabel(localized(locale, "Phone", "الهاتف"))}
                      <input
                        value={phone}
                        onChange={(e) => setPhone(e.target.value)}
                        placeholder="01xxxxxxxxx"
                        dir="ltr"
                        style={fieldStyle}
                      />
                    </label>
                    <button
                      type="button"
                      onClick={handleSaveProfile}
                      disabled={savingProfile}
                      className="by-btn"
                      style={{ alignSelf: "flex-start", ...(savingProfile ? { opacity: 0.55, cursor: "not-allowed" } : {}) }}
                    >
                      {savingProfile ? (
                        <Loader2 size={15} style={{ animation: "spin 1s linear infinite" }} />
                      ) : (
                        localized(locale, "Save changes", "حفظ التغييرات")
                      )}
                    </button>
                  </div>
                </div>

                <div>
                  {sectionHeading(localized(locale, "Change password", "تغيير كلمة المرور"))}
                  <div style={{ ...cardStyle, padding: "1.4rem", display: "flex", flexDirection: "column", gap: "0.85rem" }}>
                    <label>
                      {fieldLabel(localized(locale, "Current password", "كلمة المرور الحالية"))}
                      <input
                        type="password"
                        value={currentPw}
                        onChange={(e) => setCurrentPw(e.target.value)}
                        style={fieldStyle}
                      />
                    </label>
                    <label>
                      {fieldLabel(localized(locale, "New password", "كلمة المرور الجديدة"))}
                      <input
                        type="password"
                        value={newPw}
                        onChange={(e) => setNewPw(e.target.value)}
                        placeholder={localized(locale, "Min 8 characters", "8 أحرف على الأقل")}
                        style={fieldStyle}
                      />
                    </label>
                    <button
                      type="button"
                      onClick={handleChangePassword}
                      disabled={changingPw || !currentPw || !newPw}
                      className="by-btn"
                      style={{
                        alignSelf: "flex-start",
                        ...(changingPw || !currentPw || !newPw ? { opacity: 0.55, cursor: "not-allowed" } : {}),
                      }}
                    >
                      {changingPw ? (
                        <Loader2 size={15} style={{ animation: "spin 1s linear infinite" }} />
                      ) : (
                        localized(locale, "Change password", "تغيير كلمة المرور")
                      )}
                    </button>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Desktop: sidebar beside content. Spinner keyframes (scoped). */}
      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        @media (min-width: 900px) {
          .by-profile .by-profile-layout { flex-direction: row; }
          .by-profile .by-profile-sidebar { width: 280px; }
          .by-profile .by-profile-sidebar > div { position: sticky; top: 92px; }
        }
      `}</style>
    </section>
  );
}
