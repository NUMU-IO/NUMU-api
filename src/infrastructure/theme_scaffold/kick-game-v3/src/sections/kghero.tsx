"use client";
import { useState } from "react";
import { Link, useLocale } from "@numueg/theme-sdk";
import { ShoppingBag } from "lucide-react";
import { applyImageTransform, asImageTransform, asString, localized, type SectionRenderProps } from "./_shared";

const KGHero = ({ instance }: SectionRenderProps) => {
  const s = instance.settings ?? {};
  const locale = useLocale();
  const headline = asString(s.headline) || localized(locale, "NEW DROPS", "وصل جديد");
  const subtitle = asString(s.subtitle) || localized(locale, "ELEVATE YOUR GAME", "ارفع مستواك");
  const ctaText = asString(s.cta_text) || localized(locale, "SHOP NOW", "اتسوّق دلوقتي");
  const ctaLink = asString(s.cta_link) || "/products";
  const heroImage = asString(s.hero_image_url);
  const heroImageTransform = asImageTransform(s.hero_image_url);

  const [imgLoaded, setImgLoaded] = useState(false);
  const [imgError, setImgError] = useState(false);

  return (
    <section
      className="kg-hero"
      style={{
        background: "#fcfbf7",
        padding: "0",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr",
          minHeight: "80vh",
        }}
      >
        {/* Image area */}
        <div style={{ position: "relative", minHeight: "60vh" }}>
          {imgError || !heroImage ? (
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
              <ShoppingBag size={64} color="#d9cd9a" strokeWidth={1} />
            </div>
          ) : (
            <img
              src={heroImage}
              alt=""
              onLoad={() => setImgLoaded(true)}
              onError={() => setImgError(true)}
              style={{
                width: "100%",
                height: "100%",
                objectFit: "contain",
                opacity: imgLoaded ? 1 : 0,
                transition: "opacity 0.4s ease",
                position: "absolute",
                inset: 0,
                ...applyImageTransform(heroImageTransform, "contain"),
              }}
            />
          )}

          {/* Overlay gradient */}
          <div
            style={{
              position: "absolute",
              inset: 0,
              background:
                "linear-gradient(to top, rgba(18,18,18,0.85) 0%, rgba(18,18,18,0.3) 40%, transparent 70%)",
              zIndex: 1,
            }}
          />

          {/* Content overlay */}
          <div
            style={{
              position: "absolute",
              bottom: 0,
              left: 0,
              right: 0,
              zIndex: 2,
              padding: "48px 24px",
              display: "flex",
              flexDirection: "column",
              alignItems: "flex-start",
              gap: "8px",
            }}
          >
            <h1
              className="kg-heading"
              style={{
                color: "#fcfbf7",
                fontSize: "clamp(2.5rem, 8vw, 5rem)",
                fontWeight: 700,
                textTransform: "uppercase",
                letterSpacing: "-0.02em",
                lineHeight: 0.95,
                margin: 0,
              }}
            >
              {headline}
            </h1>
            <p
              style={{
                color: "#d9cd9a",
                fontSize: "0.8125rem",
                fontWeight: 700,
                textTransform: "uppercase",
                letterSpacing: "0.1em",
                margin: "8px 0 24px",
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
        </div>
      </div>
    </section>
  );
};

export default KGHero;
