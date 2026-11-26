{{/*
R-8 (#144): the process-runs plane. One values block, processRuns.families, is the single source of every
ResourceFlavor, the ClusterQueue's quota, every runs NodePool and the backend.kubernetes block of every hardware
profile (plan docs/superpowers/plans/2026-11-26-r8-chart-process-runs.md, R8-R5).
*/}}

{{/* The runs taint and node label (fixed names, R-8 plan Global Constraints). */}}
{{- define "stac-higher.runsTaintKey" -}}stac-higher.io/runs{{- end -}}
{{- define "stac-higher.runsFlavorLabel" -}}stac-higher.io/flavor{{- end -}}

{{/*
Every family, keys defaulted to the cpu-default shape (absent keys only: a false stays false). JSON object
name -> family. quota and nodePool.requirements are never defaulted (validateRuns refuses them missing).
*/}}
{{- define "stac-higher.runsAllFamilies" -}}
{{- $out := dict -}}
{{- range $name, $raw := .Values.processRuns.families -}}
{{- $f := deepCopy ($raw | default dict) -}}
{{- range $k, $d := dict "enabled" true "dedicatedNodes" true "extraTaints" list "gpuResource" "" "runtimeClass" "" -}}
{{- if not (hasKey $f $k) -}}
{{- $_ := set $f $k $d -}}
{{- end -}}
{{- end -}}
{{- if and (hasKey $f "quota") (not (hasKey $f.quota "gpus")) -}}
{{- $_ := set $f.quota "gpus" 0 -}}
{{- end -}}
{{- $np := $f.nodePool | default dict -}}
{{- $head := $np.headroom | default dict -}}
{{- range $k := list "cpu" "memoryGi" -}}
{{- if not (hasKey $head $k) -}}
{{- $_ := set $head $k 0 -}}
{{- end -}}
{{- end -}}
{{- $_ := set $np "headroom" $head -}}
{{- $_ := set $f "nodePool" $np -}}
{{- $_ := set $out $name $f -}}
{{- end -}}
{{- toJson $out -}}
{{- end -}}

{{/* Enabled families only (JSON object name -> family). */}}
{{- define "stac-higher.runsFamilies" -}}
{{- $out := dict -}}
{{- range $name, $f := include "stac-higher.runsAllFamilies" . | fromJson -}}
{{- if $f.enabled -}}
{{- $_ := set $out $name $f -}}
{{- end -}}
{{- end -}}
{{- toJson $out -}}
{{- end -}}

{{/* Enabled family names, the default family first, then the rest sorted (JSON array). */}}
{{- define "stac-higher.runsFamiliesOrdered" -}}
{{- $fams := include "stac-higher.runsFamilies" . | fromJson -}}
{{- $def := .Values.processRuns.defaultFamily -}}
{{- $out := list -}}
{{- if hasKey $fams $def -}}
{{- $out = append $out $def -}}
{{- end -}}
{{- range $n := keys $fams | sortAlpha -}}
{{- if ne $n $def -}}
{{- $out = append $out $n -}}
{{- end -}}
{{- end -}}
{{- toJson $out -}}
{{- end -}}

{{/* The ClusterQueue's covered resources: cpu, memory, then each distinct gpuResource, sorted (JSON array). */}}
{{- define "stac-higher.runsCoveredResources" -}}
{{- $gpus := list -}}
{{- range $n, $f := include "stac-higher.runsFamilies" . | fromJson -}}
{{- if $f.gpuResource -}}
{{- $gpus = append $gpus $f.gpuResource -}}
{{- end -}}
{{- end -}}
{{- concat (list "cpu" "memory") ($gpus | uniq | sortAlpha) | toJson -}}
{{- end -}}

{{/* The runs toleration every dedicated family's pods carry (JSON object). */}}
{{- define "stac-higher.runsToleration" -}}
{{- dict "key" (include "stac-higher.runsTaintKey" .) "operator" "Equal" "value" "true" "effect" "NoSchedule" | toJson -}}
{{- end -}}

