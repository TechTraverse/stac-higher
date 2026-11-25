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
{{- include "stac-higher.mirrorRef" (dict "root" .root "ref" (include "stac-higher.imageRef" .)) -}}
{{- end -}}

{{/* Any image ref in its pulled form (also the third-party catalog images). (dict "root" $ "ref" <ref>) */}}
{{- define "stac-higher.mirrorRef" -}}
{{- $ref := .ref -}}
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

{{/* Hook annotations for Helm, Argo CD and Flux. (dict "weight" -30 "deleteOnSuccess" bool) */}}
{{- define "stac-higher.hookAnnotations" -}}
helm.sh/hook: pre-install,pre-upgrade
helm.sh/hook-weight: {{ .weight | quote }}
helm.sh/hook-delete-policy: {{ ternary "before-hook-creation,hook-succeeded" "before-hook-creation" (default false .deleteOnSuccess) }}
argocd.argoproj.io/hook: PreSync
argocd.argoproj.io/sync-wave: {{ .weight | quote }}
argocd.argoproj.io/hook-delete-policy: {{ ternary "BeforeHookCreation,HookSucceeded" "BeforeHookCreation" (default false .deleteOnSuccess) }}
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

{{/* Hostname of publicUrls.app: trailing slash trimmed and no port (an Ingress host must not carry one). */}}
{{- define "stac-higher.appHost" -}}
{{- (urlParse (trimSuffix "/" .Values.publicUrls.app)).hostname -}}
{{- end -}}

{{/* Hook-copy Secret names in external-secrets mode, by logical name. */}}
{{- define "stac-higher.hookSecretSuffix" -}}
{{- $m := dict "dbMaster" "hook-db-master" "dbOwner" "hook-db-owner" "dbStacApi" "hook-db-stac-api" "dbStacReader" "hook-db-stac-reader" "dbProxyPolicy" "hook-db-proxy-policy" "dbCubeServer" "hook-db-cube-server" -}}
{{- get $m .name -}}
{{- end -}}

