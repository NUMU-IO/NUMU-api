import { EditableText } from "../lib/EditableText";
import type { EmpSectionProps } from "../lib/section";

interface NotFoundSettings {
  code?: string;
  title?: string;
  message?: string;
  cta_text?: string;
  cta_link?: string;
}

/** 404 page body — oversized code numeral, message and a CTA back to the shop. */
export default function NotFound({ id, settings }: EmpSectionProps) {
  const s = settings as NotFoundSettings;

  return (
    <section
      className="nt-container"
      style={{ paddingBlock: "6rem", textAlign: "center" }}
    >
      <p
        className="nt-display"
        style={{ fontSize: "clamp(4rem, 18vw, 9rem)", lineHeight: 1 }}
      >
        {s.code || "404"}
      </p>
      <EditableText
        as="h1"
        className="nt-heading"
        sectionId={id}
        settingId="title"
        value={s.title ?? "الصفحة غير موجودة"}
        style={{ marginBottom: "0.75rem" }}
      />
      <EditableText
        as="p"
        className="nt-muted"
        sectionId={id}
        settingId="message"
        value={
          s.message ??
          "يبدو أن الصفحة التي تبحث عنها غير متاحة أو تم نقلها."
        }
        style={{ marginBottom: "2rem" }}
      />
      <a className="nt-btn" href={s.cta_link || "/products"}>
        <EditableText
          as="span"
          sectionId={id}
          settingId="cta_text"
          value={s.cta_text ?? "العودة للتسوق"}
        />
      </a>
    </section>
  );
}
