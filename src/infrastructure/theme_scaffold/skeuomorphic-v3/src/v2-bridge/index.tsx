/**
 * v2-bridge — per-theme compat shim for V2 sections ported via
 * `numu-theme migrate`. DELETE this file once each section has been
 * rewritten to use idiomatic V3 SDK hooks + components.
 *
 * SDK packaging note: we intentionally do NOT import from
 * `@numueg/theme-sdk/v2-compat`. That subpath is a separate tsup entry
 * with its own bundled copy of ShopContext/NuMuProvider — using it
 * yields two SDK module instances at runtime ("useShop must be used
 * within NuMuProvider" even though the section is inside one). Inline
 * V2-shaped adapters here so we only touch the main SDK entry.
 */

import type {
  ComponentPropsWithoutRef,
  ComponentType,
  ReactNode,
} from "react";
import { useMemo, useState } from "react";
import {
  ProductCard,
  useThemeSettings,
  useProducts as useSdkProducts,
  useCollections,
  useCustomer,
  useLocalization,
} from "@numueg/theme-sdk";
import type {
  SectionInstance,
  Product,
  Customer,
} from "@numueg/theme-sdk";

// ── V2-shaped hooks ───────────────────────────────────────────────────────
//
// V2 sections destructure `{products, categories, newArrivals, bestSellers,
// isLoading}` off the bazaar's ProductsContext. The V3 SDK splits that
// across useProducts + useCollections + (no equivalent for newArrivals /
// bestSellers — fall back to product slices). Inlining keeps everything on
// the main SDK entry's single ShopContext.

interface V2Category {
  id: string;
  name: string;
  slug?: string;
  image_url?: string | null;
  description?: string | null;
}

interface V2ProductsState {
  products: Product[];
  categories: V2Category[];
  newArrivals: Product[];
  bestSellers: Product[];
  isLoading: boolean;
  loading: boolean;
}

export function useProducts(): V2ProductsState {
  const { products, loading: pLoading } = useSdkProducts();
  const { collections, loading: cLoading } = useCollections();
  return useMemo(() => {
    const categories: V2Category[] = collections.map((c) => ({
      id: c.id,
      name: c.name,
      slug: c.slug,
      image_url: c.image_url ?? null,
      description: c.description ?? null,
    }));
    return {
      products,
      categories,
      newArrivals: products.slice(0, 8),
      bestSellers: products.slice(0, 8),
      isLoading: pLoading || cLoading,
      loading: pLoading || cLoading,
    };
  }, [products, collections, pLoading, cLoading]);
}

export function useCategories(): { categories: V2Category[]; loading: boolean } {
  const { collections, loading } = useCollections();
  return useMemo(
    () => ({
      categories: collections.map((c) => ({
        id: c.id,
        name: c.name,
        slug: c.slug,
        image_url: c.image_url ?? null,
        description: c.description ?? null,
      })),
      loading,
    }),
    [collections, loading],
  );
}

export function useAuth(): { user: Customer | null; isAuthenticated: boolean } {
  const customer = useCustomer();
  return useMemo(
    () => ({ user: customer, isAuthenticated: customer !== null }),
    [customer],
  );
}

export function useLanguage(): {
  language: "ar" | "en";
  direction: "rtl" | "ltr";
  setLanguage: (next: "ar" | "en") => void;
  t: (key: string, fallback?: string) => string;
} {
  const { locale, direction, translations, setLocale } = useLocalization();
  const norm: "ar" | "en" = locale === "ar" ? "ar" : "en";
  return useMemo(
    () => ({
      language: norm,
      direction,
      setLanguage: (next: "ar" | "en") => setLocale(next),
      t: (key: string, fallback?: string) =>
        translations[key] ?? fallback ?? key,
    }),
    [norm, direction, translations, setLocale],
  );
}

/**
 * V2 sections that called `useStore()` mostly read `.themeSettings.theme.*`
 * (colour/font tokens) or `.themeSettings.identity.*` (logo/name). Map flat
 * V3 global_settings keys back into that nested shape.
 */
