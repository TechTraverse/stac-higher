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
Parse one CIDR. (string) -> JSON {ok, v6, octets: [a,b,c,d], bits, int}: int is the address as an integer. IPv6
is recognised by a ':' and only its prefix length is read (IPv6 is refused for runs, I-191).
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
{{- $int := 0 -}}
{{- range $o := splitList "." (index $parts 0) -}}
{{- if gt (atoi $o) 255 -}}
{{- $ok = false -}}
{{- end -}}
{{- $oct = append $oct (atoi $o) -}}
{{- $int = add (mul $int 256) (atoi $o) -}}
{{- end -}}
{{- $bits := atoi (index $parts 1) -}}
{{- dict "ok" (and $ok (le $bits 32)) "v6" false "octets" $oct "bits" $bits "int" $int | toJson -}}
{{- else -}}
{{- dict "ok" false "v6" false | toJson -}}
{{- end -}}
{{- end -}}

{{/* True ("true") when two parsed IPv4 CIDRs share any address, else "". (dict "a" <parsed> "b" <parsed>) */}}
{{- define "stac-higher.runsCidrOverlap" -}}
{{- $p := min (int .a.bits) (int .b.bits) -}}
{{- $size := 1 -}}
{{- range until (int (sub 32 $p)) -}}
{{- $size = mul $size 2 -}}
{{- end -}}
{{- if eq (div (int64 .a.int) $size) (div (int64 .b.int) $size) -}}true{{- end -}}
{{- end -}}

{{/* IPv4 space no runs policy may reach unless named explicitly: [cidr, what] pairs (JSON). */}}
{{- define "stac-higher.runsNeverCidrs" -}}
{{- list (list "0.0.0.0/8" "this-network") (list "127.0.0.0/8" "loopback") (list "169.254.0.0/16" "link-local: IMDS and the Pod Identity agent") | toJson -}}
{{- end -}}
{{- define "stac-higher.runsPrivateCidrs" -}}
{{- list (list "10.0.0.0/8" "RFC 1918") (list "172.16.0.0/12" "RFC 1918") (list "192.168.0.0/16" "RFC 1918") (list "100.64.0.0/10" "CGNAT") | toJson -}}
{{- end -}}

{{/*
The scanner's 443 rule always carves these out of 0.0.0.0/0 (lead ruling on R-8): link-local, RFC 1918 and
CGNAT. Constants, not values: processRuns.network.scannerEgressExcept only adds to them, and a private
destination a scan needs is named in processRuns.network.scannerPrivateCidrs. (JSON array)
*/}}
{{- define "stac-higher.runsScannerExcept" -}}
{{- list "169.254.0.0/16" "10.0.0.0/8" "172.16.0.0/12" "192.168.0.0/16" "100.64.0.0/10" | toJson -}}
{{- end -}}

