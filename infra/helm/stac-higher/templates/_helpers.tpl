{{/* Names */}}
{{- define "stac-higher.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "stac-higher.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/* Labels. Call with (dict "root" $ "component" "app"). */}}
{{- define "stac-higher.selectorLabels" -}}
app.kubernetes.io/name: {{ include "stac-higher.name" .root }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "stac-higher.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .root.Chart.Name .root.Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "stac-higher.selectorLabels" . }}
app.kubernetes.io/version: {{ .root.Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .root.Release.Service }}
{{- end -}}

{{/* Image keys: kept equal to IMAGE_KEYS in .github/scripts/release-manifest.mjs (a test asserts it). */}}
{{- define "stac-higher.imageKeys" -}}
["app", "pipeline", "titiler", "cube-server", "image-scanner", "process-runtime", "process-runtime-stactools", "auth-proxy"]
{{- end -}}

{{/* Upstream image reference. (dict "root" $ "key" "app") */}}
{{- define "stac-higher.imageRef" -}}
{{- $g := .root.Values.global -}}
{{- $digest := get ($g.imageDigests | default dict) .key | default "" -}}
{{- if $digest -}}
{{- printf "%s-%s@%s" $g.imageRepository .key $digest -}}
{{- else -}}
{{- printf "%s-%s:%s" $g.imageRepository .key (default .root.Chart.AppVersion $g.imageTag) -}}
{{- end -}}
{{- end -}}

{{/* Pulled form: the registry host is swapped for its mirror prefix when one is configured. */}}
{{- define "stac-higher.pullRef" -}}
{{- $ref := include "stac-higher.imageRef" . -}}
{{- $host := index (splitList "/" $ref) 0 -}}
{{- $mirrors := .root.Values.registry.mirrorPrefixes | default dict -}}
{{- $prefix := get $mirrors $host | default "" -}}
{{- if eq $host "ghcr.io" -}}
{{- $prefix = .root.Values.global.imageRegistry | default $prefix -}}
{{- end -}}
{{- if $prefix -}}
{{- printf "%s%s" (trimSuffix "/" $prefix) (trimPrefix $host $ref) -}}
{{- else -}}
{{- $ref -}}
{{- end -}}
{{- end -}}

{{/* Container image: always the pulled form. */}}
{{- define "stac-higher.image" -}}
{{- include "stac-higher.pullRef" . -}}
{{- end -}}

{{/* ServiceAccount name, verbatim from identity.serviceAccounts (never release-derived). */}}
{{- define "stac-higher.serviceAccountName" -}}
{{- get .root.Values.identity.serviceAccounts .component -}}
{{- end -}}

{{/* Hook annotations for Helm, Argo CD and Flux. (dict "weight" -30) */}}
{{- define "stac-higher.hookAnnotations" -}}
helm.sh/hook: pre-install,pre-upgrade
helm.sh/hook-weight: {{ .weight | quote }}
helm.sh/hook-delete-policy: before-hook-creation
argocd.argoproj.io/hook: PreSync
argocd.argoproj.io/sync-wave: {{ .weight | quote }}
argocd.argoproj.io/hook-delete-policy: BeforeHookCreation
{{- end -}}

{{/* Pod security. (dict "uid" 1000) */}}
{{- define "stac-higher.podSecurityContext" -}}
runAsNonRoot: true
runAsUser: {{ .uid }}
runAsGroup: {{ .uid }}
fsGroup: {{ .uid }}
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{/* Container security. (dict "readOnly" true) */}}
{{- define "stac-higher.containerSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: {{ .readOnly }}
capabilities:
  drop:
    - ALL
{{- end -}}

{{/* Host of publicUrls.app, trailing slash trimmed. */}}
{{- define "stac-higher.appHost" -}}
{{- (urlParse (trimSuffix "/" .Values.publicUrls.app)).host -}}
{{- end -}}

{{/* Cross-field rules. Every message starts with the offending key path. */}}
{{- define "stac-higher.validate" -}}
{{- $v := .Values -}}
{{- if ne .Release.Namespace $v.identity.namespace -}}
{{- fail (printf "identity.namespace is %q but the release namespace is %q; they must match (the Pod Identity associations name it)" $v.identity.namespace .Release.Namespace) -}}
{{- end -}}
{{- if and (eq $v.auth.mode "bypass") (not $v.auth.allowDevBypass) -}}
{{- fail "auth.allowDevBypass must be true to render auth.mode=bypass" -}}
{{- end -}}
{{- if eq $v.auth.mode "oidc" -}}
{{- if not $v.auth.issuer -}}
{{- fail "auth.issuer is required when auth.mode=oidc" -}}
{{- end -}}
{{- if not $v.auth.clientId -}}
{{- fail "auth.clientId is required when auth.mode=oidc" -}}
{{- end -}}
{{- end -}}
{{- if eq $v.identity.mode "irsa" -}}
{{- range $c := list "app" "pipeline" -}}
{{- if not (get $v.identity.roleArns $c) -}}
{{- fail (printf "identity.roleArns.%s is required when identity.mode=irsa" $c) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- $u := urlParse (trimSuffix "/" $v.publicUrls.app) -}}
{{- if or $u.path $u.query $u.fragment -}}
{{- fail (printf "publicUrls.app must be a bare origin without a path, query or fragment, got %q" $v.publicUrls.app) -}}
{{- end -}}
{{- if eq $v.database.sslMode "verify-full" -}}
{{- $cm := not (not $v.database.caBundle.existingConfigMap) -}}
{{- $pem := not (not $v.database.caBundle.pem) -}}
{{- if eq $cm $pem -}}
{{- fail "database.caBundle needs exactly one of existingConfigMap or pem when database.sslMode=verify-full" -}}
{{- end -}}
{{- end -}}
{{- $keys := include "stac-higher.imageKeys" . | fromJsonArray -}}
{{- $digests := $v.global.imageDigests | default dict -}}
{{- range $k := keys $digests | sortAlpha -}}
{{- if not (has $k $keys) -}}
{{- fail (printf "global.imageDigests.%s is not a known image key (known: %s)" $k (join ", " $keys)) -}}
{{- end -}}
{{- if not (regexMatch "^sha256:[0-9a-f]{64}$" (toString (get $digests $k))) -}}
{{- fail (printf "global.imageDigests.%s must match sha256:<64 hex>" $k) -}}
{{- end -}}
{{- end -}}
{{- if $digests -}}
{{- range $k := list "app" "pipeline" "image-scanner" "process-runtime" "process-runtime-stactools" -}}
{{- if not (hasKey $digests $k) -}}
{{- fail (printf "global.imageDigests.%s is missing: when any digest is set, every referenced image needs one" $k) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
