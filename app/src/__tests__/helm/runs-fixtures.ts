// R-8 (#144): the gpu-family scenario's GPU family and profile, shared by the helm render suites
// (tests/values/gpu-family.yaml carries the same values for scripts/check-helm.sh).
/** The gpu-family scenario's GPU family and profile (inline here; tests/values/gpu-family.yaml carries the same). */
export const GPU_FAMILY = {
  gpuResource: "nvidia.com/gpu",
  extraTaints: [{ key: "nvidia.com/gpu", effect: "NoSchedule" }],
  quota: { cpu: 16, memoryGi: 64, gpus: 2 },
  nodePool: {
    requirements: [
      { key: "eks.amazonaws.com/instance-gpu-manufacturer", operator: "In", values: ["nvidia"] },
      { key: "eks.amazonaws.com/instance-gpu-name", operator: "In", values: ["l4"] },
      { key: "kubernetes.io/arch", operator: "In", values: ["amd64"] },
      { key: "karpenter.sh/capacity-type", operator: "In", values: ["on-demand"] },
    ],
    headroom: { cpu: 4, memoryGi: 16 },
  },
};
/** Spec §3.2's gpu-l4 profile (queue wait 900 s so it fits the default credential cap). */
export const GPU_PROFILE = {
  id: "gpu-l4",
  label: "GPU — NVIDIA L4 (24 GB)",
  description: "Single L4. Inference, cupy/numba kernels, medium models.",
  tier: "gpu",
  accelerator: { vendor: "nvidia", model: "l4", memory_gb: 24 },
  cpu: { min: 2, max: 16, default: 4 },
  memory_mb: { min: 4096, max: 65536, default: 16384 },
  gpu_count: { min: 1, max: 1, default: 1 },
  max_queue_wait_seconds: 900,
  image: null,
  backend: {},
};
