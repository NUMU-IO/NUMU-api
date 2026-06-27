import { RichText } from "@numueg/theme-sdk";
import { EditableText } from "../lib/EditableText";
import type { EmpSectionProps } from "../lib/section";

interface RichTextSettings {
  title?: string;
  content?: string;
  align?: "start" | "center";
  width?: "narrow" | "wide";
}

/**
 * Rich-text content block — body for the `page` template (About / Shipping /
 * Returns / Terms CMS pages). The merchant's HTML lives in `content` and is
 * rendered through the SDK's sanitising `<RichText>`; the optional title is
 * inline-editable.
 */
export default function RichTextSection({ id, settings }: EmpSectionProps) {
  const s = settings as RichTextSettings;
  const align = s.align === "center" ? "center" : "start";
  const narrow = s.width !== "wide";

  return (
    <section className="nt-container" style={{ paddingBlock: "3rem" }}>
      <div
        className="nt-richtext"
        style={{
          textAlign: align,
          maxWidth: narrow ? "44rem" : "64rem",
          marginInline: align === "center" ? "auto" : undefined,
        }}
      >
        {s.title ? (
          <EditableText
            as="h1"
            className="nt-display-sm"
            sectionId={id}
            settingId="title"
            value={s.title}
            style={{ marginBottom: "1.5rem" }}
          />
        ) : null}
        {s.content ? (
          <RichText className="nt-prose" html={s.content} />
        ) : (
          <p className="nt-placeholder">أضف المحتوى من إعدادات القسم.</p>
        )}
      </div>
    </section>
  );
}
