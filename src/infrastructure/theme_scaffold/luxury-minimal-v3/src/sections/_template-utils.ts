/**
 * Template & section sanitisation helpers.
 *
 * These complement the storefront-level fix in
 * `numu-storefront/src/lib/resolve-theme.ts` — that one handles the
 * common case (settings saved with section types from a previously-
 * active theme). These helpers are a defense-in-depth: even if the
 * storefront's sanitisation is bypassed (e.g. mounting via a different
 * host, dev preview, customizer iframe with stale data), the bundle
 * still renders a sensible preview rather than a wall of "Unknown
 * section" placeholders.
 *
 * Themes migrating off `v2-bridge` should copy this file (and the
 * fallback usage pattern in main.tsx). Once the published SDK ships
 * its own `sanitizeTemplate`, this can be deleted.
 */

import type { SectionInstance } from "@numueg/theme-sdk";

export interface MaybeOrderedTemplate {
  name?: string;
  sections?: Record<string, SectionInstance> | SectionInstance[];
  order?: string[];
}

export interface ResolvedSection {
  id: string;
  instance: SectionInstance;
}

/**
 * Normalise a template/group into an ordered list of section instances.
 * Accepts both the V3 `{sections: Record, order: string[]}` shape AND
 * the array-of-instances shape that some theme.json presets use.
 */
export function resolveSections(
  group: MaybeOrderedTemplate | undefined,
): ResolvedSection[] {
  if (!group) return [];
  if (Array.isArray(group.sections)) {
    return group.sections.map((instance, idx) => ({
      id: `${instance.type}-${idx}`,
      instance,
    }));
  }
  const map = (group.sections ?? {}) as Record<string, SectionInstance>;
  const order = group.order ?? Object.keys(map);
  const out: ResolvedSection[] = [];
  for (const id of order) {
    const instance = map[id];
    if (instance) out.push({ id, instance });
  }
  return out;
}

/**
 * Pick which template to render. Preference order:
 *   1. host-provided customisation (themeSettings.templates[name])
 *      — but only if at least one section type is in our registry
 *   2. theme's own built-in preset (from theme.json)
 *
 * The first guard catches the "merchant switched themes, stale
 * customisation has 0 sections we recognise" case. Without it, the
 * bundle renders a stack of UnknownSection placeholders.
 */
export function selectTemplateSections(
  hostTemplate: MaybeOrderedTemplate | undefined,
  builtinTemplate: MaybeOrderedTemplate | undefined,
  isKnown: (sectionType: string) => boolean,
): ResolvedSection[] {
  const hostSections = resolveSections(hostTemplate);
  if (hostSections.length === 0) {
    return resolveSections(builtinTemplate);
  }
  const anyKnown = hostSections.some(({ instance }) => isKnown(instance.type));
  if (!anyKnown) {
    return resolveSections(builtinTemplate);
  }
  // Filter unknowns out section-by-section so partial customisations
  // still render the bits that map cleanly.
  return hostSections.filter(({ instance }) => isKnown(instance.type));
}
