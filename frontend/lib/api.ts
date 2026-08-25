"use client";

export type Role = "operator" | "reviewer" | "admin";
export type DemoStatus = "recording" | "recorded" | "labeled" | "approved" | "rejected";
export type LabelValue = "success" | "failure";
export type AutoLabel = "accept" | "review" | "reject";
export type RunStatus = "pending" | "running" | "succeeded" | "failed" | "cancelled";

export interface User {
  id: string;
  username: string;
  display_name: string;
  role: Role;
  is_active: boolean;
  created_at: string;
}

export interface Task {
  id: string;
  title: string;
  instruction: string;
  max_steps: number;
  hints: string[];
}

export interface Demo {
  id: string;
  task_id: string;
  operator_id: string;
  operator_name: string | null;
  created_at: string;
  seed: number;
  fps: number;
  num_frames: number;
  duration_s: number;
  size_bytes: number;
  auto_success: boolean;
  auto_success_frame: number | null;
  latency_p50_ms: number;
  latency_p95_ms: number;
  control_jitter_ms: number;
  dropped_frames: number;
  status: DemoStatus;
  label: LabelValue | null;
  trim_start: number;
  trim_end: number | null;
  review_notes: string;
  reviewer_id: string | null;
  reviewer_name: string | null;
  reviewed_at: string | null;
  has_wrist?: boolean;
  has_trajectory?: boolean;
  auto_label?: AutoLabel;
  auto_label_reason?: string;
}

export interface DemoPage {
  items: Demo[];
  total: number;
  limit: number;
  offset: number;
}

export interface Trajectory {
  num_frames: number;
  fps: number;
  trim_start: number;
  trim_end: number;
  state: number[][];
  action: number[][];
  ee_pose: number[][];
  success: boolean[];
  cmd_age_ms: number[];
  rtt_ms: number[];
  tick_dt_ms: number[];
}

export interface Summary {
  total: number;
  by_status: Record<string, number>;
  by_label: Record<string, number>;
  by_task: Record<string, Record<string, number>>;
  approved_successes: number;
  valid_demo_count: number;
  success_rate: number;
  approval_rate: number;
  total_frames: number;
  total_hours: number;
  total_size_bytes: number;
  median_latency_ms: number;
  p95_latency_ms: number;
}

export interface DatasetExport {
  id: string;
  name: string;
  format: string;
  created_at: string;
  tasks: string[];
  include_failures: boolean;
  num_episodes: number;
  num_frames: number;
  size_bytes: number;
  path: string;
  dvc_hash: string | null;
  status: string;
  error_message?: string | null;
}

export interface TrainingRun {
  id: string;
  name: string;
  dataset_id?: string;
  export_id?: string | null;
  created_at: string;
  status: RunStatus;
  config: Record<string, any>;
  metrics?: Record<string, any>;
  output_dir: string;
  epoch?: number;
  train_loss?: number | null;
  validation_loss?: number | null;
  checkpoints?: TrainingCheckpoint[];
  error: string | null;
  started_at?: string | null;
  finished_at: string | null;
}

export interface TrainingCheckpoint {
  id: string;
  filename: string;
  epoch: number;
  validation_loss: number | null;
  size_bytes: number;
  created_at: string;
  is_best_validation: boolean;
  is_latest: boolean;
}

export interface TrainingRequest {
  dataset_id: string;
  name: string;
  policy: "bc" | "bc-rnn";
  epochs: number;
  batch_size: number;
  num_workers: number;
  device: "auto" | "cpu" | "cuda";
  learning_rate: number;
  seed: number;
  save_every_n_epochs: number | null;
  sequence_length: number;
  rnn_hidden_dim: number;
  rnn_layers: number;
  normalize_observations: boolean;
  observation_profile: "minimal" | "all";
  rollout_enabled: boolean;
  rollout_every_n_epochs: number;
  rollout_episodes: number;
  rollout_horizon: number;
}

