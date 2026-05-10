import type { SourceItem } from "../../../api/client";

export interface SessionMessage {
  role: string;
  content: string;
  sources: SourceItem[];
}
