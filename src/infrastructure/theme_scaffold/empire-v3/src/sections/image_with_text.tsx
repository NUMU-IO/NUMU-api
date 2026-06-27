import { EditableImage } from "@numueg/theme-sdk";
import { EditableText } from "../lib/EditableText";
import type { EmpSectionProps } from "../lib/section";

interface IwtSettings {
  eyebrow?: string;
  title?: string;
  subtitle?: string;
  cta_text?: string;
  cta_link?: string;
  image?: string;
  image_position?: "start" | "end" | "background";
  overlay?: boolean;
}

/**
 * Image-with-text editorial block. Two modes:
 *  - "background": full-bleed image with a dark overlay and centered copy
 *    (used as a page hero, e.g. About / Lookbook).
 *  - "start" / "end": side-by-side image + copy column.
 * Title/subtitle/CTA + the image are inline-editable.
 */
export default function ImageWithText({ id, settings }: EmpSectionProps) {
  const s = settings as IwtSettings;
  const pos = s.image_position ?? "start";
  const image =
    s.image ||
    "https://images.unsplash.com/photo-1558618666-fcd25c85cd64?w=1600";
  const hasCta = Boolean(s.cta_text);

  const Copy = (
    <div className="empire-iwt__copy">
      {s.eyebrow ? (
        <EditableText
          as="p"
          className="empire-label"
          sectionId={id}
          settingId="eyebrow"
          value={s.eyebrow}
        />
      ) : null}
      <EditableText
        as="h2"
        className="empire-heading"
        sectionId={id}
        settingId="title"
        value={s.title ?? "قصة علامتنا"}
      />
      {s.subtitle ? (
        <EditableText
          as="p"
          className="empire-iwt__sub"
          sectionId={id}
          settingId="subtitle"
          value={s.subtitle}
        />
      ) : null}
      {hasCta ? (
        <a
          className={pos === "background" ? "empire-btn-light" : "empire-btn"}
          href={s.cta_link || "/products"}
        >
          <EditableText
            as="span"
            sectionId={id}
            settingId="cta_text"
            value={s.cta_text as string}
          />
        </a>
      ) : null}
    </div>
  );

  if (pos === "background") {
    return (
      <section className="empire-iwt empire-iwt--bg">
        <EditableImage
          className="empire-iwt__bgimg"
          sectionId={id}
          settingId="image"
          src={image}
          alt=""
        />
        {s.overlay !== false ? <div className="empire-iwt__overlay" /> : null}
        <div className="empire-container empire-iwt__bgcontent">{Copy}</div>
      </section>
    );
  }

  return (
    <section className="empire-section empire-bg-white">
      <div className={`empire-container empire-iwt empire-iwt--${pos}`}>
        <div className="empire-iwt__media">
          <EditableImage
            sectionId={id}
            settingId="image"
            src={image}
            alt={(s.title as string) || ""}
          />
        </div>
        {Copy}
      </div>
    </section>
  );
}
