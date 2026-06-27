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
  if (!anyKnown) return resolveSections(builtinTemplate);
  return hostSections.filter(({ instance }) => isKnown(instance.type));
}
