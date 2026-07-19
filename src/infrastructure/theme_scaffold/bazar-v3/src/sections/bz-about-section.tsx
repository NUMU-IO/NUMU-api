"use client";

import { Link, useLocale, useResolvedSettings } from "@numueg/theme-sdk";
import { Gem, Shield, Users, Palette, Star, type LucideIcon } from "lucide-react";
import {
  applyImageTransform,
  asImageTransform,
  asString,
  asImageUrl,
  demoOrPlaceholder,
  localized,
  readBlocks,
  useDemo,
  type SectionRenderProps,
} from "./_shared";
import { InlineEditable } from "./_inline-editable";

interface ValueCard {
  icon: string;
  title: string;
  description: string;
}

// Icon key → Lucide component. The block's `icon` setting is a select whose
// values map here; anything unrecognised falls back to a star.
const ICONS: Record<string, LucideIcon> = {
  gem: Gem,
  shield: Shield,
  users: Users,
  palette: Palette,
  star: Star,
};
const iconFor = (key: string): LucideIcon => ICONS[key.toLowerCase()] ?? Star;

const fallbackValues = (locale: string | undefined): ValueCard[] => [
  { icon: "gem", title: localized(locale, "QUALITY", "الجودة"), description: localized(locale, "EVERY PIECE IS CRAFTED WITH OBSESSIVE ATTENTION TO DETAIL. WE SOURCE ONLY THE FINEST MATERIALS THAT MEET OUR UNCOMPROMISING STANDARDS.", "كل قطعة معمولة باهتمام شديد بالتفاصيل. بنختار أجود الخامات اللي بتوصل لمعاييرنا اللي مفيهاش تنازل.") },
  { icon: "shield", title: localized(locale, "AUTHENTICITY", "الأصالة"), description: localized(locale, "NO IMITATIONS. NO SHORTCUTS. EVERYTHING WE OFFER IS GENUINE, ORIGINAL, AND TRUE TO ITS CRAFT.", "مفيش تقليد. مفيش طرق مختصرة. كل اللي بنقدمه أصلي وحقيقي ووفي لحرفته.") },
  { icon: "users", title: localized(locale, "COMMUNITY", "المجتمع"), description: localized(locale, "WE BUILD MORE THAN A STORE — WE BUILD CONNECTIONS. OUR CUSTOMERS ARE OUR COLLABORATORS IN THE PURSUIT OF BETTER.", "إحنا بنبني أكتر من مجرد متجر — بنبني علاقات. عملاؤنا شركاؤنا في السعي للأفضل.") },
  { icon: "palette", title: localized(locale, "CRAFT", "الحِرفة"), description: localized(locale, "THE INVISIBLE DETAILS MATTER MOST. FROM STITCHING TO FINISH, EVERY STEP IS A DELIBERATE ACT OF CREATION.", "التفاصيل الخفية هي الأهم. من الخياطة للمسة الأخيرة، كل خطوة فعل إبداع مقصود.") },
];

