import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import * as client from "../api/client";
import { useMemoryActions } from "./useMemoryActions";

afterEach(() => vi.restoreAllMocks());

it("returning to a cached filter cancels the intervening request", async () => {
  const item = { key: "initial", value: "A" } as client.MemoryItem;
  let resolveOther!: (value: never) => void;
  const list = vi.spyOn(client, "listMemories").mockImplementation((_user, options) => {
    if (options?.factType === "decision") {
      return new Promise((resolve) => {
        resolveOther = resolve;
      }) as never;
    }
    return Promise.resolve({ data: { items: [item], total: 1, next_cursor: null } }) as never;
  });
  const { result } = renderHook(() => useMemoryActions("u"));
  await waitFor(() => expect(result.current.memories).toEqual([item]));
  act(() => result.current.setFactTypeFilter("decision"));
  await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
  act(() => result.current.setFactTypeFilter(undefined));
  await waitFor(() => expect(result.current.loading).toBe(false));
  await act(async () =>
    resolveOther({ data: { items: [{ key: "wrong-scope", value: "B" }], total: 1 } } as never),
  );
  expect(result.current.memories).toEqual([item]);
});

it("preserves semantic intent and all visible filters through a pending project debounce", async () => {
  vi.spyOn(client, "listMemories").mockResolvedValue({ data: { items: [], total: 0 } } as never);
  const search = vi
    .spyOn(client, "searchMemories")
    .mockResolvedValue({ data: { memories: [{ key: "topic.a", value: "reference" }] } } as never);
  const { result } = renderHook(() => useMemoryActions("u", "reference"));
  await waitFor(() => expect(client.listMemories).toHaveBeenCalled());
  act(() => {
    result.current.setSearch("owner");
    result.current.setProjectFilter("atlas");
    result.current.setFactTypeFilter("fact");
    result.current.setStatusFilter("confirmed");
  });
  await act(() => result.current.handleSemanticSearch());
  expect(search).toHaveBeenCalledWith(
    "owner",
    "u",
    10,
    undefined,
    expect.objectContaining({
      memoryKind: "reference",
      projectId: "atlas",
      factType: "fact",
      assertionStatus: "confirmed",
    }),
  );
  await act(() => new Promise((resolve) => setTimeout(resolve, 350)));
  expect(result.current.semanticResults?.[0].key).toBe("topic.a");
});

it("does not restore stale semantic results after creating a memory", async () => {
  const item = { key: "new", value: "current", revision: 1 } as client.MemoryItem;
  vi.spyOn(client, "listMemories").mockResolvedValue({
    data: { items: [], memories: [], total: 0, next_cursor: null },
  } as never);
  vi.spyOn(client, "createMemory").mockResolvedValue({ data: item } as never);
  let resolveSearch!: (value: never) => void;
  vi.spyOn(client, "searchMemories").mockImplementation(
    () =>
      new Promise((resolve) => {
        resolveSearch = resolve;
      }) as never,
  );
  const { result } = renderHook(() => useMemoryActions("u"));
  await waitFor(() => expect(client.listMemories).toHaveBeenCalled());
  act(() => result.current.setSearch("old"));
  let searching!: Promise<void>;
  act(() => {
    searching = result.current.handleSemanticSearch();
  });
  await act(() => result.current.handleCreate({ key: "new", value: "current" }));
  await act(async () => {
    resolveSearch({ data: { memories: [{ key: "old", value: "stale" }] } } as never);
    await searching;
  });
  expect(result.current.semanticResults).toBeNull();
  expect(result.current.displayMemories.map((m) => m.key)).toEqual(["new"]);
});

it("refresh invalidates an older search response", async () => {
  vi.spyOn(client, "listMemories").mockResolvedValue({
    data: { items: [], memories: [], total: 0, next_cursor: null },
  } as never);
  let resolveSearch!: (value: never) => void;
  vi.spyOn(client, "searchMemories").mockImplementation(
    () =>
      new Promise((resolve) => {
        resolveSearch = resolve;
      }) as never,
  );
  const { result } = renderHook(() => useMemoryActions("u"));
  await waitFor(() => expect(client.listMemories).toHaveBeenCalled());
  act(() => result.current.setSearch("query"));
  let searching!: Promise<void>;
  act(() => {
    searching = result.current.handleSemanticSearch();
  });
  await act(() => result.current.load());
  await act(async () => {
    resolveSearch({ data: { memories: [] } } as never);
    await searching;
  });
  expect(result.current.semanticResults).toBeNull();
});

it("pushes lifecycle filters into server-side pagination", async () => {
  const list = vi.spyOn(client, "listMemories").mockResolvedValue({
    data: { items: [], memories: [], total: 0, next_cursor: null },
  } as never);
  const { result } = renderHook(() => useMemoryActions("u"));
  await waitFor(() => expect(list).toHaveBeenCalled());

  act(() => {
    result.current.setFactTypeFilter("decision");
    result.current.setStatusFilter("pending");
  });

  await waitFor(() =>
    expect(list).toHaveBeenLastCalledWith(
      "u",
      expect.objectContaining({ factType: "decision", assertionStatus: "pending" }),
    ),
  );
});