export interface EvaluationEpisode {
  seed: number;
  success: boolean;
  steps: number;
  video: string | null;
}

export interface EvaluationRun {
  id: string;
  training_run_id: string;
  checkpoint_id: string;
  task_name: string;
  status: RunStatus;
  num_episodes: number;
  success_rate: number | null;
  mean_episode_length: number | null;
  episodes: EvaluationEpisode[];
  created_at: string;
  finished_at: string | null;
  error: string | null;
}

export interface EvaluationRequest {
  training_run_id: string;
  checkpoint_id: string;
  num_rollouts: number;
  horizon: number | null;
  seed: number;
  record_videos: number;
}

export interface EvalRun {
  id: string;
  training_run_id: string;
  created_at: string;
  status: RunStatus;
  task_id: string;
  num_episodes: number;
  success_rate: number;
  mean_episode_length: number;
  details: Record<string, any>;
  error: string;
  finished_at: string | null;
}

type BackendTask = {
  name: string;
  description: string;
  instruction: string;
  hints: string[];
  max_steps: number;
};

type BackendDemo = {
  id: string;
  task_name: string;
  operator_id: string;
  status: DemoStatus;
  outcome: LabelValue | null;
  note: string;
  reviewer_id: string | null;
  reviewed_at: string | null;
  fps: number | null;
  num_frames: number | null;
  duration_s: number | null;
  size_bytes: number | null;
  trim_start_s: number | null;
  trim_end_s: number | null;
  has_wrist: boolean;
  has_trajectory: boolean;
  created_at: string;
  auto_label: AutoLabel;
  auto_label_reason: string;
};

type BackendPage<T> = {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
};

type BackendSummary = {
  total: number;
  by_status: Record<string, number>;
  by_outcome: Record<string, number>;
  by_task: Record<string, number>;
  labeled_count: number;
  reviewed_count: number;
  success_count: number;
  approved_count: number;
  success_rate: number;
  approval_rate: number;
  total_frames: number;
  total_duration_hours: number;
  total_size_bytes: number;
};

type BackendDataset = {
  id: string;
  name: string;
  task_names: string[];
  include_failures: boolean;
  format: string;
  status: string;
  num_episodes: number;
  num_frames: number;
  size_bytes: number | null;
  created_at: string;
  error_message?: string | null;
};

const configuredOrigin =
  process.env.NEXT_PUBLIC_API_URL || process.env.NEXT_PUBLIC_API_ORIGIN || "http://localhost:8000";

export const API_ORIGIN = configuredOrigin.replace(/\/$/, "");
const API_PREFIX = "/api/v1";
const TOKEN_KEY = "telecollect.token";
const REFRESH_TOKEN_KEY = "telecollect.refresh_token";

export function apiUrl(path: string) {
  if (path.startsWith("http://") || path.startsWith("https://")) return path;
  const normalisedPath = path.startsWith("/") ? path : `/${path}`;
  if (normalisedPath === "/health") return `${API_ORIGIN}/health`;
  const versionedPath = normalisedPath.startsWith(API_PREFIX)
    ? normalisedPath
    : `${API_PREFIX}${normalisedPath}`;
  return `${API_ORIGIN}${versionedPath}`;
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null) {
  if (typeof window === "undefined") return;
  if (token) window.localStorage.setItem(TOKEN_KEY, token);
  else {
    window.localStorage.removeItem(TOKEN_KEY);
    window.localStorage.removeItem(REFRESH_TOKEN_KEY);
  }
}

function setRefreshToken(token: string | null) {
  if (typeof window === "undefined") return;
  if (token) window.localStorage.setItem(REFRESH_TOKEN_KEY, token);
  else window.localStorage.removeItem(REFRESH_TOKEN_KEY);
}

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function parseResponse(response: Response) {
  if (response.status === 204) return undefined;
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) return response.text();
  return response.json();
}