{{/*
One CIDR a runs allow policy will admit. IPv4 only (IPv6 fails closed, I-191); never this-network, loopback or
link-local; a public range needs a prefix of at least .publicMin; a range overlapping RFC 1918 or CGNAT space is
refused unless .privateMin is set, and then needs a prefix of at least .privateMin.
(dict "key" <path> "cidr" <string> "publicMin" N "privateMin" N|0)
*/}}
{{- define "stac-higher.runsCheckCidr" -}}
{{- $c := include "stac-higher.runsCidr" .cidr | fromJson -}}
{{- if $c.v6 -}}
{{- fail (printf "%s: %q is IPv6; IPv6 clusters are not supported for runs (docs/ISSUES.md I-191)" .key (toString .cidr)) -}}
{{- end -}}
{{- if not $c.ok -}}
{{- fail (printf "%s: %q is not an IPv4 CIDR (a.b.c.d/n)" .key (toString .cidr)) -}}
{{- end -}}
{{- range $r := include "stac-higher.runsNeverCidrs" . | fromJsonArray -}}
{{- if include "stac-higher.runsCidrOverlap" (dict "a" $c "b" (include "stac-higher.runsCidr" (index $r 0) | fromJson)) -}}
{{- fail (printf "%s: %q overlaps %s (%s); no runs policy may allow it" $.key (toString $.cidr) (index $r 0) (index $r 1)) -}}
{{- end -}}
{{- end -}}
{{- $private := "" -}}
{{- range $r := include "stac-higher.runsPrivateCidrs" . | fromJsonArray -}}
{{- if include "stac-higher.runsCidrOverlap" (dict "a" $c "b" (include "stac-higher.runsCidr" (index $r 0) | fromJson)) -}}
{{- $private = printf "%s (%s)" (index $r 0) (index $r 1) -}}
{{- end -}}
{{- end -}}
{{- if $private -}}
{{- if not .privateMin -}}
{{- fail (printf "%s: %q overlaps %s; this list holds public ranges only" .key (toString .cidr) $private) -}}
{{- end -}}
{{- if lt (int $c.bits) (int .privateMin) -}}
{{- fail (printf "%s: %q overlaps %s and is wider than /%d; name the private destinations themselves" .key (toString .cidr) $private (int .privateMin)) -}}
{{- end -}}
{{- else if lt (int $c.bits) (int .publicMin) -}}
{{- fail (printf "%s: %q is wider than /%d; name the destination's own ranges" .key (toString .cidr) (int .publicMin)) -}}
{{- end -}}
{{- end -}}

{{/* processRuns.defaultFamily names an enabled CPU family (checked before anything resolves a profile to it). */}}
{{- define "stac-higher.runsCheckDefaultFamily" -}}
{{- $fams := include "stac-higher.runsFamilies" . | fromJson -}}
{{- $def := .Values.processRuns.defaultFamily -}}
{{- if not (hasKey $fams $def) -}}
{{- fail (printf "processRuns.defaultFamily %q must name an enabled entry of processRuns.families (enabled: %s)" $def (keys $fams | sortAlpha | join ", ")) -}}
{{- end -}}
{{- if (get $fams $def).gpuResource -}}
{{- fail (printf "processRuns.defaultFamily %q is a GPU family (gpuResource %q); the default family takes CPU profiles and image scans" $def (get $fams $def).gpuResource) -}}
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
{{- include "stac-higher.runsCheckDefaultFamily" . -}}
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
{{- if or (not $c.ok) $c.v6 (ne (int $c.bits) 32) (hasKey $p.ipBlock "except") -}}
{{- fail (printf "processRuns.network.dns: ipBlock %q must be one IPv4 address (/32): port 53 to a range is an exfiltration channel to any resolver in it (IPv6 is not supported for runs, docs/ISSUES.md I-191)" (toString $p.ipBlock.cidr)) -}}
{{- end -}}
{{- include "stac-higher.runsCheckCidr" (dict "key" "processRuns.network.dns" "cidr" $p.ipBlock.cidr "publicMin" 32 "privateMin" 32) -}}
{{- end -}}
{{- end -}}
{{- if and (not $v.aws.s3PrefixListCidrs) (not $net.objectStore) -}}
{{- fail "aws.s3PrefixListCidrs and processRuns.network.objectStore are both empty: runs need at least one object-store peer (Terraform's helm_values carries the S3 prefix list; an in-cluster store goes in processRuns.network.objectStore)" -}}
{{- end -}}
{{- /* The S3 managed prefix list is public ranges (/15 to /22 today): /12 or narrower, never private space. */ -}}
{{- range $cidr := $v.aws.s3PrefixListCidrs -}}
{{- include "stac-higher.runsCheckCidr" (dict "key" "aws.s3PrefixListCidrs" "cidr" $cidr "publicMin" 12 "privateMin" 0) -}}
{{- end -}}
{{- /* An explicit object-store ipBlock may be private (an interface endpoint, an in-VPC store), but /24 or narrower. */ -}}
{{- range $p := $net.objectStore -}}
{{- if hasKey $p "ipBlock" -}}
{{- include "stac-higher.runsCheckCidr" (dict "key" "processRuns.network.objectStore" "cidr" $p.ipBlock.cidr "publicMin" 12 "privateMin" 24) -}}
{{- end -}}
{{- end -}}
{{- /* A private destination a scan needs on 443 (ECR or S3 interface endpoints, an in-VPC mirror): /24 or narrower. */ -}}
{{- range $cidr := $net.scannerPrivateCidrs -}}
{{- include "stac-higher.runsCheckCidr" (dict "key" "processRuns.network.scannerPrivateCidrs" "cidr" $cidr "publicMin" 24 "privateMin" 24) -}}
{{- end -}}
{{- include "stac-higher.validateRunsProfiles" . -}}
{{- range $cidr := $net.scannerEgressExcept -}}
{{- $c := include "stac-higher.runsCidr" $cidr | fromJson -}}
{{- if or (not $c.ok) $c.v6 -}}
{{- fail (printf "processRuns.network.scannerEgressExcept: %q is not an IPv4 CIDR (a.b.c.d/n)" (toString $cidr)) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/* The hardware-profile document as supplied: config.hardwareProfiles, else the chart's eks-cpu set (a dict). */}}
{{- define "stac-higher.runsProfileSource" -}}
{{- if .Values.config.hardwareProfiles -}}
{{- .Values.config.hardwareProfiles | toJson -}}
{{- else -}}
{{- .Files.Get "files/hardware-profiles/eks-cpu.json" -}}
{{- end -}}
{{- end -}}

