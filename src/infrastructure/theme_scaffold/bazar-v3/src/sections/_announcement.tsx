"use client";

import { useEffect, useState } from "react";
import { Link } from "@numueg/theme-sdk";

export interface BzAnnouncementMessage {
  /** Locale-resolved message text (the header resolves EN/AR before passing). */
  text: string;
  /** Optional link the whole message points to. */
  link?: string;
}

/**
 * BzAnnouncementBar — the amber promo strip above the header. It is its OWN bar
 * (decoupled from the nav) and is driven by the merchant's announcement message
 * BLOCKS (added in the header section's block list — "merchant can add it").
 *
 * Two modes:
 *   • slide  → auto-rotates through the messages, one at a time, with a gentle
 *              slide/fade, every `speed` seconds (a customizable promo carousel).
 *   • static → shows the first message only, no animation.
 *
 * Renders nothing when there are no non-empty messages, so clearing the blocks
 * (or disabling it) removes the bar entirely. Not sticky — it scrolls away above
 * the sticky header, exactly like V2.
 */
export default function BzAnnouncementBar({
  messages,
  mode = "slide",
  speed = 4,
}: {
  messages: BzAnnouncementMessage[];
  mode?: string;
  speed?: number;
}) {
  const items = messages.filter((m) => m.text && m.text.trim());
  const [idx, setIdx] = useState(0);

  const sliding = mode !== "static" && items.length > 1;
  // Clamp to the schema's 2–10s range; guard against a 0 that would spin.
  const intervalMs = Math.max(2, Math.min(10, speed || 4)) * 1000;

  useEffect(() => {
    if (!sliding) return;
    const id = setInterval(
      () => setIdx((i) => (i + 1) % items.length),
      intervalMs,
    );
    return () => clearInterval(id);
  }, [sliding, intervalMs, items.length]);

  // Keep the index valid if the merchant deletes blocks in the editor.
  useEffect(() => {
    if (idx >= items.length) setIdx(0);
  }, [items.length, idx]);

  if (items.length === 0) return null;
  const current = sliding ? items[idx] ?? items[0] : items[0];

  const label = (
    <span className="bz-label text-[var(--bz-dark)] whitespace-nowrap">
      {current.text}
    </span>
  );

  return (
    <div
      className="bg-[var(--bz-amber)] overflow-hidden"
      role="complementary"
      aria-label="Announcements"
    >
      <div
        className="container mx-auto px-4 flex items-center justify-center text-center"
        style={{ minHeight: "1.875rem" }}
      >
        {/* key remounts the node each slide so the CSS slide/fade replays */}
        <div
          key={sliding ? idx : "static"}
          className="bz-ann-slide py-1.5"
          aria-live="polite"
        >
          {current.link ? (
            <Link
              to={current.link}
              className="hover:opacity-80 transition-opacity"
            >
              {label}
            </Link>
          ) : (
            label
          )}
        </div>
      </div>
    </div>
  );
}
