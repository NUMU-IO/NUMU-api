import type { ComponentType } from "react";
import {
  defineThemeEntry,
  Section,
  useDirection,
  type ThemeSettingsV3,
} from "@numueg/theme-sdk";
import manifest from "../theme.json";

import Header from "./sections/Header";
import Footer from "./sections/Footer";
import Hero from "./sections/hero";
import Marquee from "./sections/marquee";
import FeaturedCollection from "./sections/featured_collection";
import Categories from "./sections/categories";
import PromoBanner from "./sections/promo_banner";
import Testimonials from "./sections/testimonials";
import Newsletter from "./sections/newsletter";
import ProductDetails from "./sections/product_details";
import SizeChart from "./sections/size_chart";
import FrequentlyBought from "./sections/frequently_bought";
import ProductGrid from "./sections/product_grid";
import CartSummary from "./sections/cart_summary";
import OrderConfirmation from "./sections/order_confirmation";
import ImageWithText from "./sections/image_with_text";
import RichTextSection from "./sections/rich_text";
import NotFound from "./sections/not_found";
import SearchResults from "./sections/search_results";
import Account from "./sections/account";
import AboutSection from "./sections/about_section";

const SECTION_REGISTRY: Record<string, ComponentType<any>> = {
  header: Header,
  footer: Footer,
  hero: Hero,
  marquee: Marquee,
  featured_collection: FeaturedCollection,
  categories: Categories,
  promo_banner: PromoBanner,
  testimonials: Testimonials,
  newsletter: Newsletter,
  product_details: ProductDetails,
  size_chart: SizeChart,
  frequently_bought: FrequentlyBought,
  product_grid: ProductGrid,
  cart_summary: CartSummary,
  order_confirmation: OrderConfirmation,
  image_with_text: ImageWithText,
  rich_text: RichTextSection,
  not_found: NotFound,
  search_results: SearchResults,
  account: Account,
  about_section: AboutSection,
};

const isKnown = (type: string) => Boolean(SECTION_REGISTRY[type]);

interface SectionLike {
  type: string;
  settings?: Record<string, any>;
  blocks?: Record<string, any> | any[];
  block_order?: string[];
  disabled?: boolean;
}
interface GroupLike {
  sections?: Record<string, SectionLike> | SectionLike[];
  order?: string[];
}

// The theme's own default templates/groups, baked into the bundle. The host
// passes EMPTY templates for a marketplace PREVIEW (before install / before the
// merchant customizes), expecting the bundle to fall back to these.
const PRESETS = (manifest as any).presets ?? {};
const BUILTIN_TEMPLATES: Record<string, GroupLike> = PRESETS.templates ?? {};
const BUILTIN_GROUPS: Record<string, GroupLike> = PRESETS.section_groups ?? {};

/** Normalise a section instance so blocks are always `{map}` + `order[]`
 *  (presets store blocks as an array; resolved host data stores a map). */
function normaliseInstance(instance: SectionLike): SectionLike {
  if (Array.isArray(instance.blocks)) {
    const map: Record<string, any> = {};
    const order: string[] = [];
    instance.blocks.forEach((b: any, i: number) => {
      const id = `${b?.type ?? "block"}-${i}`;
      map[id] = b;
      order.push(id);
    });
    return { ...instance, blocks: map, block_order: order };
  }
  return instance;
}

/** Normalise a template/group (array OR map+order) → ordered instance list. */
function resolveSections(
  group: GroupLike | undefined,
): Array<{ id: string; instance: SectionLike }> {
  if (!group || !group.sections) return [];
  if (Array.isArray(group.sections)) {
    return group.sections.map((instance, idx) => ({
      id: `${instance.type}-${idx}`,
      instance: normaliseInstance(instance),
    }));
  }
  const map = group.sections as Record<string, SectionLike>;
  const order = group.order ?? Object.keys(map);
  const out: Array<{ id: string; instance: SectionLike }> = [];
  for (const id of order) {
    const instance = map[id];
    if (instance) out.push({ id, instance: normaliseInstance(instance) });
  }
  return out;
}