{{/* Kubernetes Secret name the pre-install hooks read. (dict "root" $ "name" "dbMaster") */}}
{{- define "stac-higher.hookSecretName" -}}
{{- if eq .root.Values.secrets.mode "existing" -}}
{{- get .root.Values.secrets.existing .name -}}
{{- else -}}
{{- printf "%s-%s" (include "stac-higher.fullname" .root) (include "stac-higher.hookSecretSuffix" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{/* Runtime Secret names in external-secrets mode, by logical name (R-7 added the four service roles). */}}
{{- define "stac-higher.runtimeSecretSuffix" -}}
{{- $m := dict "platform" "platform" "dbOwner" "db-owner" "dbStacApi" "db-stac-api" "dbStacReader" "db-stac-reader" "dbProxyPolicy" "db-proxy-policy" "dbCubeServer" "db-cube-server" -}}
{{- get $m .name -}}
{{- end -}}

{{/* Kubernetes Secret name the runtime pods read; "" for an unset dockerHub.
In external-secrets mode every runtime pod reads a runtime copy, never a hook copy (I-156); only dbMaster,
which no runtime pod reads, resolves to its hook copy. (dict "root" $ "name" "platform") */}}
{{- define "stac-higher.secretName" -}}
{{- $suffix := include "stac-higher.runtimeSecretSuffix" . -}}
{{- if eq .root.Values.secrets.mode "existing" -}}
{{- get .root.Values.secrets.existing .name -}}
{{- else if $suffix -}}
{{- printf "%s-%s" (include "stac-higher.fullname" .root) $suffix | trunc 63 | trimSuffix "-" -}}
{{- else if eq .name "dockerHub" -}}
{{- if .root.Values.secrets.dockerHubArn -}}
{{- printf "%s-dockerhub" (include "stac-higher.fullname" .root) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- else -}}
{{- include "stac-higher.hookSecretName" . -}}
{{- end -}}
{{- end -}}

{{/*
One ExternalSecret. (dict "root" $ "name" <k8s Secret name> "remoteKey" <key> "json" bool "keys" <list> "hook" bool "deleteOnSuccess" bool "refreshInterval" <string>)
keys items are strings or {key, remoteKey}. With json, each string key is both the Secret key and the
remoteRef.property of remoteKey. Without json, an item is {key, remoteKey} and maps the whole remote value.
*/}}
{{- define "stac-higher.externalSecret" -}}
{{- $r := .root -}}
{{- $es := $r.Values.secrets.externalSecrets -}}
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: {{ .name }}
  namespace: {{ $r.Release.Namespace }}
  labels:
    {{- include "stac-higher.labels" (dict "root" $r "component" (ternary "db-hook" "secrets" (default false .hook))) | nindent 4 }}
  {{- if .hook }}
  annotations:
    {{- include "stac-higher.hookAnnotations" (dict "weight" -30 "deleteOnSuccess" (default false .deleteOnSuccess)) | nindent 4 }}
  {{- end }}
spec:
  refreshInterval: {{ default $es.refreshInterval .refreshInterval | quote }}
  secretStoreRef:
    name: {{ $es.storeRef.name | quote }}
    kind: {{ $es.storeRef.kind }}
  target:
    name: {{ .name }}
    creationPolicy: Owner
    deletionPolicy: Retain
  data:
    {{- $json := .json -}}
    {{- $remoteKey := .remoteKey -}}
    {{- range $k := .keys }}
    {{- $secretKey := $k -}}
    {{- $key := $remoteKey -}}
    {{- if kindIs "map" $k -}}
    {{- $secretKey = $k.key -}}
    {{- $key = $k.remoteKey -}}
    {{- end }}
    - secretKey: {{ $secretKey }}
      remoteRef:
        key: {{ $key | quote }}
        {{- if $json }}
        property: {{ $secretKey }}
        {{- end }}
        conversionStrategy: Default
        decodingStrategy: None
        metadataPolicy: None
    {{- end }}
{{- end -}}

{{/* Cross-field rules. Every message starts with the offending key path. */}}
{{- define "stac-higher.validate" -}}
{{- $v := .Values -}}
{{- $full := include "stac-higher.fullname" . -}}
{{- if gt (len $full) 42 -}}
{{- fail (printf "fullname %q is %d characters; the maximum is 42 (the longest appended suffix, -hook-db-proxy-policy, is 21 and object names stop at 63; shorten the release name or set fullnameOverride)" $full (len $full)) -}}
{{- end -}}
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
{{- range $c := list "app" "pipeline" "titiler" "cubeServer" -}}
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
{{- if eq $v.secrets.mode "external-secrets" -}}
{{- if not $v.secrets.externalSecrets.storeRef.name -}}
{{- fail "secrets.externalSecrets.storeRef.name is required when secrets.mode=external-secrets" -}}
{{- end -}}
{{- range $k := list "db_stac_higher_owner" "db_stac_api" "db_stac_reader" "db_stac_proxy_policy" "db_cube_server" "session_secret" "catalog_bff_shared_secret" "internal_api_token" "credentials_master_key" -}}
{{- if not (get $v.secrets.arns $k) -}}
{{- fail (printf "secrets.arns.%s is required when secrets.mode=external-secrets" $k) -}}
{{- end -}}
{{- end -}}
{{- if not $v.database.masterSecretArn -}}
{{- fail "database.masterSecretArn is required when secrets.mode=external-secrets" -}}
{{- end -}}
{{- else -}}
{{- range $k := list "platform" "dbMaster" "dbOwner" "dbStacApi" "dbStacReader" "dbProxyPolicy" "dbCubeServer" -}}
{{- if not (get $v.secrets.existing $k) -}}
{{- fail (printf "secrets.existing.%s is required when secrets.mode=existing" $k) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- if not (kindIs "invalid" $v.aws.processMaxCredentialSeconds) -}}
{{- $doc := include "stac-higher.hardwareProfilesJson" . | fromJson -}}
{{- range $p := $doc.profiles -}}
{{- $wait := int $p.max_queue_wait_seconds -}}
{{- if ge (add $wait (int $v.processRuns.credentialGraceSeconds)) (int $v.aws.processMaxCredentialSeconds) -}}
{{- fail (printf "config.hardwareProfiles: profile %q has max_queue_wait_seconds %d, which with the %ds grace leaves no run time under aws.processMaxCredentialSeconds %d" $p.id $wait (int $v.processRuns.credentialGraceSeconds) (int $v.aws.processMaxCredentialSeconds)) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- range $c := list "app" "pipeline" -}}
{{- range $k := keys ((get $v $c).env | default dict) | sortAlpha -}}
{{- if regexMatch "(SECRET|TOKEN|PASSWORD|_KEY$|_KEY_ID$)" (upper $k) -}}
{{- fail (printf "%s.env.%s looks like a secret; env values are rendered into a ConfigMap, so wire secrets through the secrets layer instead" $c $k) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- range $k := list "AUTH_MODE" "AUTH_BYPASS_FORCE" "DEV_AUTH_IDENTITY" -}}
{{- if hasKey ($v.app.env | default dict) $k -}}
{{- fail (printf "app.env.%s is not allowed: it would bypass the dev-bypass guard; use auth.mode, auth.allowDevBypass and auth.devIdentity instead" $k) -}}
{{- end -}}
{{- end -}}
{{- if and $v.pipeline.metrics.serviceMonitor.enabled $v.networkPolicy.enabled (not $v.networkPolicy.metricsFrom) -}}
{{- fail "pipeline.metrics.serviceMonitor.enabled with networkPolicy.enabled needs networkPolicy.metricsFrom (the Prometheus peers), or default-deny blocks scraping" -}}
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
{{- include "stac-higher.validateCatalog" . -}}
{{- if $digests -}}
{{- range $k := list "app" "pipeline" "image-scanner" "process-runtime" "process-runtime-stactools" "titiler" "cube-server" "auth-proxy" -}}
{{- if not (hasKey $digests $k) -}}
{{- fail (printf "global.imageDigests.%s is missing: when any digest is set, every referenced image needs one" $k) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/* Hardware-profile document: the deployment's, else the chart's eks-cpu set. Pretty JSON. */}}
{{- define "stac-higher.hardwareProfilesJson" -}}
{{- if .Values.config.hardwareProfiles -}}
{{- .Values.config.hardwareProfiles | toPrettyJson -}}
{{- else -}}
{{- .Files.Get "files/hardware-profiles/eks-cpu.json" | trim -}}
{{- end -}}
{{- end -}}

{{/* Image-policy document: the deployment's, else the chart's copy of infra/image-policy/default.json. */}}
{{- define "stac-higher.imagePolicyJson" -}}
{{- if .Values.config.imagePolicy -}}
{{- .Values.config.imagePolicy | toPrettyJson -}}
{{- else -}}
{{- .Files.Get "files/image-policy/default.json" | trim -}}
{{- end -}}
{{- end -}}

{{/* CA volume, or nothing when database.sslMode=disable. (dict "root" $ "hook" bool)
With existingConfigMap both modes use it (items maps its key to ca-bundle.pem). With the chart-owned bundle
(caBundle.pem): hook=true -> <fullname>-hook-db-ca (rendered by the hooks), hook=false -> <fullname>-db-ca. */}}
{{- define "stac-higher.caVolume" -}}
{{- $d := .root.Values.database -}}
{{- if eq $d.sslMode "verify-full" -}}
name: db-ca
configMap:
  {{- if $d.caBundle.existingConfigMap }}
  name: {{ $d.caBundle.existingConfigMap }}
  items:
    - key: {{ $d.caBundle.key }}
      path: ca-bundle.pem
  {{- else }}
  name: {{ include "stac-higher.fullname" .root }}-{{ ternary "hook-db-ca" "db-ca" (default false .hook) }}
  items:
    - key: ca-bundle.pem
      path: ca-bundle.pem
  {{- end }}
{{- end -}}
{{- end -}}

{{/* Matching mount, or nothing when database.sslMode=disable. (dict "root" $) */}}
{{- define "stac-higher.caVolumeMount" -}}
{{- if eq .root.Values.database.sslMode "verify-full" -}}
name: db-ca
mountPath: /etc/stac-higher/db-ca
readOnly: true
{{- end -}}
{{- end -}}

{{/* Rolls pods when the config changes: sha256 of the rendered config ConfigMaps. */}}
{{- define "stac-higher.configChecksum" -}}
{{- $b := .Template.BasePath -}}
{{- cat (include (print $b "/config-files.yaml") .) (include (print $b "/config-env.yaml") .) (include (print $b "/db-ca.yaml") .) | sha256sum -}}
{{- end -}}

{{/* DB credentials from a Secret's username/password keys, never from a URL. (dict "secret" <name>) */}}
{{- define "stac-higher.dbCredentialEnv" -}}
- name: PGUSER
  valueFrom:
    secretKeyRef:
      name: {{ .secret }}
      key: username
- name: PGPASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .secret }}
      key: password
{{- end -}}

