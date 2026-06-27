import { useState } from "react";
import {
  useProductOptional,
  useVariantSelection,
  useRelatedProducts,
  useProductSizeChart,
  useCart,
  useLocalization,
  useShop,
  defaultVariant,
} from "@numueg/theme-sdk";
import { EditableText } from "../lib/EditableText";
import type { EmpSectionProps } from "../lib/section";
import { ProductCard } from "../lib/ProductCard";
import { openCart } from "../lib/cartUI";
import { useT } from "../lib/i18n";
import FrequentlyBought from "./frequently_bought";

interface PdpSettings {
  add_to_cart_label?: string;
  show_compare_price?: boolean;
  show_related?: boolean;
  show_whatsapp?: boolean;
  show_fbt?: boolean;
}

/**
 * Premium product detail page — gallery + buy box. Variant picker (option axes
 * or flat variant list), inline size guide, quantity, full-width add-to-cart +
 * WhatsApp, share row, a trust strip, and the "frequently bought" bundle
 * embedded directly in the buy column.
 */
export default function ProductDetails({ id, settings }: EmpSectionProps) {
  const s = settings as PdpSettings;
  const t = useT();
  const product = useProductOptional();
  const { cart, addItem, updateNote } = useCart();
  const { formatMoney } = useLocalization();
  const shop = useShop();
  const chart = useProductSizeChart();
  const [pending, setPending] = useState(false);
  const [activeImg, setActiveImg] = useState(0);
  const [qty, setQty] = useState(1);
  const [pickedId, setPickedId] = useState<string | undefined>(undefined);
  const [pickedSize, setPickedSize] = useState<string | undefined>(undefined);
  const [sizeOpen, setSizeOpen] = useState(false);
  const [copied, setCopied] = useState(false);

  const variantSel = useVariantSelection(
    product ?? { options: [], variants: [] },
    { autoSelect: true },
  );
  const related = useRelatedProducts(product?.id, { limit: 4 });

  if (!product) {
    return (
      <section className="nt-page nt-container">
        <p className="nt-placeholder">
          {t("Product not found.", "لم يتم العثور على المنتج.")}
        </p>
      </section>
    );
  }

  const opts = product.options ?? [];
  const variants = product.variants ?? [];
  const hasOptions = opts.length > 0;
  const hasVariantList = !hasOptions && variants.length > 1;

  // Sizes from the size chart — when a product has no real option axes (the
  // common case here: one SKU, sizes only described in the chart) we still let
  // the buyer pick a size, mirroring how the bazaar storefront renders sizes.
  // The choice is recorded on the cart note (the SDK's add-to-cart carries no
  // per-line properties and there's a single variant to attach it to).
  const chartSizes = (chart?.rows ?? [])
    .map((r) => r.size)
    .filter((sz): sz is string => Boolean(sz));
  // A chart can be "enabled" but empty (merchant toggled it on, never filled
  // it) — only treat it as showable when it actually has rows or an image.
  const chartHasContent =
    !!chart && (chartSizes.length > 0 || Boolean(chart.image_url));
  const needsSize = !hasOptions && !hasVariantList && chartSizes.length > 0;

  const { selection, variant, select, availability, isComplete } = variantSel;
  const fallbackVariant = hasOptions
    ? null
    : (variants.find((v) => v.id === pickedId) ??
        defaultVariant(product) ??
        variants[0] ??
        null);
  const activeVariant = hasOptions ? variant : fallbackVariant;

  const currency = product.currency || shop?.currency;
  const activePrice = Number(activeVariant?.price ?? product.price) || 0;
  const compareRaw = Number(activeVariant?.compare_at_price ?? product.compare_at_price) || 0;
  const compareAt =
    s.show_compare_price !== false && compareRaw > activePrice ? compareRaw : null;

  const images = product.images ?? [];
  const mainImage = images[activeImg] ?? images[0];
  const purchasable =
    product.in_stock &&
    (activeVariant?.is_in_stock ?? true) &&
    (hasOptions ? isComplete : true) &&
    (needsSize ? Boolean(pickedSize) : true);
  const productId = product.id;
  const productName = product.name;
  const selectedVariantId = activeVariant?.id ?? pickedId;

  const href = `/products/${product.slug}`;
  const shareUrl = shop?.formatUrl ? shop.formatUrl(href) : href;
  const enc = encodeURIComponent(shareUrl);
  const waNumber = ((shop?.social_links?.whatsapp as string) || "").replace(/\D/g, "");
  const waHref = waNumber
    ? `https://wa.me/${waNumber}?text=${encodeURIComponent(`${product.name} — ${shareUrl}`)}`
    : `https://wa.me/?text=${encodeURIComponent(`${product.name} — ${shareUrl}`)}`;

  async function handleAdd() {
    if (pending || !purchasable) return;
    setPending(true);
    try {
      // Record the chosen size on the cart note so the merchant fulfils the
      // right size. Replace any prior line for this product (re-adds, qty
      // changes) so the note stays one line per product.
      if (needsSize && pickedSize) {
        const prefix = `• ${productName} — `;
        const tag = `${prefix}${t("Size", "المقاس")}: `;
        const prev = cart?.note ?? "";
        const kept = prev
          .split("\n")
          .filter((line) => line && !line.startsWith(prefix))
          .join("\n");
        const next = `${kept ? `${kept}\n` : ""}${tag}${pickedSize}`;
        await updateNote(next);
      }
      await addItem(productId, selectedVariantId, qty);
      openCart();
    } finally {
      setPending(false);
    }
  }

  function copyLink() {
    try {
      navigator.clipboard?.writeText(shareUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* ignore */
    }
  }

  const variantLabel = (v: (typeof variants)[number], i: number) =>
    v.name ||
    Object.values(v.option_values ?? {}).join(" / ") ||
    `${t("Option", "الخيار")} ${i + 1}`;

  const shares = [
    { name: "telegram", href: `https://t.me/share/url?url=${enc}`, icon: <IconTelegram /> },
    { name: "x", href: `https://twitter.com/intent/tweet?url=${enc}`, icon: <IconX /> },
    { name: "facebook", href: `https://www.facebook.com/sharer/sharer.php?u=${enc}`, icon: <IconFacebook /> },
    { name: "whatsapp", href: `https://wa.me/?text=${enc}`, icon: <IconWhatsApp /> },
  ];

  return (
    <section className="nt-container" style={{ paddingBlock: "2.5rem" }}>
      <nav className="nt-breadcrumb nt-label">
        <a href="/">{t("Home", "الرئيسية")}</a>
        <span>/</span>
        <a href="/products">{t("Shop", "المتجر")}</a>
        <span>/</span>
        <span style={{ color: "var(--nt-fg)" }}>{product.name}</span>
      </nav>

      <div className="nt-pdp">
        {/* Gallery */}
        <div className="nt-pdp__gallery">
          <div className="nt-pdp__main-img">
            {compareAt ? (
              <span className="nt-badge nt-badge--blue" style={{ insetInlineStart: "auto" }}>
                -{Math.round((1 - activePrice / compareAt) * 100)}%
              </span>
            ) : null}
            {mainImage ? (
              <img src={mainImage.url} alt={mainImage.alt || product.name} />
            ) : (
              <div className="nt-card__placeholder" />
            )}
          </div>
          {images.length > 1 ? (
            <div className="nt-pdp__thumbs">
              {images.map((img, i) => (
                <button
                  key={img.id ?? i}
                  type="button"
                  className={`nt-pdp__thumb${i === activeImg ? " is-active" : ""}`}
                  onClick={() => setActiveImg(i)}
                  aria-label={`${t("Image", "صورة")} ${i + 1}`}
                >
                  <img src={img.url} alt={img.alt || ""} />
                </button>
              ))}
            </div>
          ) : null}
        </div>

        {/* Buy box */}
        <div className="nt-pdp__info">
          {product.category ? (
            <p className="nt-label" style={{ marginBottom: "0.5rem" }}>
              {product.category}
            </p>
          ) : null}
          <h1 className="nt-pdp__title">{product.name}</h1>

          <div className="nt-pdp__pricerow">
            <p className="nt-pdp__price">
              {compareAt ? (
                <span className="nt-card__compare">{formatMoney(compareAt, currency)}</span>
              ) : null}
              {formatMoney(activePrice, currency)}
            </p>
            <span className={`nt-pdp__stock${product.in_stock ? " is-in" : " is-out"}`}>
              {product.in_stock ? t("In stock", "متوفر") : t("Out of stock", "نفذ المخزون")}
              <span className="nt-pdp__dot" aria-hidden />
            </span>
          </div>

          {product.description ? (
            <p className="nt-pdp__desc">{product.description}</p>
          ) : null}

          <div className="nt-pdp__divider" />

          {/* Option-axis picker */}
          {opts.map((opt) => (
            <div key={opt.name} className="nt-pdp__optgroup">
              <div className="nt-pdp__opt-head">
                {chartHasContent ? (
                  <button type="button" className="nt-pdp__sizelink" onClick={() => setSizeOpen(true)}>
                    <SizeIcon /> {t("Size guide", "دليل المقاسات")}
                  </button>
                ) : <span />}
                <span className="nt-pdp__opt-label">{opt.name}</span>
              </div>
              <div className="nt-pdp__sizes">
                {opt.values.map((value) => {
                  const selected = selection[opt.name] === value;
                  const reachable = availability[opt.name]?.has(value) ?? true;
                  return (
                    <button
                      key={value}
                      type="button"
                      className={`nt-sizebox${selected ? " is-active" : ""}`}
                      aria-pressed={selected}
                      disabled={!reachable && !selected}
                      onClick={() => select(opt.name, value)}
                    >
                      {value}
                    </button>
                  );
                })}
              </div>
            </div>
          ))}

          {/* Flat variant picker */}
          {hasVariantList ? (
            <div className="nt-pdp__optgroup">
              <div className="nt-pdp__opt-head">
                {chartHasContent ? (
                  <button type="button" className="nt-pdp__sizelink" onClick={() => setSizeOpen(true)}>
                    <SizeIcon /> {t("Size guide", "دليل المقاسات")}
                  </button>
                ) : <span />}
                <span className="nt-pdp__opt-label">{t("Option", "الخيار")}</span>
              </div>
              <div className="nt-pdp__sizes">
                {variants.map((v, i) => {
                  const isSel = (activeVariant?.id ?? null) === v.id;
                  const inStock = v.is_in_stock ?? v.in_stock ?? true;
                  return (
                    <button
                      key={v.id}
                      type="button"
                      className={`nt-sizebox${isSel ? " is-active" : ""}`}
                      aria-pressed={isSel}
                      disabled={!inStock}
                      onClick={() => setPickedId(v.id)}
                    >
                      {variantLabel(v, i)}
                    </button>
                  );
                })}
              </div>
            </div>
          ) : null}

          {/* Size picker derived from the size chart (no real option axes) */}
          {needsSize ? (
            <div className="nt-pdp__optgroup">
              <div className="nt-pdp__opt-head">
                <button type="button" className="nt-pdp__sizelink" onClick={() => setSizeOpen(true)}>
                  <SizeIcon /> {t("Size guide", "دليل المقاسات")}
                </button>
                <span className="nt-pdp__opt-label">{t("Size", "المقاس")}</span>
              </div>
              <div className="nt-pdp__sizes">
                {chartSizes.map((sz) => {
                  const selected = pickedSize === sz;
                  return (
                    <button
                      key={sz}
                      type="button"
                      className={`nt-sizebox${selected ? " is-active" : ""}`}
                      aria-pressed={selected}
                      onClick={() => setPickedSize(sz)}
                    >
                      {sz}
                    </button>
                  );
                })}
              </div>
              {!pickedSize ? (
                <p className="nt-pdp__hint">
                  {t("Please select a size", "اختر المقاس من فضلك")}
                </p>
              ) : null}
            </div>
          ) : chartHasContent && !hasOptions && !hasVariantList ? (
            <button
              type="button"
              className="nt-pdp__sizelink nt-pdp__sizelink--standalone"
              onClick={() => setSizeOpen(true)}
            >
              <SizeIcon /> {t("Size guide", "دليل المقاسات")}
            </button>
          ) : null}

          {/* Quantity */}
          <div className="nt-pdp__qtyblock">
            <span className="nt-pdp__opt-label">{t("Quantity", "الكمية")}</span>
            <div className="nt-qty nt-pdp__qty" aria-label={t("Quantity", "الكمية")}>
              <button type="button" aria-label={t("Decrease", "تقليل")} onClick={() => setQty((q) => Math.max(1, q - 1))}>
                −
              </button>
              <span>{qty}</span>
              <button type="button" aria-label={t("Increase", "زيادة")} onClick={() => setQty((q) => q + 1)}>
                +
              </button>
            </div>
          </div>

          {/* CTAs */}
          <button
            className="nt-btn nt-btn--block"
            type="button"
            style={{ marginTop: "1.25rem" }}
            disabled={!purchasable || pending}
            onClick={handleAdd}
          >
            {pending ? (
              "..."
            ) : !product.in_stock ? (
              t("Sold out", "نفذ المخزون")
            ) : needsSize && !pickedSize ? (
              t("Select a size", "اختر المقاس")
            ) : (
              <EditableText
                as="span"
                sectionId={id}
                settingId="add_to_cart_label"
                value={s.add_to_cart_label || t("Add to cart", "أضف إلى السلة")}
              />
            )}
          </button>
          {s.show_whatsapp !== false ? (
            <a className="nt-btn-outline nt-btn--block" href={waHref} target="_blank" rel="noopener noreferrer" style={{ marginTop: "0.625rem" }}>
              {t("Ask via WhatsApp", "اسأل عبر واتساب")} <IconWhatsApp />
            </a>
          ) : null}

          {/* Share */}
          <div className="nt-pdp__share">
            <button type="button" className="nt-pdp__sharebtn" onClick={copyLink} aria-label={t("Copy link", "نسخ الرابط")}>
              {copied ? <IconCheck /> : <IconLink />}
            </button>
            {shares.map((sh) => (
              <a key={sh.name} className="nt-pdp__sharebtn" href={sh.href} target="_blank" rel="noopener noreferrer" aria-label={sh.name}>
                {sh.icon}
              </a>
            ))}
          </div>

          {/* Trust row */}
          <ul className="nt-pdp__trust">
            <li>
              <TruckIcon />
              <span className="nt-pdp__trust-main">{t("Fast shipping", "شحن سريع")}</span>
              <span className="nt-pdp__trust-sub">{t("All Egypt", "كل مصر")}</span>
            </li>
            <li>
              <ReturnIcon />
              <span className="nt-pdp__trust-main">{t("Returns", "إرجاع")}</span>
              <span className="nt-pdp__trust-sub">{t("14 days", "خلال ١٤ يوم")}</span>
            </li>
            <li>
              <LockIcon />
              <span className="nt-pdp__trust-main">{t("Authentic", "أصلي")}</span>
              <span className="nt-pdp__trust-sub">{t("100% guaranteed", "مضمون 100%")}</span>
            </li>
          </ul>

          {/* Frequently bought — embedded in the buy column */}
          {s.show_fbt !== false ? (
            <div className="nt-pdp__fbt">
              <FrequentlyBought
                id="fbt-pdp"
                settings={{ enabled: true, embedded: true, max_items: 3, title: t("Frequently bought together", "يُشترى عادةً معاً") }}
              />
            </div>
          ) : null}
        </div>
      </div>

      {s.show_related !== false && related.items.length > 0 ? (
        <div style={{ marginTop: "4rem" }}>
          <h2 className="nt-heading" style={{ marginBottom: "1.5rem" }}>
            {t("You may also like", "منتجات مشابهة")}
          </h2>
          <div className="nt-grid" style={{ ["--cols" as string]: 4 }}>
            {related.items.map((p) => (
              <ProductCard key={p.id} product={p} />
            ))}
          </div>
        </div>
      ) : null}

      {/* Size guide modal */}
      {sizeOpen && chartHasContent && chart ? (
        <div className="nt-modal" role="dialog" aria-modal="true" aria-label={t("Size guide", "دليل المقاسات")}>
          <div className="nt-modal__overlay" onClick={() => setSizeOpen(false)} />
          <div className="nt-modal__panel">
            <div className="nt-modal__head">
              <h2 className="nt-modal__title">
                {t("Size guide", "دليل المقاسات")}{chart.unit ? ` (${chart.unit})` : ""}
              </h2>
              <button className="nt-drawer__close" type="button" onClick={() => setSizeOpen(false)} aria-label={t("Close", "إغلاق")}>
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
                  <path d="M18 6 6 18M6 6l12 12" />
                </svg>
              </button>
            </div>
            <div className="nt-modal__body">
              {chart.image_url ? <img className="nt-sizeguide__img" src={chart.image_url} alt="" /> : null}
              <table className="nt-sizetable">
                <thead>
                  <tr>
                    <th>{t("Size", "المقاس")}</th>
                    {chart.column_headers.map((h, i) => (
                      <th key={i}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {chart.rows.map((row, ri) => (
                    <tr key={ri}>
                      <th scope="row">{row.size}</th>
                      {chart.column_headers.map((_, ci) => (
                        <td key={ci}>{row.values[ci] ?? "—"}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
              {chart.notes ? (
                <p className="nt-muted" style={{ fontSize: "0.8125rem", marginTop: "1rem" }}>{chart.notes}</p>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}

/* ── Icons ── */
const SizeIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M3 7h18v10H3z" /><path d="M7 7v3M11 7v5M15 7v3M19 7v5" /></svg>
);
const TruckIcon = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M14 18V6H2v12h2" /><path d="M14 9h4l4 4v5h-3" /><circle cx="7" cy="18" r="2" /><circle cx="17" cy="18" r="2" /></svg>
);
const ReturnIcon = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M3 7v6h6" /><path d="M3.5 13a9 9 0 1 0 2.3-9.3L3 7" /></svg>
);
const LockIcon = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" /><path d="m9 12 2 2 4-4" /></svg>
);
const IconLink = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" /><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" /></svg>
);
const IconCheck = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M20 6 9 17l-5-5" /></svg>
);
const IconTelegram = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden><path d="M21.94 4.3 18.6 20.04c-.25 1.11-.91 1.39-1.85.86l-5.1-3.76-2.46 2.37c-.27.27-.5.5-1.03.5l.37-5.2L18.99 6.1c.41-.37-.09-.57-.64-.2L6.04 13.8l-5.03-1.57c-1.09-.34-1.11-1.09.23-1.61l19.65-7.57c.91-.34 1.7.2 1.41 1.45z" /></svg>
);
const IconX = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden><path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z" /></svg>
);
const IconFacebook = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden><path d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z" /></svg>
);
const IconWhatsApp = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden><path d="M.057 24l1.687-6.163a11.867 11.867 0 0 1-1.587-5.946C.16 5.335 5.495 0 12.05 0a11.817 11.817 0 0 1 8.413 3.488 11.824 11.824 0 0 1 3.48 8.414c-.003 6.557-5.338 11.892-11.893 11.892a11.9 11.9 0 0 1-5.688-1.448L.057 24zm6.597-3.807c1.676.995 3.276 1.591 5.392 1.592 5.448 0 9.886-4.434 9.889-9.885.002-5.462-4.415-9.89-9.881-9.892-5.452 0-9.887 4.434-9.889 9.884a9.86 9.86 0 0 0 1.51 5.26l-.999 3.648 3.978-1.04zm11.387-5.464c-.074-.124-.272-.198-.57-.347-.297-.149-1.758-.868-2.031-.967-.272-.099-.47-.149-.669.149-.198.297-.768.967-.941 1.165-.173.198-.347.223-.644.074-.297-.149-1.255-.462-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.297-.347.446-.521.151-.172.2-.296.3-.495.099-.198.05-.372-.025-.521-.075-.148-.669-1.611-.916-2.206-.242-.579-.487-.501-.669-.51l-.57-.01c-.198 0-.52.074-.792.372s-1.04 1.017-1.04 2.479 1.065 2.876 1.213 3.074c.149.198 2.095 3.2 5.076 4.487.71.306 1.263.489 1.694.626.712.226 1.36.194 1.872.118.571-.085 1.758-.719 2.006-1.413.248-.695.248-1.29.173-1.414z" /></svg>
);