{{/* A family's taints as NodePool / ResourceFlavor taints: {key, value?, effect}. (family) -> JSON array */}}
{{- define "stac-higher.runsExtraTaints" -}}
{{- $out := list -}}
{{- range $t := .extraTaints -}}
{{- $e := dict "key" $t.key "effect" $t.effect -}}
{{- if $t.value -}}
{{- $_ := set $e "value" $t.value -}}
{{- end -}}
{{- $out = append $out $e -}}
{{- end -}}
{{- toJson $out -}}
{{- end -}}

{{/*
The backend.kubernetes block every profile of a family carries (JSON object; K-5's five keys).
(dict "root" $ "name" <family> "family" <normalised family>)
*/}}
{{- define "stac-higher.runsBackendBlock" -}}
{{- $f := .family -}}
{{- $sel := dict -}}
{{- $tols := list -}}
{{- if $f.dedicatedNodes -}}
{{- $_ := set $sel (include "stac-higher.runsFlavorLabel" .root) .name -}}
{{- $tols = append $tols (include "stac-higher.runsToleration" .root | fromJson) -}}
{{- end -}}
{{- range $t := $f.extraTaints -}}
{{- if $t.value -}}
{{- $tols = append $tols (dict "key" $t.key "operator" "Equal" "value" $t.value "effect" $t.effect) -}}
{{- else -}}
{{- $tols = append $tols (dict "key" $t.key "operator" "Exists" "effect" $t.effect) -}}
{{- end -}}
{{- end -}}
{{- $ext := dict -}}
{{- if $f.gpuResource -}}
{{- $_ := set $ext $f.gpuResource "gpu_count" -}}
{{- end -}}
{{- $rc := $f.runtimeClass | default nil -}}
{{- dict "queue" "process-runs" "node_selector" $sel "tolerations" $tols "runtime_class" $rc "extended_resources" $ext | toJson -}}
{{- end -}}

{{/*
Parse one CIDR. (string) -> JSON {ok, v6, octets: [a,b,c,d], bits}. IPv6 is recognised by a ':' and only its
prefix length is read.
*/}}
{{- define "stac-higher.runsCidr" -}}
{{- $s := toString . -}}
{{- if contains ":" $s -}}
{{- $m := regexFind "/[0-9]{1,3}$" $s -}}
{{- dict "ok" (not (empty $m)) "v6" true "bits" (trimPrefix "/" $m | default "-1" | atoi) | toJson -}}
{{- else if regexMatch "^[0-9]{1,3}\\.[0-9]{1,3}\\.[0-9]{1,3}\\.[0-9]{1,3}/[0-9]{1,2}$" $s -}}
{{- $parts := splitList "/" $s -}}
{{- $oct := list -}}
{{- $ok := true -}}
{{- range $o := splitList "." (index $parts 0) -}}
{{- if gt (atoi $o) 255 -}}
{{- $ok = false -}}
{{- end -}}
{{- $oct = append $oct (atoi $o) -}}
{{- end -}}
{{- $bits := atoi (index $parts 1) -}}
{{- dict "ok" (and $ok (le $bits 32)) "v6" false "octets" $oct "bits" $bits | toJson -}}
{{- else -}}
{{- dict "ok" false "v6" false | toJson -}}
{{- end -}}
{{- end -}}

