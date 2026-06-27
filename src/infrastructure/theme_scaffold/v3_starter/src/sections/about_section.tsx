import {
  EditableImage,
  type BlockInstance,
} from "@numueg/theme-sdk";
import { EditableText } from "../lib/EditableText";
import type { EmpSectionProps } from "../lib/section";

interface AboutSettings {
  eyebrow?: string;
  title?: string;
  body?: string;
  image?: string;
}

/**
 * About / brand-story block — eyebrow + display heading + body paragraph beside
 * an image, with an optional row of stat highlights (`stat` blocks: value +
 * label). All copy + the image are inline-editable.
 */
export default function AboutSection({
  id,
  settings,
  blocks,
  blockOrder,
}: EmpSectionProps) {
  const s = settings as AboutSettings;

  const stats = (blockOrder ?? [])
    .map((bid) => ({ bid, block: blocks?.[bid] }))
    .filter(
      (x): x is { bid: string; block: BlockInstance } =>
        !!x.block && !x.block.disabled && x.block.type === "stat",
    );

  return (
    <section className="nt-section nt-bg-white">
      <div className="nt-container nt-about">
        <div className="nt-about__copy">
          {s.eyebrow ? (
            <EditableText
              as="p"
              className="nt-label"
              sectionId={id}
              settingId="eyebrow"
              value={s.eyebrow}
            />
          ) : null}
          <EditableText
            as="h2"
            className="nt-display-sm"
            sectionId={id}
            settingId="title"
            value={s.title ?? "صُنع بشغف، يدوم مدى الحياة"}
          />
          <EditableText
            as="p"
            className="nt-about__body"
            sectionId={id}
            settingId="body"
            value={
              s.body ??
              "بدأنا كاستوديو صغير مستقل، وكبرنا بفضل عملائنا. كل قطعة تُختار بعناية لتجمع بين التصميم النظيف والجودة التي تدوم."
            }
          />
          {stats.length > 0 ? (
            <div className="nt-about__stats">
              {stats.map(({ bid, block }) => (
                <div className="nt-about__stat" key={bid}>
                  <EditableText
                    as="span"
                    className="nt-about__statvalue"
                    sectionId={id}
                    blockId={bid}
                    settingId="value"
                    value={(block.settings.value as string) || "—"}
                  />
                  <EditableText
                    as="span"
                    className="nt-about__statlabel"
                    sectionId={id}
                    blockId={bid}
                    settingId="label"
                    value={(block.settings.label as string) || ""}
                  />
                </div>
              ))}
            </div>
          ) : null}
        </div>
        <div className="nt-about__media">
          <EditableImage
            sectionId={id}
            settingId="image"
            src={
              s.image ||
              "https://images.unsplash.com/photo-1441984904996-e0b6ba687e04?w=1200"
            }
            alt={(s.title as string) || ""}
          />
        </div>
      </div>
    </section>
  );
}
