"use client";

import { useEffect } from "react";
import {
  Link,
  Money,
  useCart,
  useLocale,
  type CartItem,
} from "@numueg/theme-sdk";
import { Minus, Plus, ShoppingBag, X } from "lucide-react";
import { localized } from "./_shared";

/**
 * BzCartDrawer — off-canvas mini-cart. Clicking the cart icon opens this
 * slide-in panel (a "small window", not a separate page): a dimmed scrim, a
 * panel that slides from the END side (right in LTR, left in RTL) with a smooth
 * animation, the line items (image, qty stepper, remove), a subtotal, and a
 * Checkout CTA. A "View cart" link still leads to the full /cart page.
 *
 * Reuses the same `useCart()` surface as the bz-cart page so item edits stay in
 * sync. Always mounted so it animates both in and out.
 */
export default function BzCartDrawer({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const { cart, updateQuantity, removeItem, loading } = useCart();
  const locale = useLocale();

  const items: CartItem[] = cart?.items ?? [];
  const currency = cart?.currency;
  const subtotal = cart?.subtotal ?? 0;
  const count = items.reduce((n, it) => n + it.quantity, 0);

  // Lock body scroll + close on Escape while open.
  useEffect(() => {
    if (typeof document === "undefined") return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = open ? "hidden" : prev || "";
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    if (open) window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener("keydown", onKey);
    };
  }, [open, onClose]);

  return (
    <>
      {/* Scrim */}
      <div
        aria-hidden="true"
        onClick={onClose}
        className={`fixed inset-0 z-[60] bg-black/50 backdrop-blur-[2px] transition-opacity duration-300 ${
          open ? "opacity-100" : "opacity-0 pointer-events-none"
        }`}
      />
      {/* Drawer panel — slides from the END side (right in LTR / left in RTL) */}
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={localized(locale, "Shopping cart", "سلة التسوق")}
        aria-hidden={!open}
        className={`fixed inset-y-0 end-0 z-[65] flex w-[90%] max-w-[400px] flex-col bg-[var(--bz-cream)] shadow-2xl transition-transform duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] ${
          open ? "translate-x-0" : "translate-x-full rtl:-translate-x-full"
        }`}
      >
        {/* Header */}
        <div className="flex items-center justify-between gap-3 bg-[var(--bz-dark)] px-5 py-4">
          <span className="bz-heading text-lg text-[var(--bz-amber)]">
            {localized(locale, "CART", "السلة")} ({count})
          </span>
          <button
            type="button"
            onClick={onClose}
            aria-label={localized(locale, "Close cart", "إغلاق السلة")}
            className="-me-1 p-1 text-[var(--bz-amber)] transition-opacity hover:opacity-80"
          >
            <X size={22} aria-hidden="true" />
          </button>
        </div>

        {items.length === 0 ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-4 px-6 text-center">
            <div className="flex h-16 w-16 items-center justify-center rounded-full bg-[var(--bz-amber)]/15">
              <ShoppingBag
                size={24}
                className="text-[var(--bz-amber)]"
                aria-hidden="true"
              />
            </div>
            <p className="bz-heading text-2xl text-[var(--bz-dark)]">
              {localized(locale, "YOUR CART IS EMPTY", "سلتك فاضية")}
            </p>
            <Link
              to="/products"
              onClick={onClose}
              className="bz-btn bz-btn-filled px-6 py-3 text-xs"
            >
              {localized(locale, "CONTINUE SHOPPING", "كمّل تسوّق")}
            </Link>
          </div>
        ) : (
          <>
            {/* Items (scrollable) */}
            <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
              {items.map((it) => (
                <div
                  key={it.id}
                  className="flex gap-3 rounded-xl border border-[var(--bz-dark)]/10 bg-white/70 p-3"
                >
                  {it.image_url ? (
                    <img
                      src={it.image_url}
                      alt={it.name}
                      className="h-20 w-16 shrink-0 rounded-lg bg-[var(--bz-cream)] object-cover"
                    />
                  ) : (
                    <div className="flex h-20 w-16 shrink-0 items-center justify-center rounded-lg bg-[var(--bz-cream)]">
                      <ShoppingBag
                        size={20}
                        className="text-[var(--bz-dark)]/20"
                        aria-hidden="true"
                      />
                    </div>
                  )}
                  <div className="flex min-w-0 flex-1 flex-col">
                    <h3 className="bz-heading truncate text-sm text-[var(--bz-dark)]">
                      {it.name}
                    </h3>
                    {it.variant_name && (
                      <p className="bz-label mt-0.5 text-[10px] text-[var(--bz-gray)]">
                        {it.variant_name}
                      </p>
                    )}
                    <p className="bz-heading mt-1 text-sm text-[var(--bz-dark)]">
                      <Money amount={it.price * it.quantity} currency={currency} />
                    </p>
                    <div className="mt-auto flex items-center justify-between pt-2">
                      <div className="flex items-center overflow-hidden rounded-full border border-[var(--bz-dark)]/15">
                        <button
                          type="button"
                          onClick={() => updateQuantity(it.id, it.quantity - 1)}
                          disabled={loading}
                          aria-label="Decrease quantity"
                          className="flex h-7 w-7 items-center justify-center text-[var(--bz-dark)]/60 transition-colors hover:text-[var(--bz-amber)] disabled:opacity-40"
                        >
                          <Minus size={12} aria-hidden="true" />
                        </button>
                        <span className="w-7 text-center text-xs font-bold text-[var(--bz-dark)]">
                          {it.quantity}
                        </span>
                        <button
                          type="button"
                          onClick={() => updateQuantity(it.id, it.quantity + 1)}
                          disabled={loading}
                          aria-label="Increase quantity"
                          className="flex h-7 w-7 items-center justify-center text-[var(--bz-dark)]/60 transition-colors hover:text-[var(--bz-amber)] disabled:opacity-40"
                        >
                          <Plus size={12} aria-hidden="true" />
                        </button>
                      </div>
                      <button
                        type="button"
                        onClick={() => removeItem(it.id)}
                        disabled={loading}
                        aria-label="Remove item"
                        className="flex h-7 w-7 items-center justify-center text-[var(--bz-dark)]/40 transition-colors hover:text-[var(--bz-dark)] disabled:opacity-40"
                      >
                        <X size={14} aria-hidden="true" />
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>

            {/* Footer */}
            <div className="space-y-3 border-t border-[var(--bz-dark)]/10 bg-[var(--bz-cream)] px-5 py-4">
              <div className="flex items-center justify-between">
                <span className="bz-label text-[var(--bz-gray)]">
                  {localized(locale, "SUBTOTAL", "الإجمالي الفرعي")}
                </span>
                <span className="bz-heading text-lg text-[var(--bz-dark)]">
                  <Money amount={subtotal} currency={currency} />
                </span>
              </div>
              <p className="text-[10px] text-[var(--bz-gray)]">
                {localized(
                  locale,
                  "Shipping & taxes calculated at checkout.",
                  "الشحن والضرائب تُحسب عند الدفع.",
                )}
              </p>
              <Link
                to="/checkout"
                onClick={onClose}
                className="bz-btn bz-btn-filled flex w-full items-center justify-center rounded-full py-3 text-center text-xs"
              >
                {localized(locale, "CHECKOUT", "إتمام الشراء")}
              </Link>
              <Link
                to="/cart"
                onClick={onClose}
                className="bz-label block text-center text-[var(--bz-dark)]/60 transition-colors hover:text-[var(--bz-amber)]"
              >
                {localized(locale, "VIEW CART", "عرض السلة")}
              </Link>
            </div>
          </>
        )}
      </aside>
    </>
  );
}