{{/*
The family of each profile (JSON object profile id -> family name): processRuns.profileFamilies, else
defaultFamily for a CPU profile (gpu_count null). Fails naming processRuns.profileFamilies for an unmapped GPU
profile, a mapping to a missing or disabled family, a GPU profile on a CPU family or the reverse, and a mapping
whose profile is not in the document.
*/}}
{{- define "stac-higher.runsProfileFamilies" -}}
{{- $pr := .Values.processRuns -}}
{{- include "stac-higher.runsCheckDefaultFamily" . -}}
{{- $fams := include "stac-higher.runsFamilies" . | fromJson -}}
{{- $doc := include "stac-higher.runsProfileSource" . | fromJson -}}
{{- $map := $pr.profileFamilies | default dict -}}
{{- $out := dict -}}
{{- range $p := $doc.profiles -}}
{{- $gpu := not (kindIs "invalid" $p.gpu_count) -}}
{{- $name := "" -}}
{{- if hasKey $map $p.id -}}
{{- $name = get $map $p.id -}}
{{- if not (hasKey $fams $name) -}}
{{- fail (printf "processRuns.profileFamilies.%s: %q is not an enabled entry of processRuns.families" $p.id $name) -}}
{{- end -}}
{{- else if $gpu -}}
{{- fail (printf "processRuns.profileFamilies: GPU profile %q (config.hardwareProfiles) needs a family with a gpuResource; map it in processRuns.profileFamilies" $p.id) -}}
{{- else -}}
{{- $name = $pr.defaultFamily -}}
{{- end -}}
{{- $isGpuFamily := not (empty (get $fams $name).gpuResource) -}}
{{- if and $gpu (not $isGpuFamily) -}}
{{- fail (printf "processRuns.profileFamilies.%s: GPU profile %q is mapped to %q, which has no gpuResource" $p.id $p.id $name) -}}
{{- end -}}
{{- if and (not $gpu) $isGpuFamily -}}
{{- fail (printf "processRuns.profileFamilies.%s: CPU profile %q (gpu_count null) is mapped to GPU family %q" $p.id $p.id $name) -}}
{{- end -}}
{{- $_ := set $out $p.id $name -}}
{{- end -}}
{{- range $id := keys $map | sortAlpha -}}
{{- if not (hasKey $out $id) -}}
{{- fail (printf "processRuns.profileFamilies.%s: no hardware profile %q in config.hardwareProfiles" $id $id) -}}
{{- end -}}
{{- end -}}
{{- toJson $out -}}
{{- end -}}

