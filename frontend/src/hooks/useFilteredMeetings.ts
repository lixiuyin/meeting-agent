import { useMemo, useState, useCallback } from "react";
import { useDebounce } from "./useDebounce";
import type { MeetingInfo } from "../api/client";

export type SortField = "date" | "name" | "type" | "status";
export type SortOrder = "asc" | "desc";

export interface FilterOptions {
  searchQuery: string;
  sortField: SortField;
  sortOrder: SortOrder;
}

export function useFilteredMeetings(meetings: MeetingInfo[]) {
  const [searchQuery, setSearchQuery] = useState("");
  const [sortField, setSortField] = useState<SortField>("date");
  const [sortOrder, setSortOrder] = useState<SortOrder>("desc");

  // Debounce search query for better performance
  const debouncedSearchQuery = useDebounce(searchQuery, 300);

  const toggleSortOrder = useCallback(() => {
    setSortOrder((prev) => (prev === "asc" ? "desc" : "asc"));
  }, []);

  const filteredMeetings = useMemo(() => {
    let result = [...meetings];

    if (debouncedSearchQuery.trim()) {
      const query = debouncedSearchQuery.toLowerCase();
      result = result.filter(
        (m) =>
          m.title.toLowerCase().includes(query) ||
          (m.file_name?.toLowerCase().includes(query) ?? false) ||
          (m.transcript_preview?.toLowerCase().includes(query) ?? false),
      );
    }

    result.sort((a, b) => {
      let comparison = 0;
      switch (sortField) {
        case "date":
          comparison = new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
          break;
        case "name":
          comparison = a.title.localeCompare(b.title);
          break;
        case "type":
          comparison = (a.file_type ?? "").localeCompare(b.file_type ?? "");
          break;
        case "status":
          comparison = a.status.localeCompare(b.status);
          break;
      }
      return sortOrder === "asc" ? comparison : -comparison;
    });

    return result;
  }, [meetings, debouncedSearchQuery, sortField, sortOrder]);

  return useMemo(
    () => ({
      filteredMeetings,
      searchQuery,
      setSearchQuery,
      debouncedSearchQuery,
      sortField,
      setSortField,
      sortOrder,
      toggleSortOrder,
      isSearching: searchQuery !== debouncedSearchQuery,
    }),
    [filteredMeetings, searchQuery, debouncedSearchQuery, sortField, sortOrder, toggleSortOrder],
  );
}
