"use client";

/**
 * Demo build of the teleoperation client.
 *
 * The real one opens a WebSocket to the simulator, sends a command every
 * 33.3 ms and paints the JPEG frames the server streams back.  There is no
 * server here, so this file keeps the *interface* — `connect`, `disconnect`,
 * `reset`, `startRecording`, `stopRecording`, and the four callbacks — and
 * replaces the transport with a small kinematic toy simulator that runs in the
 * browser at the same 30 Hz and renders its own camera views onto a canvas.
 *
 * The input devices below (`InputCollector`, `KEY_BINDINGS`, `KEY_HELP`) are
 * copied verbatim from the real client: keyboard, mouse and gamepad handling is
 * pure browser code with no server in it, so the demo exercises exactly the
 * code the product ships.
 */

import { db, newId, save } from "./demo-data";
import type { Demo } from "./api";

export interface FrameState {
  seq: number;
  client_ts: number;
  server_ts: number;
  step: number;
  sim_t: number;
  state: number[];
  ee: number[];
  action: number[];
  success: boolean;
  task_id: string;
  recording: boolean;
  rec_frames: number;
  cmd_age_ms: number;
  dropped: number;
  tick_ms: number;
  work_ms: number;
  images: [string, number][];
}

export interface TeleopEvent {
  t: string;
  [key: string]: unknown;
}

export interface LatencyStats {
  rtt: number;
  rttP95: number;
  fps: number;
  cmdAge: number;
  tick: number;
  work: number;
  dropped: number;
  decode?: number;
  decodeP95?: number;
  decodeDropped?: number;
  visual?: number;
  visualP95?: number;
}

export interface AxisInput {
  linear: [number, number, number];
  angular: [number, number, number];
  gripper: number;
}

const CONTROL_HZ = 30;
const CONTROL_PERIOD_MS = 1000 / CONTROL_HZ;
const RTT_WINDOW = 90;

/** Metres per second at full stick deflection — matches the real teleop limit. */
const MAX_LIN_VEL = 0.28;
const MAX_ANG_VEL = 2.2;

const FRONT_SIZE = 256;
const WRIST_SIZE = 128;

// ---------------------------------------------------------------------------
// tiny 3-D helpers: just enough to draw a believable camera view
// ---------------------------------------------------------------------------

type Vec3 = [number, number, number];

