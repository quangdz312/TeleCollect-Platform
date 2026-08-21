/** Hợp đồng dữ liệu với `/api/v1/labeling` — khớp `src/api/labeling.py`.
 *
 *  Đường thu tự động: tool kịch bản sinh episode, người chấm xem lại rồi cho
 *  Đạt/Loại. Khác hẳn đường teleop trong `types.ts` — ở đây không có realtime,
 *  mọi thứ là job nền và file trên đĩa.
 */

export type Decision = "approved" | "rejected";
export type JobStatus = "queued" | "running" | "succeeded" | "failed";
export type AutoLabel = "accept" | "review" | "reject";

export interface TaskOption {
  task: string;
  /** Stored dataset identifier. Not for display — use `tool_label`. */
  tool: string;
  tool_label?: string;
  default_horizon: number;
}

export interface ReasonOption {
  code: string;
  label_vi: string;
  label_en: string;
  check: string | null;
  penalties: string[];
}

export interface WorkspaceSummary {
  root: string;
  datasets: number;
  episodes: number;
  reviewed: number;
  approved: number;
  approved_successes: number;
  rejected: number;
  pending: number;
  auto_approved: number;
  auto_rejected: number;
  human_reviewed: number;
  audit_pending: number;
  audit_reviewed: number;
  audit_failed: number;
  audit_error_rate: number | null;
  per_task: Record<string, {
    total: number;
    reviewed: number;
    pending: number;
    approved: number;
    approved_successes: number;
    rejected: number;
  }>;
  per_quality: Record<string, {
    total: number;
    reviewed: number;
    pending: number;
    approved: number;
    approved_successes: number;
    rejected: number;
    recovery: number;
  }>;
  collection_batch_id: string | null;
  task_filter: string | null;
  available_batches: string[];
  scorer_version: string | null;
}

export interface LabelingConfig {
  tasks: TaskOption[];
  qualities: string[];
  reasons: ReasonOption[];
  workspace: WorkspaceSummary;
  /** Khoá dạng `"lift:poor"`. */
  suggested_seeds: Record<string, number>;
}

export interface CollectionJob {
  id: string;
  kind: "collect" | "render";
  status: JobStatus;
  request: { task: string; quality: string; episodes: number; seed: number };
  done: number;
  total: number;
  progress: number;
  log: string[];
  error: string | null;
  result: {
    episodes?: number;
    successes?: number;
    profile_version?: string;
    corpus_episodes?: number;
  };
}

export interface LabelRecord {
  episode_id: string;
  human_decision: Decision;
  reasons: string[];
  note: string;
  reviewer: string;
  reviewed_at: string;
  decision_source?: "human" | "auto_gate";
  gate_version?: string | null;
  gate_reason?: string;
}

/** Điểm máy chỉ có mặt khi client xin — mặc định server giữ lại để chấm mù. */
export interface AutoFlags {
  failed_checks: string[];
  unavailable_checks: string[];
  worst_penalty: string | null;
  worst_penalty_value: number;
}

export interface Episode {
  episode_id: string;
  demo: string;
  display_name: string;
  task: string;
  length: number;
  suggested_trim_start: number;
  suggested_trim_end: number;
  trim_head_frames: number;
  trim_tail_frames: number;
  scorer_version: string;
  label: LabelRecord | null;
  video_ready: boolean;
  /** Chỉ có khi `include_score=true`. */
  requested_quality?: string;
  recorded_success?: boolean;
  auto_score?: number;
  gate_decision?: string;
  auto_flags?: AutoFlags;
  auto_label: AutoLabel;
  auto_label_reason: string;
  gate_action: "approve" | "reject" | "review" | "audit";
  audit_required: boolean;
  auto_gate_version: string;
  auto_gate_reason: string;
}

export interface ShadowReport {
  shadow: {
    matched: number;
    approved: number;
    rejected: number;
    auc: number | null;
    tau_reject: number | null;
    tau_approve: number | null;
    approve_zone_count: number;
    wrong_approval_bound: number | null;
    review_yield: number | null;
    review_yield_is_meaningful: boolean;
    thresholds_crossed: boolean;
    gate_ready: boolean;
    reasons: string[];
  };
  reason_counts: Record<string, number>;
  checks_that_missed: { episode_id: string; reason: string; check: string; status: string }[];
  workspace: WorkspaceSummary;
  targets: { min_auc: number; target_approve_zone: number; min_reviews_for_yield: number };
}

