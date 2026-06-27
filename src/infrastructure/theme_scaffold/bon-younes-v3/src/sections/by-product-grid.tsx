"use client";

import { Link, useLocale, useProducts, useResolvedSettings, type Product } from "@numueg/theme-sdk";
import { asString, asNumber, localized, type SectionRenderProps } from "./_shared";
import { InlineEditable } from "./_inline-editable";

/**
 * by-product-grid — the listing-page workhorse for the `products`
 * template. Renders a responsive grid of product cards with image,
 * name, and price. Reads from `useProducts()` which pulls from the
 * page context's `data.products` (the storefront's products route
 * pre-fetches 50; bundle gets them for free).
 *
 * Settings:
 *   - title            — eyebrow above the grid
 *   - subtitle         — supporting copy
 *   - columns_desktop  — grid columns at desktop (default 4)
 *   - columns_mobile   — grid columns at mobile (default 2)
 *   - max_items        — cap items rendered (0 = no cap; defaults to all)
 *   - empty_state_text — copy when there are no products yet
 */
export default function ByProductGrid({
  instance,
  sectionId,
}: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const { products, loading } = useProducts();
  const locale = useLocale();

  const title = asString(s.title) || localized(locale, "All drinks", "كل المشروبات");
  const subtitle =
    asString(s.subtitle) ||
    localized(
      locale,
      "From the first espresso of the morning to the last slice of basbousa.",
      "من أول إسبريسو الصبح لآخر قطعة بسبوسة.",
    );
  const colsDesktop = Math.max(1, Math.min(6, asNumber(s.columns_desktop, 4)));
  const colsMobile = Math.max(1, Math.min(3, asNumber(s.columns_mobile, 2)));
  const maxItems = asNumber(s.max_items, 0);
  const emptyState =
    asString(s.empty_state_text) ||
    localized(locale, "We're brewing fresh — come back in a moment.", "بنحضّر طازة — تعالى كمان شوية.");

  const displayed: Product[] =
    maxItems > 0 ? products.slice(0, maxItems) : products;

  const gridStyle = {
    display: "grid",
    gap: "1.5rem",
    gridTemplateColumns: `repeat(${colsMobile}, minmax(0, 1fr))`,
    ["--by-grid-cols-desktop" as string]: colsDesktop,
  } as React.CSSProperties;

  return (
    <section
      className="by-product-grid"
      data-by-section={sectionId}
      style={{ padding: "2.5rem 0", background: "var(--by-cream, #fdf8ee)" }}
    >
      <div className="by-shell" style={{ maxWidth: 1200, margin: "0 auto", padding: "0 1rem" }}>
        <header style={{ textAlign: "center", marginBottom: "2rem" }}>
          <h1
            style={{
              fontFamily: "var(--by-display, 'Playfair Display', serif)",
              fontSize: "clamp(1.75rem, 3vw, 2.5rem)",
              color: "var(--by-espresso, #3a2418)",
              margin: 0,
            }}
          >
            <InlineEditable
              sectionId={sectionId}
              settingKey="title"
              value={title}
            />
          </h1>
          <p
            style={{
              color: "rgba(58,36,24,0.7)",
              maxWidth: 640,
              margin: "0.5rem auto 0",
              fontSize: "0.95rem",
              lineHeight: 1.5,
            }}
          >
            <InlineEditable
              sectionId={sectionId}
              settingKey="subtitle"
              value={subtitle}
              multiline
            />
          </p>
        </header>

        {loading && products.length === 0 ? (
          <div
            style={{ textAlign: "center", padding: "3rem 0", color: "rgba(58,36,24,0.6)" }}
          >
            {localized(locale, "Loading drinks…", "بنحمّل المشروبات…")}
          </div>
        ) : displayed.length === 0 ? (
          <div
            style={{
              textAlign: "center",
              padding: "3rem 0",
              color: "rgba(58,36,24,0.6)",
              fontStyle: "italic",
            }}
          >
            <InlineEditable
              sectionId={sectionId}
              settingKey="empty_state_text"
              value={emptyState}
              multiline
            />
          </div>
        ) : (
          <div style={gridStyle} className="by-product-grid__grid">
            {displayed.map((p) => (
              <ProductCard key={p.id} product={p} />
            ))}
          </div>
        )}
      </div>
      <style>{`
        .by-product-grid__grid {
          --cols-desktop: var(--by-grid-cols-desktop, 4);
        }
        @media (min-width: 768px) {
          .by-product-grid__grid {
            grid-template-columns: repeat(var(--cols-desktop), minmax(0, 1fr)) !important;
          }
        }
      `}</style>
    </section>
  );
}

function ProductCard({ product }: { product: Product }) {
  const price = typeof product.price === "number" ? product.price : 0;
  const compareAt =
    typeof product.compare_at_price === "number"
      ? product.compare_at_price
      : null;
  const slugOrId = product.slug || product.id;
  const image = product.images?.[0]?.url || null;

  return (
    <Link
      to={`/products/${slugOrId}`}
      className="by-product-card"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "0.5rem",
        textDecoration: "none",
        color: "var(--by-espresso, #3a2418)",
      }}
    >
      <div
        style={{
          aspectRatio: "4 / 5",
          background: "rgba(58,36,24,0.06)",
          borderRadius: 12,
          overflow: "hidden",
        }}
      >
        {image && (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={image}
            alt={product.name}
            style={{ width: "100%", height: "100%", objectFit: "cover" }}
            loading="lazy"
          />
        )}
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: "0.5rem" }}>
        <span style={{ fontWeight: 600, fontSize: "0.95rem", lineHeight: 1.3 }}>
          {product.name}
        </span>
        <span style={{ display: "flex", gap: "0.4rem", fontSize: "0.85rem" }}>
          {compareAt && compareAt > price && (
            <span style={{ textDecoration: "line-through", color: "rgba(58,36,24,0.45)" }}>
              {compareAt} EGP
            </span>
          )}
          <span style={{ color: "var(--by-caramel, #b07a4a)" }}>{price} EGP</span>
        </span>
      </div>
    </Link>
  );
}
