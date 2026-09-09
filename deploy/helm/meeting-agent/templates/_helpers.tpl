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
{{- fail "backend.replicaCount must be 1 — the current architecture uses SQLite and local file storage, which do not support multi-replica deployments. See docs/adr/ADR-006-single-instance-deployment.md" -}}
{{- end -}}
{{- end -}}

{{/* Resolve an immutable digest when supplied, otherwise retain tag compatibility. */}}
{{- define "meeting-agent.image" -}}
{{- $image := .image -}}
{{- $root := .root -}}
{{- if $image.digest -}}
{{- if not (regexMatch "^sha256:[a-f0-9]{64}$" $image.digest) -}}
{{- fail "image.digest must use the form sha256:<64 lowercase hex characters>" -}}
{{- end -}}
{{- printf "%s@%s" $image.repository $image.digest -}}
{{- else -}}
{{- printf "%s:%s" $image.repository ($image.tag | default $root.Chart.AppVersion) -}}
{{- end -}}
{{- end -}}
