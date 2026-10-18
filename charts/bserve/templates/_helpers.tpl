{{- define "bserve.fullname" -}}
{{- .Release.Name | trunc 50 | trimSuffix "-" -}}
{{- end -}}

{{- define "bserve.selectorLabels" -}}
app.kubernetes.io/name: bserve
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "bserve.labels" -}}
{{ include "bserve.selectorLabels" . }}
app.kubernetes.io/version: {{ include "bserve.tag" . | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "bserve.tag" -}}
{{- required "image.tag must be set to a git commit SHA" .Values.image.tag -}}
{{- end -}}

{{- define "bserve.image" -}}
{{ .Values.image.repository }}:{{ include "bserve.tag" . }}
{{- end -}}

{{- define "bserve.restrictedContainer" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: ["ALL"]
{{- end -}}

{{- define "bserve.restrictedPod" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
seccompProfile:
  type: RuntimeDefault
{{- end -}}