export function useStore(): {
  themeSettings: {
    theme: Record<string, string | undefined>;
    identity: Record<string, string | undefined>;
  };
} {
  const settings = useThemeSettings();
  return useMemo(() => {
    const g = (settings.global_settings ?? {}) as Record<string, unknown>;
    const get = (k: string): string | undefined =>
      typeof g[k] === "string" ? (g[k] as string) : undefined;
    return {
      themeSettings: {
        theme: {
          primary_color: get("primary_color"),
          accent_color: get("accent_color"),
          background_color: get("background_color"),
          text_color: get("text_color"),
          heading_font: get("heading_font"),
        },
        identity: {
          logo_url: get("logo_url"),
          store_name: get("store_name"),
        },
      },
    };
  }, [settings]);
}

/**
 * V2's `useTheme` returned `{ resolveComponent }`. V3 themes are isolated
 * bundles with no inter-theme overrides; map every ProductCard lookup to
 * the SDK's canonical ProductCard.
 */
export function useTheme() {
  return {
    resolveComponent: (name: string): ComponentType<unknown> | null => {
      if (name === "ProductCard")
        return ProductCard as unknown as ComponentType<unknown>;
      return null;
    },
  };
}

// ── V2 type re-shaping ────────────────────────────────────────────────────

export interface SectionComponentProps {
  section: SectionInstance & { id?: string };
}

export function v2Section<P extends SectionComponentProps>(
  Component: (props: P) => ReactNode,
): (props: { instance: SectionInstance }) => ReactNode {
  return function V2SectionAdapter({ instance }) {
    const props = { section: instance } as unknown as P;
    return Component(props);
  };
}

// ── editable.section() compat ────────────────────────────────────────────

export const editable = {
  section: (_sectionId: string, _key: string) => ({}),
  block: (_blockId: string, _key: string) => ({}),
};

// ── Misc re-exports the V2 sections often imported ────────────────────────

export { ProductCard, useThemeSettings };

export function Link({
  to,
  children,
  ...rest
}: { to: string; children: ReactNode } & Omit<
  ComponentPropsWithoutRef<"a">,
  "href"
>) {
  return (
    <a href={to} {...rest}>
      {children}
    </a>
  );
}

export const PLACEHOLDER_HERO =
  "https://images.unsplash.com/photo-1483985988355-763728e1935b?auto=format&fit=crop&w=1200&q=60";
export const PLACEHOLDER_SQUARE =
  "https://images.unsplash.com/photo-1483985988355-763728e1935b?auto=format&fit=crop&w=600&h=600&q=60";

// ── useNewsletterSubmit shim ──────────────────────────────────────────────

const _EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export interface UseNewsletterSubmitOptions {
  successMessage?: string;
  errorEmptyMessage?: string;
  errorInvalidMessage?: string;
  showToast?: boolean;
  contentCategory?: string;
  eventType?: "lead" | "subscribe";
}

export interface UseNewsletterSubmitResult {
  email: string;
  setEmail: (value: string) => void;
  submitted: boolean;
  handleSubmit: (event?: { preventDefault?: () => void }) => void;
}

export function useNewsletterSubmit(
  _options: UseNewsletterSubmitOptions = {},
): UseNewsletterSubmitResult {
  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);

  const handleSubmit = (event?: { preventDefault?: () => void }) => {
    event?.preventDefault?.();
    const trimmed = email.trim();
    if (!trimmed) return;
    if (!_EMAIL_REGEX.test(trimmed)) return;
    setSubmitted(true);
  };

  return { email, setEmail, submitted, handleSubmit };
}

// ── Testimonials helper shim ──────────────────────────────────────────────

export interface TestimonialReview {
  name: string;
  city: string;
  text: string;
  rating: number;
}

export function getReviewsFromSettings(
  s: Record<string, unknown>,
): TestimonialReview[] {
  const reviews: TestimonialReview[] = [];
  for (let i = 1; i <= 3; i++) {
    const name = s[`review_${i}_name`] as string | undefined;
    const text = s[`review_${i}_text`] as string | undefined;
    if (name && text) {
      reviews.push({
        name,
        city: (s[`review_${i}_city`] as string) ?? "",
        text,
        rating: (s[`review_${i}_rating`] as number) ?? 5,
      });
    }
  }
  return reviews;
}
