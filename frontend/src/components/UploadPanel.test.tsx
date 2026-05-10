import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { message } from "antd";
import { beforeEach, describe, expect, it, vi } from "vitest";

import UploadPanel from "./UploadPanel";

const createMeetingMock = vi.fn().mockResolvedValue({ data: { meeting_id: 101 } });
const uploadMeetingMock = vi.fn().mockResolvedValue({ data: {} });
const deleteMeetingMock = vi.fn().mockResolvedValue({});

vi.mock("../api/client", () => ({
  createMeeting: (...args: unknown[]) => createMeetingMock(...args),
  uploadMeeting: (...args: unknown[]) => uploadMeetingMock(...args),
  deleteMeeting: (...args: unknown[]) => deleteMeetingMock(...args),
  formatApiErrorMessage: (error: unknown, fallback = "Request failed") =>
    error instanceof Error && error.message ? error.message : fallback,
  ApiError: class ApiError extends Error {},
}));

vi.mock("react-intl", () => ({
  useIntl: () => ({
    formatMessage: (descriptor: { id: string }) => descriptor.id,
  }),
}));

describe("UploadPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("warns when title is missing", async () => {
    const warningSpy = vi.spyOn(message, "warning").mockImplementation(() => undefined as never);
    render(<UploadPanel onSuccess={vi.fn()} />);

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["hello"], "notes.txt", { type: "text/plain" });
    fireEvent.change(fileInput, { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: /upload\.submit\.createNew/i }));

    await waitFor(() => {
      expect(warningSpy).toHaveBeenCalled();
    });
  });

  it("creates meeting, uploads file and calls onSuccess", async () => {
    const onSuccess = vi.fn();
    render(<UploadPanel onSuccess={onSuccess} />);

    fireEvent.change(screen.getByPlaceholderText("upload.target.titlePlaceholder"), {
      target: { value: "Roadmap Review" },
    });

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["hello"], "notes.txt", { type: "text/plain" });
    fireEvent.change(fileInput, { target: { files: [file] } });

    fireEvent.click(screen.getByRole("button", { name: /upload\.submit\.createNew/i }));

    await waitFor(() => expect(createMeetingMock).toHaveBeenCalled());
    await waitFor(() => expect(uploadMeetingMock).toHaveBeenCalled());
    await waitFor(() => expect(onSuccess).toHaveBeenCalled());
  });

  it("deletes empty meeting when all uploads fail", async () => {
    uploadMeetingMock.mockRejectedValueOnce(new Error("Upload failed"));
    const onSuccess = vi.fn();
    render(<UploadPanel onSuccess={onSuccess} />);

    fireEvent.change(screen.getByPlaceholderText("upload.target.titlePlaceholder"), {
      target: { value: "Audio Upload" },
    });

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["hello"], "voice.mp3", { type: "audio/mpeg" });
    fireEvent.change(fileInput, { target: { files: [file] } });

    fireEvent.click(screen.getByRole("button", { name: /upload\.submit\.createNew/i }));

    await waitFor(() => expect(createMeetingMock).toHaveBeenCalled());
    await waitFor(() => expect(uploadMeetingMock).toHaveBeenCalled());
    await waitFor(() => expect(deleteMeetingMock).toHaveBeenCalledWith(101));
    expect(onSuccess).not.toHaveBeenCalled();
  });
});
