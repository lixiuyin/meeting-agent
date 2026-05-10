import { createContext, useContext, type ReactNode } from "react";
import type { SourceItem } from "../api/client";

interface MessageActionsContextValue {
  copiedId: string | null;
  onCopy: (content: string, id: string) => void;
  onRegenerate: () => void;
  onOpenViewer: (source: SourceItem, preferPlayback?: boolean) => void;
  onOpenMeetingSummary: (meetingId: number, fallbackContent?: string) => void;
  onOpenFileSummary: (meetingId: number, fileId?: number, fallbackContent?: string) => void;
  onCopySourceSnippet: (content: string) => void;
}

const MessageActionsContext = createContext<MessageActionsContextValue | null>(null);

export function MessageActionsProvider({
  value,
  children,
}: {
  value: MessageActionsContextValue;
  children: ReactNode;
}) {
  return <MessageActionsContext.Provider value={value}>{children}</MessageActionsContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useMessageActions() {
  const ctx = useContext(MessageActionsContext);
  if (!ctx) throw new Error("useMessageActions must be used within MessageActionsProvider");
  return ctx;
}