const sub = (a: Vec3, b: Vec3): Vec3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const cross = (a: Vec3, b: Vec3): Vec3 => [
  a[1] * b[2] - a[2] * b[1],
  a[2] * b[0] - a[0] * b[2],
  a[0] * b[1] - a[1] * b[0],
];
const dot = (a: Vec3, b: Vec3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const norm = (a: Vec3): Vec3 => {
  const length = Math.hypot(a[0], a[1], a[2]) || 1;
  return [a[0] / length, a[1] / length, a[2] / length];
};

/** Pinhole camera: world point -> pixel, plus depth for painter's ordering. */
class Camera {
  private right: Vec3;
  private up: Vec3;
  private forward: Vec3;
  private eye: Vec3;
  private focal: number;
  private size: number;

  constructor(eye: Vec3, target: Vec3, worldUp: Vec3, focal: number, size: number) {
    this.eye = eye;
    this.focal = focal;
    this.size = size;
    this.forward = norm(sub(target, eye));
    this.right = norm(cross(this.forward, worldUp));
    this.up = cross(this.right, this.forward);
  }

  project(p: Vec3): { x: number; y: number; depth: number } {
    const d = sub(p, this.eye);
    const depth = Math.max(0.02, dot(d, this.forward));
    return {
      x: this.size / 2 + (this.focal * dot(d, this.right)) / depth,
      y: this.size / 2 - (this.focal * dot(d, this.up)) / depth,
      depth,
    };
  }
}

// ---------------------------------------------------------------------------
// the toy simulator
// ---------------------------------------------------------------------------

/** Table top, matching the real arena: x in [-0.16, 0.76], y in [-0.42, 0.42]. */
const TABLE = { x0: -0.16, x1: 0.76, y0: -0.42, y1: 0.42 };
const CUBE_HALF = 0.021;
const TARGET_RADIUS = 0.055;
const SUCCESS_HOLD_TICKS = 12; // 0.4 s, same rule as the simulator

interface World {
  ee: Vec3;
  yaw: number;
  gripper: number;
  cube: Vec3;
  cubeYaw: number;
  target: [number, number];
  held: boolean;
  success: boolean;
  successStreak: number;
  successFrame: number | null;
}

function randomWorld(seed: number): World {
  let a = seed >>> 0;
  const random = () => {
    a = (a * 1664525 + 1013904223) >>> 0;
    return a / 4294967296;
  };
  const cube: Vec3 = [0.27 + random() * 0.17, -0.18 + random() * 0.36, CUBE_HALF];
  let target: [number, number] = [0.27 + random() * 0.17, -0.18 + random() * 0.36];
  // Keep the goal away from the cube, or the episode starts already solved.
  while (Math.hypot(target[0] - cube[0], target[1] - cube[1]) < 0.16) {
    target = [0.27 + random() * 0.17, -0.18 + random() * 0.36];
  }
  return {
    ee: [0.36, 0, 0.22],
    yaw: 0,
    gripper: 1,
    cube,
    cubeYaw: random() * Math.PI,
    target,
    held: false,
    success: false,
    successStreak: 0,
    successFrame: null,
  };
}

function clamp(value: number, low: number, high: number) {
  return Math.min(high, Math.max(low, value));
}

/** One control tick of the toy physics. */
function advance(world: World, input: AxisInput, dt: number) {
  // The operator's keyboard frame is the front camera's: "forward" is -x.
  world.ee[0] = clamp(world.ee[0] + input.linear[0] * MAX_LIN_VEL * dt, 0.14, 0.54);
  world.ee[1] = clamp(world.ee[1] + input.linear[1] * MAX_LIN_VEL * dt, -0.3, 0.3);
  world.ee[2] = clamp(world.ee[2] + input.linear[2] * MAX_LIN_VEL * dt, 0.028, 0.4);
  world.yaw += input.angular[2] * MAX_ANG_VEL * dt;
  world.gripper = input.gripper;

  const closed = world.gripper < 0.5;
  const reach = Math.hypot(
    world.ee[0] - world.cube[0],
    world.ee[1] - world.cube[1],
    world.ee[2] - (world.cube[2] + 0.012),
  );

  if (!world.held && closed && reach < 0.045) world.held = true;
  if (world.held && !closed) world.held = false;

  if (world.held) {
    world.cube = [world.ee[0], world.ee[1], Math.max(CUBE_HALF, world.ee[2] - 0.012)];
  } else {
    // Gravity, such as it is.
    world.cube[2] = Math.max(CUBE_HALF, world.cube[2] - 1.2 * dt);
  }

  const onTarget =
    !world.held &&
    world.cube[2] <= CUBE_HALF + 0.002 &&
    Math.hypot(world.cube[0] - world.target[0], world.cube[1] - world.target[1]) < TARGET_RADIUS;
  world.successStreak = onTarget ? world.successStreak + 1 : 0;
  world.success = world.successStreak >= SUCCESS_HOLD_TICKS;
}

/**
 * A 6-joint pose that puts the wrist roughly where the end effector is.
 * Not real inverse kinematics — just enough that the joint readout on the
 * console moves coherently with the arm instead of showing dead numbers.
 */
function pseudoJoints(world: World): number[] {
  const [x, y, z] = world.ee;
  const radius = Math.hypot(x, y);
  const waist = Math.atan2(y, x);
  const shoulder = clamp(1.1 - radius * 2.2 - (z - 0.2) * 1.4, -1.85, 1.25);
  const elbow = clamp(-0.6 + radius * 2.0 + (0.25 - z) * 1.1, -1.76, 1.6);
  const wristAngle = clamp(1.45 - shoulder - elbow, -1.86, 2.23);
  return [waist, shoulder, elbow, 0, wristAngle, world.yaw, world.gripper];
}

// ---------------------------------------------------------------------------
// rendering
// ---------------------------------------------------------------------------

function makeCanvas(size: number): HTMLCanvasElement {
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  return canvas;
}

function fillPolygon(
  context: CanvasRenderingContext2D,
  points: { x: number; y: number }[],
  fill: string,
  stroke?: string,
) {
  context.beginPath();
  points.forEach((p, index) => (index ? context.lineTo(p.x, p.y) : context.moveTo(p.x, p.y)));
  context.closePath();
  context.fillStyle = fill;
  context.fill();
  if (stroke) {
    context.strokeStyle = stroke;
    context.lineWidth = 1;
    context.stroke();
  }
}

function drawBox(
  context: CanvasRenderingContext2D,
  camera: Camera,
  centre: Vec3,
  half: number,
  yaw: number,
  colour: [number, number, number],
) {
  const c = Math.cos(yaw);
  const s = Math.sin(yaw);
  const corners: Vec3[] = [];
  for (const sz of [-1, 1]) {
    for (const [sx, sy] of [
      [-1, -1],
      [1, -1],
      [1, 1],
      [-1, 1],
    ]) {
      corners.push([
        centre[0] + half * (sx * c - sy * s),
        centre[1] + half * (sx * s + sy * c),
        centre[2] + half * sz,
      ]);
    }
  }
  const faces: [number[], number][] = [
    [[0, 1, 2, 3], 0.55], // bottom
    [[4, 5, 6, 7], 1.0], // top
    [[0, 1, 5, 4], 0.78],
    [[1, 2, 6, 5], 0.68],
    [[2, 3, 7, 6], 0.78],
    [[3, 0, 4, 7], 0.68],
  ];
  const projected = corners.map((corner) => camera.project(corner));
  // Painter's algorithm: far faces first, so the near ones cover them.
  faces
    .map(([indices, shade]) => ({
      indices,
      shade,
      depth: indices.reduce((sum, i) => sum + projected[i].depth, 0) / indices.length,
    }))
    .sort((a, b) => b.depth - a.depth)
    .forEach(({ indices, shade }) => {
      const [r, g, b] = colour.map((v) => Math.round(v * shade));
      fillPolygon(
        context,
        indices.map((i) => projected[i]),
        `rgb(${r},${g},${b})`,
        "rgba(0,0,0,0.35)",
      );
    });
}

function drawSegment(
  context: CanvasRenderingContext2D,
  camera: Camera,
  from: Vec3,
  to: Vec3,
  width: number,
  colour: string,
) {
  const a = camera.project(from);
  const b = camera.project(to);
  context.beginPath();
  context.moveTo(a.x, a.y);
  context.lineTo(b.x, b.y);
  context.strokeStyle = colour;
  context.lineCap = "round";
  // Thin the line with distance so the arm reads as three-dimensional.
  context.lineWidth = (width * 0.6) / ((a.depth + b.depth) / 2);
  context.stroke();
}

function drawScene(
  context: CanvasRenderingContext2D,
  camera: Camera,
  world: World,
  size: number,
  showArm: boolean,
) {
  // Background and floor haze.
  const sky = context.createLinearGradient(0, 0, 0, size);
  sky.addColorStop(0, "#33415a");
  sky.addColorStop(1, "#12161f");
  context.fillStyle = sky;
  context.fillRect(0, 0, size, size);

  // Table.
  fillPolygon(
    context,
    [
      camera.project([TABLE.x0, TABLE.y0, 0]),
      camera.project([TABLE.x1, TABLE.y0, 0]),
      camera.project([TABLE.x1, TABLE.y1, 0]),
      camera.project([TABLE.x0, TABLE.y1, 0]),
    ],
    "#c8bda4",
    "rgba(0,0,0,0.25)",
  );

  // Target zone.
  const ring = Array.from({ length: 28 }, (_, i) => {
    const angle = (i / 28) * Math.PI * 2;
    return camera.project([
      world.target[0] + TARGET_RADIUS * Math.cos(angle),
      world.target[1] + TARGET_RADIUS * Math.sin(angle),
      0.001,
    ]);
  });
  fillPolygon(context, ring, world.success ? "rgba(52,211,153,0.75)" : "rgba(52,211,153,0.35)", "#34d399");

  if (showArm) {
    // Base column, then three links up to the wrist.  Purely illustrative —
    // the shoulder/elbow angles come from `pseudoJoints`.
    const base: Vec3 = [0, 0, 0];
    const shoulder: Vec3 = [0, 0, 0.127];
    const planar = Math.hypot(world.ee[0], world.ee[1]);
    const direction: Vec3 = [world.ee[0] / (planar || 1), world.ee[1] / (planar || 1), 0];
    const elbow: Vec3 = [
      direction[0] * planar * 0.42,
      direction[1] * planar * 0.42,
      0.127 + 0.24,
    ];
    const wrist: Vec3 = [
      world.ee[0] - direction[0] * 0.05,
      world.ee[1] - direction[1] * 0.05,
      world.ee[2] + 0.11,
    ];
    drawSegment(context, camera, base, shoulder, 26, "#2b3038");
    drawSegment(context, camera, shoulder, elbow, 20, "#3c434e");
    drawSegment(context, camera, elbow, wrist, 16, "#4a525f");
    drawSegment(context, camera, wrist, world.ee, 11, "#232830");
  }

  // Jaws: two bars either side of the grasp point, opening with the command.
  const gap = 0.012 + world.gripper * 0.023;
  const c = Math.cos(world.yaw);
  const s = Math.sin(world.yaw);
  for (const side of [-1, 1]) {
    const offset: Vec3 = [-s * gap * side, c * gap * side, 0];
    drawSegment(
      context,
      camera,
      [world.ee[0] + offset[0], world.ee[1] + offset[1], world.ee[2] + 0.035],
      [world.ee[0] + offset[0], world.ee[1] + offset[1], world.ee[2] - 0.012],
      7,
      world.held ? "#f59e0b" : "#8b93a1",
    );
  }

  drawBox(context, camera, world.cube, CUBE_HALF, world.cubeYaw, [214, 46, 46]);

  if (world.success) {
    context.fillStyle = "rgba(16,185,129,0.9)";
    context.fillRect(0, size - 22, size, 22);
    context.fillStyle = "#04140d";
    context.font = "bold 13px system-ui, sans-serif";
    context.fillText("TASK COMPLETE", 8, size - 6);
  }
}

function renderFront(context: CanvasRenderingContext2D, world: World) {
  const camera = new Camera([1.05, 0, 0.56], [0.32, 0, 0.06], [0, 0, 1], 300, FRONT_SIZE);
  drawScene(context, camera, world, FRONT_SIZE, true);
}

function renderWrist(context: CanvasRenderingContext2D, world: World) {
  // Mounted just behind and above the grasp point, looking down the tool axis.
  const camera = new Camera(
    [world.ee[0] - 0.05, world.ee[1], world.ee[2] + 0.1],
    [world.ee[0] + 0.01, world.ee[1], world.ee[2] - 0.1],
    [1, 0, 0],
    120,
    WRIST_SIZE,
  );
  drawScene(context, camera, world, WRIST_SIZE, false);
}

// ---------------------------------------------------------------------------
// the client
// ---------------------------------------------------------------------------

export class TeleopClient {
  private timer: number | null = null;
  private seq = 0;
  private rttSamples: number[] = [];
  private frameTimes: number[] = [];
  private open = false;
  private world = randomWorld(1);
  private taskId = "pick_place";
  private step = 0;
  private recording = false;
  private recFrames = 0;
  private recSeed = 0;
  private recLatencies: number[] = [];
  private busy = false;

  input: AxisInput = { linear: [0, 0, 0], angular: [0, 0, 0], gripper: 1 };

  onFrame: ((state: FrameState, images: Map<string, ImageBitmap>) => void) | null = null;
  onEvent: ((event: TeleopEvent) => void) | null = null;
  onStatus: ((status: "connecting" | "open" | "closed" | "error", detail?: string) => void) | null =
    null;
  onStats: ((stats: LatencyStats) => void) | null = null;

  private frontCanvas = makeCanvas(FRONT_SIZE);
  private wristCanvas = makeCanvas(WRIST_SIZE);

  /** Kept only so the call site matches the real client. */
  readonly token: string;

  constructor(token: string) {
    this.token = token;
  }

  connect(task: string) {
    this.taskId = task;
    this.onStatus?.("connecting");
    // A short handshake, so "Connecting…" is actually visible.
    window.setTimeout(() => {
      this.open = true;
      this.step = 0;
      this.world = randomWorld(Date.now() & 0xffff);
      this.onStatus?.("open");
      this.onEvent?.({ t: "hello", session_id: newId("session"), task_id: task });
      this.timer = window.setInterval(() => void this.tick(), CONTROL_PERIOD_MS);
    }, 420);
  }

  disconnect() {
    if (this.timer !== null) {
      window.clearInterval(this.timer);
      this.timer = null;
    }
    if (this.open) {
      this.open = false;
      this.onStatus?.("closed", "demo session ended");
    }
  }

  get connected() {
    return this.open;
  }

  reset(task?: string, seed?: number) {
    if (task) this.taskId = task;
    const nextSeed = seed ?? (Date.now() & 0xffff);
    this.world = randomWorld(nextSeed);
    this.step = 0;
    if (this.recording) this.stopRecording(false);
    this.onEvent?.({ t: "event", kind: "reset", task_id: this.taskId, seed: nextSeed });
  }

  startRecording() {
    if (!this.open || this.recording) return;
    this.recSeed = Date.now() & 0xffff;
    this.world = randomWorld(this.recSeed);
    this.step = 0;
    this.recording = true;
    this.recFrames = 0;
    this.recLatencies = [];
    this.onEvent?.({ t: "event", kind: "recording_started", seed: this.recSeed });
  }

  /**
   * Ends the take.  When kept, a recording is written into the demo store just
   * as the real backend writes a row to the database — which is what makes the
   * "Review the take just saved" link land on a page with real content.
   */
  stopRecording(keep: boolean) {
    if (!this.recording) return;
    this.recording = false;
    const frames = this.recFrames;
    if (!keep || frames < 5) {
      this.onEvent?.({ t: "event", kind: "recording_discarded" });
      return;
    }

    const sorted = [...this.recLatencies].sort((a, b) => a - b);
    const p50 = sorted[Math.floor(sorted.length / 2)] ?? 12;
    const store = db();
    const token = getTokenId();
    const operator = store.users.find((u) => u.id === token) ?? store.users[0];
    const demo: Demo = {
      id: newId("demo"),
      task_id: this.taskId,
      operator_id: operator.id,
      operator_name: operator.display_name,
      created_at: new Date().toISOString(),
      seed: this.recSeed,
      fps: CONTROL_HZ,
      num_frames: frames,
      duration_s: frames / CONTROL_HZ,
      size_bytes: frames * 6_100,
      auto_success: this.world.success,
      auto_success_frame: this.world.successFrame,
      latency_p50_ms: p50,
      latency_p95_ms: sorted[Math.floor(sorted.length * 0.95)] ?? p50 + 10,
      control_jitter_ms: 1.4,
      dropped_frames: 0,
      status: "recorded",
      label: null,
      trim_start: 0,
      trim_end: null,
      review_notes: "",
      reviewer_id: null,
      reviewer_name: null,
      reviewed_at: null,
    };
    store.demos = [demo, ...store.demos];
    save();

    this.onEvent?.({
      t: "event",
      kind: "recording_saved",
      episode_id: demo.id,
      frames,
      duration_s: demo.duration_s.toFixed(1),
      latency_p50_ms: p50.toFixed(0),
      auto_success: demo.auto_success,
    });
  }

  // -- the loop ---------------------------------------------------------
  private async tick() {
    if (!this.open || this.busy) return;
    this.busy = true;
    try {
      const startedAt = performance.now();
      const wasSuccess = this.world.success;
      advance(this.world, this.input, 1 / CONTROL_HZ);
      this.step += 1;
      if (this.world.success && !wasSuccess) {
        this.world.successFrame = this.recording ? this.recFrames : this.step;
        this.onEvent?.({ t: "event", kind: "task_success" });
      }
      if (this.recording) {
        this.recFrames += 1;
        if (this.recFrames >= 900) {
          this.onEvent?.({ t: "event", kind: "time_limit" });
          this.stopRecording(true);
        }
      }

      const front = this.frontCanvas.getContext("2d");
      const wrist = this.wristCanvas.getContext("2d");
      if (front) renderFront(front, this.world);
      if (wrist) renderWrist(wrist, this.world);

      const images = new Map<string, ImageBitmap>([
        ["front", await createImageBitmap(this.frontCanvas)],
        ["wrist", await createImageBitmap(this.wristCanvas)],
      ]);

      const work = performance.now() - startedAt;
      // Everything is local, so there is no true round trip to report; this is
      // the honest local number (render + decode) plus a plausible link delay,
      // and it is labelled as a demo in the README rather than dressed up.
      const rtt = work + 6 + Math.random() * 9;
      this.rttSamples.push(rtt);
      if (this.rttSamples.length > RTT_WINDOW) this.rttSamples.shift();
      if (this.recording) this.recLatencies.push(rtt);

      const now = performance.now();
      this.frameTimes.push(now);
      while (this.frameTimes.length && now - this.frameTimes[0] > 1000) this.frameTimes.shift();

      this.seq += 1;
      const joints = pseudoJoints(this.world);
      this.onFrame?.(
        {
          seq: this.seq,
          client_ts: now,
          server_ts: now,
          step: this.step,
          sim_t: this.step / CONTROL_HZ,
          state: joints,
          ee: [...this.world.ee, 0, 0, 1, 0],
          action: joints,
          success: this.world.success,
          task_id: this.taskId,
          recording: this.recording,
          rec_frames: this.recFrames,
          cmd_age_ms: 2 + Math.random() * 4,
          dropped: 0,
          tick_ms: CONTROL_PERIOD_MS + (Math.random() - 0.5) * 2,
          work_ms: work,
          images: [
            ["front", 0],
            ["wrist", 0],
          ],
        },
        images,
      );
      this.onStats?.({
        rtt,
        rttP95: percentile(this.rttSamples, 95),
        fps: this.frameTimes.length,
        cmdAge: 2 + Math.random() * 4,
        tick: CONTROL_PERIOD_MS,
        work,
        dropped: 0,
      });
    } finally {
      this.busy = false;
    }
  }
}

function getTokenId() {
  if (typeof window === "undefined") return null;
  const token = window.localStorage.getItem("telecollect.token");
  return token?.startsWith("demo:") ? token.slice(5) : null;
}

function percentile(values: number[], p: number) {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const index = Math.min(sorted.length - 1, Math.floor((p / 100) * sorted.length));
  return sorted[index];
}

// ---------------------------------------------------------------------------
// input devices -- unchanged from the real client
// ---------------------------------------------------------------------------

/**
 * Keyboard mapping follows robosuite's Keyboard driver. Its ArrowUp command is
 * already -x, which matches the front camera view: the camera looks back along
 * -x, so "forward" on the keyboard is -x in the world.
 */
export const KEY_BINDINGS: Record<string, { axis: "linear" | "angular"; index: number; sign: number }> = {
  ArrowUp: { axis: "linear", index: 0, sign: -1 },
  ArrowDown: { axis: "linear", index: 0, sign: +1 },
  ArrowLeft: { axis: "linear", index: 1, sign: -1 },
  ArrowRight: { axis: "linear", index: 1, sign: +1 },
  Period: { axis: "linear", index: 2, sign: -1 },
  Semicolon: { axis: "linear", index: 2, sign: +1 },
  KeyY: { axis: "angular", index: 0, sign: +1 },
  KeyH: { axis: "angular", index: 0, sign: -1 },
  KeyE: { axis: "angular", index: 1, sign: -1 },
  KeyR: { axis: "angular", index: 1, sign: +1 },
  KeyP: { axis: "angular", index: 2, sign: +1 },
  KeyO: { axis: "angular", index: 2, sign: -1 },
};

export const KEY_HELP: [string, string][] = [
  ["Up / Down", "Move away / toward the camera"],
  ["Left / Right", "Move left / right"],
  [". / ;", "Lower / raise"],
  ["E / R", "Rotate the wrist (roll)"],
  ["Y / H", "Rotate the wrist (pitch)"],
  ["O / P", "Rotate the wrist (yaw)"],
  ["Space", "Toggle the gripper"],
  ["Shift", "Precision mode (30 % speed)"],
];

const PRECISION_SCALE = 0.3;

export class InputCollector {
  private pressed = new Set<string>();
  private gripperClosed = false;
  private pointer: { dx: number; dy: number } = { dx: 0, dy: 0 };
  private wheel = 0;
  gamepadIndex: number | null = null;

  onGripperChange: ((closed: boolean) => void) | null = null;

  keyDown(event: KeyboardEvent): boolean {
    if (event.code === "Space") {
      if (!event.repeat) {
        this.gripperClosed = !this.gripperClosed;
        this.onGripperChange?.(this.gripperClosed);
      }
      return true;
    }
    if (event.code === "ShiftLeft" || event.code === "ShiftRight" || event.code in KEY_BINDINGS) {
      this.pressed.add(event.code);
      return true;
    }
    return false;
  }

  keyUp(event: KeyboardEvent): boolean {
    return this.pressed.delete(event.code);
  }

  clear() {
    this.pressed.clear();
    this.pointer = { dx: 0, dy: 0 };
    this.wheel = 0;
  }

  /** Mouse drag over the camera view: horizontal -> world y, vertical -> world x. */
  pointerDrag(dx: number, dy: number) {
    this.pointer = { dx, dy };
  }
  pointerRelease() {
    this.pointer = { dx: 0, dy: 0 };
  }
  wheelDelta(delta: number) {
    this.wheel = delta;
  }

  setGripper(closed: boolean) {
    this.gripperClosed = closed;
    this.onGripperChange?.(closed);
  }

  sample(): AxisInput {
    const linear: [number, number, number] = [0, 0, 0];
    const angular: [number, number, number] = [0, 0, 0];

    for (const code of this.pressed) {
      const binding = KEY_BINDINGS[code];
      if (!binding) continue;
      const target = binding.axis === "linear" ? linear : angular;
      target[binding.index] += binding.sign;
    }

    // Mouse: normalised drag distance, clamped so a big flick is still a
    // bounded command rather than a lurch.
    linear[0] += clamp(this.pointer.dy / 120, -1, 1);
    linear[1] += clamp(this.pointer.dx / 120, -1, 1);
    linear[2] += clamp(-this.wheel / 240, -1, 1);
    this.wheel *= 0.55; // decay: one notch of scroll is an impulse, not a hold

    const pad = this.readGamepad();
    if (pad) {
      linear[0] += pad.linear[0];
      linear[1] += pad.linear[1];
      linear[2] += pad.linear[2];
      angular[0] += pad.angular[0];
      angular[1] += pad.angular[1];
      angular[2] += pad.angular[2];
      if (pad.gripperClosed !== null) this.setGripper(pad.gripperClosed);
    }

    const precision =
      this.pressed.has("ShiftLeft") || this.pressed.has("ShiftRight") ? PRECISION_SCALE : 1;

    return {
      linear: linear.map((v) => clamp(v, -1, 1) * precision) as [number, number, number],
      angular: angular.map((v) => clamp(v, -1, 1) * precision) as [number, number, number],
      gripper: this.gripperClosed ? 1 : -1,
    };
  }

  /** Standard-layout gamepad: sticks for translation/rotation, triggers for
   *  height, shoulder buttons for the gripper. */
  private readGamepad():
    | { linear: [number, number, number]; angular: [number, number, number]; gripperClosed: boolean | null }
    | null {
    if (typeof navigator === "undefined" || !navigator.getGamepads) return null;
    const pads = navigator.getGamepads();
    const pad = pads.find((p) => p && p.connected);
    if (!pad) {
      this.gamepadIndex = null;
      return null;
    }
    this.gamepadIndex = pad.index;

    const dead = (v: number) => (Math.abs(v) < 0.12 ? 0 : v);
    const triggers = dead(pad.buttons[7]?.value ?? 0) - dead(pad.buttons[6]?.value ?? 0);
    let gripperClosed: boolean | null = null;
    if (pad.buttons[5]?.pressed) gripperClosed = true;
    else if (pad.buttons[4]?.pressed) gripperClosed = false;

    return {
      linear: [-dead(pad.axes[1] ?? 0), dead(pad.axes[0] ?? 0), triggers],
      angular: [0, -dead(pad.axes[3] ?? 0), -dead(pad.axes[2] ?? 0)],
      gripperClosed,
    };
  }
}
