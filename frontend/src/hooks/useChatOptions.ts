import { useState, useMemo } from "react";

export interface ChatOptions {
  topK: number;
  useWebSearch: boolean;
  fileTypes?: string[];
  dateFrom?: string;
  dateTo?: string;
  ragMode?: "native" | "hybrid" | "multimodal" | "hybrid_multimodal" | "auto";
}

export function useChatOptions() {
  const [paramsExpanded, setParamsExpanded] = useState(false);
  const [topK, setTopK] = useState<number>(5);
  const [useWebSearch, setUseWebSearch] = useState(false);
  const [selectedTypeFilters, setSelectedTypeFilters] = useState<string[]>([]);
  const [dateFrom, setDateFrom] = useState<string>("");
  const [dateTo, setDateTo] = useState<string>("");
  const [ragMode, setRagMode] = useState<
    "native" | "hybrid" | "multimodal" | "hybrid_multimodal" | "auto"
  >("auto");

  const chatOptions = useMemo<ChatOptions>(
    () => ({
      topK,
      useWebSearch,
      fileTypes: selectedTypeFilters.length > 0 ? selectedTypeFilters : undefined,
      dateFrom: dateFrom || undefined,
      dateTo: dateTo || undefined,
      ragMode,
    }),
    [topK, useWebSearch, selectedTypeFilters, dateFrom, dateTo, ragMode],
  );

  const activeParamCount = useMemo(() => {
    let count = 0;
    if (topK !== 5) count++;
    if (useWebSearch) count++;
    if (selectedTypeFilters.length > 0) count++;
    if (dateFrom || dateTo) count++;
    if (ragMode !== "auto") count++;
    return count;
  }, [topK, useWebSearch, selectedTypeFilters, dateFrom, dateTo, ragMode]);

  return {
    paramsExpanded,
    setParamsExpanded,
    topK,
    setTopK,
    useWebSearch,
    setUseWebSearch,
    selectedTypeFilters,
    setSelectedTypeFilters,
    dateFrom,
    setDateFrom,
    dateTo,
    setDateTo,
    ragMode,
    setRagMode,
    chatOptions,
    activeParamCount,
  };
}
