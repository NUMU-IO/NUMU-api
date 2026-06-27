"use client";

import { Link, useLocale, useProducts, usePage, useResolvedSettings, type Product } from "@numueg/theme-sdk";
import { useMemo } from "react";
import { asNumber, asString, localized, type SectionRenderProps } from "./_shared";
import { InlineEditable } from "./_inline-editable";

/**
 * by-search-results — body for the `search` template. Reads the
 * visitor's query from `usePage().data.query` (storefront /search
 * route fetches matches and stashes them on page.data). Falls back
 * to client-side filter against `useProducts()` if data.results is
 * absent, so the section still renders something in the customizer
 * preview (where the storefront hasn't done a server-side query).
 *
 * Settings:
 *   - placeholder_when_no_query — copy when the URL has no ?q=
 *   - no_results_text           — copy when query matches nothing
 *   - columns_desktop           — grid columns at desktop (default 3)
 */
export default function BySearchResults({
  instance,
  sectionId,
}: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const page = usePage();
  const { products } = useProducts();
  const locale = useLocale();

  const placeholderEmpty =
    asString(s.placeholder_when_no_query) ||
    localized(locale, "Search the menu — try 'latte' or 'mango'.", "دوّر في المنيو — جرّب 'لاتيه' أو 'مانجو'.");
  const noResultsText =
    asString(s.no_results_text) ||
    localized(locale, "No matches. Try a different keyword or browse all drinks.", "مفيش نتائج. جرّب كلمة تانية أو اتفرّج على كل المشروبات.");
  const cols = Math.max(1, Math.min(5, asNumber(s.columns_desktop, 3)));

  // The storefront's search route puts the query string on page.data.q;
  // results (if it pre-fetched) live on page.data.results.
  const query = asString((page?.data as Record<string, unknown> | undefined)?.q);
  const preFetchedResults = (page?.data as { results?: Product[] } | undefined)
    ?.results;

  const matches: Product[] = useMemo(() => {
    if (preFetchedResults && preFetchedResults.length > 0)
      return preFetchedResults;
    if (!query) return [];
    const needle = query.toLowerCase();
    return products.filter(
      (p) =>
        p.name?.toLowerCase().includes(needle) ||
        p.description?.toLowerCase().includes(needle),
    );
  }, [preFetchedResults, query, products]);

  const hasQuery = Boolean(query);
  const hasMatches = matches.length > 0;

  return (
    <section
      className="by-search-results"
      data-by-section={sectionId}
      style={{
        padding: "3rem 1rem",
        background: "var(--by-cream, #fdf8ee)",
        minHeight: "60vh",
      }}
    >
      <div style={{ maxWidth: 1100, margin: "0 auto" }}>
        <header style={{ marginBottom: "2rem" }}>
          <p
            style={{
              fontFamily: "var(--by-mono, 'JetBrains Mono', monospace)",
              fontSize: "0.8rem",
              color: "var(--by-caramel, #b07a4a)",
              letterSpacing: "0.18em",
              textTransform: "uppercase",
              margin: 0,
            }}
          >
            {localized(locale, "Search", "بحث")}
          </p>
          <h1
            style={{
              fontFamily: "var(--by-display, 'Playfair Display', serif)",
              fontSize: "clamp(1.75rem, 3vw, 2.5rem)",
              color: "var(--by-espresso, #3a2418)",
              margin: "0.25rem 0 0",
            }}
          >
            {hasQuery ? (
              <>{localized(locale, "Results for", "نتائج البحث عن")} &ldquo;{query}&rdquo;</>
            ) : (
              <InlineEditable
                sectionId={sectionId}
                settingKey="placeholder_when_no_query"
                value={placeholderEmpty}
              />
            )}
          </h1>
        </header>

        {hasQuery && !hasMatches && (
          <div
            style={{
              textAlign: "center",
              padding: "3rem 0",
              color: "rgba(58,36,24,0.6)",
            }}
          >
            <InlineEditable
              sectionId={sectionId}
              settingKey="no_results_text"
              value={noResultsText}
              multiline
            />
          </div>
        )}

        {hasMatches && (
          <div
            style={{
              display: "grid",
              gap: "1.25rem",
              gridTemplateColumns: `repeat(2, minmax(0, 1fr))`,
            }}
            className="by-search-results__grid"
          >
            {matches.map((p) => {
              const slugOrId = p.slug || p.id;
              const image = p.images?.[0]?.url || null;
              return (
                <Link
                  key={p.id}
                  to={`/products/${slugOrId}`}
                  style={{
                    display: "flex",
                    gap: "1rem",
                    padding: "1rem",
                    background: "white",
                    borderRadius: 12,
                    textDecoration: "none",
                    color: "var(--by-espresso, #3a2418)",
                    boxShadow: "0 1px 2px rgba(58,36,24,0.05)",
                  }}
                >
                  <div
                    style={{
                      width: 80,
                      height: 80,
                      flex: "0 0 auto",
                      background: "rgba(58,36,24,0.06)",
                      borderRadius: 8,
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
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontWeight: 600, fontSize: "0.95rem" }}>
                      {p.name}
                    </div>
                    {p.description && (
                      <div
                        style={{
                          fontSize: "0.85rem",
                          color: "rgba(58,36,24,0.65)",
                          marginTop: "0.25rem",
                          display: "-webkit-box",
                          WebkitLineClamp: 2,
                          WebkitBoxOrient: "vertical",
                          overflow: "hidden",
                        }}
                      >
                        {p.description}
                      </div>
                    )}
                    <div
                      style={{
                        fontSize: "0.85rem",
                        color: "var(--by-caramel, #b07a4a)",
                        marginTop: "0.4rem",
                      }}
                    >
                      {typeof p.price === "number" ? `${p.price} EGP` : ""}
                    </div>
                  </div>
                </Link>
              );
            })}
          </div>
        )}
      </div>
      <style>{`
        @media (min-width: 900px) {
          .by-search-results__grid {
            grid-template-columns: repeat(${cols}, minmax(0, 1fr)) !important;
          }
        }
      `}</style>
    </section>
  );
}
