"use client";
import { Link, Money, useProducts, useLocale, type Product } from "@numueg/theme-sdk";
import { asString, asNumber, localized, type SectionRenderProps } from "./_shared";

/**
 * Kick Game featured-collection.
 *
 * Faithful port of the V2 KGFeatured: uppercase title + "VIEW ALL" link, dense
 * 8px-gap horizontal scroll rail. V2 delegated card rendering to the theme's
 * resolveComponent("ProductCard") (KGProductCard); there's no resolveComponent
 * in V3, so the KGProductCard markup is inlined here (square image on #f0efe9,
 * left badges, black quick-add bar, brand tag + name + price) — mirroring how
 * vionne inlines its card.
 */
const KGFeatured = ({ instance }: SectionRenderProps) => {
  const { products } = useProducts();
  const s = instance.settings ?? {};
  const locale = useLocale();
  const title = asString(s.title) || localized(locale, "BEST-SELLERS", "الأكثر مبيعاً");
  const viewAllLink = asString(s.view_all_link) || "/products";
  const viewAllText = asString(s.view_all_text) || localized(locale, "VIEW ALL", "شوف الكل");

  const count = asNumber(s.product_count, 8) || 8;
  const displayed = products.slice(0, count);

  if (displayed.length === 0) return null;

  return (
    <section
      className="kg-featured"
      style={{ background: "#fcfbf7", padding: "32px 0" }}
    >
      <div style={{ maxWidth: "1280px", margin: "0 auto", padding: "0 16px" }}>
        {/* Header */}
        <div
          style={{
            display: "flex",
            alignItems: "baseline",
            justifyContent: "space-between",
            marginBottom: "16px",
          }}
        >
          <h2
            className="kg-heading"
            style={{
              fontSize: "1.5rem",
              fontWeight: 700,
              textTransform: "uppercase",
              letterSpacing: "-0.02em",
              color: "#121212",
              margin: 0,
            }}
          >
            {title}
          </h2>
          <Link
            to={viewAllLink}
            style={{
              fontSize: "0.6875rem",
              fontWeight: 700,
              textTransform: "uppercase",
              letterSpacing: "0.04em",
              color: "#121212",
              textDecoration: "underline",
              textUnderlineOffset: "3px",
            }}
          >
            {viewAllText}
          </Link>
        </div>

        {/* Horizontal scrollable product row with dense 8px gaps */}
        <div
          style={{
            display: "flex",
            gap: "8px",
            overflowX: "auto",
            paddingBottom: "8px",
            scrollSnapType: "x mandatory",
          }}
          className="scrollbar-hide"
        >
          {displayed.map((product) => (
            <div
              key={product.id}
              style={{
                minWidth: "200px",
                maxWidth: "220px",
                flexShrink: 0,
                scrollSnapAlign: "start",
              }}
            >
              <KGCard product={product} />
            </div>
          ))}
        </div>
      </div>
    </section>
  );
};

/**
 * Inlined Kick Game product card — mirrors KGProductCard's makeStyles():
 * square image on #f0efe9 with scale-on-hover, left-aligned category/discount
 * badges, brand tag, name, price.
 */
export function KGCard({ product }: { product: Product }) {
  const price = product.variants?.[0]?.price ?? product.price ?? 0;
  const compareAt = product.compare_at_price;
  const hasDiscount = typeof compareAt === "number" && compareAt > price;
  const discountPct = hasDiscount
    ? Math.round(((compareAt - price) / compareAt) * 100)
    : 0;
  const categoryBadge = product.tags?.[0] || product.category;
  const brandTag = product.tags?.[1] || product.tags?.[0];
  const image = product.images?.[0]?.url;

  return (
    <Link
      to={`/product/${product.slug || product.id}`}
      className="kg-product-card group block"
      style={{ textDecoration: "none" }}
      data-testid="storefront-product-card"
    >
      {/* Image */}
      <div className="relative overflow-hidden aspect-square bg-[#f0efe9]">
        {image ? (
          <img
            src={image}
            alt={product.name}
            className="w-full h-full object-cover transition-transform duration-500 group-hover:scale-[1.03]"
            loading="lazy"
          />
        ) : (
          <div className="absolute inset-0 vn-shimmer" />
        )}
        {/* Left badges */}
        <div className="absolute top-2 start-2 flex flex-col items-start gap-1">
          {hasDiscount && (
            <span className="text-[10px] font-bold uppercase tracking-wider px-2.5 py-1 text-white bg-[#c0392b]">
              -{discountPct}%
            </span>
          )}
          {categoryBadge && (
            <span className="text-[10px] font-bold uppercase tracking-wider px-2.5 py-1 text-white bg-[#121212]">
              {categoryBadge}
            </span>
          )}
        </div>
      </div>

      {/* Info */}
      <div className="mt-3 text-start">
        {brandTag && (
          <p className="text-[10px] font-bold uppercase tracking-[0.08em] mb-1 text-[#121212aa]">
            {brandTag}
          </p>
        )}
        <h3 className="font-medium text-sm leading-snug line-clamp-2 transition-colors group-hover:opacity-60 text-[#121212]">
          {product.name}
        </h3>
        <div className="mt-1.5 flex items-baseline gap-2">
          <span className="font-bold text-sm text-[#121212]">
            <Money amount={price} currency={product.currency} />
          </span>
          {hasDiscount && (
            <span className="text-xs text-[#12121260] line-through">
              <Money amount={compareAt} currency={product.currency} />
            </span>
          )}
        </div>
      </div>
    </Link>
  );
}

export default KGFeatured;