export type DiversityScope = "approved" | "reviewed" | "all";

export interface DiversityReport {
  task: string;
  scope: DiversityScope;
  episodes: number;
  success_rate: number | null;
  coverage: { overall: number | null; x: number | null; y: number | null; reference_episodes: number };
  status: { code: string; label: string; detail: string };
  quality: Array<{
    quality: string; total: number; success: number; failure: number;
    approved: number; rejected: number; pending: number;
  }>;
  position_sets: Array<{
    key: string;
    label: string;
    points: Array<{
      episode_id: string; quality: string; decision: string; success: boolean;
      x: number; y: number; z: number;
    }>;
  }>;
  length_histogram: { edges: number[]; counts: number[] };
  phases: Array<{ phase: string; action_scale: number; failures: number }>;
}

import { apiUrl, getToken } from "./api";

const BASE = "/labeling";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init?.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(apiUrl(BASE + path), { ...init, headers });
  const body = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error((body as { detail?: string })?.detail ?? `${response.status} ${response.statusText}`);
  }
  return body as T;
}

function post<T>(path: string, payload?: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    body: JSON.stringify(payload ?? {}),
  });
}

export const labeling = {
  config: () => request<LabelingConfig>("/config"),
  overview: (params?: { collectionBatchId?: string; task?: string }) => {
    const query = new URLSearchParams();
    if (params?.collectionBatchId) query.set("collection_batch_id", params.collectionBatchId);
    if (params?.task) query.set("task", params.task);
    const suffix = query.size ? `?${query}` : "";
    return request<WorkspaceSummary>(`/overview${suffix}`);
  },

  startRun: (payload: { task: string; quality: string; episodes: number; seed: number; collection_batch_id: string }) =>
    post<CollectionJob>("/runs", payload),

  run: (jobId: string) => request<CollectionJob>(`/runs/${jobId}`),

  episodes: (params: { task?: string; quality?: string; collectionBatchId?: string; status: string; includeScore: boolean }) => {
    const query = new URLSearchParams({
      status: params.status,
      include_score: String(params.includeScore),
    });
    if (params.task) query.set("task", params.task);
    if (params.quality) query.set("quality", params.quality);
    if (params.collectionBatchId) query.set("collection_batch_id", params.collectionBatchId);
    return request<{ episodes: Episode[]; count: number; total: number }>(`/episodes?${query}`);
  },

  detail: (episodeId: string, includeScore: boolean) =>
    request<Episode>(
      `/episodes/detail?episode_id=${encodeURIComponent(episodeId)}&include_score=${includeScore}`,
    ),

  requestVideo: (episodeId: string) =>
    post<{ status: "ready" | "rendering" }>(
      `/episodes/video?episode_id=${encodeURIComponent(episodeId)}`,
    ),

  videoUrl: (episodeId: string) => {
    const token = getToken();
    const query = new URLSearchParams({ episode_id: episodeId });
    if (token) query.set("token", token);
    return apiUrl(`${BASE}/video?${query}`);
  },

  submitLabel: (payload: {
    episode_id: string;
    decision: Decision;
    reasons: string[];
    note: string;
    reviewer: string;
    blind: boolean;
  }) => post<{ label: LabelRecord; workspace: WorkspaceSummary }>("/labels", payload),

  report: () => request<ShadowReport>("/report"),

  applyAutoGate: () => post<{
    result: {
      approved: number; rejected: number; audit: number; review: number; skipped: number;
      auto_approve_enabled: boolean; disabled_tasks: string[];
      audit_error_rates: Record<string, number>;
    };
    workspace: WorkspaceSummary;
  }>("/auto-gate/apply"),

  diversity: (task: string, scope: DiversityScope) => {
    const query = new URLSearchParams({ task, scope });
    return request<DiversityReport>(`/diversity?${query}`);
  },
};
