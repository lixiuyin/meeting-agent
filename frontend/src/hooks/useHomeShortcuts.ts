import { useEffect } from "react";
import type { RefObject } from "react";

interface ShortcutDeps {
  inputFocused: boolean;
  textareaRef: RefObject<{ blur(): void; focus(): void } | null>;
  handleSendRef: { current: () => void };
}

/**
 * Registers global keyboard shortcuts for the Home page:
 *  - Cmd/Ctrl+Enter  → send message
 *  - Escape           → blur textarea
 *  - /                → focus textarea (when no input is focused)
 */
export function useHomeShortcuts(deps: ShortcutDeps) {
  const { inputFocused, textareaRef, handleSendRef } = deps;

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        // handleSend internally guards against empty input and active sends
        handleSendRef.current();
      }
      if (e.key === "Escape" && inputFocused) {
        textareaRef.current?.blur(); // TextAreaRef has .blur()
      }
      if (
        e.key === "/" &&
        !inputFocused &&
        document.activeElement?.tagName !== "INPUT" &&
        document.activeElement?.tagName !== "TEXTAREA"
      ) {
        e.preventDefault();
        (textareaRef.current as HTMLElement | null)?.focus();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [inputFocused, textareaRef, handleSendRef]);
}
