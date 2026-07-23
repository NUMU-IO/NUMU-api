// Shared guards from @numueg/theme-kit (import+re-export: local binding + public export).
import { localized, asString, asNumber, asArray } from "@numueg/theme-kit";
export { localized, asString, asNumber, asArray };

import type { SectionInstance } from "@numueg/theme-sdk";

export interface SectionRenderProps {
  instance: SectionInstance;
  sectionId: string;
}

// ── Non-destructive image transform (focal / zoom / rotation) ────────────────
// Now provided by the SDK (@numueg/theme-sdk >= 0.11.0) instead of a local
// copy that had to be hand-synced with the merchant-hub editor and 13 other
// themes. Re-exported from here so every section keeps importing it from
// "./_shared" unchanged. The SDK build is pinned against the previous local
// implementation by a parity suite, so this swap is render-identical.
export {
  applyImageTransform,
  asImageTransform,
  type ImageTransform,
} from "@numueg/theme-sdk";
