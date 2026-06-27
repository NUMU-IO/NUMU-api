/**
 * Local dev harness for `numu-theme dev` (plain Vite + index.html).
 *
 * The marketplace bundle's real entry is `main.tsx` (mount/createApp). Here we
 * call that same `mount(el, ctx)` with a mock Arabic store, catalog and cart so
 * the full provider tree (cart, shop, page, localization, direction) is live —
 * every section renders exactly as it will inside the storefront, including the
 * live "Add to cart" → drawer flow. This file is NOT part of the built theme.js.
 *
 * `themeSettings` is sent with EMPTY templates/section_groups on purpose so the
 * bundle exercises its own theme.json presets (the marketplace-preview path).
 */
import { mount } from "./main";
import type { Product, Store, Cart, Collection } from "@numueg/theme-sdk";

const img = (seed: string) => ({
  id: seed,
  url: `https://picsum.photos/seed/${seed}/1000/1000`,
  alt: seed,
  position: 0,
});

const products: Product[] = [
  {
    id: "p1",
    name: "تيشيرت قطن ثقيل",
    slug: "heavy-tee",
    description: "تيشيرت قطن 280 جرام، قصة واسعة ولمسة ناعمة.",
    price: 450,
    compare_at_price: 600,
    currency: "EGP",
    images: [img("tee"), img("tee2")],
    category: "ملابس",
    tags: ["جديد"],
    options: [{ name: "المقاس", position: 0, values: ["S", "M", "L", "XL"] }],
    variants: [
      { id: "p1-s", position: 0, option_values: { المقاس: "S" }, price: 450, inventory_quantity: 5, is_in_stock: true },
      { id: "p1-m", position: 1, option_values: { المقاس: "M" }, price: 450, inventory_quantity: 5, is_in_stock: true },
      { id: "p1-l", position: 2, option_values: { المقاس: "L" }, price: 450, inventory_quantity: 0, is_in_stock: false },
      { id: "p1-xl", position: 3, option_values: { المقاس: "XL" }, price: 450, inventory_quantity: 3, is_in_stock: true },
    ],
    in_stock: true,
    attributes: {
      size_chart: {
        mode: "custom",
        unit: "cm",
        column_headers: ["الصدر", "الطول", "الكتف"],
        rows: [
          { size: "S", values: ["96", "70", "42"] },
          { size: "M", values: ["102", "72", "44"] },
          { size: "L", values: ["108", "74", "46"] },
          { size: "XL", values: ["114", "76", "48"] },
        ],
        notes: "القياسات بالسنتيمتر، بهامش ±1 سم.",
      },
    },
  } as Product,
  {
    id: "p2",
    name: "حقيبة جلد طبيعي",
    slug: "leather-bag",
    description: "حقيبة جلد طبيعي بخامة فاخرة تدوم لسنوات.",
    price: 1250,
    currency: "EGP",
    images: [img("bag")],
    category: "إكسسوارات",
    options: [],
    variants: [
      { id: "p2-o", position: 0, option_values: {}, price: 1250, inventory_quantity: 8, is_in_stock: true },
    ],
    in_stock: true,
  },
  {
    id: "p3",
    name: "حذاء رياضي أبيض",
    slug: "white-sneakers",
    description: "حذاء رياضي كلاسيكي بتصميم نظيف يناسب كل الإطلالات.",
    price: 980,
    compare_at_price: 1200,
    currency: "EGP",
    images: [img("shoe")],
    category: "أحذية",
    tags: ["الأكثر مبيعاً"],
    options: [{ name: "المقاس", position: 0, values: ["40", "41", "42", "43"] }],
    variants: [
      { id: "p3-40", position: 0, option_values: { المقاس: "40" }, price: 980, inventory_quantity: 4, is_in_stock: true },
      { id: "p3-41", position: 1, option_values: { المقاس: "41" }, price: 980, inventory_quantity: 4, is_in_stock: true },
      { id: "p3-42", position: 2, option_values: { المقاس: "42" }, price: 980, inventory_quantity: 2, is_in_stock: true },
      { id: "p3-43", position: 3, option_values: { المقاس: "43" }, price: 980, inventory_quantity: 0, is_in_stock: false },
    ],
    in_stock: true,
  },
  {
    id: "p4",
    name: "نظارة شمسية",
    slug: "sunglasses",
    description: "نظارة شمسية بإطار معدني خفيف وعدسات مستقطبة.",
    price: 520,
    currency: "EGP",
    images: [img("glasses")],
    category: "إكسسوارات",
    options: [],
    variants: [
      { id: "p4-o", position: 0, option_values: {}, price: 520, inventory_quantity: 12, is_in_stock: true },
    ],
    in_stock: true,
  },
  {
    id: "p5",
    name: "ساعة يد كلاسيكية",
    slug: "classic-watch",
    description: "ساعة يد بتصميم أنيق وحزام جلد.",
    price: 1800,
    currency: "EGP",
    images: [img("watch")],
    category: "إكسسوارات",
    options: [],
    variants: [
      { id: "p5-o", position: 0, option_values: {}, price: 1800, inventory_quantity: 6, is_in_stock: true },
    ],
    in_stock: true,
  },
];

