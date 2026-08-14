"use client";

import { API_ORIGIN, apiUrl } from "./api";
import type { AxisInput, FrameState, LatencyStats, TeleopEvent } from "./teleop";

const CONTROL_HZ = 30;
const RTT_WINDOW = 120;

type ObsMessage = {
  type: "obs";
  seq: number;
  sim_time: number;
  qpos: number[];
  qvel: number[];
  ee_pose: number[];
  gripper_closed: boolean;
  task_success: boolean;
  session_state: "idle" | "recording" | "closed";
  episode_id: string | null;
  recorded_steps: number;
};

type StatsMessage = {
  type: "stats";
  ticks: number;
  dropped_frames: number;
  p50_latency_ms?: number;
  p95_latency_ms: number;
  overruns: number;
  jitter_rms_ms?: number;
  control_hz_actual?: number;
};

type CreatedSession = {
  session_id: string;
  task_name: string;
  state: string;
  ws_url: string;
};

function percentile(values: number[], fraction: number): number {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const index = Math.min(sorted.length - 1, Math.ceil(fraction * sorted.length) - 1);
  return sorted[Math.max(0, index)];
}

function wsOrigin() {
  const configured = process.env.NEXT_PUBLIC_WS_BASE || API_ORIGIN;
  return configured.replace(/^http/, "ws").replace(/\/$/, "");
}

async function bitmapFromPayload(blob: Blob): Promise<{ camera: string; bitmap: ImageBitmap; decodeMs: number } | null> {
  if (blob.size < 2) return null;
  const cameraIndex = new Uint8Array(await blob.slice(0, 1).arrayBuffer())[0];
  const jpeg = blob.slice(1, undefined, "image/jpeg");
  const decodeStartedAt = performance.now();
  const bitmap = await createImageBitmap(jpeg);
  return {
    camera: cameraIndex === 1 ? "wrist" : "front",
    bitmap,
    decodeMs: performance.now() - decodeStartedAt,
  };
}

export class TeleopClient {
  private ws: WebSocket | null = null;
  private timer: number | null = null;
  private seq = 0;
  private sessionId: string | null = null;
  private taskId = "lift_cube";
  private inputSentAt = new Map<number, number>();
  private rttSamples: number[] = [];
  private frameTimes: number[] = [];
  private decodeSamples: number[] = [];
  private latestImages = new Map<string, ImageBitmap>();
  private latestObs: ObsMessage | null = null;
  private latestStats: StatsMessage | null = null;

  input: AxisInput = { linear: [0, 0, 0], angular: [0, 0, 0], gripper: 1 };

  onFrame: ((state: FrameState, images: Map<string, ImageBitmap>) => void) | null = null;
  onEvent: ((event: TeleopEvent) => void) | null = null;
  onStatus: ((status: "connecting" | "open" | "closed" | "error", detail?: string) => void) | null =
    null;
  onStats: ((stats: LatencyStats) => void) | null = null;

  constructor(readonly token: string) {}

