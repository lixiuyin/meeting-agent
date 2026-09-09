import { useState } from "react";
import { Alert, Button, Input, Select, Space } from "antd";
import { useIntl } from "react-intl";
import { api, ApiError, formatApiErrorMessage } from "../../api/client-core";
import { MaterialSelect } from "./ProjectFields";
import { useProjects, type Project } from "./useProjects";

export default function ProjectDirectoryPanel({ onSaved }: { onSaved?: () => void }) {
  const { formatMessage: t } = useIntl();
  const { projects, error: loadError, refresh } = useProjects();
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const [aliases, setAliases] = useState<string[]>([]);
  const [files, setFiles] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [revision, setRevision] = useState(0);
  const [conflict, setConflict] = useState<Project | null>(null);
  const selectProject = (p?: Project) => {
    setId(p?.project_id ?? "");
    setName(p?.name ?? "");
    setAliases(p?.aliases ?? []);
    setFiles(p?.file_ids ?? []);
    setRevision(p?.revision ?? 0);
    setSaved(false);
    setConflict(null);
    setError("");
  };
  return (
    <Space orientation="vertical" style={{ width: "100%" }}>
      <Alert type="info" title={t({ id: "memory.projects.notice" })} />
      <Select
        showSearch
        allowClear
        style={{ width: 300, maxWidth: "100%" }}
        placeholder={t({ id: "memory.projects.edit" })}
        aria-label={t({ id: "memory.projects.edit" })}
        options={projects.map((p) => ({ value: p.project_id, label: p.name }))}
        onChange={(value) => {
          const p = projects.find((p) => p.project_id === value);
          selectProject(p);
        }}
      />
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          setBusy(true);
          setSaved(false);
          try {
            const response = await api.put("/memory/projects", {
              project_id: id.trim(),
              name: name.trim(),
              aliases,
              file_ids: files,
              expected_revision: revision,
            });
            setRevision(response.data.revision ?? revision + 1);
            setConflict(null);
            setSaved(true);
            setError("");
            refresh();
            onSaved?.();
          } catch (err) {
            if (err instanceof ApiError && err.status === 409) {
              setConflict((err.details?.current as Project | undefined) ?? null);
              refresh();
            }
            setError(formatApiErrorMessage(err));
          } finally {
            setBusy(false);
          }
        }}
      >
        <Space wrap>
          <Input
            required
            maxLength={120}
            aria-label="Project ID"
            placeholder="Project ID"
            value={id}
            onChange={(e) => {
              setId(e.target.value);
              setRevision(0);
              setSaved(false);
            }}
            disabled={revision > 0}
          />
          <Input
            required
            maxLength={200}
            aria-label={t({ id: "memory.projects.name" })}
            placeholder={t({ id: "memory.projects.name" })}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <Select
            mode="tags"
            style={{ width: 250 }}
            aria-label={t({ id: "memory.projects.aliases" })}
            placeholder={t({ id: "memory.projects.aliases" })}
            value={aliases}
            onChange={setAliases}
          />
          <MaterialSelect value={files} onChange={setFiles} />
          <Button htmlType="submit" loading={busy} disabled={!id.trim() || !name.trim()}>
            {t({ id: "memory.projects.save" })}
          </Button>
        </Space>
      </form>
      {(error || loadError) && <Alert type="error" title={error || loadError} />}
      {conflict && (
        <Alert
          type="warning"
          title={t({ id: "memory.projects.conflict" })}
          description={
            <Space orientation="vertical">
              <span>
                {t(
                  { id: "memory.projects.remoteBindings" },
                  { files: conflict.file_ids.join(", ") || "—" },
                )}
              </span>
              <span>
                {t({ id: "memory.projects.localBindings" }, { files: files.join(", ") || "—" })}
              </span>
              <Button onClick={() => selectProject(conflict)}>
                {t({ id: "memory.projects.loadLatest" })}
              </Button>
            </Space>
          }
        />
      )}
      {saved && <Alert type="success" title={t({ id: "memory.projects.saved" })} />}
    </Space>
  );
}