const BzAboutSection = ({ instance, sectionId }: SectionRenderProps) => {
  const s = useResolvedSettings(instance);
  const demo = useDemo();
  const locale = useLocale();

  const eyebrow = asString(s.eyebrow) || localized(locale, "ABOUT US", "من نحن");
  const headline = asString(s.headline) || localized(locale, "OUR STORY", "قصتنا");
  const quote =
    asString(s.quote) ||
    localized(locale, "THE THINGS WE KEEP CLOSE SAY EVERYTHING ABOUT WHO WE ARE.", "الأشياء اللي بنحبها بتحكي كل حاجة عننا.");
  const whoLabel = asString(s.who_label) || localized(locale, "WHO WE ARE", "مين إحنا");
  const whoHeadline = asString(s.who_headline) || localized(locale, "WELCOME TO BAZAR", "أهلًا بيك في بازار");
  const description =
    asString(s.description) ||
    localized(
      locale,
      "WE STARTED WITH A SIMPLE BELIEF — THAT EVERYDAY OBJECTS DESERVE THE SAME CARE AND INTENTION AS THE EXTRAORDINARY ONES. BAZAR WAS BORN FROM A DESIRE TO BRIDGE THE GAP BETWEEN FUNCTION AND BEAUTY, BETWEEN CRAFT AND COMMERCE.",
      "بدأنا بفكرة بسيطة — إن الحاجات اللي بنستخدمها كل يوم تستاهل نفس الاهتمام والإتقان زي الحاجات الاستثنائية. بازار اتولد من رغبة إننا نوصّل بين الوظيفة والجمال، بين الحِرفة والتجارة.",
    );
  const marqueeText =
    asString(s.marquee_text) ||
    localized(locale, "QUALITY • AUTHENTICITY • COMMUNITY • CRAFT • BOLD VISION •", "جودة • أصالة • مجتمع • حِرفة • رؤية جريئة •");
  const valuesLabel = asString(s.values_label) || localized(locale, "WHAT DRIVES US", "اللي بيحركنا");
  const valuesHeadline = asString(s.values_headline) || localized(locale, "OUR VALUES", "قيمنا");
  const ctaLabel = asString(s.cta_label) || localized(locale, "NEXT STEPS", "الخطوة الجاية");
  const ctaHeadline = asString(s.cta_headline) || localized(locale, "READY TO DISCOVER?", "جاهز تكتشف؟");
  const imageUrl = asImageUrl(s.image_url);
  // Non-destructive focal/zoom/rotation for the faded hero background image.
  // Undefined → the image renders exactly as before (object-cover).
  const imageTransform = asImageTransform(s.image_url);
  const ctaText = asString(s.cta_text) || localized(locale, "EXPLORE OUR COLLECTION", "استكشف تشكيلتنا");
  const ctaLink = asString(s.cta_link) || "/products";
  const contactCtaText = asString(s.contact_cta_text) || localized(locale, "GET IN TOUCH", "تواصل معانا");
  const contactCtaLink = asString(s.contact_cta_link) || "/contact";

  // Repeatable value cards come from editor `value` blocks; if the merchant
  // hasn't added any, use the curated demo set (or neutral placeholders
  // outside demo mode).
  const configured: ValueCard[] = readBlocks(instance, "value")
    .map((r) => ({
      icon: asString(r.icon) || "star",
      title: asString(r.title),
      description: asString(r.description),
    }))
    .filter((v) => v.title);
  const values =
    configured.length > 0 ? configured : demoOrPlaceholder(demo, fallbackValues(locale));

  return (
    <div className="bg-[var(--bz-cream)]">
      {/* HERO */}
      <section className="relative min-h-[50vh] md:min-h-[60vh] flex flex-col items-center justify-center overflow-hidden bz-wavy-bg py-16 md:py-0">
        {imageUrl && (
          <img src={imageUrl} alt="" className="absolute inset-0 w-full h-full object-cover opacity-20" style={applyImageTransform(imageTransform, "cover")} />
        )}
        <div className="relative z-10 text-center px-4">
          <span className="bz-label text-[var(--bz-dark)]/60 tracking-[0.2em] sm:tracking-[0.3em]">
            <InlineEditable sectionId={sectionId} settingKey="eyebrow" value={eyebrow} />
          </span>
          <h1 className="bz-heading text-3xl sm:text-5xl md:text-7xl lg:text-8xl text-[var(--bz-dark)] mt-3 md:mt-4 leading-none">
            <InlineEditable sectionId={sectionId} settingKey="headline" value={headline} />
          </h1>
          {quote && (
            <p className="text-[var(--bz-dark)]/50 mt-3 md:mt-4 text-sm md:text-base max-w-md mx-auto italic">
              &ldquo;
              <InlineEditable sectionId={sectionId} settingKey="quote" value={quote} multiline />
              &rdquo;
            </p>
          )}
        </div>
        <div aria-hidden="true" className="absolute top-8 md:top-12 start-4 md:start-8 w-12 h-12 sm:w-16 sm:h-16 md:w-20 md:h-20 bg-[var(--bz-amber)] bz-blob opacity-30" />
        <div aria-hidden="true" className="absolute bottom-16 md:bottom-24 end-6 md:end-12 w-10 h-10 sm:w-14 sm:h-14 md:w-16 md:h-16 bg-[var(--bz-navy)] bz-blob opacity-20" />
        <svg viewBox="0 0 1440 80" className="absolute bottom-0 w-full" preserveAspectRatio="none" aria-hidden="true">
          <path d="M0,80 C300,20 600,60 900,30 C1100,10 1300,50 1440,25 L1440,80 Z" fill="var(--bz-cream)" />
        </svg>
      </section>

      {/* WHO WE ARE */}
      <section className="py-12 md:py-16 lg:py-24 bg-[var(--bz-cream)]">
        <div className="container mx-auto px-4 max-w-3xl text-center">
          <span className="bz-label text-[var(--bz-amber)]">
            <InlineEditable sectionId={sectionId} settingKey="who_label" value={whoLabel} />
          </span>
          <h2 className="bz-heading text-2xl sm:text-3xl md:text-5xl mt-3 md:mt-4 text-[var(--bz-dark)] leading-tight">
            <InlineEditable sectionId={sectionId} settingKey="who_headline" value={whoHeadline} />
          </h2>
          <p className="text-[var(--bz-dark)]/60 mt-6 md:mt-8 leading-relaxed text-sm md:text-base whitespace-pre-line">
            <InlineEditable sectionId={sectionId} settingKey="description" value={description} multiline />
          </p>
        </div>
      </section>

      {/* MARQUEE */}
      <section className="bg-[var(--bz-navy)] py-5 overflow-hidden" aria-label={marqueeText}>
        <div className="bz-marquee-track" aria-hidden="true">
          {[...Array(6)].map((_, i) => (
            <span key={i} className="bz-heading text-lg sm:text-xl md:text-2xl lg:text-3xl text-[var(--bz-amber)] whitespace-nowrap mx-6 sm:mx-8">
              {marqueeText}
            </span>
          ))}
        </div>
      </section>

      {/* VALUES — hidden entirely when the merchant has configured none, so a
          live store shows no orphaned heading over an empty grid. */}
      {values.length > 0 && (
      <section className="relative py-12 md:py-20 lg:py-32 bg-[var(--bz-dark)] overflow-hidden">
        <svg viewBox="0 0 1440 60" className="absolute top-0 start-0 w-full -mt-px" preserveAspectRatio="none" aria-hidden="true">
          <path d="M0,0 C360,50 720,10 1080,40 C1260,55 1380,20 1440,30 L1440,0 L0,0 Z" fill="var(--bz-navy)" />
        </svg>
        <div className="container mx-auto px-4">
          <div className="text-center mb-10 md:mb-14">
            <span className="bz-label text-[var(--bz-amber)]">
              <InlineEditable sectionId={sectionId} settingKey="values_label" value={valuesLabel} />
            </span>
            <h2 className="bz-heading text-2xl sm:text-3xl md:text-5xl text-[var(--bz-cream)] mt-3 md:mt-4">
              <InlineEditable sectionId={sectionId} settingKey="values_headline" value={valuesHeadline} />
            </h2>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 sm:gap-5 lg:gap-6">
            {values.map((value, i) => {
              const Icon = iconFor(value.icon);
              return (
                <div key={`${value.title}-${i}`} className="rounded-2xl md:rounded-3xl border-2 border-[var(--bz-amber)]/20 bg-[var(--bz-navy)] p-5 sm:p-6 md:p-8 text-center hover:border-[var(--bz-amber)] transition-colors duration-300">
                  <div className="inline-flex items-center justify-center w-12 h-12 sm:w-14 sm:h-14 rounded-full bg-[var(--bz-amber)]/10 mb-4 md:mb-6">
                    <Icon size={24} className="text-[var(--bz-amber)]" aria-hidden="true" />
                  </div>
                  <h3 className="bz-heading text-base md:text-lg text-[var(--bz-cream)]">{value.title}</h3>
                  <p className="text-[var(--bz-cream)]/40 mt-3 md:mt-4 text-xs leading-relaxed">{value.description}</p>
                </div>
              );
            })}
          </div>
        </div>
        <svg viewBox="0 0 1440 60" className="absolute bottom-0 start-0 w-full mb-[-1px]" preserveAspectRatio="none" aria-hidden="true">
          <path d="M0,60 C360,10 720,50 1080,20 C1260,5 1380,40 1440,30 L1440,60 L0,60 Z" fill="var(--bz-cream)" />
        </svg>
      </section>
      )}

      {/* CTA */}
      <section className="py-12 md:py-20 lg:py-28 bg-[var(--bz-cream)]">
        <div className="container mx-auto px-4 text-center">
          <span className="bz-label text-[var(--bz-amber)]">
            <InlineEditable sectionId={sectionId} settingKey="cta_label" value={ctaLabel} />
          </span>
          <h2 className="bz-heading text-2xl sm:text-3xl md:text-5xl text-[var(--bz-dark)] mt-3 md:mt-4 leading-tight">
            <InlineEditable sectionId={sectionId} settingKey="cta_headline" value={ctaHeadline} />
          </h2>
          <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-center gap-3 sm:gap-4 mt-8 md:mt-10 max-w-md sm:max-w-none mx-auto">
            <Link to={ctaLink} className="bz-btn bz-btn-filled bz-btn-amber rounded-full text-[10px] sm:text-[11px] px-6 sm:px-8 py-3 justify-center">
              <InlineEditable sectionId={sectionId} settingKey="cta_text" value={ctaText} />
            </Link>
            <Link to={contactCtaLink} className="bz-btn rounded-full text-[10px] sm:text-[11px] px-6 sm:px-8 py-3 justify-center">
              <InlineEditable sectionId={sectionId} settingKey="contact_cta_text" value={contactCtaText} />
            </Link>
          </div>
        </div>
      </section>
    </div>
  );
};

export default BzAboutSection;
