import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client-core";
import AudioPlayer from "../components/AudioPlayer";
import { TextPreview } from "../components/materials/file-views/MediaViewers";

describe("TextPreview evidence coordinates", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("highlights and scrolls the exact cited character window", async () => {
    vi.spyOn(api, "get").mockResolvedValue({ data: "Prefix exact evidence suffix" });
    const scrollSpy = vi.spyOn(Element.prototype, "scrollIntoView");

    render(<TextPreview url="/source.txt" fileName="source.txt" windowStart={7} windowEnd={21} />);

    const evidence = await screen.findByTestId("exact-evidence-window");
    expect(evidence).toHaveTextContent("exact evidence");
    await waitFor(() => expect(scrollSpy).toHaveBeenCalledWith({ block: "center" }));
  });

  it("clamps malformed coordinates without hiding the source text", async () => {
    vi.spyOn(api, "get").mockResolvedValue({ data: "Short source" });

    render(
      <TextPreview url="/source.txt" fileName="source.txt" windowStart={999} windowEnd={1000} />,
    );

    expect(await screen.findByText("Short source")).toBeInTheDocument();
    expect(screen.queryByTestId("exact-evidence-window")).not.toBeInTheDocument();
  });

  it("interprets backend offsets as Unicode code points", async () => {
    vi.spyOn(api, "get").mockResolvedValue({ data: "A😀exact evidence" });

    render(<TextPreview url="/unicode.txt" fileName="unicode.txt" windowStart={2} windowEnd={7} />);

    expect(await screen.findByTestId("exact-evidence-window")).toHaveTextContent("exact");
  });
});

describe("AudioPlayer evidence window", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("stops playback at the cited end timestamp", () => {
    const pause = vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
    render(<AudioPlayer src="/clip.mp3" seekTo={3} seekEnd={5} />);
    const audio = screen.getByLabelText("Audio player");
    Object.defineProperty(audio, "currentTime", { configurable: true, value: 5.1 });

    fireEvent.timeUpdate(audio);

    expect(pause).toHaveBeenCalledTimes(1);
  });
});
