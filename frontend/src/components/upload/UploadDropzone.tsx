import { InboxOutlined } from "@ant-design/icons";
import type { UploadFile } from "antd";
import type { RcFile } from "antd/es/upload/interface";
import { Form, Upload } from "antd";
import { useIntl } from "react-intl";
import { MAX_FILES } from "./fileUtils";
import { SelectedFilesList } from "./SelectedFilesList";

interface Props {
  fileList: UploadFile[];
  uploading: boolean;
  currentFileIndex: number;
  previewByUid: Record<string, string>;
  onAddFile: (file: RcFile) => void;
  onRemoveFile: (uid: string) => void;
}

export function UploadDropzone({
  fileList,
  uploading,
  currentFileIndex,
  previewByUid,
  onAddFile,
  onRemoveFile,
}: Props) {
  const { formatMessage } = useIntl();

  return (
    <Form.Item style={{ marginBottom: 12 }}>
      <Upload.Dragger
        fileList={fileList}
        beforeUpload={(file) => {
          onAddFile(file);
          return false;
        }}
        maxCount={MAX_FILES}
        accept=".mp4,.mkv,.avi,.mov,.webm,.m4v,.3gp,.mp3,.wav,.aac,.flac,.m4a,.ogg,.wma,.opus,.pdf,.pptx,.ppt,.doc,.docx,.xls,.xlsx,.csv,.txt,.md,.json,.xml,.html,.png,.jpg,.jpeg,.bmp,.tiff,.tif,.webp,.gif"
        onRemove={(file) => {
          onRemoveFile(file.uid);
          return false;
        }}
        showUploadList={false}
        multiple={true}
        style={{
          borderRadius: 12,
          border: "1.5px dashed var(--color-border-strong)",
          background: "var(--color-bg-muted)",
          padding: "16px 0",
        }}
      >
        <p className="ant-upload-drag-icon" style={{ marginBottom: 8 }}>
          <InboxOutlined style={{ fontSize: 28, color: "var(--color-primary)" }} />
        </p>
        <p
          style={{
            margin: 0,
            fontSize: 13,
            color: "var(--color-text-secondary)",
            fontWeight: 500,
          }}
        >
          {formatMessage({ id: "upload.dropzone.prompt" })}
        </p>
        <p style={{ margin: "4px 0 0", fontSize: 12, color: "var(--color-text-muted)" }}>
          {formatMessage({ id: "upload.dropzone.hint" }, { maxFiles: MAX_FILES })}
        </p>
      </Upload.Dragger>

      <SelectedFilesList
        fileList={fileList}
        uploading={uploading}
        currentFileIndex={currentFileIndex}
        previewByUid={previewByUid}
        onRemoveFile={onRemoveFile}
      />
    </Form.Item>
  );
}
