"use client";
import { useState } from "react";
import { Link, useLocale } from "@numueg/theme-sdk";
import { ShoppingBag } from "lucide-react";
import { applyImageTransform, asImageTransform, asString, localized, type SectionRenderProps } from "./_shared";

const KGPromoBanner = ({ instance }: SectionRenderProps) => {
  const s = instance.settings ?? {};
  const locale = useLocale();
  const badge = asString(s.badge_text);
  const headline = asString(s.headline) || localized(locale, "25% OFF ACCESSORIES", "خصم ٢٥٪ على الإكسسوارات");
  const subtitle = asString(s.subtitle) || localized(locale, "Limited time. Don't sleep.", "لفترة محدودة. متفوّتش الفرصة.");
  const ctaText = asString(s.cta_text) || localized(locale, "SHOP NOW", "اتسوّق دلوقتي");
  const ctaLink = asString(s.cta_link) || "/products?category=accessories";
  const imageUrl = asString(s.image_url);
  const imageTransform = asImageTransform(s.image_url);

  const [imgLoaded, setImgLoaded] = useState(false);
  const [imgError, setImgError] = useState(false);

  return (
    <section
      className="kg-promo"
      style={{ background: "#fcfbf7", padding: "32px 0" }}
    >
      <div style={{ maxWidth: "1280px", margin: "0 auto", padding: "0 16px" }}>
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            alignItems: "center",
            gap: "24px",
            background: "#fcfbf7",
            padding: "24px 0",
          }}
        >
          {/* Text side */}
          <div style={{ flex: "1 1 300px" }}>
            {badge && (
              <span
                className="kg-badge"
                style={{
                  display: "inline-block",
                  padding: "4px 10px",
                  borderRadius: "0.4rem",
                  background: "#121212",
                  color: "#fcfbf7",
                  fontSize: "0.625rem",
                  fontWeight: 700,
                  textTransform: "uppercase",
                  letterSpacing: "0.06em",
                  marginBottom: "12px",
                }}
              >
                {badge}
              </span>
            )}
            <h3
              className="kg-heading"
              style={{
                fontSize: "clamp(1.5rem, 4vw, 2.5rem)",
                fontWeight: 700,
                textTransform: "uppercase",
                letterSpacing: "-0.02em",
                color: "#121212",
                lineHeight: 1,
                margin: "0 0 8px",
              }}
            >
              {headline}
            </h3>
            <p
              style={{
                fontSize: "0.8125rem",
                color: "rgba(18,18,18,0.6)",
                margin: "0 0 24px",
              }}
            >
              {subtitle}
            </p>
            <Link
              to={ctaLink}
              className="kg-btn"
              style={{
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                height: "40px",
                padding: "0 32px",
                background: "#121212",
                color: "#fcfbf7",
                fontSize: "0.75rem",
                fontWeight: 700,
                textTransform: "uppercase",
                letterSpacing: "0.06em",
                borderRadius: "4px",
                border: "none",
                textDecoration: "none",
                cursor: "pointer",
              }}
            >
              {ctaText}
            </Link>
          </div>

          {/* Image side */}
          <div
            style={{
              flex: "0 0 200px",
              width: "200px",
              height: "200px",
              overflow: "hidden",
            }}
          >
            {imgError || !imageUrl ? (
              <div
                style={{
                  width: "100%",
                  height: "100%",
                  background: "#121212",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                }}
              >
                <ShoppingBag size={48} color="#d9cd9a" strokeWidth={1} />
              </div>
            ) : (
              <img
                src={imageUrl}
                alt=""
                onLoad={() => setImgLoaded(true)}
                onError={() => setImgError(true)}
                style={{
                  width: "100%",
                  height: "100%",
                  objectFit: "cover",
                  opacity: imgLoaded ? 1 : 0,
                  transition: "opacity 0.3s ease",
                  ...applyImageTransform(imageTransform, "cover"),
                }}
              />
            )}
          </div>
        </div>
      </div>
    </section>
  );
};

export default KGPromoBanner;
