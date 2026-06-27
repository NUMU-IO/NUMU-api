"use client";
import { Link } from "@numueg/theme-sdk";
import { applyImageTransform, asImageTransform, asString, type ImageTransform, type SectionRenderProps } from "./_shared";

interface Item {
  image: string;
  imageTransform?: ImageTransform;
  label: string;
  link: string;
}

export default function CollectionStrip({ instance }: SectionRenderProps) {
  const s = instance.settings ?? {};
  const title = asString(s.title);
  const layout = (asString(s.layout) || "image-row") as "image-row" | "text-strip";

  const items: Item[] = [];
  for (let i = 1; i <= 4; i++) {
    const image = asString(s[`item_${i}_image`]);
    const imageTransform = asImageTransform(s[`item_${i}_image`]);
    const label = asString(s[`item_${i}_label`]);
    const link = asString(s[`item_${i}_link`]);
    if (!image && !label) continue;
    items.push({ image, imageTransform, label, link });
  }

  if (!items.length) return null;

  if (layout === "text-strip") {
    return (
      <section className="py-3 md:py-4 bg-background border-y border-border">
        <div className="container mx-auto px-4">
          <div className="flex flex-wrap items-center justify-center gap-x-8 gap-y-2 text-xs md:text-sm tracking-[0.2em] uppercase">
            {title && <span className="font-semibold text-foreground">{title}</span>}
            {items.map((it, i) =>
              it.link ? (
                <Link key={i} to={it.link} className="text-muted-foreground hover:text-foreground transition-colors">
                  {it.label}
                </Link>
              ) : (
                <span key={i} className="text-muted-foreground">{it.label}</span>
              ),
            )}
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="py-8 md:py-10 bg-background">
      <div className="container mx-auto px-4">
        {title && (
          <h2 className="text-xl md:text-2xl font-semibold tracking-tight text-center mb-6 text-foreground" style={{ fontFamily: "var(--font-heading, serif)" }}>{title}</h2>
        )}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 md:gap-4">
          {items.map((it, i) => {
            const card = (
              <div className="relative aspect-[4/5] overflow-hidden rounded-lg bg-muted group">
                {it.image && (
                  <img
                    src={it.image}
                    alt={it.label}
                    className={`absolute inset-0 w-full h-full object-cover transition-transform duration-500 ${it.imageTransform ? "" : "group-hover:scale-105"}`}
                    style={applyImageTransform(it.imageTransform, "cover")}
                  />
                )}
                <div className="absolute inset-0 bg-gradient-to-t from-black/55 via-black/15 to-transparent" />
                {it.label && (
                  <span className="absolute bottom-3 inset-x-0 text-center text-white text-xs md:text-sm font-semibold uppercase tracking-[0.2em] px-2">
                    {it.label}
                  </span>
                )}
              </div>
            );
            return it.link ? (
              <Link key={i} to={it.link} className="block">
                {card}
              </Link>
            ) : (
              <div key={i}>{card}</div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
