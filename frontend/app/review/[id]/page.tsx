"use client";

import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { TrimTimeline } from "@/components/TrimTimeline";
import { ScriptedReviewDetail } from "@/components/ScriptedReviewDetail";
import { AutoLabelBadge } from "@/components/AutoLabelBadge";
import {
  Alert,
  Badge,
  Button,
  Card,
  Empty,
  Field,
  Sparkline,
  TextArea,
} from "@/components/ui";
import { api, mediaUrl, type Demo, type Trajectory } from "@/lib/api";
import { bytes, timeAgo } from "@/lib/format";

export default function ReviewDetailPage() {
  const { id } = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const { user } = useAuth();
  const router = useRouter();
  const requestedReturnTo = searchParams.get("returnTo");
  const returnTo = requestedReturnTo?.startsWith("/review") ? requestedReturnTo : "/review?status=recorded";

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const wristRef = useRef<HTMLVideoElement | null>(null);

  const [demo, setDemo] = useState<Demo | null>(null);
  const [trajectory, setTrajectory] = useState<Trajectory | null>(null);
  const [trim, setTrim] = useState<[number, number]>([0, 0]);
  const [playhead, setPlayhead] = useState(0);
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [loopTrim, setLoopTrim] = useState(true);

  const canReview = user?.role === "reviewer" || user?.role === "admin";

  if (!user) return null;
  if (searchParams.get("source") === "scripted") {
    return <ScriptedReviewDetail episodeId={decodeURIComponent(id)} returnTo={returnTo} />;
  }

  const load = useCallback(async () => {
    const [d, t] = await Promise.all([api.demo(id), api.trajectory(id)]);
    setDemo(d);
    setTrajectory(t);
    setTrim([d.trim_start, d.trim_end ?? d.num_frames]);
    setNotes(d.review_notes);
  }, [id]);

  useEffect(() => {
    if (!user) return;
    void load().catch((exc) => setError(exc.message));
  }, [user, load]);

  // Keep both camera views on the same frame and confine playback to the clip.
  useEffect(() => {
    const video = videoRef.current;
    if (!video || !demo) return;
    const fps = demo.fps;

    const onTime = () => {
      const frame = Math.round(video.currentTime * fps);
      setPlayhead(frame);
      const wrist = wristRef.current;
      if (wrist && Math.abs(wrist.currentTime - video.currentTime) > 0.08) {
        wrist.currentTime = video.currentTime;
      }
      if (loopTrim && frame >= trim[1]) {
        video.currentTime = trim[0] / fps;
      }
    };
    video.addEventListener("timeupdate", onTime);
    return () => video.removeEventListener("timeupdate", onTime);
  }, [demo, trim, loopTrim]);

  const seek = useCallback(
    (frame: number) => {
      const video = videoRef.current;
      if (!video || !demo) return;
      video.currentTime = frame / demo.fps;
      if (wristRef.current) wristRef.current.currentTime = frame / demo.fps;
      setPlayhead(frame);
    },
    [demo],
  );

  const submit = useCallback(
    async (body: Parameters<typeof api.review>[1]) => {
      setBusy(true);
      setError(null);
      setSaved(null);
      try {
        const updated = await api.review(id, {
          trim_start: trim[0],
          trim_end: trim[1],
          notes,
          ...body,
        });
        setDemo(updated);
        setSaved(
          body.approve === true
            ? "Approved — this demonstration is now eligible for export."
            : body.approve === false
              ? "Rejected — it will not enter a training set."
              : "Saved.",
        );
      } catch (exc) {
        setError(exc instanceof Error ? exc.message : "Failed to save review");
      } finally {
        setBusy(false);
      }
    },
    [id, trim, notes],
  );

  const charts = useMemo(() => buildCharts(trajectory), [trajectory]);

  if (error && !demo) return <Alert>{error}</Alert>;
  if (!demo || !trajectory) return <Empty>Loading recording…</Empty>;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <Link href={returnTo} className="text-xs text-accent-400 hover:underline">
            ← Review queue
          </Link>
          <h1 className="font-heading mt-1 text-[22px] font-bold tracking-tight">{demo.task_id}</h1>
          <p className="text-sm text-ink-400">
            {demo.operator_name} · {timeAgo(demo.created_at)} · seed {demo.seed} ·{" "}
            {demo.num_frames} frames @ {demo.fps} Hz · {bytes(demo.size_bytes)}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <AutoLabelBadge label={demo.auto_label ?? "review"} reason={demo.auto_label_reason} />
          {demo.auto_success ? (
            <Badge tone="ok">auto-check: completed @ frame {demo.auto_success_frame}</Badge>
          ) : (
            <Badge tone="neutral">auto-check: not completed</Badge>
          )}
          {demo.status === "approved" && <Badge tone="ok">approved · {demo.label}</Badge>}
          {demo.status === "rejected" && <Badge tone="bad">rejected</Badge>}
          {demo.status === "recorded" && <Badge tone="warn">awaiting review</Badge>}
        </div>
      </div>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
        <div className="space-y-5">
          <Card title="Playback">
            <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_180px]">
              <video
                ref={videoRef}
                src={mediaUrl(`/api/demos/${demo.id}/video/front`)}
                controls
                loop={!loopTrim}
                className="w-full rounded-lg border border-tech-border bg-tech-bg"
              />
              {demo.has_wrist ? (
                <video
                  ref={wristRef}
                  src={mediaUrl(`/api/demos/${demo.id}/video/wrist`)}
                  muted
                  className="w-full self-start rounded-lg border border-tech-border bg-tech-bg"
                />
              ) : (
                <div className="grid aspect-square w-full place-items-center self-start rounded-lg border border-tech-border bg-tech-bg-alt text-xs text-tech-muted">
                  No wrist camera
                </div>
              )}
            </div>

            <div className="mt-4">
              <TrimTimeline
                numFrames={demo.num_frames}
                fps={demo.fps}
                start={trim[0]}
                end={trim[1]}
                playhead={playhead}
                successFrames={trajectory.success}
                onChange={(start, end) => setTrim([start, end])}
                onSeek={seek}
              />
            </div>

            <div className="mt-3 flex flex-wrap items-center gap-2">
              <label className="flex items-center gap-1.5 text-xs text-ink-400">
                In
                <input
                  type="number"
                  step={0.01}
                  min={0}
                  max={trim[1] / demo.fps}
                  value={(trim[0] / demo.fps).toFixed(2)}
                  onChange={(e) => {
                    const frame = Math.round(Number(e.target.value) * demo.fps);
                    if (Number.isFinite(frame)) setTrim([Math.min(frame, trim[1] - 1), trim[1]]);
                  }}
                  className="w-20 rounded-md border border-ink-700/60 bg-ink-850/60 px-2 py-1 tabular text-ink-100"
                />
                s
              </label>
              <label className="flex items-center gap-1.5 text-xs text-ink-400">
                Out
                <input
                  type="number"
                  step={0.01}
                  min={trim[0] / demo.fps}
                  max={demo.num_frames / demo.fps}
                  value={(trim[1] / demo.fps).toFixed(2)}
                  onChange={(e) => {
                    const frame = Math.round(Number(e.target.value) * demo.fps);
                    if (Number.isFinite(frame))
                      setTrim([trim[0], Math.min(demo.num_frames, Math.max(frame, trim[0] + 1))]);
                  }}
                  className="w-20 rounded-md border border-ink-700/60 bg-ink-850/60 px-2 py-1 tabular text-ink-100"
                />
                s
              </label>
              <Button variant="subtle" onClick={() => seek(trim[0])}>
                Jump to in-point
              </Button>
              <Button variant="subtle" onClick={() => setTrim([playhead, trim[1]])}>
                Set in = playhead
              </Button>
              <Button variant="subtle" onClick={() => setTrim([trim[0], playhead])}>
                Set out = playhead
              </Button>
              <Button
                variant="ghost"
                onClick={() => setTrim([0, demo.num_frames])}
                disabled={trim[0] === 0 && trim[1] === demo.num_frames}
              >
                Reset trim
              </Button>
              {demo.auto_success && demo.auto_success_frame !== null && (
                <Button
                  variant="ghost"
                  onClick={() =>
                    setTrim([
                      trim[0],
                      Math.min(demo.num_frames, (demo.auto_success_frame ?? 0) + demo.fps / 2),
                    ])
                  }
                >
                  Trim to completion
                </Button>
              )}
              <label className="ml-auto flex items-center gap-2 text-xs text-ink-400">
                <input
                  type="checkbox"
                  checked={loopTrim}
                  onChange={(e) => setLoopTrim(e.target.checked)}
                />
                Loop the trimmed clip
              </label>
            </div>
          </Card>

          <Card
            title="Recorded signals"
            subtitle="Every row here shares a frame index with the video above."
          >
            <div className="grid gap-5 lg:grid-cols-2">
              <div>
                <p className="mb-1 text-xs text-ink-400">Joint targets (action)</p>
                <Sparkline series={charts.action} height={150} xLabel="frame" />
              </div>
              <div>
                <p className="mb-1 text-xs text-ink-400">End-effector position</p>
                <Sparkline series={charts.ee} height={150} xLabel="frame" />
              </div>
              <div>
                <p className="mb-1 text-xs text-ink-400">Gripper aperture</p>
                <Sparkline series={charts.gripper} height={110} xLabel="frame" />
              </div>
              <div>
                <p className="mb-1 text-xs text-ink-400">Control latency</p>
                <Sparkline series={charts.latency} height={110} xLabel="frame" />
              </div>
            </div>
          </Card>
        </div>

        <div className="space-y-5">
          <Card title="Verdict">
            {!canReview ? (
              <Alert tone="info">
                Only reviewers can label and approve. This is your recording; its status is
                shown above.
              </Alert>
            ) : (
              <div className="space-y-3">
                <div className="grid grid-cols-2 gap-2">
                  <Button
                    variant={demo.label === "success" ? "success" : "ghost"}
                    disabled={busy}
                    onClick={() => submit({ label: "success" })}
                  >
                    Label success
                  </Button>
                  <Button
                    variant={demo.label === "failure" ? "danger" : "ghost"}
                    disabled={busy}
                    onClick={() => submit({ label: "failure" })}
                  >
                    Label failure
                  </Button>
                </div>

                <Field
                  label="Reviewer notes"
                  hint="Stored with the demonstration and carried into the export."
                >
                  <TextArea
                    value={notes}
                    onChange={(e) => setNotes(e.target.value)}
                    placeholder="e.g. grasp slipped once, recovered; usable"
                  />
                </Field>

                <div className="grid grid-cols-2 gap-2">
                  <Button
                    variant="success"
                    disabled={busy || demo.label === null}
                    onClick={() => submit({ approve: true })}
                    title={demo.label === null ? "Label it first" : undefined}
                  >
                    Approve
                  </Button>
                  <Button variant="danger" disabled={busy} onClick={() => submit({ approve: false })}>
                    Reject
                  </Button>
                </div>
                <Button
                  variant="subtle"
                  className="w-full"
                  disabled={busy}
                  onClick={() => submit({})}
                >
                  Save trim &amp; notes only
                </Button>
                {demo.status !== "recorded" && (
                  <Button
                    variant="ghost"
                    className="w-full"
                    disabled={busy}
                    onClick={async () => {
                      setBusy(true);
                      try {
                        setDemo(await api.reopen(demo.id));
                        setSaved("Reopened for review.");
                      } finally {
                        setBusy(false);
                      }
                    }}
                  >
                    Reopen for review
                  </Button>
                )}

                {error && <Alert>{error}</Alert>}
                {saved && <Alert tone="ok">{saved}</Alert>}
              </div>
            )}
          </Card>

          <Card title="Capture quality">
            <dl className="space-y-1.5 text-xs">
              <Row label="Latency p50" value={`${demo.latency_p50_ms.toFixed(1)} ms`} />
              <Row label="Latency p95" value={`${demo.latency_p95_ms.toFixed(1)} ms`} />
              <Row label="Control jitter (RMS)" value={`${demo.control_jitter_ms.toFixed(2)} ms`} />
              <Row label="Late ticks" value={String(demo.dropped_frames)} />
              <Row label="Storage" value={bytes(demo.size_bytes)} />
              <Row
                label="Bytes per frame"
                value={bytes(demo.size_bytes / Math.max(1, demo.num_frames))}
              />
              {demo.reviewer_name && <Row label="Reviewed by" value={demo.reviewer_name} />}
            </dl>
          </Card>

          {(user.role === "admin" || demo.operator_id === user.id) && (
            <Card title="Danger zone">
              <Button
                variant="danger"
                className="w-full"
                disabled={busy}
                onClick={async () => {
                  if (!confirm("Delete this recording and its media permanently?")) return;
                  setBusy(true);
                  try {
                    await api.deleteDemo(demo.id);
                    router.push(returnTo);
                  } catch (exc) {
                    setError(exc instanceof Error ? exc.message : "Delete failed");
                    setBusy(false);
                  }
                }}
              >
                Delete recording
              </Button>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <dt className="text-ink-400">{label}</dt>
      <dd className="tabular">{value}</dd>
    </div>
  );
}