{{/*
An object-store CIDR is narrow and never link-local: IPv4 only, prefix /8 or longer, first octet not 169
(IMDS 169.254.169.254, the Pod Identity agent 169.254.170.23). (dict "key" <path> "cidr" <string>)
*/}}
{{- define "stac-higher.runsCheckObjectStoreCidr" -}}
{{- $c := include "stac-higher.runsCidr" .cidr | fromJson -}}
{{- if $c.v6 -}}
{{- fail (printf "%s: %q is IPv6; IPv6 clusters are not supported for runs (docs/ISSUES.md I-191)" .key .cidr) -}}
{{- end -}}
{{- if not $c.ok -}}
{{- fail (printf "%s: %q is not an IPv4 CIDR (a.b.c.d/n)" .key .cidr) -}}
{{- end -}}
{{- if lt (int $c.bits) 8 -}}
{{- fail (printf "%s: %q is wider than /8; the runs' object-store egress must be the object store's own ranges" .key .cidr) -}}
{{- end -}}
{{- if eq (int (index $c.octets 0)) 169 -}}
{{- fail (printf "%s: %q may cover link-local (169.254.0.0/16: IMDS and the Pod Identity agent); no runs policy may allow it" .key .cidr) -}}
{{- end -}}
{{- end -}}

{{/* R-8 cross-field rules for processRuns. Every message starts with the offending key path. */}}
{{- define "stac-higher.validateRuns" -}}
{{- $v := .Values -}}
{{- $pr := $v.processRuns -}}
{{- if or (eq $pr.namespace .Release.Namespace) (eq $pr.namespace $v.identity.namespace) -}}
{{- fail (printf "processRuns.namespace %q must differ from the release namespace %q: the runs namespace holds nothing but runs (docs/ISSUES.md I-184)" $pr.namespace .Release.Namespace) -}}
{{- end -}}
{{- $all := include "stac-higher.runsAllFamilies" . | fromJson -}}
{{- $fams := include "stac-higher.runsFamilies" . | fromJson -}}
{{- $def := $pr.defaultFamily -}}
{{- if not (hasKey $fams $def) -}}
{{- fail (printf "processRuns.defaultFamily %q must name an enabled entry of processRuns.families (enabled: %s)" $def (keys $fams | sortAlpha | join ", ")) -}}
{{- end -}}
{{- if (get $fams $def).gpuResource -}}
{{- fail (printf "processRuns.defaultFamily %q is a GPU family (gpuResource %q); the default family takes CPU profiles and image scans" $def (get $fams $def).gpuResource) -}}
{{- end -}}
{{- $nodes := and $pr.clusterObjects $pr.nodes.enabled -}}
{{- $many := gt (len $fams) 1 -}}
{{- range $name := keys $fams | sortAlpha -}}
{{- $f := get $fams $name -}}
{{- $key := printf "processRuns.families.%s" $name -}}
{{- if not (and (hasKey $f "quota") (hasKey $f.quota "cpu") (hasKey $f.quota "memoryGi")) -}}
{{- fail (printf "%s.quota is required: {cpu, memoryGi, gpus} (integers), the family's Kueue nominal quota" $key) -}}
{{- end -}}
{{- if and (gt (int $f.quota.gpus) 0) (not $f.gpuResource) -}}
{{- fail (printf "%s.quota.gpus is %d but the family has no gpuResource; set gpuResource (e.g. nvidia.com/gpu) or gpus: 0" $key (int $f.quota.gpus)) -}}
{{- end -}}
{{- if and (not $f.dedicatedNodes) (or $many $nodes) -}}
{{- fail (printf "%s.dedicatedNodes must be true %s: the flavor label is what tells families' nodes apart" $key (ternary "with more than one enabled family" "while processRuns.nodes.enabled renders NodePools" $many)) -}}
{{- end -}}
{{- range $t := $f.extraTaints -}}
{{- if eq $t.key (include "stac-higher.runsTaintKey" $) -}}
{{- fail (printf "%s.extraTaints: %q is the runs taint, which dedicatedNodes already adds" $key $t.key) -}}
{{- end -}}
{{- end -}}
{{- $reqs := $f.nodePool.requirements | default list -}}
{{- range $r := $reqs -}}
{{- if has $r.key (list (include "stac-higher.runsFlavorLabel" $) "karpenter.sh/nodepool") -}}
{{- fail (printf "%s.nodePool.requirements: %q is set by the chart (the NodePool's template label), never a requirement" $key $r.key) -}}
{{- end -}}
{{- end -}}
{{- if and $nodes (not $reqs) -}}
{{- fail (printf "%s.nodePool.requirements is required while processRuns.nodes.enabled: the NodePool's Karpenter requirements (EKS Auto Mode labels)" $key) -}}
{{- end -}}
{{- end -}}
{{- $n := $pr.nodes -}}
{{- if lt (int $n.terminationGracePeriodHours) 25 -}}
{{- fail (printf "processRuns.nodes.terminationGracePeriodHours is %d; it must be >= 25 (a run lasts up to 24 h plus the Job's 10 min slack)" (int $n.terminationGracePeriodHours)) -}}
{{- end -}}
{{- if gt (add $n.expireAfterHours $n.terminationGracePeriodHours) 504 -}}
{{- fail (printf "processRuns.nodes.expireAfterHours (%d) + terminationGracePeriodHours (%d) exceeds 504 h, EKS Auto Mode's 21-day maximum" (int $n.expireAfterHours) (int $n.terminationGracePeriodHours)) -}}
{{- end -}}
{{- if and $nodes $n.nodeClass.create -}}
{{- if not $n.nodeClass.role -}}
{{- fail "processRuns.nodes.nodeClass.role is required with processRuns.nodes.nodeClass.create: the IAM role name of the runs nodes (it needs an EKS access entry; the cluster repo knows it)" -}}
{{- end -}}
{{- range $k := list "subnetSelectorTerms" "securityGroupSelectorTerms" -}}
{{- if not (get $n.nodeClass $k) -}}
{{- fail (printf "processRuns.nodes.nodeClass.%s is required with processRuns.nodes.nodeClass.create (the cluster repo knows the runs nodes' subnets and security groups)" $k) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- $net := $pr.network -}}
{{- if not $net.dns -}}
{{- fail "processRuns.network.dns is required: the NetworkPolicy peers of the cluster's DNS. EKS Auto Mode has no kube-dns Service: use [{ipBlock: {cidr: <service CIDR .10>/32}}]" -}}
{{- end -}}
{{- range $p := $net.dns -}}
{{- if hasKey $p "ipBlock" -}}
{{- $c := include "stac-higher.runsCidr" $p.ipBlock.cidr | fromJson -}}
{{- if or (not $c.ok) (and $c.v6 (ne (int $c.bits) 128)) (and (not $c.v6) (ne (int $c.bits) 32)) -}}
{{- fail (printf "processRuns.network.dns: ipBlock %q must be one address (/32, or /128 for IPv6): port 53 to a range is an exfiltration channel to any resolver in it" (toString $p.ipBlock.cidr)) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- if and (not $v.aws.s3PrefixListCidrs) (not $net.objectStore) -}}
{{- fail "aws.s3PrefixListCidrs and processRuns.network.objectStore are both empty: runs need at least one object-store peer (Terraform's helm_values carries the S3 prefix list; an in-cluster store goes in processRuns.network.objectStore)" -}}
{{- end -}}
{{- range $cidr := $v.aws.s3PrefixListCidrs -}}
{{- include "stac-higher.runsCheckObjectStoreCidr" (dict "key" "aws.s3PrefixListCidrs" "cidr" $cidr) -}}
{{- end -}}
{{- range $p := $net.objectStore -}}
{{- if hasKey $p "ipBlock" -}}
{{- include "stac-higher.runsCheckObjectStoreCidr" (dict "key" "processRuns.network.objectStore" "cidr" $p.ipBlock.cidr) -}}
{{- end -}}
{{- end -}}
{{- range $cidr := $net.scannerEgressExcept -}}
{{- $c := include "stac-higher.runsCidr" $cidr | fromJson -}}
{{- if or (not $c.ok) $c.v6 -}}
{{- fail (printf "processRuns.network.scannerEgressExcept: %q is not an IPv4 CIDR (a.b.c.d/n)" (toString $cidr)) -}}
{{- end -}}
{{- end -}}
{{- end -}}