{{/* Env for a db hook container: libpq PG* from the chart's database settings and one Secret.
(dict "root" $ "secret" <name>). HOME and PYTHONDONTWRITEBYTECODE suit the Python containers and are harmless to node. */}}
{{- define "stac-higher.dbJobEnv" -}}
{{- $d := .root.Values.database -}}
- name: PGHOST
  value: {{ $d.host | quote }}
- name: PGPORT
  value: {{ $d.port | toString | quote }}
- name: PGDATABASE
  value: {{ $d.name | quote }}
{{- if eq $d.sslMode "verify-full" }}
- name: PGSSLMODE
  value: verify-full
- name: PGSSLROOTCERT
  value: /etc/stac-higher/db-ca/ca-bundle.pem
{{- end }}
{{ include "stac-higher.dbCredentialEnv" (dict "secret" .secret) }}
- name: HOME
  value: /tmp
- name: PYTHONDONTWRITEBYTECODE
  value: "1"
{{- end -}}

{{/* One run_sql.py setting from a role Secret. (dict "name" "API" "secret" <name> "keys" (list "username" "password")) */}}
{{- define "stac-higher.dbSqlEnv" -}}
{{- $n := .name -}}
{{- $s := .secret -}}
{{- range $k := .keys }}
- name: STAC_HIGHER_SQL_{{ $n }}_{{ upper $k | replace "USERNAME" "ROLE" }}
  valueFrom:
    secretKeyRef:
      name: {{ $s }}
      key: {{ $k }}
{{- end }}
{{- end -}}