{{/*
The profile document with every profile's backend.kubernetes derived from its family (JSON). A document that
already carries backend.kubernetes must equal the derived block (compared as JSON), so nothing is overwritten
silently; other backend keys are kept.
*/}}
{{- define "stac-higher.runsProfileDoc" -}}
{{- $doc := include "stac-higher.runsProfileSource" . | fromJson -}}
{{- $fams := include "stac-higher.runsFamilies" . | fromJson -}}
{{- $of := include "stac-higher.runsProfileFamilies" . | fromJson -}}
{{- $profiles := list -}}
{{- range $p := $doc.profiles -}}
{{- $name := get $of $p.id -}}
{{- $block := include "stac-higher.runsBackendBlock" (dict "root" $ "name" $name "family" (get $fams $name)) | fromJson -}}
{{- $backend := $p.backend | default dict -}}
{{- if and (hasKey $backend "kubernetes") (ne (toJson $backend.kubernetes) (toJson $block)) -}}
{{- fail (printf "config.hardwareProfiles: profile %q has backend.kubernetes %s, but processRuns.families.%s derives %s; remove the block (the chart derives it) or make them equal" $p.id (toJson $backend.kubernetes) $name (toJson $block)) -}}
{{- end -}}
{{- $_ := set $backend "kubernetes" $block -}}
{{- $_ := set $p "backend" $backend -}}
{{- $profiles = append $profiles $p -}}
{{- end -}}
{{- $_ := set $doc "profiles" $profiles -}}
{{- toJson $doc -}}
{{- end -}}

{{/*
R8-R5 / K6-R11: every profile's maxima fit its family's quota (a run whose maximum exceeds the quota pends
forever), and an image scan (1 CPU, the image policy's scan_limits.memory_mb) fits the default family.
*/}}
{{- define "stac-higher.validateRunsProfiles" -}}
{{- $fams := include "stac-higher.runsFamilies" . | fromJson -}}
{{- $of := include "stac-higher.runsProfileFamilies" . | fromJson -}}
{{- $doc := include "stac-higher.runsProfileSource" . | fromJson -}}
{{- range $p := $doc.profiles -}}
{{- $name := get $of $p.id -}}
{{- $q := (get $fams $name).quota -}}
{{- $key := printf "processRuns.families.%s.quota" $name -}}
{{- if gt (float64 $p.cpu.max) (float64 $q.cpu) -}}
{{- fail (printf "%s.cpu (%d) is below profile %q's cpu.max (%v): its largest run could never be admitted" $key (int $q.cpu) $p.id $p.cpu.max) -}}
{{- end -}}
{{- if gt (float64 $p.memory_mb.max) (float64 (mul (int $q.memoryGi) 1024)) -}}
{{- fail (printf "%s.memoryGi (%d Gi) is below profile %q's memory_mb.max (%v MiB): its largest run could never be admitted" $key (int $q.memoryGi) $p.id $p.memory_mb.max) -}}
{{- end -}}
{{- if and (not (kindIs "invalid" $p.gpu_count)) (gt (float64 $p.gpu_count.max) (float64 $q.gpus)) -}}
{{- fail (printf "%s.gpus (%d) is below profile %q's gpu_count.max (%v): its largest run could never be admitted" $key (int $q.gpus) $p.id $p.gpu_count.max) -}}
{{- end -}}
{{- end -}}
{{- $def := .Values.processRuns.defaultFamily -}}
{{- $policy := include "stac-higher.imagePolicyJson" . | fromJson -}}
{{- $scanMb := (($policy.scan_limits | default dict).memory_mb | default 0) -}}
{{- if gt (float64 $scanMb) (float64 (mul (int (get $fams $def).quota.memoryGi) 1024)) -}}
{{- fail (printf "processRuns.families.%s.quota.memoryGi (%d Gi) is below config.imagePolicy scan_limits.memory_mb (%v MiB): image scans run on the default family and could never be admitted" $def (int (get $fams $def).quota.memoryGi) $scanMb) -}}
{{- end -}}
{{- end -}}

