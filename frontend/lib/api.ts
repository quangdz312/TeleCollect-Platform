"use client";

/**
 * Demo build of the TeleCollect API client.
 *
 * The exported surface is identical to the real client — same types, same
 * `api.*` methods, same `getToken` / `setToken` / `mediaUrl` — but nothing here
 * touches the network.  Every call is served from the in-memory store in
 * `./demo-data`, after a short artificial delay so loading states, disabled
 * buttons and spinners behave the way they do against a real server.
 *
 * To point this folder back at a real backend, restore the original
 * `lib/api.ts` and `lib/teleop.ts` and set `NEXT_PUBLIC_API_ORIGIN`.
 */

export type Role = "operator" | "reviewer" | "admin";
export type DemoStatus = "recording" | "recorded" | "approved" | "rejected";
export type LabelValue = "success" | "failure";
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
}

export interface TrainingRun {
  id: string;
  name: string;
  export_id: string | null;
  created_at: string;
  status: RunStatus;
  config: Record<string, unknown>;
  metrics: Record<string, any>;
  output_dir: string;
  error: string;
  finished_at: string | null;
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

import {
  TASKS,
  TRAIN_DURATION_MS,
  db,
  liveEval,
  liveRunStatus,
  newId,
  roleFor,
  runHistoryFor,
  runLogFor,
  save,
  summarise,
  trajectoryFor,
} from "./demo-data";

/** No server in this build; kept so anything importing it still type-checks. */
export const API_ORIGIN = "";

export function apiUrl(path: string) {
  return path;
}

const TOKEN_KEY = "telecollect.token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null) {
  if (typeof window === "undefined") return;
  if (token) window.localStorage.setItem(TOKEN_KEY, token);
  else window.localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** Stand-in for network latency, so the UI's loading states are exercised. */
function delay<T>(value: T, ms = 140): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

function currentUser(): User {
  const token = getToken();
  const id = token?.startsWith("demo:") ? token.slice(5) : null;
  const user = id ? db().users.find((u) => u.id === id) : null;
  if (!user) throw new ApiError(401, "Not signed in");
  return user;
}

export const api = {
  /**
   * Any password is accepted — there is nothing to authenticate against.  An
   * unknown username creates an account on the spot and takes its role from the
   * name ("…admin…" -> admin, "…review…" -> reviewer, otherwise operator), so
   * every part of the UI is reachable without a user table.
   */
  async login(username: string, password: string) {
    await delay(null, 250);
    if (!username.trim() || !password) {
      throw new ApiError(400, "Enter a username and a password");
    }
    const store = db();
    let user = store.users.find((u) => u.username === username.trim());
    if (!user) {
      user = {
        id: newId("u"),
        username: username.trim(),
        display_name: username.trim(),
        role: roleFor(username),
        is_active: true,
        created_at: new Date().toISOString(),
      };
      store.users.push(user);
      save();
    }
    if (!user.is_active) throw new ApiError(403, "This account is disabled");
    setToken(`demo:${user.id}`);
    return user;
  },

  me: async () => delay(currentUser(), 60),
  users: async () => delay([...db().users], 80),

  createUser: async (body: {
    username: string;
    password: string;
    display_name?: string;
    role: Role;
  }) => {
    const store = db();
    if (store.users.some((u) => u.username === body.username)) {
      throw new ApiError(409, "That username is taken");
    }
    const user: User = {
      id: newId("u"),
      username: body.username,
      display_name: body.display_name || body.username,
      role: body.role,
      is_active: true,
      created_at: new Date().toISOString(),
    };
    store.users.push(user);
    save();
    return delay(user, 180);
  },

  updateUser: async (
    id: string,
    body: Partial<{ role: Role; is_active: boolean; password: string; display_name: string }>,
  ) => {
    const user = db().users.find((u) => u.id === id);
    if (!user) throw new ApiError(404, "No such user");
    if (body.role !== undefined) user.role = body.role;
    if (body.is_active !== undefined) user.is_active = body.is_active;
    if (body.display_name !== undefined) user.display_name = body.display_name;
    save();
    return delay(user, 140);
  },

  tasks: async () => delay(TASKS, 60),

  health: async () =>
    delay(
      {
        version: "1.0.0-demo",
        control_hz: 30,
        active_sessions: 0,
        max_sessions: 4,
        tasks: TASKS.map((t) => t.id),
        anonymize_faces: false,
      } as Record<string, any>,
      60,
    ),

  demos: async (
    params: {
      task_id?: string;
      status?: DemoStatus;
      label?: LabelValue;
      needs_review?: boolean;
      operator_id?: string;
      limit?: number;
      offset?: number;
    } = {},
  ): Promise<DemoPage> => {
    const limit = params.limit ?? 50;
    const offset = params.offset ?? 0;
    const matching = db()
      .demos.filter(
        (demo) =>
          (!params.task_id || demo.task_id === params.task_id) &&
          (!params.status || demo.status === params.status) &&
          (!params.label || demo.label === params.label) &&
          (!params.operator_id || demo.operator_id === params.operator_id) &&
          (!params.needs_review || demo.status === "recorded"),
      )
      .sort((a, b) => b.created_at.localeCompare(a.created_at));
    return delay({
      items: matching.slice(offset, offset + limit),
      total: matching.length,
      limit,
      offset,
    });
  },

  demo: async (id: string) => {
    const demo = db().demos.find((d) => d.id === id);
    if (!demo) throw new ApiError(404, "No such recording");
    return delay(demo, 90);
  },

  summary: async () => delay(summarise(), 90),

  trajectory: async (id: string, stride = 1) => {
    const demo = db().demos.find((d) => d.id === id);
    if (!demo) throw new ApiError(404, "No such recording");
    return delay(trajectoryFor(demo, stride), 160);
  },

  review: async (
    id: string,
    body: {
      label?: LabelValue;
      approve?: boolean;
      trim_start?: number;
      trim_end?: number;
      notes?: string;
    },
  ) => {
    const store = db();
    const demo = store.demos.find((d) => d.id === id);
    if (!demo) throw new ApiError(404, "No such recording");
    const reviewer = currentUser();
    if (reviewer.role === "operator") {
      throw new ApiError(403, "Only a reviewer or an admin can review recordings");
    }

    if (body.label !== undefined) demo.label = body.label;
    if (body.trim_start !== undefined) demo.trim_start = body.trim_start;
    if (body.trim_end !== undefined) demo.trim_end = body.trim_end;
    if (body.notes !== undefined) demo.review_notes = body.notes;
    if (body.approve !== undefined) {
      demo.status = body.approve ? "approved" : "rejected";
      // Approving without an explicit label means "yes, this is the success the
      // auto-check saw", which is what the real endpoint infers too.
      if (body.approve && demo.label === null) demo.label = "success";
      demo.reviewer_id = reviewer.id;
      demo.reviewer_name = reviewer.display_name;
      demo.reviewed_at = new Date().toISOString();
    }
    save();
    return delay(demo, 220);
  },

  reopen: async (id: string) => {
    const demo = db().demos.find((d) => d.id === id);
    if (!demo) throw new ApiError(404, "No such recording");
    demo.status = "recorded";
    demo.reviewer_id = null;
    demo.reviewer_name = null;
    demo.reviewed_at = null;
    save();
    return delay(demo, 160);
  },

  deleteDemo: async (id: string) => {
    const store = db();
    store.demos = store.demos.filter((d) => d.id !== id);
    save();
    return delay(undefined as void, 160);
  },

  exports: async () => delay([...db().exports], 90),

  createExport: async (body: {
    name: string;
    format: string;
    tasks: string[];
    include_failures: boolean;
    overwrite: boolean;
  }) => {
    const store = db();
    if (!body.name.trim()) throw new ApiError(400, "Give the export a name");
    if (!body.overwrite && store.exports.some((e) => e.name === body.name)) {
      throw new ApiError(409, `An export named "${body.name}" already exists — tick Overwrite`);
    }

    const eligible = store.demos.filter(
      (demo) =>
        demo.status === "approved" &&
        (body.include_failures || demo.label === "success") &&
        (body.tasks.length === 0 || body.tasks.includes(demo.task_id)),
    );
    if (eligible.length === 0) {
      throw new ApiError(400, "No approved recordings match that filter");
    }

    const frames = eligible.reduce(
      (sum, demo) => sum + ((demo.trim_end ?? demo.num_frames) - demo.trim_start),
      0,
    );
    const record: DatasetExport = {
      id: newId("exp"),
      name: body.name,
      format: body.format,
      created_at: new Date().toISOString(),
      tasks: body.tasks,
      include_failures: body.include_failures,
      num_episodes: eligible.length,
      num_frames: frames,
      size_bytes: frames * (body.format === "lerobot" ? 6_100 : 5_400),
      path: `data/exports/${body.name}`,
      dvc_hash: Array.from(
        { length: 32 },
        () => "0123456789abcdef"[Math.floor(Math.random() * 16)],
      ).join(""),
    };
    store.exports = [record, ...store.exports.filter((e) => e.name !== body.name)];
    save();
    return delay(record, 900);
  },

  exportInfo: async (id: string) => {
    const record = db().exports.find((e) => e.id === id);
    if (!record) throw new ApiError(404, "No such export");
    return delay(record as unknown as Record<string, any>, 120);
  },

  deleteExport: async (id: string) => {
    const store = db();
    store.exports = store.exports.filter((e) => e.id !== id);
    save();
    return delay(undefined as void, 160);
  },

  dvc: async () =>
    delay(
      {
        available: true,
        status: { clean: true, remote: "s3://telecollect-demo/datasets" },
      },
      90,
    ),

  runs: async () =>
    delay(
      db()
        .runs.map((run) => ({ ...run, status: liveRunStatus(run) }))
        .sort((a, b) => b.created_at.localeCompare(a.created_at)),
      100,
    ),

  run: async (id: string) => {
    const run = db().runs.find((r) => r.id === id);
    if (!run) throw new ApiError(404, "No such run");
    return delay({ ...run, status: liveRunStatus(run) }, 90);
  },

  runLog: async (id: string) => {
    const run = db().runs.find((r) => r.id === id);
    if (!run) throw new ApiError(404, "No such run");
    return delay(runLogFor(run), 110);
  },

  runHistory: async (id: string) => {
    const run = db().runs.find((r) => r.id === id);
    if (!run) throw new ApiError(404, "No such run");
    return delay(runHistoryFor(run), 110);
  },

  /**
   * Starts a job that finishes after ~45 s of wall time.  The training page
   * polls every 4 s while anything is running, so the loss curve fills in and
   * the badge flips to "succeeded" on its own — no reload needed.
   */
  createRun: async (body: Record<string, unknown>) => {
    const store = db();
    const source = store.exports.find((e) => e.id === body.export_id);
    const run: TrainingRun = {
      id: newId("run"),
      name: String(body.name || "untitled"),
      export_id: (body.export_id as string) ?? null,
      created_at: new Date().toISOString(),
      status: "running",
      config: { ...body, export_name: source?.name ?? "", __demo_live: true },
      metrics: {
        best_val_l1: 0.0402,
        num_episodes: source?.num_episodes ?? 0,
        num_frames: source?.num_frames ?? 0,
        train_seconds: Math.round(TRAIN_DURATION_MS / 1000),
        device: "cuda",
      },
      output_dir: `data/runs/${String(body.name || "untitled")}`,
      error: "",
      finished_at: null,
    };
    store.runs = [run, ...store.runs];
    save();
    return delay(run, 320);
  },

  evals: async (training_run_id?: string) =>
    delay(
      db()
        .evals.map(liveEval)
        .filter((item) => !training_run_id || item.training_run_id === training_run_id)
        .sort((a, b) => b.created_at.localeCompare(a.created_at)),
      100,
    ),

  createEval: async (body: Record<string, unknown>) => {
    const store = db();
    const item: EvalRun = {
      id: newId("eval"),
      training_run_id: String(body.training_run_id),
      created_at: new Date().toISOString(),
      status: "running",
      task_id: String(body.task_id),
      num_episodes: Number(body.num_episodes ?? 25),
      success_rate: 0,
      mean_episode_length: 0,
      details: { __demo_live: true, record_videos: 3 },
      error: "",
      finished_at: null,
    };
    store.evals = [item, ...store.evals];
    save();
    return delay(item, 320);
  },
};

/**
 * Recording playback.
 *
 * There is no media server here, so every clip resolves to one of two files in
 * `public/demo/` — a real pick-and-place episode rendered out of the simulator.
 * That keeps the review timeline, the trim handles and the two synchronised
 * camera views behaving exactly as they do against live footage.
 */
export function mediaUrl(path: string) {
  return path.includes("wrist") ? "/demo/wrist.webm" : "/demo/front.webm";
}

export { resetDemoData } from "./demo-data";
