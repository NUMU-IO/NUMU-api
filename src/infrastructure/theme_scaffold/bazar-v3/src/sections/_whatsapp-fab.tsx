"use client";

import { useLocale, useThemeSettings } from "@numueg/theme-sdk";
import { asString } from "./_shared";

/**
 * BzWhatsAppFab — floating WhatsApp button. Ports V2's WhatsAppButton, with
 * two deliberate fixes the flag called out:
 *
 *  1. The REAL WhatsApp glyph (V2 used lucide's generic `MessageCircle`
 *     chat-bubble). The path below is the official WhatsApp mark.
 *  2. It is RAISED clear of the mobile bottom tab bar. V2 pinned it at
 *     `bottom-5 left-5`, which sat on top of the tab bar's first item and
 *     covered "حسابي" / Account (the screenshot bug). Here it floats above
 *     the bar (tab-bar height + safe-area) on mobile and drops to the normal
 *     corner on md+ where there is no tab bar.
 *
 * Only renders when the merchant has set a WhatsApp number/link in the
 * "Social links" global-settings group (`social_whatsapp`).
 */
export default function BzWhatsAppFab() {
  const settings = useThemeSettings();
  const locale = useLocale();
  const raw = asString(
    (settings.global_settings as Record<string, unknown> | undefined)
      ?.social_whatsapp,
  ).trim();
  if (!raw) return null;

  // Accept either a full link (https://wa.me/… or https://api.whatsapp.com/…)
  // or a bare number — normalise a bare number to a wa.me deep link.
  const href = /^https?:\/\//i.test(raw)
    ? raw
    : `https://wa.me/${raw.replace(/\D/g, "")}`;

  const isAr = (locale || "").toLowerCase().startsWith("ar");
  const label = isAr ? "تواصل عبر واتساب" : "Chat on WhatsApp";

  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      aria-label={label}
      title={label}
      data-bz-whatsapp
      // z-30 keeps it under the mobile menu overlay (z-60) and the tab bar
      // (z-40) — and since it's raised above the bar, it never overlaps a tab.
      className="fixed z-30 end-4 md:end-6 flex h-14 w-14 items-center justify-center rounded-full bg-[#25D366] text-white shadow-[0_6px_20px_rgba(37,211,102,0.45)] transition-transform hover:scale-105 active:scale-95"
      style={{ bottom: "calc(env(safe-area-inset-bottom, 0px) + 5rem)" }}
    >
      <svg
        viewBox="0 0 24 24"
        fill="currentColor"
        className="h-7 w-7"
        aria-hidden="true"
      >
        <path d="M.057 24l1.687-6.163c-1.041-1.804-1.588-3.849-1.587-5.946.003-6.556 5.338-11.891 11.893-11.891 3.181.001 6.167 1.24 8.413 3.488 2.245 2.248 3.481 5.236 3.48 8.414-.003 6.557-5.338 11.892-11.893 11.892-1.99-.001-3.951-.5-5.688-1.448l-6.305 1.654zm6.597-3.807c1.676.995 3.276 1.591 5.392 1.592 5.448 0 9.886-4.434 9.889-9.885.002-5.462-4.415-9.89-9.881-9.892-5.452 0-9.887 4.434-9.889 9.884-.001 2.225.651 3.891 1.746 5.634l-.999 3.648 3.742-.981zm11.387-5.464c-.074-.124-.272-.198-.57-.347-.297-.149-1.758-.868-2.031-.967-.272-.099-.47-.149-.669.149-.198.297-.768.967-.941 1.165-.173.198-.347.223-.644.074-.297-.149-1.255-.462-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.297-.347.446-.521.151-.172.2-.296.3-.495.099-.198.05-.372-.025-.521-.075-.148-.669-1.611-.916-2.206-.242-.579-.487-.501-.669-.51l-.57-.01c-.198 0-.52.074-.792.372s-1.04 1.016-1.04 2.479 1.065 2.876 1.213 3.074c.149.198 2.096 3.2 5.077 4.487.709.306 1.263.489 1.694.626.712.226 1.36.194 1.872.118.571-.085 1.758-.719 2.006-1.413.248-.695.248-1.29.173-1.414z" />
      </svg>
    </a>
  );
}
