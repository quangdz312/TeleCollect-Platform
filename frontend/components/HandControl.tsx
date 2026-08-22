"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { DrawingUtils, FilesetResolver, HandLandmarker, type NormalizedLandmark } from "@mediapipe/tasks-vision";
import type { AxisInput } from "@/lib/teleop";
import { HandCommandMapper, type HandControlState } from "@/lib/hand-control";
import { Badge, Button } from "@/components/ui";

const WASM_URL = "/mediapipe/wasm";
const MODEL_URL = "/mediapipe/hand_landmarker.task";
const SAMPLE_WINDOW = 180;

type HandDiagnostics = {
  inferenceP50: number;
  inferenceP95: number;
  mappingP50: number;
  frameIntervalP50: number;
  cameraFps: number;
};

/** What the camera pane exposes so its buttons can live outside it. */
export type HandControls = {
  starting: boolean;
  enabled: boolean;
  detected: boolean;
  calibrated: boolean;
  active: boolean;
  toggleCamera: () => void;
  calibrate: () => void;
  toggleActive: () => void;
};

export function HandControl({
  onInput,
  onGripper,
  compact = false,
  onControls,
}: {
  onInput: (input: AxisInput | null) => void;
  onGripper: (closed: boolean) => void;
  /** Fills its container and drops the readouts, for the camera column. */
  compact?: boolean;
  /** Receives the camera's controls so the parent can render them elsewhere. */
  onControls?: (controls: HandControls) => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const mapperRef = useRef(new HandCommandMapper());
  const landmarkRef = useRef<NormalizedLandmark[] | undefined>(undefined);
  const worldLandmarkRef = useRef<NormalizedLandmark[] | undefined>(undefined);
  const activeRef = useRef(false);
  const rafRef = useRef<number | null>(null);
  const landmarkerRef = useRef<HandLandmarker | null>(null);
  const inferenceSamples = useRef<number[]>([]);
  const mappingSamples = useRef<number[]>([]);
  const intervalSamples = useRef<number[]>([]);
  const previousFrameAt = useRef<number | null>(null);
  const previousVideoTime = useRef(-1);
  const diagnosticsUpdatedAt = useRef(0);
  const [enabled, setEnabled] = useState(false);
  const [starting, setStarting] = useState(false);
  const [active, setActive] = useState(false);
  const [status, setStatus] = useState("Camera off");
  const [state, setState] = useState<HandControlState | null>(null);
  const [diagnostics, setDiagnostics] = useState<HandDiagnostics | null>(null);

  useEffect(() => { activeRef.current = active; }, [active]);

  const stop = useCallback(() => {
    if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    rafRef.current = null;
    landmarkerRef.current?.close();
    landmarkerRef.current = null;
    setEnabled(false);
    setStarting(false);
    setActive(false);
    activeRef.current = false;
    videoRef.current?.srcObject && (videoRef.current.srcObject as MediaStream).getTracks().forEach((track) => track.stop());
    if (videoRef.current) videoRef.current.srcObject = null;
    onInput(null);
    mapperRef.current.reset();
    landmarkRef.current = undefined;
    worldLandmarkRef.current = undefined;
    setState(null);
    setDiagnostics(null);
    inferenceSamples.current = [];
    mappingSamples.current = [];
    intervalSamples.current = [];
    previousFrameAt.current = null;
    previousVideoTime.current = -1;
    setStatus("Camera off");
  }, [onInput]);

  const start = useCallback(async () => {
    try {
      if (starting || enabled) return;
      setStarting(true);
      setStatus("Loading MediaPipe…");
      const vision = await FilesetResolver.forVisionTasks(WASM_URL);
      const landmarker = await HandLandmarker.createFromOptions(vision, {
        baseOptions: { modelAssetPath: MODEL_URL, delegate: "GPU" },
        runningMode: "VIDEO",
        numHands: 1,
        minHandDetectionConfidence: 0.6,
        minTrackingConfidence: 0.6,
      });
      landmarkerRef.current = landmarker;
      setStatus("Requesting camera permission…");
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user", width: 640, height: 480 }, audio: false });
      const video = videoRef.current;
      if (!video) return;
      video.srcObject = stream;
      await video.play();
      setEnabled(true);
      setStarting(false);
      setStatus("Show one hand, then calibrate");
      const detect = () => {
        if (!video.srcObject || landmarkerRef.current !== landmarker) return;
        // requestAnimationFrame can run faster than the webcam. Do not run
        // MediaPipe twice for the same source frame.
        if (video.currentTime === previousVideoTime.current) {
          rafRef.current = requestAnimationFrame(detect);
          return;
        }
        previousVideoTime.current = video.currentTime;
        const frameAt = performance.now();
        if (previousFrameAt.current !== null) {
          pushSample(intervalSamples.current, frameAt - previousFrameAt.current);
        }
        previousFrameAt.current = frameAt;
        const inferenceStartedAt = performance.now();
        const result = landmarker.detectForVideo(video, frameAt);
        pushSample(inferenceSamples.current, performance.now() - inferenceStartedAt);
        landmarkRef.current = result.landmarks[0];
        worldLandmarkRef.current = result.worldLandmarks[0];
        const mappingStartedAt = performance.now();
        const next = mapperRef.current.update(
          landmarkRef.current,
          activeRef.current,
          frameAt,
          worldLandmarkRef.current,
        );
        pushSample(mappingSamples.current, performance.now() - mappingStartedAt);
        setState(next);
        onInput(next.active && next.detected ? next.input : null);
        onGripper(next.input.gripper > 0);
        draw(canvasRef.current, video, result.landmarks[0]);
        if (frameAt - diagnosticsUpdatedAt.current >= 250) {
          diagnosticsUpdatedAt.current = frameAt;
          const frameIntervalP50 = percentile(intervalSamples.current, 0.5);
          setDiagnostics({
            inferenceP50: percentile(inferenceSamples.current, 0.5),
            inferenceP95: percentile(inferenceSamples.current, 0.95),
            mappingP50: percentile(mappingSamples.current, 0.5),
            frameIntervalP50,
            cameraFps: frameIntervalP50 > 0 ? 1000 / frameIntervalP50 : 0,
          });
        }
        rafRef.current = requestAnimationFrame(detect);
      };
      rafRef.current = requestAnimationFrame(detect);
    } catch (error) {
      stop();
      const message = (error as Error).message || "Cannot start camera";
      setStatus(message.includes("Permission") || message.includes("denied") ? "Camera permission denied" : `Camera error: ${message}`);
    }
  }, [enabled, onGripper, onInput, starting, stop]);

  useEffect(() => () => stop(), [stop]);

  const calibrate = useCallback(() => {
    if (!landmarkRef.current) { setStatus("No hand detected"); return; }
    mapperRef.current.calibrate(landmarkRef.current);
    setStatus("Calibrated — hold Activate to move");
  }, []);

  const toggleActive = useCallback(() => {
    const next = !activeRef.current;
    activeRef.current = next;
    setActive(next);
    if (!next) onInput(null);
  }, [onInput]);

  // The buttons render in the right-hand column with the rest of the session
  // controls, so the state they need is published rather than drawn here.
  useEffect(() => {
    onControls?.({
      starting,
      enabled,
      detected: Boolean(state?.detected),
      calibrated: Boolean(state?.calibrated),
      active,
      toggleCamera: enabled ? stop : () => void start(),
      calibrate,
      toggleActive,
    });
  }, [onControls, starting, enabled, state?.detected, state?.calibrated, active, stop, start, calibrate, toggleActive]);

  if (compact) {
    return (
      <div className="relative h-full w-full bg-black">
        <video ref={videoRef} muted playsInline className="h-full w-full -scale-x-100 object-cover" />
        <canvas ref={canvasRef} width={640} height={480} className="pointer-events-none absolute inset-0 h-full w-full -scale-x-100" />
        <span className="absolute left-1.5 top-1.5 rounded bg-black/60 px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-ink-300">
          hand
        </span>
        {!enabled && (
          <div className="absolute inset-0 grid place-items-center px-2 text-center text-[11px] text-ink-400">
            {status}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="relative aspect-[4/3] overflow-hidden rounded-lg border border-tech-border bg-tech-bg">
        <video ref={videoRef} muted playsInline className="h-full w-full -scale-x-100 object-cover" />
        <canvas ref={canvasRef} width={640} height={480} className="pointer-events-none absolute inset-0 h-full w-full -scale-x-100" />
        <div className="absolute left-2 top-2"><Badge tone={state?.clutched ? "warn" : state?.detected ? "ok" : "neutral"}>{state?.clutched ? "CLUTCHED — đưa tay về tâm" : state?.detected ? `Gesture: ${state.gesture}` : status}</Badge></div>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button variant="subtle" disabled={starting} onClick={enabled ? stop : () => void start()}>{starting ? "Starting…" : enabled ? "Stop camera" : "Start camera"}</Button>
        <Button variant="ghost" disabled={!enabled || !state?.detected} onClick={calibrate}>Calibrate</Button>
        <Button
          variant={active ? "danger" : "success"}
          disabled={!enabled || !state?.calibrated}
          onClick={toggleActive}
        >{active ? "Stop hand control" : "Activate hand control"}</Button>
      </div>
      <div className="grid grid-cols-3 gap-2 text-xs text-ink-400">
        <span>Depth ×{state?.scaleRatio.toFixed(2) ?? "1.00"}</span>
        <span>Gripper {state?.input.gripper === 1 ? "closed" : "open"}</span>
        <span>{state?.clutched ? "Clutched" : state?.active ? "Control active" : "Control stopped"}</span>
        <span className="font-mono">Forward/back {signed(state?.input.linear[0])}</span>
        <span className="font-mono">Left/right {signed(state?.input.linear[1])}</span>
        <span className="font-mono">Up/down {signed(state?.input.linear[2])}</span>
        <span className="font-mono">Xoay Z {signed(state?.input.angular[2])}</span>
        <span className="font-mono">{state?.rollVelocityDeg.toFixed(1) ?? "0.0"}°/s</span>
        <span>{state?.rotationActive ? "ROTATING" : "Rotation idle"}</span>
      </div>
      <div className="rounded-md border border-ink-700/60 bg-ink-850/60 p-2 text-[11px] text-ink-400">
        <div className="mb-1 font-semibold uppercase tracking-wider text-ink-300">Hand latency diagnostics</div>
        <div className="grid grid-cols-2 gap-x-3 gap-y-1">
          <span>MediaPipe p50</span><span className="text-right font-mono">{ms(diagnostics?.inferenceP50)}</span>
          <span>MediaPipe p95</span><span className="text-right font-mono">{ms(diagnostics?.inferenceP95)}</span>
          <span>Gesture mapping p50</span><span className="text-right font-mono">{ms(diagnostics?.mappingP50)}</span>
          <span>Camera frame interval</span><span className="text-right font-mono">{ms(diagnostics?.frameIntervalP50)}</span>
          <span>Tracking rate</span><span className="text-right font-mono">{diagnostics ? `${diagnostics.cameraFps.toFixed(1)} fps` : "—"}</span>
        </div>
      </div>
      <p className="text-xs text-ink-400">Move your hand to drive XYZ. Turn your wrist left or right to change the palm direction and rotate the gripper about Z; stop turning and the gripper stops. Open hand: open the gripper. Fist: close it. Hold 👍 to clutch.</p>
    </div>
  );
}

function signed(value = 0) {
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}`;
}

function pushSample(samples: number[], value: number) {
  samples.push(value);
  if (samples.length > SAMPLE_WINDOW) samples.splice(0, samples.length - SAMPLE_WINDOW);
}

function percentile(samples: number[], fraction: number) {
  if (!samples.length) return 0;
  const sorted = [...samples].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.max(0, Math.ceil(sorted.length * fraction) - 1))];
}

function ms(value?: number) {
  return value === undefined ? "—" : `${value.toFixed(1)} ms`;
}

function draw(canvas: HTMLCanvasElement | null, video: HTMLVideoElement, landmarks?: NormalizedLandmark[]) {
  if (!canvas) return;
  canvas.width = video.videoWidth || 640;
  canvas.height = video.videoHeight || 480;
  const context = canvas.getContext("2d");
  if (!context) return;
  context.clearRect(0, 0, canvas.width, canvas.height);
  if (!landmarks) return;
  const drawing = new DrawingUtils(context);
  drawing.drawConnectors(landmarks, HandLandmarker.HAND_CONNECTIONS, { color: "#34d399", lineWidth: 3 });
  drawing.drawLandmarks(landmarks, { color: "#fbbf24", radius: 3 });
}