/** Prefer the host's customisation; fall back to bundled presets (preview). */
function selectSections(
  host: GroupLike | undefined,
  builtin: GroupLike | undefined,
): Array<{ id: string; instance: SectionLike }> {
  const hostList = resolveSections(host).filter((s) => isKnown(s.instance.type));
  if (hostList.length > 0) return hostList;
  return resolveSections(builtin).filter((s) => isKnown(s.instance.type));
}

function styleVars(global: Record<string, any>): React.CSSProperties {
  const vars: Record<string, string> = {};
  if (global.accent_color) vars["--nt-accent"] = global.accent_color;
  if (global.foreground_color) vars["--nt-fg"] = global.foreground_color;
  if (global.background_color) vars["--nt-bg"] = global.background_color;
  if (global.font_family) {
    const stack = `"${global.font_family}", "Inter", system-ui, sans-serif`;
    vars["--nt-font-body"] = stack;
    vars["--nt-font-display"] = stack;
  }
  return vars as React.CSSProperties;
}

function renderList(
  list: Array<{ id: string; instance: SectionLike }>,
  keyPrefix: string,
  groupId?: string,
  extra?: Record<string, unknown>,
) {
  return list.map(({ id, instance }) => {
    if (instance.disabled) return null;
    const Component = SECTION_REGISTRY[instance.type];
    if (!Component) return null;
    // <Section> emits the data-section-id the customizer's PreviewBridge reads
    // for click-to-select; passing the id down lets each component wire
    // <EditableText>/<EditableImage> for inline field editing.
    return (
      <Section
        key={`${keyPrefix}-${id}`}
        id={id}
        type={instance.type}
        groupId={groupId}
      >
        <Component
          id={id}
          type={instance.type}
          settings={instance.settings}
          blocks={instance.blocks}
          blockOrder={instance.block_order}
          {...extra}
        />
      </Section>
    );
  });
}

interface ThemeProps {
  themeSettings: ThemeSettingsV3;
  currentTemplate: string;
}

export default function Theme({ themeSettings, currentTemplate }: ThemeProps) {
  const pageType = currentTemplate || "home";
  const global = themeSettings.global_settings || {};
  const dir = useDirection();

  const hostTemplates = (themeSettings.templates ?? {}) as Record<
    string,
    GroupLike
  >;
  const hostTemplate =
    hostTemplates[pageType] ?? hostTemplates.page ?? hostTemplates.home;
  const builtinTemplate =
    BUILTIN_TEMPLATES[pageType] ??
    BUILTIN_TEMPLATES.page ??
    BUILTIN_TEMPLATES.home;

  const hostGroups = (themeSettings.section_groups ?? {}) as Record<
    string,
    GroupLike
  >;
  const headerSections = selectSections(hostGroups.header, BUILTIN_GROUPS.header);
  const footerSections = selectSections(hostGroups.footer, BUILTIN_GROUPS.footer);
  const bodySections = selectSections(hostTemplate, builtinTemplate);

  // The header is fixed/overlay; pages that don't open with a full-bleed hero
  // need top padding so their first section clears it.
  const firstType = bodySections[0]?.instance.type;
  const bleedTop = firstType === "hero";

  return (
    <div className="nt" dir={dir} style={styleVars(global)}>
      {renderList(headerSections, "hg", "header", { solidHeader: !bleedTop })}
      {!bleedTop && headerSections.length > 0 ? (
        <div className="nt-spacer-top" aria-hidden="true" />
      ) : null}
      {bodySections.length > 0 ? (
        renderList(bodySections, pageType)
      ) : (
        <section className="nt-page nt-container">
          <p className="nt-placeholder">
            No template configured for "{pageType}".
          </p>
        </section>
      )}
      {renderList(footerSections, "fg", "footer")}
    </div>
  );
}

// ── Entry: ONE definition → mount (client/hydrate) + createApp (SSR) ────────
const entry = defineThemeEntry(({ themeSettings, currentTemplate }) => (
  <Theme themeSettings={themeSettings} currentTemplate={currentTemplate} />
));

export const mount = entry.mount;
export const createApp = entry.createApp;
