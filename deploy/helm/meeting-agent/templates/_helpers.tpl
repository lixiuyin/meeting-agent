{{/*
Expand the name of the chart.
*/}}
{{- define "meeting-agent.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "meeting-agent.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "meeting-agent.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{ include "meeting-agent.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "meeting-agent.selectorLabels" -}}
app.kubernetes.io/name: {{ include "meeting-agent.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Validate single-replica constraint for SQLite + local FS backend.
Multiple replicas cause WAL corruption and file/vector store splits.
*/}}
{{- define "meeting-agent.validateSingleReplica" -}}
{{- if gt (int .Values.backend.replicaCount) 1 -}}
{{- fail "backend.replicaCount must be 1 — the current architecture uses SQLite and local file storage, which do not support multi-replica deployments. See docs/adr/0001-single-instance-deployment.md" -}}
{{- end -}}
{{- if .Values.autoscaling.enabled -}}
{{- fail "autoscaling.enabled must be false — the current architecture uses SQLite and local file storage, which do not support horizontal scaling. See docs/adr/0001-single-instance-deployment.md" -}}
{{- end -}}
{{- end -}}