function errorMessage(payload: unknown, fallback: string) {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item) =>
          item && typeof item === "object" && "msg" in item
            ? String((item as { msg: unknown }).msg)
            : String(item),
        )
        .join("; ");
    }
  }
  if (typeof payload === "string" && payload.trim()) return payload;
  return fallback;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  let response: Response;
  try {
    response = await fetch(apiUrl(path), { ...init, headers });
  } catch (exc) {
    throw new ApiError(
      0,
      `Cannot reach backend at ${API_ORIGIN}. Start FastAPI on port 8000 or set NEXT_PUBLIC_API_URL.`,
    );
  }

  const payload = await parseResponse(response);
  if (!response.ok) {
    throw new ApiError(response.status, errorMessage(payload, response.statusText));
  }
  return payload as T;
}

function toTask(task: BackendTask): Task {
  return {
    id: task.name,
    // The label has to be the identifier, not the description. `description`
    // holds the Vietnamese sentence shown to whoever collects the episode
    // ("Gap khoi lap phuong tren ban..."), so using it here filled the task
    // dropdowns with instructions instead of names, and stopped the DB-backed
    // `lift_cube` from matching the scripted `lift_cube` — the same task then
    // appeared twice, once per spelling.
    title: task.name,
    instruction: task.instruction,
    max_steps: task.max_steps,
    hints: task.hints,
  };
}

function toDemo(demo: BackendDemo): Demo {
  const fps = demo.fps || 30;
  const duration = demo.duration_s || 0;
  const frames = demo.num_frames || Math.round(duration * fps);
  const trimStart = Math.round((demo.trim_start_s || 0) * fps);
  const trimEnd = demo.trim_end_s == null ? null : Math.round(demo.trim_end_s * fps);

  return {
    id: demo.id,
    task_id: demo.task_name,
    operator_id: demo.operator_id,
    operator_name: demo.operator_id,
    created_at: demo.created_at,
    seed: 0,
    fps,
    num_frames: frames,
    duration_s: duration,
    size_bytes: demo.size_bytes || 0,
    auto_success: demo.outcome === "success",
    auto_success_frame: null,
    latency_p50_ms: 0,
    latency_p95_ms: 0,
    control_jitter_ms: 0,
    dropped_frames: 0,
    status: demo.status,
    label: demo.outcome,
    trim_start: trimStart,
    trim_end: trimEnd,
    review_notes: demo.note || "",
    reviewer_id: demo.reviewer_id,
    reviewer_name: demo.reviewer_id,
    reviewed_at: demo.reviewed_at,
    has_wrist: demo.has_wrist,
    has_trajectory: demo.has_trajectory,
    auto_label: demo.auto_label || "review",
    auto_label_reason: demo.auto_label_reason || "",
  };
}

function toSummary(summary: BackendSummary): Summary {
  const byLabel = {
    success: summary.by_outcome.success || 0,
    failure: summary.by_outcome.failure || 0,
  };
  const byTask = Object.fromEntries(
    Object.entries(summary.by_task).map(([task, total]) => [
      task,
      {
        total,
        success: 0,
        approved: 0,
        rejected: 0,
      },
    ]),
  );
  const valid = Math.min(summary.success_count, summary.approved_count);
  return {
    total: summary.total,
    by_status: summary.by_status,
    by_label: byLabel,
    by_task: byTask,
    approved_successes: valid,
    valid_demo_count: valid,
    success_rate: summary.success_rate,
    approval_rate: summary.approval_rate,
    total_frames: summary.total_frames,
    total_hours: summary.total_duration_hours,
    total_size_bytes: summary.total_size_bytes,
    median_latency_ms: 0,
    p95_latency_ms: 0,
  };
}

function toExport(dataset: BackendDataset): DatasetExport {
  return {
    id: dataset.id,
    name: dataset.name,
    format: dataset.format,
    created_at: dataset.created_at,
    tasks: dataset.task_names,
    include_failures: dataset.include_failures,
    num_episodes: dataset.num_episodes,
    num_frames: dataset.num_frames,
    size_bytes: dataset.size_bytes || 0,
    path: `data/datasets/${dataset.id}.${dataset.format === "robomimic" ? "hdf5" : "zip"}`,
    dvc_hash: null,
    status: dataset.status,
    error_message: dataset.error_message,
  };
}

