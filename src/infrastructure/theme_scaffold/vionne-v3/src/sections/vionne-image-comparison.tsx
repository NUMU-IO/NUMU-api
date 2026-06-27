"use client";
import { useEffect, useRef, useState } from "react";
import { useLocale, useResolvedSettings } from "@numueg/theme-sdk";
import { applyImageTransform, asImageTransform, asImageUrl, asString, localized, type SectionRenderProps } from "./_shared";
import { InlineEditable } from "./_inline-editable";

const ASPECT_CLASS: Record<string, string> = {
  "16-9": "aspect-[16/9]",
  "4-3": "aspect-[4/3]",
  "1-1": "aspect-square",
  "3-4": "aspect-[3/4]",
  "2-3": "aspect-[2/3]",
};

const VionneImageComparison = ({ instance, sectionId }: SectionRenderProps) => {
  const locale = useLocale();
  const s = useResolvedSettings(instance);
  const eyebrow = asString(s.eyebrow);
  const title = asString(s.title);
  const subtitle = asString(s.subtitle);
  // Merchant-uploaded images arrive as `{ url, alt }` objects; `asImageUrl`
  // pulls the URL so a just-uploaded Before/After picture actually shows
  // (raw `s.before_image` rendered `[object Object]` → looked like the
  // upload "didn't save").
  const beforeImage = asImageUrl(s.before_image);
  const beforeImageTransform = asImageTransform(s.before_image);
  const beforeLabel = asString(s.before_label);
  const afterImage = asImageUrl(s.after_image);
  const afterImageTransform = asImageTransform(s.after_image);
  const afterLabel = asString(s.after_label);
  const initialPos = Math.max(5, Math.min(95, Number(s.initial_position ?? 50)));
  const animateToCenter = s.animate_to_center !== false;
  const fullWidth = s.full_width !== false;
  const showLabels = s.show_labels === true;
  const aspectClass = ASPECT_CLASS[asString(s.aspect, "3-4")] ?? ASPECT_CLASS["3-4"];

  const containerRef = useRef<HTMLDivElement>(null);
  const draggingRef = useRef(false);
  const isRtlRef = useRef(false);
  // Set to true the first time the user interacts. Cancels the entrance
  // animation mid-flight so it doesn't fight the drag.
  const userInteractedRef = useRef(false);
  const [position, setPosition] = useState(initialPos);

  // Detect RTL on mount.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    isRtlRef.current = getComputedStyle(el).direction === "rtl";
  }, []);

  // Animate to center on first reveal. Runs once. Bails out if the user
  // started dragging — otherwise the RAF loop keeps calling setPosition
  // and overwrites whatever the user just dragged to.
  useEffect(() => {
    if (!animateToCenter) return;
    const el = containerRef.current;
    if (!el) return;
    let cancelled = false;
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (!e.isIntersecting) continue;
          io.disconnect();
          const start = performance.now();
          const from = initialPos;
          const to = 50;
          const duration = 1100;
          const tick = (now: number) => {
            if (cancelled || userInteractedRef.current) return;
            const t = Math.min(1, (now - start) / duration);
            const eased = 1 - Math.pow(1 - t, 3);
            setPosition(from + (to - from) * eased);
            if (t < 1) requestAnimationFrame(tick);
          };
          requestAnimationFrame(tick);
        }
      },
      { threshold: 0.35 }
    );
    io.observe(el);
    return () => {
      cancelled = true;
      io.disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [animateToCenter]);

  const setFromPointer = (clientX: number) => {
    const el = containerRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0) return;
    const fromStart = isRtlRef.current ? rect.right - clientX : clientX - rect.left;
    const pct = (fromStart / rect.width) * 100;
    setPosition(Math.max(0, Math.min(100, pct)));
  };

  // Use refs (not state) for drag tracking — re-renders during pointer-move
  // cause the listener handlers to thrash, which feels janky.
  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    draggingRef.current = true;
    userInteractedRef.current = true;
    try {
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    } catch {
      /* ignore */
    }
    setFromPointer(e.clientX);
  };
  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!draggingRef.current) return;
    setFromPointer(e.clientX);
  };
  const stopDrag = (e: React.PointerEvent<HTMLDivElement>) => {
    draggingRef.current = false;
    try {
      (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
    } catch {
      /* ignore */
    }
  };

  // Trackpad horizontal swipe (deltaX) → scrub the comparison left/right.
  // Bound through a non-passive native listener (React's synthetic onWheel
  // is passive in 17+, so e.preventDefault() inside it is a no-op).
  // Vertical wheel (deltaY) is intentionally NOT hijacked — letting plain
  // mouse-wheel scroll past the slider keeps the page scrollable; only
  // horizontal-axis input (trackpad two-finger swipe) maps to position.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      const dx = e.deltaX;
      if (Math.abs(dx) < 2) return;
      e.preventDefault();
      userInteractedRef.current = true;
      const rect = el.getBoundingClientRect();
      if (rect.width <= 0) return;
      const deltaPct = (dx / rect.width) * 100;
      setPosition((p) =>
        Math.max(0, Math.min(100, p + (isRtlRef.current ? -deltaPct : deltaPct)))
      );
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  // Keyboard accessibility on the handle button.
  const onHandleKeyDown = (e: React.KeyboardEvent<HTMLButtonElement>) => {
    const step = e.shiftKey ? 10 : 2;
    if (e.key === "ArrowLeft") {
      e.preventDefault();
      userInteractedRef.current = true;
      setPosition((p) => Math.max(0, p + (isRtlRef.current ? step : -step)));
    } else if (e.key === "ArrowRight") {
      e.preventDefault();
      userInteractedRef.current = true;
      setPosition((p) => Math.min(100, p + (isRtlRef.current ? -step : step)));
    } else if (e.key === "Home") {
      e.preventDefault();
      userInteractedRef.current = true;
      setPosition(0);
    } else if (e.key === "End") {
      e.preventDefault();
      userInteractedRef.current = true;
      setPosition(100);
    }
  };

  const showHeader = eyebrow || title || subtitle;
  const hasMedia = Boolean(beforeImage || afterImage);

  return (
    <section className="bg-background py-0">
      {showHeader && (
        <div className="container mx-auto px-4 pt-12 md:pt-16 pb-8 md:pb-10 text-center max-w-2xl">
          {eyebrow && (
            <span className="vn-eyebrow block mb-2">
              <InlineEditable sectionId={sectionId} settingKey="eyebrow" value={eyebrow} />
            </span>
          )}
          {title && (
            <h2 className="vn-heading text-2xl md:text-4xl">
              <InlineEditable sectionId={sectionId} settingKey="title" value={title} />
            </h2>
          )}
          {subtitle && (
            <p className="text-sm md:text-base text-[var(--vn-muted)] mt-2">
              <InlineEditable sectionId={sectionId} settingKey="subtitle" value={subtitle} multiline />
            </p>
          )}
        </div>
      )}

      {!hasMedia && (
        <div className={fullWidth ? "w-full" : "container mx-auto px-4"}>
          {/* Empty-state mock: shows the actual slider layout (split panels +
              divider + circular handle) so the merchant sees the structure
              matches the erwanaa-style reference, just with no images yet. */}
          <div
            className={`relative w-full ${aspectClass} max-h-[85vh] overflow-hidden bg-[var(--vn-band)] select-none`}
          >
            <div className="absolute inset-0 grid grid-cols-2">
              <button
                type="button"
                className="group relative flex flex-col items-center justify-center gap-3 bg-[var(--vn-band)] hover:bg-[color-mix(in_srgb,var(--vn-band)_80%,var(--vn-ink)_8%)] transition-colors cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--vn-ink)] focus-visible:ring-inset"
                aria-label={localized(locale, "Upload Before image", "ارفعي صورة قبل")}
              >
                <svg width="36" height="36" viewBox="0 0 24 24" fill="none" aria-hidden="true" className="text-[var(--vn-muted)] opacity-60 group-hover:opacity-100 transition-opacity">
                  <rect x="3" y="3" width="18" height="18" rx="2" stroke="currentColor" strokeWidth="1.5" />
                  <circle cx="8.5" cy="8.5" r="1.5" stroke="currentColor" strokeWidth="1.5" />
                  <path d="M21 15l-5-5L5 21" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
                <span className="vn-eyebrow text-[var(--vn-muted)]">{localized(locale, "Before", "قبل")}</span>
              </button>
              <button
                type="button"
                className="group relative flex flex-col items-center justify-center gap-3 bg-[color-mix(in_srgb,var(--vn-band)_85%,var(--vn-ink)_5%)] hover:bg-[color-mix(in_srgb,var(--vn-band)_70%,var(--vn-ink)_12%)] transition-colors cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--vn-ink)] focus-visible:ring-inset"
                aria-label={localized(locale, "Upload After image", "ارفعي صورة بعد")}
              >
                <svg width="36" height="36" viewBox="0 0 24 24" fill="none" aria-hidden="true" className="text-[var(--vn-muted)] opacity-60 group-hover:opacity-100 transition-opacity">
                  <rect x="3" y="3" width="18" height="18" rx="2" stroke="currentColor" strokeWidth="1.5" />
                  <circle cx="8.5" cy="8.5" r="1.5" stroke="currentColor" strokeWidth="1.5" />
                  <path d="M21 15l-5-5L5 21" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
                <span className="vn-eyebrow text-[var(--vn-muted)]">{localized(locale, "After", "بعد")}</span>
              </button>
            </div>

            <div className="vn-cmp-divider-center" />
            <div className="vn-cmp-handle-center" aria-hidden="true">
              <svg width="18" height="18" viewBox="0 0 20 20" fill="none">
                <path d="M6 6L2 10L6 14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                <path d="M14 6L18 10L14 14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </div>
          </div>
        </div>
      )}

      {hasMedia && (
      <div className={fullWidth ? "w-full" : "container mx-auto px-4"}>
        {/* `max-h-[85vh]` keeps the section from blowing up on wide desktops
            (a 3:4 portrait at 1440px wide is 1920px tall — taller than the
            viewport). The image inside uses object-fit: cover so the crop
            stays graceful when height is clamped. */}
        <div
          ref={containerRef}
          className={`relative w-full ${aspectClass} max-h-[85vh] overflow-hidden bg-[var(--vn-band)] select-none touch-none cursor-ew-resize`}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={stopDrag}
          onPointerCancel={stopDrag}
          role="slider"
          aria-label="Drag to compare before and after"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(position)}
        >
          {afterImage ? (
            <img
              src={afterImage}
              alt={afterLabel || ""}
              className="absolute inset-0 w-full h-full object-cover pointer-events-none"
              style={applyImageTransform(afterImageTransform, "cover")}
              draggable={false}
            />
          ) : (
            <div className="absolute inset-0 vn-shimmer" />
          )}

          {beforeImage && (
            <div
              className="vn-cmp-clip"
              style={{ ["--vn-clip" as string]: `${100 - position}%` } as React.CSSProperties}
            >
              <img
                src={beforeImage}
                alt={beforeLabel || ""}
                className="absolute inset-0 w-full h-full object-cover pointer-events-none"
                style={applyImageTransform(beforeImageTransform, "cover")}
                draggable={false}
              />
            </div>
          )}

          {showLabels && beforeLabel && (
            <span className="vn-label absolute top-4 start-4 px-3 py-1.5 text-[10px] bg-white text-[var(--vn-ink)] rounded-full pointer-events-none z-10">
              {beforeLabel}
            </span>
          )}
          {showLabels && afterLabel && (
            <span className="vn-label absolute top-4 end-4 px-3 py-1.5 text-[10px] bg-[var(--vn-ink)] text-white rounded-full pointer-events-none z-10">
              {afterLabel}
            </span>
          )}

          <div
            className="vn-cmp-divider"
            style={{ ["--vn-pos" as string]: `${position}%` } as React.CSSProperties}
          />
          <button
            type="button"
            className="vn-cmp-handle"
            style={{ ["--vn-pos" as string]: `${position}%` } as React.CSSProperties}
            onKeyDown={onHandleKeyDown}
            aria-label="Drag to compare before and after"
          >
            <svg width="18" height="18" viewBox="0 0 20 20" fill="none" aria-hidden="true">
              <path d="M6 6L2 10L6 14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
              <path d="M14 6L18 10L14 14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
        </div>
      </div>
      )}
    </section>
  );
};

export default VionneImageComparison;
