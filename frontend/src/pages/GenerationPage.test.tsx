import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { I18nProvider } from "../i18n/I18nProvider";

const mocks = vi.hoisted(() => ({
  invokeSkill: vi.fn(),
  listAllMeetings: vi.fn(),
  listSkills: vi.fn(),
}));

vi.mock("../api/client", () => ({
  createSkill: vi.fn(),
  formatApiErrorMessage: (_error: unknown, fallback: string) => fallback,
  invokeSkill: mocks.invokeSkill,
  listAllMeetings: mocks.listAllMeetings,
  listSkills: mocks.listSkills,
}));

vi.mock("../components/generation/skillDisplay", () => ({
  iconForFileType: () => null,
  normalizeSkillName: (value: string) => value,
  normalizeSkills: (value: unknown) => value,
  SKILL_ICONS: {},
}));

vi.mock("../components/generation/CreateSkillModal", () => ({
  CreateSkillModal: () => null,
}));

vi.mock("../components/generation/GenerationConfigCard", () => ({
  GenerationConfigCard: (props: {
    meetingGroups: { key: string }[];
    selectedGroup?: { key: string };
    onSelectMeeting: (key: string) => void;
    onSelectSkill: (skill: string) => void;
    onGenerate: () => void;
  }) => (
    <div>
      <span data-testid="meeting-count">{props.meetingGroups.length}</span>
      <span data-testid="selected-meeting">{props.selectedGroup?.key ?? ""}</span>
      <button onClick={() => props.onSelectMeeting(props.meetingGroups[0]?.key)}>meeting</button>
      <button onClick={() => props.onSelectSkill("skill-a")}>skill-a</button>
      <button onClick={() => props.onSelectSkill("skill-b")}>skill-b</button>
      <button onClick={props.onGenerate}>generate</button>
    </div>
  ),
}));

vi.mock("../components/generation/GenerationOutputCard", () => ({
  GenerationOutputCard: (props: { result: string; selectedSkillDisplayName?: string }) => (
    <div data-testid="output">{`${props.selectedSkillDisplayName ?? ""}:${props.result}`}</div>
  ),
}));

import { buildMeetingGroups } from "../components/generation/meetingGroups";
import GenerationPage from "./GenerationPage";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

const meetings = [
  {
    id: 1,
    title: "Repeated title",
    file_name: "one.pdf",
    status: "ready",
    created_at: "2025-01-01T00:00:00Z",
  },
  {
    id: 2,
    title: "Repeated title",
    file_name: "two.pdf",
    status: "ready",
    created_at: "2025-01-02T00:00:00Z",
  },
] as never[];

describe("GenerationPage identity handling", () => {
  beforeEach(() => {
    mocks.invokeSkill.mockReset();
    mocks.listAllMeetings.mockResolvedValue(meetings);
    mocks.listSkills.mockResolvedValue({
      data: {
        skills: [
          {
            name: "skill-a",
            display_name: "Skill A",
            description: "A",
            examples: [],
            category: "test",
            version: "1",
          },
          {
            name: "skill-b",
            display_name: "Skill B",
            description: "B",
            examples: [],
            category: "test",
            version: "1",
          },
        ],
      },
    });
  });

  it("keeps meetings with the same title as separate stable-id choices", () => {
    const groups = buildMeetingGroups(meetings);
    expect(groups.map((group) => group.key)).toEqual(["1", "2"]);
    expect(groups.map((group) => group.ids)).toEqual([[1], [2]]);
  });

  it("ignores a generation response after the selected skill changes", async () => {
    const first = deferred<{ data: { content: string } }>();
    mocks.invokeSkill.mockReturnValueOnce(first.promise);
    render(
      <I18nProvider>
        <GenerationPage />
      </I18nProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("meeting-count")).toHaveTextContent("2"));
    await waitFor(() => expect(screen.getByTestId("output")).toHaveTextContent("Skill A:"));
    fireEvent.click(screen.getByRole("button", { name: "meeting" }));
    await waitFor(() => expect(screen.getByTestId("selected-meeting")).toHaveTextContent("1"));
    fireEvent.click(screen.getByRole("button", { name: "generate" }));
    await waitFor(() => expect(mocks.invokeSkill).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("button", { name: "skill-b" }));
    first.resolve({ data: { content: "stale A result" } });

    await waitFor(() => expect(screen.getByTestId("output")).toHaveTextContent("Skill B:"));
    expect(screen.getByTestId("output")).not.toHaveTextContent("stale A result");
  });
});