{{/* Hook Job pod pieces shared by the three db Jobs. */}}
{{- define "stac-higher.dbJobVolumes" -}}
- name: db-sql
  configMap:
    name: {{ include "stac-higher.fullname" .root }}-db-sql
{{- with include "stac-higher.caVolume" (dict "root" .root "hook" true) }}
- {{- . | nindent 2 }}
{{- end }}
- name: tmp
  emptyDir:
    sizeLimit: 64Mi
{{- end -}}

{{- define "stac-higher.dbJobMounts" -}}
- name: db-sql
  mountPath: /etc/stac-higher/db
  readOnly: true
{{- with include "stac-higher.caVolumeMount" (dict "root" .root) }}
- {{- . | nindent 2 }}
{{- end }}
- name: tmp
  mountPath: /tmp
{{- end -}}

{{- define "stac-higher.dbJobScheduling" -}}
{{- $j := .root.Values.dbJobs -}}
{{- with $j.nodeSelector }}
nodeSelector:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with $j.tolerations }}
tolerations:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with $j.affinity }}
affinity:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{/* R-7 cross-field rules for the catalog plane and the cube-server. */}}
{{- define "stac-higher.validateCatalog" -}}
{{- $v := .Values -}}
{{- if not $v.auth.issuer -}}
{{- fail "auth.issuer is required in every auth mode: the catalog auth-proxy needs its OIDC discovery URL" -}}
{{- end -}}
{{- $hosts := dict -}}
{{- range $k := list "app" "catalog" "titiler" "tipg" "cubeServer" -}}
{{- $raw := get $v.publicUrls $k -}}
{{- $u := urlParse (trimSuffix "/" $raw) -}}
{{- if or $u.path $u.query $u.fragment -}}
{{- fail (printf "publicUrls.%s must be a bare origin without a path, query or fragment, got %q" $k $raw) -}}
{{- end -}}
{{- if hasKey $hosts $u.hostname -}}
{{- fail (printf "publicUrls.%s shares host %q with publicUrls.%s; every public service needs its own host" $k $u.hostname (get $hosts $u.hostname)) -}}
{{- end -}}
{{- $_ := set $hosts $u.hostname $k -}}
{{- end -}}
{{- range $k := list "stacApi" "tipg" -}}
{{- $img := (get $v.catalog $k).image -}}
{{- if not (regexMatch "@sha256:[0-9a-f]{64}$" $img) -}}
{{- fail (printf "catalog.%s.image must be pinned by digest (<ref>@sha256:<64 hex>), got %q" $k $img) -}}
{{- end -}}
{{- end -}}
{{- range $k := list "stacApi" "authProxy" "titiler" "tipg" -}}
{{- range $e := keys ((get $v.catalog $k).env | default dict) | sortAlpha -}}
{{- if regexMatch "(SECRET|TOKEN|PASSWORD|_PASS$|_KEY$|_KEY_ID$)" (upper $e) -}}
{{- fail (printf "catalog.%s.env.%s looks like a secret; env values are rendered into a ConfigMap, so wire secrets through the secrets layer instead" $k $e) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- range $e := keys ($v.cubeServer.env | default dict) | sortAlpha -}}
{{- if regexMatch "(SECRET|TOKEN|PASSWORD|_PASS$|_KEY$|_KEY_ID$)" (upper $e) -}}
{{- fail (printf "cubeServer.env.%s looks like a secret; env values are rendered into a ConfigMap, so wire secrets through the secrets layer instead" $e) -}}
{{- end -}}
{{- end -}}
{{- if and (not $v.catalog.authProxy.rolesClaim) $v.auth.claimsMapping (hasKey $v.auth.claimsMapping "roles") (not (kindIs "string" $v.auth.claimsMapping.roles)) -}}
{{- fail "catalog.authProxy.rolesClaim must be set: auth.claimsMapping.roles is not a single claim path" -}}
{{- end -}}
{{- end -}}