{{/*
One of K-6's infra/kueue/common files, from the chart's byte-identical copy (files/kueue/common/, addendum A-R2;
kueue.test.ts pins the bytes and the rendered objects), never retyped: the CEL text never passes through the
template parser. Each document gets the chart's labels and its parameters: the runs namespace (LocalQueue
metadata.namespace, every binding's namespaceSelector) and the pipeline identity (the one literal in the
pod-update policy's match condition, replaced exactly once or the render fails). (dict "root" $ "file" <name>)
*/}}
{{- define "stac-higher.runsCommonFile" -}}
{{- $r := .root -}}
{{- $v := $r.Values -}}
{{- $path := printf "files/kueue/common/%s" .file -}}
{{- $text := $r.Files.Get $path -}}
{{- if not $text -}}
{{- fail (printf "%s is missing from the chart (a copy of infra/kueue/common/%s)" $path .file) -}}
{{- end -}}
{{- $literal := "system:serviceaccount:stac-higher:stac-higher-pipeline" -}}
{{- if eq .file "admission-policy.yaml" -}}
{{- $n := sub (len (splitList $literal $text)) 1 -}}
{{- if ne (int $n) 1 -}}
{{- fail (printf "%s: the pipeline identity %q occurs %d times, expected once (K-6 changed the policy; update templates/_runs.tpl)" $path $literal (int $n)) -}}
{{- end -}}
{{- $text = replace $literal (printf "system:serviceaccount:%s:%s" $v.identity.namespace $v.identity.serviceAccounts.pipeline) $text -}}
{{- end -}}
{{- $labels := include "stac-higher.labels" (dict "root" $r "component" "process-runs") | fromYaml -}}
{{- range $raw := regexSplit "(?m)^---[ \\t]*$" $text -1 -}}
{{- $doc := fromYaml $raw -}}
{{- if hasKey $doc "Error" -}}
{{- fail (printf "%s: %s" $path $doc.Error) -}}
{{- end -}}
{{- if $doc }}
---
{{- $_ := set $doc.metadata "labels" (merge (deepCopy ($doc.metadata.labels | default dict)) $labels) -}}
{{- if eq $doc.kind "LocalQueue" -}}
{{- $_ := set $doc.metadata "namespace" $v.processRuns.namespace -}}
{{- end -}}
{{- if eq $doc.kind "ValidatingAdmissionPolicyBinding" -}}
{{- $_ := set $doc.spec.matchResources.namespaceSelector "matchLabels" (dict "kubernetes.io/metadata.name" $v.processRuns.namespace) -}}
{{- end }}
{{ toYaml $doc }}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
The placements a process run's pod template may carry (addendum A-R3): exactly one enabled family's derived
nodeSelector + tolerations + runtimeClassName. Tolerations spelt in full, absent fields as the API reads them
(operator Equal, value and effect ""). A list of objects, deduplicated, the default family first (JSON array).
An image scan may also carry no placement at all (stac-higher.runsPlacementCel).
*/}}
{{- define "stac-higher.runsPlacements" -}}
{{- $fams := include "stac-higher.runsFamilies" . | fromJson -}}
{{- $out := list -}}
{{- $seen := dict -}}
{{- range $name := include "stac-higher.runsFamiliesOrdered" . | fromJsonArray -}}
{{- $b := include "stac-higher.runsBackendBlock" (dict "root" $ "name" $name "family" (get $fams $name)) | fromJson -}}
{{- $tols := list -}}
{{- range $t := $b.tolerations -}}
{{- $tols = append $tols (dict "key" ($t.key | default "") "operator" ($t.operator | default "Equal") "value" ($t.value | default "") "effect" ($t.effect | default "")) -}}
{{- end -}}
{{- $e := dict "nodeSelector" $b.node_selector "tolerations" $tols "runtimeClassName" ($b.runtime_class | default "") -}}
{{- if not (hasKey $seen (toJson $e)) -}}
{{- $_ := set $seen (toJson $e) true -}}
{{- $out = append $out $e -}}
{{- end -}}
{{- end -}}
{{- toJson $out -}}
{{- end -}}

