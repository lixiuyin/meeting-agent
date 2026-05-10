import { getMeetingAssetUrl } from "../../../api/client";
import { CitationMarkdown as SharedCitationMarkdown } from "../../shared/CitationMarkdown";

interface Props {
  content: string;
  sourceCount: number;
  onCiteClick: (idx: number) => void;
}

export function CitationMarkdown({ content, sourceCount, onCiteClick }: Props) {
  return (
    <SharedCitationMarkdown
      content={content}
      sourceCount={sourceCount}
      onCiteClick={onCiteClick}
      resolveAssetUrl={getMeetingAssetUrl}
      simple
    />
  );
}