const collections: Collection[] = [
  { id: "c1", name: "ملابس", slug: "clothing", product_count: 1, image_url: img("clothing").url, products },
  { id: "c2", name: "إكسسوارات", slug: "accessories", product_count: 3, image_url: img("acc").url, products },
  { id: "c3", name: "أحذية", slug: "shoes", product_count: 1, image_url: img("shoesc").url, products },
  { id: "c4", name: "وصل حديثاً", slug: "new", product_count: 2, image_url: img("new").url, products },
  { id: "c5", name: "تخفيضات", slug: "sale", product_count: 2, image_url: img("sale").url, products },
];

const store: Store = {
  id: "dev-store",
  name: "إمباير",
  slug: "nt",
  domain: "localhost",
  currency: "EGP",
  default_language: "ar",
  use_nextjs_storefront: true,
};

const initialCart: Cart = {
  id: "dev-cart",
  items: [],
  subtotal: 0,
  total: 0,
  currency: "EGP",
};

const themeSettings = {
  schema_version: 3,
  theme_id: "__THEME_ID__",
  global_settings: {},
  templates: {},
  section_groups: {},
} as any;

if (typeof window !== "undefined") {
  window.requestAnimationFrame(() => {
    const path = window.location.pathname;
    const params = new URLSearchParams(window.location.search);

    let template = params.get("template") || "home";
    let slug: string | null = params.get("slug");
    if (path.startsWith("/products/")) {
      template = "product";
      slug = decodeURIComponent(
        path.slice("/products/".length).replace(/\/$/, ""),
      );
    } else if (path === "/products") {
      template = "products";
    } else if (path.startsWith("/collections/")) {
      template = "collection";
    } else if (path.startsWith("/cart")) {
      template = "cart";
    } else if (path.startsWith("/order")) {
      template = "order-confirmation";
    }

    const activeProduct = products.find((p) => p.slug === slug) ?? products[0];

    const pageByTemplate: Record<string, any> = {
      home: { type: "home", title: "الرئيسية", data: { products, collections } },
      product: {
        type: "product",
        title: activeProduct.name,
        handle: activeProduct.slug,
        data: { product: activeProduct, products },
      },
      products: {
        type: "products",
        title: "كل المنتجات",
        data: { products, collections },
      },
      collection: {
        type: "collection",
        title: "المجموعة",
        data: { collection: collections[0], products },
      },
      cart: { type: "cart", title: "السلة", data: {} },
      "order-confirmation": {
        type: "order-confirmation",
        title: "تأكيد الطلب",
        data: {},
      },
    };

    // Dev-only template switcher, mounted OUTSIDE the theme root.
    const bar = document.createElement("div");
    bar.style.cssText =
      "position:fixed;bottom:12px;left:50%;transform:translateX(-50%);z-index:9999;display:flex;gap:4px;padding:6px;background:#000;border-radius:999px;font:500 12px sans-serif;box-shadow:0 8px 24px rgba(0,0,0,.3)";
    const navItems: Array<[string, string, string]> = [
      ["الرئيسية", "/", "home"],
      ["منتج", `/products/${products[0].slug}`, "product"],
      ["المنتجات", "/products", "products"],
      ["مجموعة", "/collections/clothing", "collection"],
      ["السلة", "/cart", "cart"],
    ];
    for (const [label, href, tpl] of navItems) {
      const a = document.createElement("a");
      a.href = href;
      a.textContent = label;
      const active = tpl === template;
      a.style.cssText = `padding:5px 12px;border-radius:999px;text-decoration:none;color:${active ? "#000" : "#e7e2d8"};background:${active ? "#e7e2d8" : "transparent"}`;
      bar.appendChild(a);
    }
    document.body.appendChild(bar);

    const root = document.getElementById("root");
    if (root) {
      mount(root, {
        store,
        themeSettings,
        currentTemplate: template,
        initialCart,
        initialProducts: products,
        initialCollections: collections,
        locale: "ar",
        page: pageByTemplate[template] ?? pageByTemplate.home,
      } as any);
    }
  });
}
