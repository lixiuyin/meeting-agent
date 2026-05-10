export const DOCUMENT_VIEWER_MODAL = {
  width: "min(96vw, 1180px)",
  top: 20,
  bodyHeight: "calc(100vh - 150px)",
  bodyPadding: 12,
  wrapClassName: "media-viewer-modal media-viewer-modal--document",
} as const;

export const AUDIO_VIEWER_MODAL = {
  width: "min(96vw, 980px)",
  top: 20,
  bodyMaxHeight: "calc(100vh - 180px)",
  bodyPadding: 12,
  wrapClassName: "media-viewer-modal media-viewer-modal--audio",
} as const;

export const VIDEO_VIEWER_MODAL = {
  width: "min(96vw, 1240px)",
  top: 16,
  bodyMaxHeight: "calc(100vh - 140px)",
  bodyPadding: 16,
  wrapClassName: "media-viewer-modal media-viewer-modal--video",
} as const;
