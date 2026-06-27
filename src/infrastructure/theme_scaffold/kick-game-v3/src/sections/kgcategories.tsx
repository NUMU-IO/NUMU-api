"use client";
import { Link, useCollections } from "@numueg/theme-sdk";
import { asString, type SectionRenderProps } from "./_shared";

const KGCategories = ({ instance }: SectionRenderProps) => {
  const { collections } = useCollections();
  const s = instance.settings ?? {};
  const title = asString(s.title);

  if (collections.length === 0) return null;

  return (
    <section
      className="kg-categories"
      style={{ background: "#fcfbf7", padding: "32px 0" }}
    >
      <div style={{ maxWidth: "1280px", margin: "0 auto", padding: "0 16px" }}>
        {title && (
          <h2
            className="kg-heading"
            style={{
              fontSize: "1.25rem",
              fontWeight: 700,
              textTransform: "uppercase",
              letterSpacing: "-0.02em",
              color: "#121212",
              margin: "0 0 16px",
            }}
          >
            {title}
          </h2>
        )}

        {/* Horizontal scrollable row */}
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
          {collections.map((cat) => (
            <Link
              key={cat.id}
              to={`/products?category=${cat.slug || cat.id}`}
              style={{
                flexShrink: 0,
                scrollSnapAlign: "start",
                width: "140px",
                textDecoration: "none",
              }}
            >
              <div
                style={{
                  width: "140px",
                  height: "140px",
                  background: "#121212",
                  overflow: "hidden",
                  borderRadius: 0,
                }}
              >
                {cat.image_url ? (
                  <img
                    src={cat.image_url}
                    alt={cat.name}
                    style={{
                      width: "100%",
                      height: "100%",
                      objectFit: "contain",
                      padding: "12px",
                    }}
                  />
                ) : (
                  <div
                    style={{
                      width: "100%",
                      height: "100%",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                    }}
                  >
                    <span
                      style={{
                        color: "rgba(255,255,255,0.15)",
                        fontSize: "2rem",
                        fontWeight: 700,
                        textTransform: "uppercase",
                      }}
                    >
                      {cat.name?.[0]}
                    </span>
                  </div>
                )}
              </div>
              <p
                style={{
                  fontSize: "0.6875rem",
                  fontWeight: 700,
                  textTransform: "uppercase",
                  letterSpacing: "0.08em",
                  color: "#121212",
                  textAlign: "center",
                  marginTop: "8px",
                }}
              >
                {cat.name}
              </p>
            </Link>
          ))}
        </div>
      </div>
    </section>
  );
};

export default KGCategories;