const COLORS = ["#4bb4ff", "#34d399", "#fbbf24", "#f87171", "#a78bfa", "#f472b6", "#22d3ee"];

function buildCharts(trajectory: Trajectory | null) {
  const empty = { action: [], ee: [], gripper: [], latency: [] };
  if (!trajectory) return empty;

  const indexed = (values: number[]) =>
    values.map((value, index) => [index, value] as [number, number]);

  return {
    action: [0, 1, 2, 3, 4, 5].map((joint) => ({
      name: `j${joint + 1}`,
      color: COLORS[joint],
      points: indexed(trajectory.action.map((row) => row[joint])),
    })),
    ee: ["x", "y", "z"].map((axis, index) => ({
      name: axis,
      color: COLORS[index],
      points: indexed(trajectory.ee_pose.map((row) => row[index])),
    })),
    gripper: [
      {
        name: "commanded",
        color: COLORS[0],
        points: indexed(trajectory.action.map((row) => row[6])),
      },
      {
        name: "measured",
        color: COLORS[1],
        points: indexed(trajectory.state.map((row) => row[6])),
      },
    ],
    latency: [
      {
        name: "round trip (ms)",
        color: COLORS[3],
        points: indexed(trajectory.rtt_ms),
      },
      {
        name: "tick interval (ms)",
        color: COLORS[2],
        points: indexed(trajectory.tick_dt_ms),
      },
    ],
  };
}
