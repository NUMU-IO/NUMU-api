import { EditableImage } from "@numueg/theme-sdk";
import { EditableText } from "../lib/EditableText";
import type { EmpSectionProps } from "../lib/section";
import { useT } from "../lib/i18n";

interface PromoSettings {
  badge_text?: string;
  headline?: string;
  subtitle?: string;
  cta_text?: string;
  cta_link?: string;
  image_url?: string;
}

/**
 * Full-width black promo banner — square image beside a badge + headline +
 * subtitle and a light pill CTA. All text + the image are inline-editable.
 */
export default function PromoBanner({ id, settings }: EmpSectionProps) {
  const s = settings as PromoSettings;
  const t = useT();
  const badge = s.badge_text ?? t("Limited offer", "عرض محدود");
  const headline = s.headline ?? t("25% off all accessories", "خصم ٢٥٪ على كل الإكسسوارات");
  const subtitle = s.subtitle ?? t("Ends this month — don't miss out!", "العرض ساري لنهاية الشهر. متفوتش الفرصة!");
  const ctaText = s.cta_text ?? t("Shop now", "تسوق الآن");
  const ctaLink = s.cta_link ?? "/products";

  return (
    <section style={{ paddingBlock: "2rem" }}>
      <div className="nt-container">
        <div className="nt-promo">
          <div className="nt-promo__img">
            <EditableImage
              sectionId={id}
              settingId="image_url"
              src={
                s.image_url ||
                "https://images.unsplash.com/photo-1611085583191-a3b181a88401?w=600"
              }
              alt=""
            />
          </div>
          <div className="nt-promo__body">
            {badge ? (
              <EditableText
                as="span"
                className="nt-promo__badge"
                sectionId={id}
                settingId="badge_text"
                value={badge}
              />
            ) : null}
            <EditableText
              as="h3"
              className="nt-promo__title"
              sectionId={id}
              settingId="headline"
              value={headline}
            />
            <EditableText
              as="p"
              className="nt-promo__sub"
              sectionId={id}
              settingId="subtitle"
              value={subtitle}
            />
            <a className="nt-btn-light" href={ctaLink}>
              <EditableText
                as="span"
                sectionId={id}
                settingId="cta_text"
                value={ctaText}
              />
            </a>
          </div>
        </div>
      </div>
    </section>
  );
}
