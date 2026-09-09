import { useEffect, useState } from "react";
import { Select, Alert } from "antd";
import { useIntl } from "react-intl";
import { api, formatApiErrorMessage, isRequestCanceled } from "../../api/client-core";
import { useProjects } from "./useProjects";
export interface Material {
  id: number;
  file_name: string;
  meeting_id: number;
  meeting_title: string;
}

export function ProjectSelect({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  const { projects, error } = useProjects();
  const { formatMessage: t } = useIntl();
  return (
    <span>
      <Select
        allowClear
        showSearch
        optionFilterProp="label"
        style={{ minWidth: 180, maxWidth: "100%" }}
        aria-label={t({ id: "memory.facts.project" })}
        placeholder={t({ id: "memory.facts.project" })}
        value={value || undefined}
        onChange={(value) => onChange(value ?? "")}
        options={projects.map((p) => ({ value: p.project_id, label: p.name }))}
      />
      {error && <Alert type="warning" title={error} />}
    </span>
  );
}

export function MaterialSelect({
  value,
  onChange,
}: {
  value: number[];
  onChange: (value: number[]) => void;
}) {
  const [query, setQuery] = useState("");
  const [materials, setMaterials] = useState<Material[]>([]);
  const [error, setError] = useState("");
  const { formatMessage: t } = useIntl();
  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => {
      api
        .get<Material[]>("/memory/projects/materials", {
          params: { q: query },
          signal: controller.signal,
        })
        .then(({ data }) => {
          if (!controller.signal.aborted) {
            setMaterials((old) => [
              ...old.filter((m) => value.includes(m.id) && !data.some((n) => n.id === m.id)),
              ...data,
            ]);
            setError("");
          }
        })
        .catch((err) => {
          if (!isRequestCanceled(err)) setError(formatApiErrorMessage(err));
        });
    }, 200);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [query, value]);
  return (
    <span>
      <Select
        mode="multiple"
        showSearch
        filterOption={false}
        onSearch={setQuery}
        value={value}
        onChange={onChange}
        maxTagCount="responsive"
        style={{ width: 300, maxWidth: "100%" }}
        aria-label={t({ id: "memory.materials" })}
        placeholder={t({ id: "memory.materials" })}
        options={materials.map((m) => ({
          value: m.id,
          label: `${m.meeting_title} / ${m.file_name}`,
        }))}
      />
      {error && <Alert type="warning" title={error} />}
    </span>
  );
}