  async connect(task: string) {
    this.disconnect(false);
    this.taskId = task;
    this.onStatus?.("connecting");

    try {
      const response = await fetch(apiUrl("/teleop/sessions"), {
        method: "POST",
        headers: {
          Authorization: `Bearer ${this.token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ task_name: task, image_size: 480 }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        const detail = body?.detail?.detail || body?.detail || `HTTP ${response.status}`;
        throw new Error(String(detail));
      }

      const created = (await response.json()) as CreatedSession;
      this.sessionId = created.session_id;
      const separator = created.ws_url.includes("?") ? "&" : "?";
      const ws = new WebSocket(`${wsOrigin()}${created.ws_url}${separator}token=${encodeURIComponent(this.token)}`);
      ws.binaryType = "blob";
      ws.onopen = () => {
        this.onStatus?.("open");
        this.onEvent?.({ t: "hello", session_id: created.session_id, task_id: created.task_name });
        this.timer = window.setInterval(() => this.sendInput(), 1000 / CONTROL_HZ);
      };
      ws.onmessage = (event) => void this.handleMessage(event.data);
      ws.onerror = () => {
        this.onStatus?.("error", "websocket error");
      };
      ws.onclose = () => {
        this.clearTimer();
        this.onStatus?.("closed");
      };
      this.ws = ws;
    } catch (error) {
      this.onStatus?.("error", (error as Error).message);
    }
  }

  disconnect(closeRemote = true) {
    if (closeRemote) this.send({ type: "session", action: "close" });
    this.clearTimer();
    this.ws?.close();
    this.ws = null;
    this.sessionId = null;
    this.latestImages.forEach((image) => image.close());
    this.latestImages.clear();
  }

  get connected() {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  reset(_task?: string, seed?: number) {
    this.send({ type: "scene", action: "reset", seed: seed ?? null });
  }

  startRecording() {
    this.send({ type: "record", action: "start" });
  }

  stopRecording(keep: boolean) {
    this.send({ type: "record", action: keep ? "stop" : "discard" });
  }

  private clearTimer() {
    if (this.timer !== null) {
      window.clearInterval(this.timer);
      this.timer = null;
    }
  }

  private send(payload: Record<string, unknown>) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(payload));
  }

  private sendInput() {
    const seq = ++this.seq;
    this.inputSentAt.set(seq, performance.now());
    if (this.inputSentAt.size > 300) {
      const oldest = this.inputSentAt.keys().next().value;
      if (oldest !== undefined) this.inputSentAt.delete(oldest);
    }
    this.send({
      type: "input",
      seq,
      client_time_ms: Date.now(),
      dx: this.input.linear[0],
      dy: this.input.linear[1],
      dz: this.input.linear[2],
      drx: this.input.angular[0],
      dry: this.input.angular[1],
      drz: this.input.angular[2],
      grip: this.input.gripper,
    });
  }

  private async handleMessage(data: unknown) {
    if (typeof data === "string") {
      this.handleJson(JSON.parse(data));
      return;
    }
    const payload = await bitmapFromPayload(data as Blob);
    if (!payload || !this.latestObs) return;
    this.latestImages.get(payload.camera)?.close();
    this.latestImages.set(payload.camera, payload.bitmap);
    this.decodeSamples.push(payload.decodeMs);
    if (this.decodeSamples.length > RTT_WINDOW) this.decodeSamples.shift();
    this.frameTimes.push(performance.now());
    while (this.frameTimes.length && performance.now() - this.frameTimes[0] > 1000) {
      this.frameTimes.shift();
    }
    this.emitFrame();
  }

  private handleJson(message: { type: string; [key: string]: unknown }) {
    if (message.type === "obs") {
      const obs = message as ObsMessage;
      this.latestObs = obs;
      const sentAt = this.inputSentAt.get(obs.seq);
      if (sentAt !== undefined) {
        this.rttSamples.push(performance.now() - sentAt);
        if (this.rttSamples.length > RTT_WINDOW) this.rttSamples.shift();
        this.inputSentAt.delete(obs.seq);
      }
      this.emitFrame();
      this.emitStats();
      return;
    }

    if (message.type === "stats") {
      this.latestStats = message as StatsMessage;
      this.emitStats();
      return;
    }

    if (message.type === "recording_started") {
      this.onEvent?.({ t: "event", kind: "recording_started", seed: "" });
      return;
    }
    if (message.type === "recording_saved") {
      this.onEvent?.({
        t: "event",
        kind: "recording_saved",
        episode_id: String(message.episode_id),
        frames: Number(message.num_steps ?? 0),
        duration_s: String(message.duration_s ?? "0"),
        latency_p50_ms: String(this.latestStats?.p50_latency_ms ?? 0),
        auto_success: Boolean(message.task_success),
      });
      return;
    }
    if (message.type === "recording_discarded") {
      this.onEvent?.({ t: "event", kind: "recording_discarded" });
      return;
    }
    if (message.type === "scene_reset") {
      this.onEvent?.({ t: "event", kind: "reset", task_id: this.taskId, seed: "" });
      return;
    }
    if (message.type === "error") {
      this.onEvent?.({ t: "error", message: `${message.code}: ${message.detail}` });
    }
  }

  private emitFrame() {
    const obs = this.latestObs;
    if (!obs) return;
    this.onFrame?.(
      {
        seq: obs.seq,
        client_ts: 0,
        server_ts: 0,
        step: this.latestStats?.ticks ?? obs.seq,
        sim_t: obs.sim_time,
        state: obs.qpos,
        ee: obs.ee_pose,
        action: obs.qvel,
        success: obs.task_success,
        task_id: this.taskId,
        recording: obs.session_state === "recording",
        rec_frames: obs.recorded_steps,
        cmd_age_ms: 0,
        dropped: this.latestStats?.dropped_frames ?? 0,
        tick_ms: 1000 / (this.latestStats?.control_hz_actual || CONTROL_HZ),
        work_ms: 0,
        images: [],
      },
      new Map(this.latestImages),
    );
  }

  private emitStats() {
    const stats = this.latestStats;
    const rtt = this.rttSamples.length ? this.rttSamples[this.rttSamples.length - 1] : 0;
    this.onStats?.({
      rtt,
      rttP95: percentile(this.rttSamples, 0.95),
      fps: this.frameTimes.length,
      cmdAge: 0,
      tick: 1000 / (stats?.control_hz_actual || CONTROL_HZ),
      work: stats?.p50_latency_ms ?? 0,
      dropped: stats?.dropped_frames ?? 0,
      decode: percentile(this.decodeSamples, 0.5),
      decodeP95: percentile(this.decodeSamples, 0.95),
    });
  }
}