{{/* POLICY_ROLES_CLAIM for the proxy write policy (ADR 0015): explicit, else the app's roles claim path, else Keycloak's. */}}
{{- define "stac-higher.proxyRolesClaim" -}}
{{- $v := .Values -}}
{{- $m := $v.auth.claimsMapping | default dict -}}
{{- if $v.catalog.authProxy.rolesClaim -}}
{{- $v.catalog.authProxy.rolesClaim -}}
{{- else if and (hasKey $m "roles") (kindIs "string" $m.roles) -}}
{{- $m.roles -}}
{{- else -}}
realm_access.roles
{{- end -}}
{{- end -}}

{{/* POLICY_WRITE_ROLES: explicit, else every raw role value the app maps to operator or admin (claims.ts: a roleMap
entry maps a raw value; an unmapped raw value passes through and survives only as member/operator/admin). */}}
{{- define "stac-higher.proxyWriteRoles" -}}
{{- $v := .Values -}}
{{- if $v.catalog.authProxy.writeRoles -}}
{{- join "," $v.catalog.authProxy.writeRoles -}}
{{- else -}}
{{- $roleMap := (($v.auth.claimsMapping | default dict).roleMap) | default dict -}}
{{- $out := list -}}
{{- range $raw, $role := $roleMap -}}
{{- if has $role (list "operator" "admin") -}}
{{- $out = append $out $raw -}}
{{- end -}}
{{- end -}}
{{- range $r := list "operator" "admin" -}}
{{- if not (hasKey $roleMap $r) -}}
{{- $out = append $out $r -}}
{{- end -}}
{{- end -}}
{{- $out | uniq | sortAlpha | join "," -}}
{{- end -}}
{{- end -}}

{{/* Rolls the catalog workloads when their config changes. */}}
{{- define "stac-higher.catalogChecksum" -}}
{{- $b := .Template.BasePath -}}
{{- cat (include (print $b "/catalog-config.yaml") .) (include (print $b "/db-ca.yaml") .) | sha256sum -}}
{{- end -}}

