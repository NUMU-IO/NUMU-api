import {
  useMemo,
  useState } from "react";
import {
  useProductOptional,
  useRelatedProducts,
  useProducts,
  usePage,
  useCart,
  useLocalization,
  useShop,
  type Product,
} from "@numueg/theme-sdk";
import { EditableText } from "../lib/EditableText";
import type { EmpSectionProps } from "../lib/section";
import { openCart } from "../lib/cartUI";

interface FbtSettings {
  title?: string;
  max_items?: number;
  /** Merchant on/off switch (belt-and-suspenders alongside the section
   *  visibility toggle, which the host honours via `instance.disabled`). */
  enabled?: boolean;
  /** When true, render only the card (no full-width section wrapper) so it can
   *  sit inside the PDP buy column. */
  embedded?: boolean;
}

/**
 * Frequently bought together — seeds the bundle with the current product and
 * appends related items (`useRelatedProducts`, falling back to the page list).
 * Each row is a togglable checkbox; "أضف الكل" adds every checked product to the
 * live cart in one pass and pops the drawer. Hidden when fewer than 2 items.
 */
export default function FrequentlyBought({ id, settings }: EmpSectionProps) {
  const s = settings as FbtSettings;
  const max = Math.max(2, Math.min(4, s.max_items ?? 3));

  const product = useProductOptional();
  const related = useRelatedProducts(product?.id, { limit: max });
  const page = usePage();
  const { addItem } = useCart();
  const { formatMoney } = useLocalization();
  const shop = useShop();

  const pageProducts = (page?.data?.products as Product[] | undefined) ?? [];
  // Last-resort fallback so the bundle still renders on PDP routes that pass
  // only the single product (no related endpoint data, no product list).
  const catalog = useProducts({ fetchIfMissing: true });

  const bundle = useMemo(() => {
    const pool =
      related.items.length > 0
        ? related.items
        : pageProducts.length > 0
          ? pageProducts
          : catalog.products;
    const seen = new Set<string>();
    const list: Product[] = [];
    if (product) {
      list.push(product);
      seen.add(product.id);
    }
    for (const p of pool) {
      if (list.length >= max) break;
      if (seen.has(p.id)) continue;
      seen.add(p.id);
      list.push(p);
    }
    return list;
  }, [product, related.items, pageProducts, catalog.products, max]);

  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const isOn = (pid: string) => selected[pid] !== false; // default on
  const [pending, setPending] = useState(false);

  const currency = product?.currency || shop?.currency;
  // Coerce to Number — related/catalog endpoints can return `price` as a string,
  // which would make `sum + p.price` concatenate ("0"+"12"+"110" → 12110)
  // instead of adding.
  const total = bundle
    .filter((p) => isOn(p.id))
    .reduce((sum, p) => sum + (Number(p.price) || 0), 0);

  if (s.enabled === false) return null;
  // On a product page the PDP buy-box embeds its own FBT (`embedded`), so a
  // standalone section here would be a duplicate. Step aside when a product is
  // in context and we're not the embedded instance. (Off-PDP, `product` is
  // null and the standalone "you might also like" use still renders.)
  if (!s.embedded && product) return null;
  if (bundle.length < 2) return null;

  async function addBundle() {
    if (pending) return;
    setPending(true);
    try {
      for (const p of bundle) {
        if (!isOn(p.id)) continue;
        await addItem(p.id, p.variants?.[0]?.id, 1);
      }
      openCart();
    } finally {
      setPending(false);
    }
  }

  const card = (
    <div className="nt-fbt-card">
      <EditableText
        as="h2"
        className="nt-fbt-card__title"
        sectionId={id}
        settingId="title"
        value={s.title ?? "يُشترى عادةً معاً"}
      />

      <div className="nt-fbt">
        {bundle.map((p, i) => {
          // Tolerate images as ProductImage objects OR raw url strings.
          const raw = p.images?.[0] as unknown;
          const imgUrl =
            typeof raw === "string" ? raw : (raw as { url?: string } | undefined)?.url;
          return (
            <div className="nt-fbt__item" key={p.id}>
              <label className="nt-fbt__card">
                <input
                  type="checkbox"
                  checked={isOn(p.id)}
                  onChange={(e) =>
                    setSelected((prev) => ({ ...prev, [p.id]: e.target.checked }))
                  }
                />
                <span className="nt-fbt__thumb">
                  {imgUrl ? (
                    <img
                      src={imgUrl}
                      alt={p.name}
                      loading="lazy"
                      onError={(e) => {
                        const el = e.target as HTMLImageElement;
                        el.style.visibility = "hidden";
                      }}
                    />
                  ) : (
                    <span className="nt-card__placeholder" aria-hidden="true" />
                  )}
                </span>
                <span className="nt-fbt__info">
                  <span className="nt-fbt__name">{p.name}</span>
                  <span className="nt-fbt__price">
                    {formatMoney(Number(p.price) || 0, currency)}
                  </span>
                </span>
              </label>
              {i < bundle.length - 1 ? (
                <span className="nt-fbt__plus" aria-hidden="true">
                  +
                </span>
              ) : null}
            </div>
          );
        })}
      </div>

      <div className="nt-fbt__foot">
        <p className="nt-fbt__total">
          الإجمالي: <strong>{formatMoney(total, currency)}</strong>
        </p>
        <button
          className="nt-btn"
          type="button"
          disabled={pending || total <= 0}
          onClick={addBundle}
        >
          {pending ? "..." : "أضف الكل للسلة"}
        </button>
      </div>
    </div>
  );

  if (s.embedded) return card;
  return (
    <section className="nt-container" style={{ paddingBlock: "2rem" }}>
      {card}
    </section>
  );
}