{{/*
The same list as a CEL literal. Kubernetes' CEL environment enables HomogeneousAggregateLiterals (and validates
them when the policy is created), so a map literal's values must share one type: each value is wrapped in
dyn(...), making every entry map(string, dyn). Plain toJson would mix a map, a list and a string and the API
server would refuse the policy. JSON is valid CEL literal syntax inside dyn().
*/}}
{{- define "stac-higher.runsPlacementsCel" -}}
{{- $entries := list -}}
{{- range $e := include "stac-higher.runsPlacements" . | fromJsonArray -}}
{{- $entries = append $entries (printf "{\"nodeSelector\": dyn(%s), \"runtimeClassName\": dyn(%s), \"tolerations\": dyn(%s)}" (toJson $e.nodeSelector) (toJson $e.runtimeClassName) (toJson $e.tolerations)) -}}
{{- end -}}
{{- printf "[%s]" (join ", " $entries) -}}
{{- end -}}

{{/* Whether the Job's pod template says it is an image scan (the jobs policy ties that to a stac-scan- name). */}}
{{- define "stac-higher.runsIsScanCel" -}}
has(object.spec.template.metadata) && has(object.spec.template.metadata.labels) &&
'stac-higher.run-kind' in object.spec.template.metadata.labels &&
object.spec.template.metadata.labels['stac-higher.run-kind'] == 'image_scan'
{{- end -}}

{{/*
The placement rule's CEL (one validation): an image scan may carry no placement at all; any run Job may carry
exactly one family's placement. `a` is one allowed placement; `t`/`e` a pod / allowed toleration.
*/}}
{{- define "stac-higher.runsPlacementCel" -}}
(variables.isScan &&
  (!has(variables.pod.nodeSelector) || size(variables.pod.nodeSelector) == 0) &&
  (!has(variables.pod.tolerations) || size(variables.pod.tolerations) == 0) &&
  (!has(variables.pod.runtimeClassName) || variables.pod.runtimeClassName == '')) ||
variables.allowed.exists(a,
  (has(variables.pod.nodeSelector) ? size(variables.pod.nodeSelector) : 0) == size(a.nodeSelector) &&
  a.nodeSelector.all(k, has(variables.pod.nodeSelector) && k in variables.pod.nodeSelector &&
    variables.pod.nodeSelector[k] == a.nodeSelector[k]) &&
  (has(variables.pod.tolerations) ? size(variables.pod.tolerations) : 0) == size(a.tolerations) &&
  (!has(variables.pod.tolerations) || variables.pod.tolerations.all(t, a.tolerations.exists(e,
    (has(t.key) ? t.key : '') == e.key && (has(t.operator) ? t.operator : 'Equal') == e.operator &&
    (has(t.value) ? t.value : '') == e.value && (has(t.effect) ? t.effect : '') == e.effect &&
    !has(t.tolerationSeconds)))) &&
  a.tolerations.all(e, has(variables.pod.tolerations) && variables.pod.tolerations.exists(t,
    (has(t.key) ? t.key : '') == e.key && (has(t.operator) ? t.operator : 'Equal') == e.operator &&
    (has(t.value) ? t.value : '') == e.value && (has(t.effect) ? t.effect : '') == e.effect &&
    !has(t.tolerationSeconds))) &&
  (has(variables.pod.runtimeClassName) ? variables.pod.runtimeClassName : '') == a.runtimeClassName)
{{- end -}}