{{/*
One catalog-plane or cube-server Deployment + Service (R-7). (dict "root" $ "component" <label> "values" <section>
"image" <pulled ref> "port" N "uid" N "gid" N "serviceAccount" <name or ""> "command" <list or nil>
"readinessPath" <path> "tmpSize" <quantity> "env" <list of env entries>)
No ServiceAccount -> the namespace default; every pod runs without an API token.
*/}}
{{- define "stac-higher.catalogDeployment" -}}
{{- $r := .root -}}
{{- $s := .values -}}
{{- $full := include "stac-higher.fullname" $r -}}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ $full }}-{{ .component }}
  namespace: {{ $r.Release.Namespace }}
  labels:
    {{- include "stac-higher.labels" (dict "root" $r "component" .component) | nindent 4 }}
spec:
  replicas: {{ $s.replicas }}
  selector:
    matchLabels:
      {{- include "stac-higher.selectorLabels" (dict "root" $r "component" .component) | nindent 6 }}
  template:
    metadata:
      labels:
        {{- include "stac-higher.labels" (dict "root" $r "component" .component) | nindent 8 }}
      annotations:
        {{- $ann := merge (deepCopy ($s.podAnnotations | default dict)) (dict "checksum/config" (include "stac-higher.catalogChecksum" $r)) }}
        {{- toYaml $ann | nindent 8 }}
    spec:
      {{- with .serviceAccount }}
      serviceAccountName: {{ . }}
      {{- end }}
      automountServiceAccountToken: false
      securityContext:
        runAsNonRoot: true
        runAsUser: {{ .uid }}
        runAsGroup: {{ .gid }}
        fsGroup: {{ .gid }}
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: {{ .component }}
          image: {{ .image | quote }}
          {{- with .command }}
          command:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          ports:
            - name: http
              containerPort: {{ .port }}
              protocol: TCP
          envFrom:
            - configMapRef:
                name: {{ $full }}-env-{{ .component }}
          env:
            {{- toYaml .env | nindent 12 }}
          readinessProbe:
            httpGet:
              path: {{ .readinessPath }}
              port: http
            initialDelaySeconds: 5
            periodSeconds: 10
            timeoutSeconds: 5
          livenessProbe:
            tcpSocket:
              port: http
            initialDelaySeconds: 20
            periodSeconds: 20
          resources:
            {{- toYaml $s.resources | nindent 12 }}
          securityContext:
            {{- include "stac-higher.containerSecurityContext" (dict "readOnly" true) | nindent 12 }}
          volumeMounts:
            {{- with include "stac-higher.caVolumeMount" (dict "root" $r) }}
            - {{- . | nindent 14 }}
            {{- end }}
            - name: tmp
              mountPath: /tmp
      volumes:
        {{- with include "stac-higher.caVolume" (dict "root" $r "hook" false) }}
        - {{- . | nindent 10 }}
        {{- end }}
        - name: tmp
          emptyDir:
            sizeLimit: {{ .tmpSize }}
      {{- with $s.nodeSelector }}
      nodeSelector:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with $s.tolerations }}
      tolerations:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with $s.affinity }}
      affinity:
        {{- toYaml . | nindent 8 }}
      {{- end }}
---
apiVersion: v1
kind: Service
metadata:
  name: {{ $full }}-{{ .component }}
  namespace: {{ $r.Release.Namespace }}
  labels:
    {{- include "stac-higher.labels" (dict "root" $r "component" .component) | nindent 4 }}
spec:
  type: ClusterIP
  selector:
    {{- include "stac-higher.selectorLabels" (dict "root" $r "component" .component) | nindent 4 }}
  ports:
    - name: http
      port: {{ .port }}
      targetPort: http
      protocol: TCP
{{- end -}}

{{/* secretKeyRef env pairs. (dict "secret" <name> "pairs" (list (list ENV key) ...)) */}}
{{- define "stac-higher.secretEnv" -}}
{{- $s := .secret -}}
{{- range $p := .pairs }}
- name: {{ index $p 0 }}
  valueFrom:
    secretKeyRef:
      name: {{ $s }}
      key: {{ index $p 1 }}
{{- end }}
{{- end -}}