/** Client-side poll cadence for `building` datasets — backend gives no SLA, just a reasonable default. */
export const DATASET_POLL_INTERVAL_MS = 3000;

function emptyTrajectory(demo: Demo): Trajectory {
  const frames = Math.max(1, demo.num_frames);
  const rows = Array.from({ length: frames }, () => Array(7).fill(0));
  return {
    num_frames: frames,
    fps: demo.fps,
    trim_start: demo.trim_start,
    trim_end: demo.trim_end ?? frames,
    state: rows,
    action: rows,
    ee_pose: Array.from({ length: frames }, () => [0, 0, 0, 0, 0, 0, 1]),
    success: Array.from({ length: frames }, () => demo.label === "success"),
    cmd_age_ms: Array.from({ length: frames }, () => 0),
    rtt_ms: Array.from({ length: frames }, () => 0),
    tick_dt_ms: Array.from({ length: frames }, () => 1000 / demo.fps),
  };
}

export const api = {
  async login(username: string, password: string) {
    const body = new URLSearchParams();
    body.set("username", username);
    body.set("password", password);
    const token = await request<{ access_token: string; refresh_token: string }>("/auth/login", {
      method: "POST",
      body,
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
    });
    setToken(token.access_token);
    setRefreshToken(token.refresh_token);
    return api.me();
  },

  me: () => request<User>("/auth/me"),
  users: () => request<User[]>("/users"),

  createUser: (body: {
    username: string;
    password: string;
    display_name?: string;
    role: Role;
  }) => request<User>("/users", { method: "POST", body: JSON.stringify(body) }),

  updateUser: (
    id: string,
    body: Partial<{ role: Role; is_active: boolean; password: string; display_name: string }>,
  ) => request<User>(`/users/${id}`, { method: "PATCH", body: JSON.stringify(body) }),

  tasks: async () => (await request<BackendTask[]>("/tasks")).map(toTask),
  teleopTasks: async () => (await request<BackendTask[]>("/teleop/tasks")).map(toTask),

  health: async () => {
    const [health, tasks] = await Promise.all([
      request<Record<string, any>>("/health").catch(() => ({ status: "unknown" })),
      api.tasks().catch(() => []),
    ]);
    return {
      version: "1.0.0",
      control_hz: 30,
      active_sessions: 0,
      max_sessions: 0,
      tasks: tasks.map((task) => task.id),
      anonymize_faces: false,
      ...health,
    };
  },

  async demos(
    params: {
      task_id?: string;
      status?: DemoStatus;
      label?: LabelValue;
      needs_review?: boolean;
      operator_id?: string;
      limit?: number;
      offset?: number;
    } = {},
  ): Promise<DemoPage> {
    const limit = params.limit ?? 50;
    const offset = params.offset ?? 0;
    const search = new URLSearchParams();
    if (params.task_id) search.set("task", params.task_id);
    if (params.status) search.set("status", params.status);
    if (params.label) search.set("outcome", params.label);
    if (params.needs_review) search.set("status", "recorded");
    if (params.operator_id) search.set("operator_id", params.operator_id);
    search.set("page", String(Math.floor(offset / limit) + 1));
    search.set("page_size", String(limit));

    const page = await request<BackendPage<BackendDemo>>(`/demos?${search}`);
    return {
      items: page.items.map(toDemo),
      total: page.total,
      limit,
      offset,
    };
  },

  demo: async (id: string) => toDemo(await request<BackendDemo>(`/demos/${id}`)),
  summary: async () => toSummary(await request<BackendSummary>("/demos/summary")),

  trajectory: async (id: string) => emptyTrajectory(await api.demo(id)),

  async review(
    id: string,
    body: {
      label?: LabelValue;
      approve?: boolean;
      trim_start?: number;
      trim_end?: number;
      notes?: string;
    },
  ) {
    let demo = await api.demo(id);
    const fps = demo.fps || 30;

    if (body.trim_start !== undefined && body.trim_end !== undefined) {
      demo = toDemo(
        await request<BackendDemo>(`/demos/${id}/trim`, {
          method: "PATCH",
          body: JSON.stringify({
            trim_start_s: body.trim_start / fps,
            trim_end_s: body.trim_end / fps,
          }),
        }),
      );
    }

    if (body.label !== undefined) {
      demo = toDemo(
        await request<BackendDemo>(`/demos/${id}/label`, {
          method: "PATCH",
          body: JSON.stringify({ outcome: body.label, note: body.notes ?? demo.review_notes }),
        }),
      );
    }

    if (body.approve !== undefined) {
      demo = toDemo(
        await request<BackendDemo>(`/demos/${id}/review`, {
          method: "POST",
          body: JSON.stringify({
            decision: body.approve ? "approve" : "reject",
            note: body.notes ?? demo.review_notes,
          }),
        }),
      );
    }

    return demo;
  },

  reopen: async (id: string) =>
    toDemo(await request<BackendDemo>(`/demos/${id}/reopen`, { method: "POST" })),

  deleteDemo: (id: string) => request<void>(`/demos/${id}`, { method: "DELETE" }),

  /**
   * `POST /api/v1/demos/upload` (multipart) — uploads a demo recorded elsewhere
   * (e.g. real hardware) so it enters the review queue. Uses `XMLHttpRequest`
   * instead of `request()`/`fetch` for `xhr.upload.onprogress`, the only way to
   * get real upload progress across browsers. Deliberately skips the shared
   * `request()` 401-refresh-retry: a `FormData` with already-consumed `File`
   * streams can't be safely replayed.
   */
  uploadDemo: (params: {
    taskName: string;
    front: File;
    wrist?: File | null;
    trajectory?: File | null;
    onProgress?: (percent: number) => void;
  }): Promise<Demo> =>
    new Promise((resolve, reject) => {
      const formData = new FormData();
      formData.set("task_name", params.taskName);
      formData.set("front", params.front);
      if (params.wrist) formData.set("wrist", params.wrist);
      if (params.trajectory) formData.set("trajectory", params.trajectory);

      const xhr = new XMLHttpRequest();
      xhr.open("POST", apiUrl("/demos/upload"));
      const token = getToken();
      if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);

      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) {
          params.onProgress?.(Math.round((event.loaded / event.total) * 100));
        }
      };

      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            resolve(toDemo(JSON.parse(xhr.responseText) as BackendDemo));
          } catch {
            reject(new ApiError(xhr.status, "Upload succeeded but the response could not be read"));
          }
          return;
        }
        let payload: unknown;
        try {
          payload = JSON.parse(xhr.responseText);
        } catch {
          payload = xhr.responseText;
        }
        reject(new ApiError(xhr.status, errorMessage(payload, xhr.statusText)));
      };
      xhr.onerror = () => reject(new ApiError(0, "Network error — could not reach the server"));

      xhr.send(formData);
    }),

  uploadReviewPackage: (params: { package: File; onProgress?: (percent: number) => void }): Promise<Demo> =>
    new Promise((resolve, reject) => {
      const formData = new FormData();
      formData.set("package", params.package);
      const xhr = new XMLHttpRequest();
      xhr.open("POST", apiUrl("/demos/upload-package"));
      const token = getToken();
      if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) params.onProgress?.(Math.round((event.loaded / event.total) * 100));
      };
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try { resolve(toDemo(JSON.parse(xhr.responseText) as BackendDemo)); } catch {
            reject(new ApiError(xhr.status, "Upload succeeded but the response could not be read"));
          }
          return;
        }
        try { reject(new ApiError(xhr.status, errorMessage(JSON.parse(xhr.responseText), xhr.statusText))); } catch {
          reject(new ApiError(xhr.status, errorMessage(xhr.responseText, xhr.statusText)));
        }
      };
      xhr.onerror = () => reject(new ApiError(0, "Network error — could not reach the server"));
      xhr.send(formData);
    }),

  exports: async () => {
    const page = await request<BackendPage<BackendDataset>>("/datasets?page=1&page_size=100");
    return page.items.map(toExport);
  },

  createExport: async (body: {
    name: string;
    format: string;
    tasks: string[];
    include_failures: boolean;
    overwrite: boolean;
    data_source: "teleop" | "scripted" | "both";
    selection_mode: "all" | "exclude_rejected" | "selected";
    selected_episode_ids?: string[];
    collection_batch_id?: string;
  }) =>
    toExport(
      await request<BackendDataset>("/datasets", {
        method: "POST",
        body: JSON.stringify({
          name: body.name,
          format: body.format,
          task_names: body.tasks,
          include_failures: body.include_failures,
          overwrite: body.overwrite,
          data_source: body.data_source,
          selection_mode: body.selection_mode,
          selected_episode_ids: body.selected_episode_ids || [],
          collection_batch_id: body.collection_batch_id || null,
        }),
      }),
    ),

  exportInfo: async (id: string) => toExport(await request<BackendDataset>(`/datasets/${id}`)),
  deleteExport: (id: string) => request<void>(`/datasets/${id}`, { method: "DELETE" }),

  dvc: async () => ({
    available: false,
    reason: "backend stores dataset zips locally in this core build",
  }),

  runs: () => request<TrainingRun[]>("/training/jobs"),
  run: (id: string) => request<TrainingRun>(`/training/jobs/${id}`),
  runLog: (id: string) => request<string>(`/training/jobs/${id}/log`),
  runHistory: async (_id: string): Promise<Record<string, number>[]> => [],
  createRun: (body: TrainingRequest) =>
    request<TrainingRun>("/training/jobs", { method: "POST", body: JSON.stringify(body) }),
  cancelRun: (id: string) =>
    request<TrainingRun>(`/training/jobs/${id}/cancel`, { method: "POST" }),
  evaluations: (trainingRunId?: string) => {
    const query = trainingRunId
      ? `?training_run_id=${encodeURIComponent(trainingRunId)}`
      : "";
    return request<EvaluationRun[]>(`/training/evaluations${query}`);
  },
  createEvaluation: (trainingRunId: string, body: EvaluationRequest) =>
    request<EvaluationRun>(`/training/jobs/${trainingRunId}/evaluations`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  cancelEvaluation: (id: string) =>
    request<EvaluationRun>(`/training/evaluations/${id}/cancel`, { method: "POST" }),
  evaluationLog: (id: string) => request<string>(`/training/evaluations/${id}/log`),
  evals: async (_training_run_id?: string): Promise<EvalRun[]> => [],
  createEval: async (_body: Record<string, unknown>): Promise<EvalRun> => {
    throw new ApiError(501, "Evaluation endpoint is not implemented in the backend core build.");
  },
};

export function mediaUrl(path: string) {
  const token = getToken();
  const legacyDemoMatch = path.match(/\/api\/demos\/([^/]+)\/video\/(front|wrist)/);
  const legacyEvalMatch = path.match(/\/api\/training\/evals\//);

  let target = path;
  if (legacyDemoMatch) {
    target = `/demos/${legacyDemoMatch[1]}/playback?camera=${legacyDemoMatch[2]}`;
  } else if (legacyEvalMatch) {
    return "";
  }

  const url = new URL(apiUrl(target));
  if (token) url.searchParams.set("token", token);
  return url.toString();
}

export function thumbnailUrl(demoId: string) {
  return mediaUrl(`/demos/${demoId}/thumbnail`);
}

export function resetDemoData() {
  return undefined;
}
