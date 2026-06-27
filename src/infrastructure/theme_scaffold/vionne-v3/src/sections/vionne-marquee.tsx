"use client";
import { Link, useLocale, useResolvedSettings } from "@numueg/theme-sdk";
import { localized, type SectionRenderProps } from "./_shared";

/**
 * Vionne Scrolling Banner section.
 *
 * Seamless infinite scroll: we render the same group of items TWICE
 * (Group A + Group A), so animating the track from `translateX(0)` to
 * `translateX(-50%)` puts the duplicate exactly where the original was at
 * the end of each cycle. The browser loops back to 0 invisibly — no
 * jump, no gap, regardless of how many items the merchant configured.
 *
 * This avoids the classic "marquee bug" where the merchant's repeat-count
 * controls the loop, and odd values cause a visible snap.
 */

const SEPARATORS: Record<string, string> = {
  dot: "•",
  slash: "/",
  pipe: "|",
  star: "★",
  diamond: "◆",
  arrow: "→",
  dash: "—",
  none: "",
};

const COLOR_PRESETS: Record<
  string,
  { bg: string; fg: string; border: string }
> = {
  ink: {
    // Dark ink on the light page band — high contrast, classic.
    bg: "var(--vn-ink)",
    fg: "var(--vn-white)",
    border: "transparent",
  },
  light: {
    bg: "var(--vn-white)",
    fg: "var(--vn-ink)",
    border: "var(--vn-border)",
  },
  band: {
    bg: "var(--vn-band)",
    fg: "var(--vn-ink)",
    border: "transparent",
  },
  accent: {
    bg: "var(--vn-sale, #b8001f)",
    fg: "var(--vn-white)",
    border: "transparent",
  },
};

const VionneMarquee = ({ instance }: SectionRenderProps) => {
  const locale = useLocale();
  // useResolvedSettings (not raw instance.settings) so editor edits + dynamic
  // sources reflect live. Marquee items scroll in a duplicated track, so they
  // stay panel-edited (item_1..6) rather than canvas inline-edit.
  const s = useResolvedSettings(instance);

  // Collect non-empty items 1..6 in their declared order. Empty slots are
  // skipped — the merchant can leave them blank without breaking the loop.
  const rawItems = [1, 2, 3, 4, 5, 6]
    .map((i) => (s[`item_${i}`] as string | undefined) ?? "")
    .map((v) => v.trim())
    .filter(Boolean);

  // If the merchant cleared every slot, fall back to a tasteful default so
  // the section never renders as an empty bar (which would silently push
  // the rest of the page up and look broken in the editor preview).
  const items =
    rawItems.length > 0
      ? rawItems
      : [
          localized(locale, "NEW SEASON", "تشكيلة الموسم الجديد"),
          localized(locale, "FREE SHIPPING", "شحن مجاني"),
          localized(locale, "WORLDWIDE DELIVERY", "توصيل لكل العالم"),
        ];

  const separatorKey = (s.separator as string) ?? "dot";
  const separator = SEPARATORS[separatorKey] ?? "•";

  const size = ((s.size as string) ?? "sm") as "sm" | "md" | "lg" | "xl";
  const colorScheme = ((s.color_scheme as string) ?? "ink") as keyof typeof COLOR_PRESETS | "custom";
  const colors =
    colorScheme === "custom"
      ? {
          bg: (s.custom_bg as string) || "#111111",
          fg: (s.custom_fg as string) || "#ffffff",
          border: "transparent",
        }
      : COLOR_PRESETS[colorScheme] ?? COLOR_PRESETS.ink;

  const direction = ((s.direction as string) ?? "left") as "left" | "right";
  const speed = Math.max(5, Math.min(180, Number(s.speed_seconds ?? 30)));
  const pauseOnHover = s.pause_on_hover !== false;
  const showBorders = s.show_borders === true;
  const paddingY = Math.max(2, Math.min(64, Number(s.padding_y ?? 14)));
  const linkUrl = (s.link_url as string) || "";

  // Build one rendering "group" — the merchant's items interleaved with the
  // separator. We then render this group twice in the track for seamless
  // looping. Joining into a single string per group keeps the visual width
  // identical between the two copies (no sub-pixel layout drift).
  const groupText = items.join(separator ? `   ${separator}   ` : "   ");

  const sectionStyle = {
    paddingBlock: `${paddingY}px`,
    "--vn-marquee-bg": colors.bg,
    "--vn-marquee-fg": colors.fg,
    "--vn-marquee-border": showBorders ? colors.border || "currentColor" : "transparent",
    "--vn-marquee-section-duration": `${speed}s`,
  } as React.CSSProperties;

  const inner = (
    <div className="vn-marquee-track" aria-hidden="true">
      {/* Group A — the merchant-visible content. */}
      <span
        className="vn-label whitespace-nowrap px-8"
      >
        {groupText}
      </span>
      {/* Group B — exact duplicate. Animation translates the track by
          -50% so this copy snaps into Group A's slot every loop. */}
      <span aria-hidden="true" className="vn-label whitespace-nowrap px-8">
        {groupText}
      </span>
    </div>
  );

  return (
    <section
      role="marquee"
      aria-label={items.join(", ")}
      data-size={size}
      data-direction={direction}
      data-pause-on-hover={pauseOnHover ? "true" : "false"}
      className="vn-marquee-section"
      style={sectionStyle}
    >
      {linkUrl ? (
        <Link
          to={linkUrl.startsWith("http") ? "#" : linkUrl}
          {...(linkUrl.startsWith("http") ? { onClick: () => window.open(linkUrl, "_blank") } : {})}
          className="block w-full"
        >
          {inner}
        </Link>
      ) : (
        inner
      )}
    </section>
  );
};

export default VionneMarquee;
