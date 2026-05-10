import { createContext, useContext, type ReactNode } from "react";
import { useSessionManager } from "../hooks/useSessionManager";

type SessionManagerReturn = ReturnType<typeof useSessionManager>;

const ChatContext = createContext<SessionManagerReturn | null>(null);

// eslint-disable-next-line react-refresh/only-export-components
export function useChatSession(): SessionManagerReturn {
  const ctx = useContext(ChatContext);
  if (!ctx) throw new Error("useChatSession must be used within ChatProvider");
  return ctx;
}

export function ChatProvider({ children }: { children: ReactNode }) {
  const session = useSessionManager();
  return <ChatContext.Provider value={session}>{children}</ChatContext.Provider>;
}
