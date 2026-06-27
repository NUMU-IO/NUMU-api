import { type BlockInstance } from "@numueg/theme-sdk";
import { EditableText } from "../lib/EditableText";
import type { EmpSectionProps } from "../lib/section";
import { useT } from "../lib/i18n";

interface TestimonialsSettings {
  title?: string;
}

/** Customer reviews — three bordered cards on the off-white canvas, each with
 *  black star rating, quote and an avatar initial. Cards come from `review`
 *  blocks the merchant arranges in the customizer. */
export default function Testimonials({
  id,
  settings,
  blocks,
  blockOrder,
}: EmpSectionProps) {
  const s = settings as TestimonialsSettings;
  const t = useT();
  const title = s.title ?? t("What our customers say", "رأي عملائنا");

  const reviews = (blockOrder ?? [])
    .map((bid) => ({ bid, block: blocks?.[bid] }))
    .filter(
      (x): x is { bid: string; block: BlockInstance } =>
        !!x.block && !x.block.disabled && x.block.type === "review",
    );

  if (reviews.length === 0) return null;

  return (
    <section className="nt-section nt-bg-white">
      <div className="nt-container">
        <EditableText
          as="h2"
          className="nt-display-sm"
          sectionId={id}
          settingId="title"
          value={title}
          style={{ textAlign: "center", marginBottom: "2rem" }}
        />
        <div className="nt-tgrid">
          {reviews.map(({ bid, block }) => {
            const rating = Math.max(
              0,
              Math.min(5, Number(block.settings.rating ?? 5)),
            );
            const author = (block.settings.author as string) || "عميل";
            const city = block.settings.city as string | undefined;
            const text = (block.settings.text as string) || "";
            return (
              <div className="nt-tcard" key={bid}>
                <div className="nt-stars" aria-label={`${rating} من 5`}>
                  {Array.from({ length: 5 }).map((_, i) => (
                    <Star key={i} filled={i < rating} />
                  ))}
                </div>
                <EditableText
                  as="p"
                  className="nt-tquote"
                  sectionId={id}
                  blockId={bid}
                  settingId="text"
                  value={`"${text}"`}
                />
                <div className="nt-tauthor">
                  <span className="nt-tavatar">{author[0]}</span>
                  <div>
                    <EditableText
                      as="p"
                      className="nt-tname"
                      sectionId={id}
                      blockId={bid}
                      settingId="author"
                      value={author}
                    />
                    {city ? (
                      <EditableText
                        as="p"
                        className="nt-tcity"
                        sectionId={id}
                        blockId={bid}
                        settingId="city"
                        value={city}
                      />
                    ) : null}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}

const Star = ({ filled }: { filled: boolean }) => (
  <svg
    width="14"
    height="14"
    viewBox="0 0 24 24"
    fill={filled ? "currentColor" : "none"}
    stroke="currentColor"
    strokeWidth="2"
    className={`nt-star${filled ? " is-filled" : ""}`}
    aria-hidden="true"
  >
    <path d="m12 2 3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z" />
  </svg>
);
