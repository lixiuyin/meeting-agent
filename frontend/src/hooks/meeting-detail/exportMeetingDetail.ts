import { message } from "antd";
import {
  exportMeeting,
  getFileTimeline,
  getMeeting,
  getMeetingAssetUrl,
  getMeetingFileUrl,
  type FileTimelineResponse,
} from "../../api/client";
import {
  buildPerFileMarkdownBundle,
  createMarkdownZipBlob,
  rewriteMeetingAssetLinks,
  sanitizeMarkdownFileName,
  triggerBlobDownload,
  type MarkdownArchiveAsset,
} from "../../utils/exportMarkdown";
import { formatApiErrorMessage } from "../../api/client";

export async function exportMeetingDetail(id: number, format: "json" | "markdown" | "txt") {
  const res = await exportMeeting(id, format);
  if (format === "markdown") {
    const meetingRes = await getMeeting(id);
    const meeting = meetingRes.data;
    const readyFiles = meeting.files.filter((f) => f.status === "ready");

    const timelines: Record<number, FileTimelineResponse> = {};
    await Promise.all(
      readyFiles.map(async (file) => {
        try {
          const tlRes = await getFileTimeline(id, file.id);
          timelines[file.id] = tlRes.data;
        } catch {
          // ignore timeline fetch failures for individual files
        }
      }),
    );

    const allMarkdownParts: string[] = [];
    const allAssets: MarkdownArchiveAsset[] = [];
    const usedAssetPaths = new Map<string, number>();

    allMarkdownParts.push(`# ${meeting.title}`);
    allMarkdownParts.push("");
    if (meeting.description) {
      allMarkdownParts.push(meeting.description);
      allMarkdownParts.push("");
    }
    allMarkdownParts.push(`*Exported at: ${new Date().toISOString()}*`);
    allMarkdownParts.push("");
    allMarkdownParts.push("---");
    allMarkdownParts.push("");

    for (const file of readyFiles) {
      const bundle = buildPerFileMarkdownBundle({
        meetingTitle: meeting.title,
        meetingId: id,
        file,
        timeline: timelines[file.id] ?? null,
        resolveAssetUrl: getMeetingAssetUrl,
        resolveFileUrl: getMeetingFileUrl,
        usedAssetPaths,
      });
      allMarkdownParts.push(bundle.markdown);
      allMarkdownParts.push("");
      allMarkdownParts.push("---");
      allMarkdownParts.push("");
      for (const asset of bundle.assets) {
        allAssets.push(asset);
      }
    }

    let combinedMarkdown = allMarkdownParts.join("\n");
    const rewritten = rewriteMeetingAssetLinks(combinedMarkdown, {
      assetDir: "linked-assets",
      resolveAssetUrl: getMeetingAssetUrl,
    });
    combinedMarkdown = rewritten.markdown;

    const seenAsset = new Set(allAssets.map((a) => `${a.sourceUrl}::${a.archivePath}`));
    for (const asset of rewritten.assets) {
      const key = `${asset.sourceUrl}::${asset.archivePath}`;
      if (seenAsset.has(key)) continue;
      seenAsset.add(key);
      allAssets.push(asset);
    }

    const mdName = `${sanitizeMarkdownFileName(meeting.title)}.md`;
    const { blob: zipBlob, failedAssets } = await createMarkdownZipBlob(
      mdName,
      combinedMarkdown,
      allAssets,
    );
    const zipName = mdName.replace(/\.md$/i, ".zip");
    triggerBlobDownload(zipName, zipBlob);
    if (failedAssets.length > 0) {
      message.warning(`Exported ZIP, but ${failedAssets.length} image(s) failed to include.`);
    } else {
      message.success("Exported successfully");
    }
    return;
  }

  const blob = new Blob([res.data.content], {
    type: format === "json" ? "application/json" : "text/plain",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = res.data.filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  message.success("Exported successfully");
}

export function showExportError(err: unknown) {
  message.error(formatApiErrorMessage(err, "Export failed"));
}
