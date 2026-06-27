"use client";
import { Link, useLocale } from "@numueg/theme-sdk";
import { applyImageTransform, asImageTransform, asString, localized, type SectionRenderProps } from "./_shared";

const BTN_FILLED =
  "inline-flex items-center justify-center gap-2 h-11 px-6 rounded-full font-semibold text-sm text-primary-foreground transition-all hover:scale-[1.02]";
const BTN_OUTLINE =
  "inline-flex items-center justify-center gap-2 h-11 px-6 rounded-full font-semibold text-sm text-foreground border border-primary transition-all hover:bg-primary hover:text-primary-foreground";

const BoutiqueAbout = ({ instance }: SectionRenderProps) => {
  const s = instance.settings ?? {};
  const locale = useLocale();
  const eyebrow = asString(s.eyebrow) || localized(locale, "About the Store", "عن المتجر");
  const headline = asString(s.headline) || localized(locale, "Elegance with a Modern Touch", "أناقة بلمسة عصرية");
  const quote = asString(s.quote);
  const description = asString(s.description);
  const image = asString(s.image);
  const imageTransform = asImageTransform(s.image);

  const ctaText = asString(s.cta_text);
  const ctaLink = asString(s.cta_link) || "/products";
  const contactCtaText = asString(s.contact_cta_text);
  const contactCtaLink = asString(s.contact_cta_link) || "/contact";

  const values = [
    { title: asString(s.value_1_title), text: asString(s.value_1_text) },
    { title: asString(s.value_2_title), text: asString(s.value_2_text) },
    { title: asString(s.value_3_title), text: asString(s.value_3_text) },
  ].filter((v) => v.title || v.text);

  return (
    <section className="bg-accent/20">
      <div className="container mx-auto px-4 py-14 md:py-20">
        <div className="text-center mb-10 md:mb-14 max-w-3xl mx-auto">
          {eyebrow && (
            <span className="inline-block mb-3 text-xs uppercase tracking-widest text-primary">
              {eyebrow}
            </span>
          )}
          {headline && (
            <h1 className="text-3xl md:text-5xl font-bold text-foreground">
              {headline}
            </h1>
          )}
        </div>

        <div className="grid md:grid-cols-2 gap-8 md:gap-12 items-start">
          <div className={image ? "" : "md:col-span-2 max-w-2xl mx-auto"}>
            {description && (
              <p className="text-base md:text-lg text-foreground/85 leading-relaxed">
                {description}
              </p>
            )}
            {(ctaText || contactCtaText) && (
              <div className="flex flex-wrap gap-3 mt-7">
                {ctaText && (
                  <Link to={ctaLink} className={BTN_FILLED} style={{ background: "hsl(var(--primary))" }}>
                    {ctaText}
                  </Link>
                )}
                {contactCtaText && (
                  <Link to={contactCtaLink} className={BTN_OUTLINE}>
                    {contactCtaText}
                  </Link>
                )}
              </div>
            )}
          </div>
          {image ? (
            <div className="relative aspect-[4/5] overflow-hidden rounded-xl bg-muted">
              <img src={image} alt="" className="absolute inset-0 w-full h-full object-cover" style={applyImageTransform(imageTransform, "cover")} />
            </div>
          ) : null}
        </div>

        {quote && (
          <figure className="mt-12 md:mt-16 max-w-3xl mx-auto bg-card rounded-xl border border-border p-7 md:p-9 text-center">
            <blockquote className="text-xl md:text-2xl font-bold text-foreground">
              "{quote}"
            </blockquote>
          </figure>
        )}

        {values.length > 0 && (
          <div className="mt-12 md:mt-16 grid md:grid-cols-3 gap-5 md:gap-6">
            {values.map((v, i) => (
              <div
                key={i}
                className="bg-card rounded-xl border border-border p-6 md:p-7"
              >
                <h3 className="text-lg md:text-xl font-bold text-foreground mb-2">{v.title}</h3>
                <p className="text-sm text-muted-foreground leading-relaxed">{v.text}</p>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
};

export default BoutiqueAbout;
