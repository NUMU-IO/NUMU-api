"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";

/**
 * Canva-style inline editor for theme text.
 *
 *  - On hover (only inside the customizer iframe, detected via
 *    `window !== window.parent`), shows a dashed accent frame around
 *    the wrapped node + a small pencil glyph in the corner.
 *  - On click, makes the wrapped node `contenteditable`, focuses it,
 *    and selects all the current text.
 *  - On blur OR Enter (without shift), posts
 *    `numu:editor:inline-edit` to `window.parent` with
 *    `{ sectionId, blockId?, key, value }`. The merchant-hub editor
 *    patches the matching setting via the existing draft store; the
 *    iframe receives `numu:theme:update` back, which re-renders this
 *    component with the persisted value.
 *  - Escape cancels and restores the original text.
 *
 *  All hover/edit affordances are inert when not inside the editor
 *  iframe (top-level public storefront), so live shoppers see plain
 *  text.
 */
export interface InlineEditableProps {
  /** The section the wrapped text belongs to (matches the `id` from
   *  `<Section id=...>`). The editor uses this to address the right
   *  section in the draft. */
  sectionId: string;
  /** The block id if this text lives inside a block; omit for
   *  section-level settings. */
  blockId?: string;
  /** Group id (header/footer/announcement-bar) if applicable. */
  groupId?: string;
  /** The settings key to patch (e.g. "headline", "subtitle"). */
  settingKey: string;
  /** The current value, used to seed the editable surface and to
   *  detect "no change" so we don't post a redundant draft update. */
  value: string;
  /** Render mode. "inline" wraps with a span (default); "block" with a div. */
  as?: "inline" | "block";
  /** Allow multi-line content. When false (default), Enter commits. */
  multiline?: boolean;
  /** Class name forwarded to the inner element. */
  className?: string;
  /** Style forwarded to the inner element. */
  style?: CSSProperties;
  /** Optional placeholder rendered when value is empty AND we're not editing. */
  placeholder?: string;
  /** When provided, replaces the default rendered children. Useful for
   *  presenting the value differently in display mode (e.g. styled
   *  fragments) while still keeping plain-text editing. */
  children?: ReactNode;
}

/** Whether to mount editor affordances (hover frame, contenteditable).
 *  Two signals, either is sufficient:
 *    1. We're mounted inside a parent frame (the customizer iframe).
 *    2. The URL has an `editor=...` flag, which the customizer adds
 *       and theme devs can flip manually for local testing.
 */
function isInsideEditor(): boolean {
  if (typeof window === "undefined") return false;
  try {
    const params = new URLSearchParams(window.location.search);
    if (params.get("editor")) return true;
  } catch {
    // ignore — URLSearchParams shouldn't throw, but be defensive
  }
  return window.parent !== window;
}

export function InlineEditable({
  sectionId,
  blockId,
  groupId,
  settingKey,
  value,
  as = "inline",
  multiline = false,
  className,
  style,
  placeholder,
  children,
}: InlineEditableProps) {
  const ref = useRef<HTMLElement | null>(null);
  const [editing, setEditing] = useState(false);
  const [inEditor, setInEditor] = useState(false);

  // We only want to mount editor affordances inside the customizer
  // iframe. SSR / first paint in the public storefront leaves
  // inEditor=false → no hover frame, no contenteditable.
  useEffect(() => {
    setInEditor(isInsideEditor());
  }, []);

  const commit = useCallback(
    (next: string) => {
      setEditing(false);
      if (ref.current) ref.current.removeAttribute("contenteditable");
      const trimmed = multiline ? next : next.replace(/\s+/g, " ").trim();
      if (trimmed === value) return;
      try {
        window.parent.postMessage(
          {
            type: "numu:editor:inline-edit",
            payload: {
              sectionId,
              blockId,
              groupId,
              key: settingKey,
              value: trimmed,
            },
          },
          "*",
        );
      } catch (err) {
        console.warn("[bazar] inline-edit postMessage failed", err);
      }
    },
    [sectionId, blockId, groupId, settingKey, value, multiline],
  );

  const cancel = useCallback(() => {
    setEditing(false);
    if (ref.current) {
      ref.current.removeAttribute("contenteditable");
      // Restore original value so an aborted edit doesn't leak the
      // half-typed text into the next render's defaultValue.
      ref.current.textContent = value;
    }
  }, [value]);

  const startEditing = useCallback(
    (e: React.MouseEvent) => {
      if (!inEditor) return;
      e.preventDefault();
      e.stopPropagation();
      const el = ref.current;
      if (!el) return;
      el.setAttribute("contenteditable", "true");
      el.dataset.numuInlineEditing = "true";
      el.focus();
      // Select all the current text so typing replaces it (Canva-style).
      const selection = window.getSelection();
      if (selection) {
        const range = document.createRange();
        range.selectNodeContents(el);
        selection.removeAllRanges();
        selection.addRange(range);
      }
      setEditing(true);
    },
    [inEditor],
  );

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLElement>) => {
      if (!editing) return;
      if (e.key === "Escape") {
        e.preventDefault();
        cancel();
        ref.current?.blur();
        return;
      }
      if (e.key === "Enter" && !multiline && !e.shiftKey) {
        e.preventDefault();
        const next = ref.current?.textContent ?? "";
        commit(next);
        ref.current?.blur();
      }
    },
    [editing, multiline, commit, cancel],
  );

  const onBlur = useCallback(() => {
    if (!editing) return;
    const next = ref.current?.textContent ?? "";
    commit(next);
  }, [editing, commit]);

  // Suppress link/button activation while we're hovering/editing inside
  // the iframe. Live shoppers don't notice this — only the editor.
  const onClickCapture = useCallback(
    (e: React.MouseEvent) => {
      if (!inEditor) return;
      if (editing) {
        // Once editing, swallow accidental link clicks within the field.
        e.stopPropagation();
      }
    },
    [inEditor, editing],
  );

  const Tag = as === "block" ? "div" : "span";
  const baseClass = "bz-inline-edit";
  const composed = [
    className,
    inEditor ? `${baseClass} ${baseClass}--armed` : baseClass,
    editing ? `${baseClass}--editing` : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <Tag
      ref={ref as React.Ref<HTMLElement & HTMLDivElement>}
      className={composed}
      style={style}
      data-numu-inline-key={settingKey}
      data-numu-inline-section={sectionId}
      data-numu-inline-block={blockId}
      onClick={inEditor ? startEditing : undefined}
      onClickCapture={onClickCapture}
      onKeyDown={onKeyDown}
      onBlur={onBlur}
      suppressContentEditableWarning
    >
      {children ?? (value || (placeholder ? placeholder : null))}
    </Tag>
  );
}
