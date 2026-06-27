import { useCollections } from "@numueg/theme-sdk";
import { EditableText } from "../lib/EditableText";
import type { EmpSectionProps } from "../lib/section";
import { useT } from "../lib/i18n";

interface CategoriesSettings {
  title?: string;
  columns_desktop?: number;
}

/**
 * Shop-by-category grid — black square tiles holding the collection image
 * (contained on black) with an uppercase tracked label beneath. Reads the
 * store's collections via the SDK.
 */
export default function Categories({ id, settings }: EmpSectionProps) {
  const s = settings as CategoriesSettings;
  const t = useT();
  const cols = Math.max(2, Math.min(6, s.columns_desktop ?? 5));
  const { collections } = useCollections({ fetchIfMissing: true });

  if (collections.length === 0) return null;

  return (
    <section className="empire-section empire-bg-white">
      <div className="empire-container">
        <EditableText
          as="h2"
          className="empire-heading"
          sectionId={id}
          settingId="title"
          value={s.title ?? t("Shop by category", "تسوق حسب الفئة")}
          style={{ marginBottom: "1.5rem" }}
        />
        <div
          className="empire-catgrid"
          style={{ ["--cols" as string]: cols }}
        >
          {collections.map((c) => (
            <a key={c.id} className="empire-cat" href={`/collections/${c.slug}`}>
              <div className="empire-cat__media">
                {c.image_url ? (
                  <img src={c.image_url} alt={c.name} loading="lazy" />
                ) : (
                  <span className="empire-cat__placeholder">
                    {c.name?.[0] ?? "?"}
                  </span>
                )}
              </div>
              <h3 className="empire-cat__name">{c.name}</h3>
            </a>
          ))}
        </div>
      </div>
    </section>
  );
}
