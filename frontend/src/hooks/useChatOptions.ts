import { useState, useMemo } from "react";
import { z } from "zod";
import type { MemoryMode, RetrievalProfile } from "../api/client-chat";
import { useLocalStorage } from "./useLocalStorage";

const RETRIEVAL_PROFILE_SCHEMA = z.enum(["fast", "balanced", "thorough"]);
const MEMORY_MODE_SCHEMA = z.enum(["off", "focused", "balanced", "deep"]);

export interface ChatOptions {
  topK?: number;
  useWebSearch: boolean;
  fileTypes?: string[];
  dateFrom?: string;
  dateTo?: string;
  validAt?: string;
  knownAt?: string;
  ragMode?: "vector" | "native" | "hybrid" | "multimodal" | "hybrid_multimodal" | "auto";
  retrievalProfile?: RetrievalProfile;
  memoryMode?: MemoryMode;
  continuationMode?: "latest" | "saved_scope" | "saved_snapshot";
}

export function useChatOptions() {
  const [paramsExpanded, setParamsExpanded] = useState(false);
  const [useWebSearch, setUseWebSearch] = useState(false);
  const [selectedTypeFilters, setSelectedTypeFilters] = useState<string[]>([]);
  const [dateFrom, setDateFrom] = useState<string>("");
  const [dateTo, setDateTo] = useState<string>("");
  const [validAt, setValidAt] = useState<string>("");
  const [knownAt, setKnownAt] = useState<string>("");
  const [continuationMode, setContinuationMode] = useState<
    "latest" | "saved_scope" | "saved_snapshot"
  >("latest");
  const [ragMode, setRagMode] = useState<
    "vector" | "native" | "hybrid" | "multimodal" | "hybrid_multimodal" | "auto"
  >("auto");
  // Persist only the two high-level operating modes. Detailed filters stay
  // request-scoped so an old search cannot silently constrain a later chat.
  const [retrievalProfile, setRetrievalProfile] = useLocalStorage<RetrievalProfile>(
    "chat-retrieval-profile",
    "balanced",
    RETRIEVAL_PROFILE_SCHEMA,
  );
  const [memoryMode, setMemoryMode] = useLocalStorage<MemoryMode>(
    "chat-memory-mode",
    "balanced",
    MEMORY_MODE_SCHEMA,
  );

  const chatOptions = useMemo<ChatOptions>(
    () => ({
      useWebSearch,
      fileTypes: selectedTypeFilters.length > 0 ? selectedTypeFilters : undefined,
      dateFrom: dateFrom || undefined,
      dateTo: dateTo || undefined,
      validAt: validAt || undefined,
      knownAt: knownAt || undefined,
      continuationMode,
      ragMode,
      retrievalProfile,
      memoryMode,
    }),
    [
      useWebSearch,
      selectedTypeFilters,
      dateFrom,
      dateTo,
      validAt,
      knownAt,
      continuationMode,
      ragMode,
      retrievalProfile,
      memoryMode,
    ],
  );

  const activeParamCount = useMemo(() => {
    let count = 0;
    if (useWebSearch) count++;
    if (selectedTypeFilters.length > 0) count++;
    if (dateFrom || dateTo) count++;
    if (validAt || knownAt) count++;
    if (continuationMode !== "latest") count++;
    if (ragMode !== "auto") count++;
    if (retrievalProfile !== "balanced") count++;
    if (memoryMode !== "balanced") count++;
    return count;
  }, [
    useWebSearch,
    selectedTypeFilters,
    dateFrom,
    dateTo,
    validAt,
    knownAt,
    continuationMode,
    ragMode,
    retrievalProfile,
    memoryMode,
  ]);

  return {
    paramsExpanded,
    setParamsExpanded,
    useWebSearch,
    setUseWebSearch,
    selectedTypeFilters,
    setSelectedTypeFilters,
    dateFrom,
    setDateFrom,
    dateTo,
    setDateTo,
    validAt,
    setValidAt,
    knownAt,
    setKnownAt,
    continuationMode,
    setContinuationMode,
    ragMode,
    setRagMode,
    retrievalProfile,
    setRetrievalProfile,
    memoryMode,
    setMemoryMode,
    chatOptions,
    activeParamCount,
  };
}
