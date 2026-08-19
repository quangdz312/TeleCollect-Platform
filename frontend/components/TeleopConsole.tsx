"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  InputCollector,
  KEY_HELP,
  type FrameState,
  type LatencyStats,
  type TeleopEvent,
} from "@/lib/teleop";
import { TeleopClient } from "@/lib/real-teleop";
import { getToken, type Task } from "@/lib/api";
import { Alert, Badge, Button, Card, Empty, Select, cx } from "@/components/ui";
import { HandControl } from "@/components/HandControl";
import type { AxisInput } from "@/lib/teleop";

type Status = "idle" | "connecting" | "open" | "closed" | "error";

interface LogLine {
  id: number;
  text: string;
  tone: "info" | "ok" | "bad";
  at: string;
}

type Quaternion = [number, number, number, number];
type RotationSafety = { twistDeg: number; limited: boolean };

const JOINT_LABELS = ["j1", "j2", "j3", "j4", "j5", "j6", "grip"];

export function TeleopConsole({ tasks }: { tasks: Task[] }) {
  const frontRef = useRef<HTMLCanvasElement | null>(null);
  const wristRef = useRef<HTMLCanvasElement | null>(null);
  const clientRef = useRef<TeleopClient | null>(null);
  const frameRef = useRef<FrameState | null>(null);
  const rotationBaselineRef = useRef<Quaternion | null>(null);
  const inputRef = useRef(new InputCollector());
  const dragRef = useRef<{ active: boolean; x: number; y: number }>({
    active: false,
    x: 0,
    y: 0,
  });
  const logId = useRef(0);
  const handInputRef = useRef<AxisInput | null>(null);

  const [taskId, setTaskId] = useState(tasks[0]?.id ?? "pick_place");
  const [status, setStatus] = useState<Status>("idle");
  const [statusDetail, setStatusDetail] = useState<string>("");
  const [frame, setFrame] = useState<FrameState | null>(null);
  const [stats, setStats] = useState<LatencyStats | null>(null);
  const [gripperClosed, setGripperClosed] = useState(false);
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [gamepad, setGamepad] = useState<string | null>(null);
  const [lastSaved, setLastSaved] = useState<string | null>(null);
  const [rotationSafety, setRotationSafety] = useState<RotationSafety>({ twistDeg: 0, limited: false });

  const task = useMemo(() => tasks.find((t) => t.id === taskId), [tasks, taskId]);

  const pushLog = useCallback((text: string, tone: LogLine["tone"] = "info") => {
    logId.current += 1;
    setLogs((previous) =>
      [
        {
          id: logId.current,
          text,
          tone,
          at: new Date().toLocaleTimeString(),
        },
        ...previous,
      ].slice(0, 60),
    );
  }, []);

  const handleHandInput = useCallback((input: AxisInput | null) => {
    if (!input) {
      handInputRef.current = null;
      return;
    }
    const mapped = applyRotationSafety(input, frameRef.current?.ee, rotationBaselineRef);
    handInputRef.current = mapped.input;
    setRotationSafety(mapped.safety);
  }, []);

  const handleHandGripper = useCallback((closed: boolean) => {
    inputRef.current.setGripper(closed);
  }, []);

  // -- connection ------------------------------------------------------
  const connect = useCallback(() => {
    const token = getToken();
    if (!token) return;
    clientRef.current?.disconnect();
    rotationBaselineRef.current = null;
    setRotationSafety({ twistDeg: 0, limited: false });

    const client = new TeleopClient(token);
    client.onStatus = (next, detail) => {
      setStatus(next);
      setStatusDetail(detail ?? "");
      if (next === "closed") pushLog(`Disconnected${detail ? `: ${detail}` : ""}`, "bad");
      if (next === "open") pushLog("Connected to the simulator", "ok");
    };
    client.onFrame = (state, images) => {
      frameRef.current = state;
      setFrame(state);
      paint(frontRef.current, images.get("front"));
      paint(wristRef.current, images.get("wrist"));
    };
    client.onStats = setStats;
    client.onEvent = (event: TeleopEvent) => handleEvent(event, pushLog, setLastSaved);
    client.connect(taskId);
    clientRef.current = client;
  }, [taskId, pushLog]);

  const disconnect = useCallback(() => {
    clientRef.current?.disconnect();
    clientRef.current = null;
    frameRef.current = null;
    rotationBaselineRef.current = null;
    setRotationSafety({ twistDeg: 0, limited: false });
    setStatus("idle");
    setFrame(null);
  }, []);

  useEffect(() => () => clientRef.current?.disconnect(), []);

  // -- input pump ------------------------------------------------------
  useEffect(() => {
    const collector = inputRef.current;
    collector.onGripperChange = setGripperClosed;

    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      if (collector.keyDown(event)) event.preventDefault();
    };
    const onKeyUp = (event: KeyboardEvent) => {
      if (collector.keyUp(event)) event.preventDefault();
    };
    const onBlur = () => collector.clear();

    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    window.addEventListener("blur", onBlur);

    let raf = 0;
    const pump = () => {
      const client = clientRef.current;
      if (client) client.input = handInputRef.current ?? collector.sample();
      const pads = navigator.getGamepads?.() ?? [];
      const pad = Array.from(pads).find((p) => p && p.connected);
      setGamepad(pad ? pad.id.slice(0, 40) : null);
      raf = requestAnimationFrame(pump);
    };
    raf = requestAnimationFrame(pump);

    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      window.removeEventListener("blur", onBlur);
      cancelAnimationFrame(raf);
    };
  }, []);

  // -- mouse on the camera view ----------------------------------------
  const onPointerDown = (event: React.PointerEvent<HTMLCanvasElement>) => {
    (event.target as HTMLCanvasElement).setPointerCapture(event.pointerId);
    dragRef.current = { active: true, x: event.clientX, y: event.clientY };
  };
  const onPointerMove = (event: React.PointerEvent<HTMLCanvasElement>) => {
    if (!dragRef.current.active) return;
    inputRef.current.pointerDrag(
      event.clientX - dragRef.current.x,
      event.clientY - dragRef.current.y,
    );
  };
  const onPointerUp = (event: React.PointerEvent<HTMLCanvasElement>) => {
    (event.target as HTMLCanvasElement).releasePointerCapture(event.pointerId);
    dragRef.current.active = false;
    inputRef.current.pointerRelease();
  };
  const onWheel = (event: React.WheelEvent<HTMLCanvasElement>) => {
    inputRef.current.wheelDelta(event.deltaY);
  };

  const connected = status === "open";
  const recording = frame?.recording ?? false;

  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
      <div className="space-y-5">
        <Card
          title={
            <span className="flex items-center gap-2">
              Teleoperation console
              <StatusPill status={status} detail={statusDetail} />
              {recording && (
                <span className="inline-flex items-center gap-1.5 text-bad-400">
                  <span className="h-2 w-2 animate-pulse rounded-full bg-bad-400" />
                  <span className="text-xs font-semibold">REC {frame?.rec_frames}</span>
                </span>
              )}
            </span>
          }
          subtitle={task?.instruction}
          actions={
            <>
              <Select
                value={taskId}
                disabled={connected}
                onChange={(e) => setTaskId(e.target.value)}
                className="w-44"
              >
                {tasks.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.title}
                  </option>
                ))}
              </Select>
              {connected ? (
                <Button variant="danger" onClick={disconnect}>
                  Disconnect
                </Button>
              ) : (
                <Button variant="primary" onClick={connect}>
                  Connect
                </Button>
              )}
            </>
          }
        >
          {/* Sized to whatever vertical space is left rather than to a fixed
              number of pixels: `flex-1 min-h-0` takes the remainder of the card
              and the square aspect derives the width from it.  That keeps the
              view as large as it can be while the transport buttons under it
              stay on screen, at any window height, with no scrolling. */}
          <div className="flex flex-col xl:h-[calc(100dvh-11.5rem)]">
          <div className="relative mx-auto aspect-square min-h-0 w-auto flex-1 overflow-hidden rounded-lg border border-ink-700 bg-black">
            <canvas
              ref={frontRef}
              width={256}
              height={256}
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
              onPointerCancel={onPointerUp}
              onWheel={onWheel}
              className={cx(
                "block h-full w-full touch-none select-none",
                connected ? "cursor-grab active:cursor-grabbing" : "opacity-30",
              )}
              style={{ imageRendering: "auto" }}
            />
            <canvas
              ref={wristRef}
              width={128}
              height={128}
              className="absolute bottom-3 right-3 h-32 w-32 rounded-md border border-ink-600 bg-black shadow-lg"
            />
            {frame?.success && (
              <div className="absolute left-3 top-3 rounded-md bg-ok-600/90 px-2.5 py-1 text-xs font-semibold text-white">
                Task complete
              </div>
            )}
            {!connected && (
              <div className="absolute inset-0 grid place-items-center text-sm text-ink-400">
                {status === "connecting" ? "Connecting…" : "Not connected"}
              </div>
            )}
          </div>

          <div className="mt-2 flex shrink-0 flex-wrap items-center gap-2">
            <Button
              variant={recording ? "danger" : "success"}
              disabled={!connected}
              onClick={() =>
                recording
                  ? clientRef.current?.stopRecording(true)
                  : clientRef.current?.startRecording()
              }
            >
              {recording ? "Stop & save" : "Start recording"}
            </Button>
            <Button
              variant="subtle"
              disabled={!connected || !recording}
              onClick={() => clientRef.current?.stopRecording(false)}
            >
              Discard take
            </Button>
            <Button
              variant="ghost"
              disabled={!connected}
              onClick={() => {
                rotationBaselineRef.current = null;
                setRotationSafety({ twistDeg: 0, limited: false });
                clientRef.current?.reset();
              }}
            >
              New scene
            </Button>
            <Button
              variant="ghost"
              disabled={!connected}
              onClick={() => {
                inputRef.current.setGripper(!gripperClosed);
              }}
            >
              {gripperClosed ? "Open gripper" : "Close gripper"}
            </Button>
            {lastSaved && (
              <Link
                href={`/review/${lastSaved}`}
                className="ml-auto text-xs text-accent-400 hover:underline"
              >
                Review the take just saved →
              </Link>
            )}
          </div>

          <p className="mt-2 shrink-0 text-xs text-ink-400">
            Recording starts from a fresh randomised scene. Drag on the view to move in the
            table plane, scroll to change height, or use the keyboard/gamepad.
          </p>
          </div>
        </Card>

        <Card title="Latency and control loop">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-7">
            <Metric
              label="Round trip"
              value={stats ? `${stats.rtt.toFixed(0)} ms` : "—"}
              tone={stats && stats.rtt > 90 ? "bad" : stats && stats.rtt > 55 ? "warn" : "ok"}
            />
            <Metric
              label="RTT p95"
              value={stats ? `${stats.rttP95.toFixed(0)} ms` : "—"}
            />
            <Metric label="Stream" value={stats ? `${stats.fps} fps` : "—"} />
            <Metric
              label="Command age"
              value={stats ? `${stats.cmdAge.toFixed(1)} ms` : "—"}
            />
            <Metric
              label="Tick interval"
              value={stats ? `${stats.tick.toFixed(1)} ms` : "—"}
              tone={stats && Math.abs(stats.tick - 33.3) > 8 ? "warn" : "ok"}
            />
            <Metric
              label="Server work"
              value={stats ? `${stats.work.toFixed(1)} ms` : "—"}
              tone={stats && stats.work > 28 ? "warn" : "ok"}
            />
            <Metric
              label="Frames dropped"
              value={stats ? String(stats.dropped) : "—"}
              tone={stats && stats.dropped > 0 ? "warn" : "ok"}
            />
            <Metric
              label="JPEG decode"
              value={stats?.decode !== undefined ? `${stats.decode.toFixed(1)} ms` : "—"}
              tone={stats?.decode !== undefined && stats.decode > 12 ? "warn" : "ok"}
            />
            <Metric
              label="Decode p95"
              value={stats?.decodeP95 !== undefined ? `${stats.decodeP95.toFixed(1)} ms` : "—"}
              tone={stats?.decodeP95 !== undefined && stats.decodeP95 > 20 ? "warn" : "ok"}
            />
            <Metric
              label="Decode frames skipped"
              value={stats?.decodeDropped !== undefined ? String(stats.decodeDropped) : "—"}
            />
            <Metric label="Visual latency" value={stats?.visual !== undefined ? `${stats.visual.toFixed(1)} ms` : "—"} tone={stats?.visual !== undefined && stats.visual > 100 ? "warn" : "ok"} />
            <Metric label="Visual p95" value={stats?.visualP95 !== undefined ? `${stats.visualP95.toFixed(1)} ms` : "—"} tone={stats?.visualP95 !== undefined && stats.visualP95 > 140 ? "bad" : "ok"} />
            <Metric label="Gripper twist" value={`${rotationSafety.twistDeg.toFixed(1)}°`} tone={rotationSafety.limited ? "bad" : Math.abs(rotationSafety.twistDeg) > 30 ? "warn" : "ok"} />
          </div>
          <p className="mt-3 text-xs text-ink-400">
            Round trip is measured on the browser clock: the server echoes back the timestamp of
            the command it acted on, so this is the true closed-loop delay from keypress to
            pixels, not a one-way estimate. Dropped frames mean this browser could not keep up
            with the stream — control is unaffected, since commands never queue.
          </p>
        </Card>
      </div>

      <div className="space-y-5">
        <Card title="Robot state">
          {frame ? (
            <div className="space-y-3">
              <div className="grid grid-cols-4 gap-2 text-xs">
                {frame.state.map((value, index) => (
                  <div
                    key={index}
                    className="rounded-md border border-ink-700/60 bg-ink-850/60 px-2 py-1.5"
                  >
                    <div className="text-[10px] uppercase text-ink-400">
                      {JOINT_LABELS[index]}
                    </div>
                    <div className="tabular">{value.toFixed(3)}</div>
                  </div>
                ))}
              </div>
              <div className="grid grid-cols-3 gap-2 text-xs">
                {["x", "y", "z"].map((axis, index) => (
                  <div
                    key={axis}
                    className="rounded-md border border-ink-700/60 bg-ink-850/60 px-2 py-1.5"
                  >
                    <div className="text-[10px] uppercase text-ink-400">ee {axis}</div>
                    <div className="tabular">{frame.ee[index].toFixed(3)} m</div>
                  </div>
                ))}
              </div>
              <div className="flex items-center justify-between text-xs text-ink-400">
                <span>step {frame.step}</span>
                <span>t = {frame.sim_t.toFixed(2)} s</span>
                <Badge tone={gripperClosed ? "warn" : "ok"}>
                  gripper {gripperClosed ? "closed" : "open"}
                </Badge>
              </div>
            </div>
          ) : (
            <Empty>Connect to see live telemetry.</Empty>
          )}
        </Card>

        <Card
          title="Controls"
          subtitle={gamepad ? `Gamepad: ${gamepad}` : "No gamepad detected"}
        >
          <dl className="space-y-1.5 text-xs">
            {KEY_HELP.map(([keys, description]) => (
              <div key={keys} className="flex items-center justify-between gap-3">
                <dt className="rounded border border-ink-600 bg-ink-850 px-1.5 py-0.5 font-mono text-[11px]">
                  {keys}
                </dt>
                <dd className="text-ink-400">{description}</dd>
              </div>
            ))}
          </dl>
          {task && task.hints.length > 0 && (
            <div className="mt-4 border-t border-ink-700/60 pt-3">
              <p className="mb-1.5 text-[11px] uppercase tracking-wider text-ink-400">
                Task hints
              </p>
              <ul className="list-disc space-y-1 pl-4 text-xs text-ink-300">
                {task.hints.map((hint) => (
                  <li key={hint}>{hint}</li>
                ))}
              </ul>
            </div>
          )}
        </Card>

        <Card title="Hand camera control" subtitle="Relative RGB depth via palm size">
          <HandControl
            onInput={handleHandInput}
            onGripper={handleHandGripper}
          />
        </Card>

        <Card title="Session log">
          {logs.length === 0 ? (
            <Empty>Nothing yet.</Empty>
          ) : (
            <ul className="max-h-64 space-y-1 overflow-y-auto text-xs">
              {logs.map((line) => (
                <li key={line.id} className="flex gap-2">
                  <span className="tabular text-ink-400">{line.at}</span>
                  <span
                    className={
                      line.tone === "ok"
                        ? "text-ok-400"
                        : line.tone === "bad"
                          ? "text-bad-400"
                          : "text-ink-300"
                    }
                  >
                    {line.text}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>

        {status === "error" && <Alert>Connection error. Check that the API is running.</Alert>}
      </div>
    </div>
  );
}

function applyRotationSafety(
  input: AxisInput,
  ee: number[] | undefined,
  baselineRef: React.MutableRefObject<Quaternion | null>,
): { input: AxisInput; safety: RotationSafety } {
  const rotation = input.angular[2];
  if (!ee || ee.length < 7) return { input, safety: { twistDeg: 0, limited: false } };
  const [qw, qx, qy, qz] = ee.slice(3, 7);
  const norm = Math.hypot(qw, qx, qy, qz);
  if (norm < 1e-6) return { input, safety: { twistDeg: 0, limited: false } };
  const w = qw / norm, x = qx / norm, y = qy / norm, z = qz / norm;
  const current: Quaternion = [w, x, y, z];
  if (baselineRef.current === null) baselineRef.current = current;
  const [bw, bx, by, bz] = baselineRef.current;
  const relativeW = bw * w + bx * x + by * y + bz * z;
  const relativeZ = bw * z - bx * y + by * x - bz * w;
  const twist = wrapRadians(2 * Math.atan2(relativeZ, relativeW));
  const softLimit = 30 * Math.PI / 180;
  const hardLimit = 45 * Math.PI / 180;
  const outward = Math.abs(twist) > 1e-4 && Math.sign(rotation) === Math.sign(twist);
  let scale = 1;
  if (outward && Math.abs(twist) >= softLimit) {
    scale = Math.max(0, Math.min(1, (hardLimit - Math.abs(twist)) / (hardLimit - softLimit)));
  }
  const safeRotation = outward ? rotation * scale : rotation;
  const toolZ: [number, number, number] = [
    2 * (x * z + w * y),
    2 * (y * z - w * x),
    1 - 2 * (x * x + y * y),
  ];
  return {
    input: {
      ...input,
      angular: toolZ.map((component) => component * safeRotation) as [number, number, number],
    },
    safety: { twistDeg: twist * 180 / Math.PI, limited: outward && scale <= 0.001 },
  };
}

function wrapRadians(angle: number) {
  return Math.atan2(Math.sin(angle), Math.cos(angle));
}

function handleEvent(
  event: TeleopEvent,
  pushLog: (text: string, tone?: LogLine["tone"]) => void,
  setLastSaved: (id: string) => void,
) {
  switch (event.t) {
    case "hello":
      pushLog(`Session ${String(event.session_id).slice(0, 8)} ready`, "ok");
      break;
    case "error":
      pushLog(String(event.message), "bad");
      break;
    case "event":
      switch (event.kind) {
        case "recording_started":
          pushLog(`Recording started (seed ${event.seed})`, "info");
          break;
        case "recording_saved":
          pushLog(
            `Saved ${event.frames} frames (${event.duration_s}s, ` +
              `latency p50 ${event.latency_p50_ms} ms) — ` +
              `${event.auto_success ? "task completed" : "task not completed"}`,
            event.auto_success ? "ok" : "info",
          );
          setLastSaved(String(event.episode_id));
          break;
        case "recording_discarded":
          pushLog("Take discarded", "info");
          break;
        case "task_success":
          pushLog("Success condition met", "ok");
          break;
        case "time_limit":
          pushLog("Time limit reached", "bad");
          break;
        case "reset":
          pushLog(`Scene reset (${event.task_id}, seed ${event.seed})`, "info");
          break;
        default:
          pushLog(String(event.kind));
      }
      break;
  }
}

function paint(canvas: HTMLCanvasElement | null, bitmap: ImageBitmap | undefined) {
  if (!canvas || !bitmap) return;
  if (canvas.width !== bitmap.width || canvas.height !== bitmap.height) {
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;
  }
  const context = canvas.getContext("2d", { alpha: false });
  context?.drawImage(bitmap, 0, 0);
}

function StatusPill({ status, detail }: { status: Status; detail: string }) {
  const tone =
    status === "open" ? "ok" : status === "connecting" ? "info" : status === "idle" ? "neutral" : "bad";
  return (
    <Badge tone={tone as "ok" | "info" | "neutral" | "bad"}>
      {status}
      {detail ? ` · ${detail}` : ""}
    </Badge>
  );
}

function Metric({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "ok" | "warn" | "bad";
}) {
  return (
    <div className="rounded-lg border border-ink-700/60 bg-ink-850/60 px-3 py-2">
      <div className="text-[10px] uppercase tracking-wider text-ink-400">{label}</div>
      <div
        className={cx(
          "mt-0.5 text-lg font-semibold tabular",
          tone === "bad" ? "text-bad-400" : tone === "warn" ? "text-warn-400" : "text-ink-100",
        )}
      >
        {value}
      </div>
    </div>
  );
}
