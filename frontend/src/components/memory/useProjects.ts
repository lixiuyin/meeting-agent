import { useEffect, useState } from "react";
import { api, formatApiErrorMessage, isRequestCanceled } from "../../api/client-core";

export interface Project {
  revision: number;
  project_id: string;
  name: string;
  aliases: string[];
  file_ids: number[];
}

export function useProjects() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [error, setError] = useState("");
  const [serial, setSerial] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    api
      .get<Project[]>("/memory/projects", { signal: controller.signal })
      .then(({ data }) => {
        if (controller.signal.aborted) return;
        setProjects(data);
        setError("");
      })
      .catch((err) => {
        if (!isRequestCanceled(err)) setError(formatApiErrorMessage(err));
      });
    return () => controller.abort();
  }, [serial]);
  return { projects, error, refresh: () => setSerial((value) => value + 1) };
}
