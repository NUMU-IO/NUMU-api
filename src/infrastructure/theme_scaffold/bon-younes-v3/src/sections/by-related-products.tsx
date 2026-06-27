"use client";

import { Link, useLocale, useProducts, useResolvedSettings, type Product } from "@numueg/theme-sdk";
import { asNumber, asString, localized, type SectionRenderProps } from "./_shared";
import { InlineEditable } from "./_inline-editable";

/**
 * by-related-products — bottom-of-PDP cross-sell rail. Pulls from
 * the page context's products list (the storefront's product route
 * pre-fetches related items into `page.data.related_products` when
 * available; we fall back to the first N of all products if it's
 * absent).
 *
 * Settings:
 *   - title        — section heading
 *   - count        — how many cards to show (default 4)
 */
export default function ByRelatedProducts({
  instance,
  sectionId,
}: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const { products } = useProducts();
  const locale = useLocale();
  const title = asString(s.title) || localized(locale, "Pairs well with", "يتحلّى مع");
  const count = Math.max(2, Math.min(8, asNumber(s.count, 4)));

  const items: Product[] = products.slice(0, count);
  if (items.length === 0) return null;

  return (
    <section
      className="by-related-products"
      data-by-section={sectionId}
      style={{
        padding: "2.5rem 0",
        background: "var(--by-hero-bg, #f7f1e8)",
        borderTop: "1px solid rgba(58,36,24,0.08)",
      }}
    >
      <div
        className="by-shell"
        style={{ maxWidth: 1200, margin: "0 auto", padding: "0 1rem" }}
      >
        <h2
          style={{
            fontFamily: "var(--by-display, 'Playfair Display', serif)",
            fontSize: "clamp(1.4rem, 2.4vw, 2rem)",
            color: "var(--by-espresso, #3a2418)",
            textAlign: "center",
            margin: "0 0 1.5rem",
          }}
        >
          <InlineEditable
            sectionId={sectionId}
            settingKey="title"
            value={title}
          />
        </h2>
        <div
          style={{
            display: "grid",
            gap: "1rem",
            gridTemplateColumns: `repeat(${Math.min(count, 4)}, minmax(0, 1fr))`,
          }}
        >
          {items.map((p) => {
            const slugOrId = p.slug || p.id;
            const image = p.images?.[0]?.url || null;
            return (
              <Link
                key={p.id}
                to={`/products/${slugOrId}`}
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
                    aspectRatio: "1 / 1",
                    background: "rgba(58,36,24,0.05)",
                    borderRadius: 10,
                    overflow: "hidden",
                  }}
                >
                  {image && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={image}
                      alt={p.name}
                      style={{ width: "100%", height: "100%", objectFit: "cover" }}
                      loading="lazy"
                    />
                  )}
                </div>
                <span
                  style={{
                    fontSize: "0.9rem",
                    fontWeight: 600,
                    textAlign: "center",
                  }}
                >
                  {p.name}
                </span>
                <span
                  style={{
                    fontSize: "0.8rem",
                    color: "var(--by-caramel, #b07a4a)",
                    textAlign: "center",
                  }}
                >
                  {typeof p.price === "number" ? `${p.price} EGP` : ""}
                </span>
              </Link>
            );
          })}
        </div>
      </div>
    </section>
  );
}
