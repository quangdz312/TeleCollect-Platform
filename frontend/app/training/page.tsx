"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import {
  Alert,
  Badge,
  Button,
  Card,
  Empty,
  Field,
  Input,
  Select,
  Sparkline,
} from "@/components/ui";
import {
  api,
  mediaUrl,
  type DatasetExport,
  type EvalRun,
  type RunStatus,
  type Task,
  type TrainingRun,
} from "@/lib/api";
import { percent, timeAgo } from "@/lib/format";

const TONES: Record<RunStatus, "ok" | "warn" | "bad" | "info" | "neutral"> = {
  succeeded: "ok",
  running: "info",
  pending: "warn",
  failed: "bad",
  cancelled: "neutral",
};

export default function TrainingPage() {
  const { user } = useAuth();
  const [exports, setExports] = useState<DatasetExport[]>([]);
  const [runs, setRuns] = useState<TrainingRun[]>([]);
  const [evals, setEvals] = useState<EvalRun[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [history, setHistory] = useState<Record<string, number>[]>([]);
  const [log, setLog] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [form, setForm] = useState({
    name: "bc-v1",
    export_id: "",
    steps: 8000,
    batch_size: 48,
    chunk_size: 16,
    image_size: 112,
    learning_rate: 0.0001,
  });
  const [evalForm, setEvalForm] = useState({ task_id: "pick_place", num_episodes: 25 });

  const canTrain = user?.role === "reviewer" || user?.role === "admin";

  const load = useCallback(async () => {
    const [e, r, v] = await Promise.all([api.exports(), api.runs(), api.evals()]);
    setExports(e.filter((item) => item.format === "lerobot"));
    setRuns(r);
    setEvals(v);
    if (!selected && r.length) setSelected(r[0].id);
    setForm((previous) =>
      previous.export_id || !e.length ? previous : { ...previous, export_id: e[0].id },
    );
  }, [selected]);

  useEffect(() => {
    if (!user) return;
    void api.tasks().then(setTasks);
    void load();
  }, [user, load]);

  // Poll while a job is in flight; stop as soon as everything is terminal.
  useEffect(() => {
    const active =
      runs.some((r) => r.status === "running" || r.status === "pending") ||
      evals.some((e) => e.status === "running" || e.status === "pending");
    if (!active) return;
    const timer = setInterval(() => void load(), 4000);
    return () => clearInterval(timer);
  }, [runs, evals, load]);

  useEffect(() => {
    if (!selected) return;
    void api.runHistory(selected).then(setHistory).catch(() => setHistory([]));
    void api.runLog(selected).then(setLog).catch(() => setLog(""));
  }, [selected, runs]);

  const selectedRun = useMemo(
    () => runs.find((run) => run.id === selected) ?? null,
    [runs, selected],
  );
  const selectedEvals = useMemo(
    () => evals.filter((item) => item.training_run_id === selected),
    [evals, selected],
  );

  const charts = useMemo(() => {
    const train = history
      .filter((row) => "train_l1" in row)
      .map((row) => [row.step, row.train_l1] as [number, number]);
    const val = history
      .filter((row) => "val_l1" in row)
      .map((row) => [row.step, row.val_l1] as [number, number]);
    return [
      { name: "train L1", color: "#4bb4ff", points: train },
      { name: "val L1 (held-out episodes)", color: "#34d399", points: val },
    ].filter((series) => series.points.length > 1);
  }, [history]);

  if (!user) return null;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-semibold">Imitation learning</h1>
        <p className="mt-0.5 text-sm text-ink-400">
          Train a behaviour-cloning policy on an approved dataset, then measure it back in the
          same simulator the demonstrations were collected in.
        </p>
      </div>

      {canTrain && (
        <Card title="New training run">
          {exports.length === 0 ? (
            <Empty>Export a LeRobot dataset first.</Empty>
          ) : (
            <>
              <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
                <Field label="Name">
                  <Input
                    value={form.name}
                    onChange={(e) => setForm({ ...form, name: e.target.value })}
                  />
                </Field>
                <Field label="Dataset">
                  <Select
                    value={form.export_id}
                    onChange={(e) => setForm({ ...form, export_id: e.target.value })}
                  >
                    {exports.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.name} ({item.num_episodes} eps)
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="Steps">
                  <Input
                    type="number"
                    value={form.steps}
                    onChange={(e) => setForm({ ...form, steps: Number(e.target.value) })}
                  />
                </Field>
                <Field label="Batch size">
                  <Input
                    type="number"
                    value={form.batch_size}
                    onChange={(e) => setForm({ ...form, batch_size: Number(e.target.value) })}
                  />
                </Field>
                <Field label="Action chunk" hint="frames predicted per step">
                  <Input
                    type="number"
                    value={form.chunk_size}
                    onChange={(e) => setForm({ ...form, chunk_size: Number(e.target.value) })}
                  />
                </Field>
                <Field label="Image size">
                  <Input
                    type="number"
                    value={form.image_size}
                    onChange={(e) => setForm({ ...form, image_size: Number(e.target.value) })}
                  />
                </Field>
              </div>
              <div className="mt-4 flex items-center gap-3">
                <Button
                  variant="primary"
                  disabled={busy}
                  onClick={async () => {
                    setBusy(true);
                    setError(null);
                    try {
                      const run = await api.createRun(form);
                      setSelected(run.id);
                      await load();
                    } catch (exc) {
                      setError(exc instanceof Error ? exc.message : "Failed to start");
                    } finally {
                      setBusy(false);
                    }
                  }}
                >
                  Start training
                </Button>
                <span className="text-xs text-ink-400">
                  Runs as a separate process so it cannot disturb live teleoperation.
                </span>
              </div>
              {error && (
                <div className="mt-3">
                  <Alert>{error}</Alert>
                </div>
              )}
            </>
          )}
        </Card>
      )}

      <div className="grid gap-5 lg:grid-cols-[320px_minmax(0,1fr)]">
        <Card title="Runs">
          {runs.length === 0 ? (
            <Empty>No training runs yet.</Empty>
          ) : (
            <ul className="space-y-2">
              {runs.map((run) => (
                <li key={run.id}>
                  <button
                    onClick={() => setSelected(run.id)}
                    className={`w-full rounded-lg border px-3 py-2 text-left text-sm transition-colors ${
                      selected === run.id
                        ? "border-accent-500/60 bg-ink-850"
                        : "border-ink-700/50 hover:bg-ink-850"
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate font-medium">{run.name}</span>
                      <Badge tone={TONES[run.status]}>{run.status}</Badge>
                    </div>
                    <div className="mt-0.5 text-xs text-ink-400">
                      {String(run.config.export_name ?? "")} · {timeAgo(run.created_at)}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <div className="space-y-5">
          {!selectedRun ? (
            <Empty>Select a run.</Empty>
          ) : (
            <>
              <Card
                title={selectedRun.name}
                subtitle={`${selectedRun.status} · ${JSON.stringify(selectedRun.config.steps)} steps · chunk ${JSON.stringify(selectedRun.config.chunk_size)}`}
                actions={<Badge tone={TONES[selectedRun.status]}>{selectedRun.status}</Badge>}
              >
                {charts.length > 0 ? (
                  <Sparkline series={charts} height={180} xLabel="training step" />
                ) : (
                  <Empty>Waiting for the first logged step…</Empty>
                )}

                {selectedRun.metrics?.best_val_l1 != null && (
                  <div className="mt-3 flex flex-wrap gap-4 text-xs text-ink-400">
                    <span>
                      best val L1{" "}
                      <span className="text-ink-100 tabular">
                        {Number(selectedRun.metrics.best_val_l1).toFixed(4)}
                      </span>
                    </span>
                    <span>
                      {String(selectedRun.metrics.num_episodes)} episodes /{" "}
                      {String(selectedRun.metrics.num_frames)} frames
                    </span>
                    <span>{String(selectedRun.metrics.train_seconds)}s on {String(selectedRun.metrics.device)}</span>
                    {selectedRun.config.dvc_hash ? (
                      <span className="font-mono">
                        dvc {String(selectedRun.config.dvc_hash).slice(0, 12)}
                      </span>
                    ) : null}
                  </div>
                )}
                {selectedRun.error && (
                  <div className="mt-3">
                    <Alert>
                      <pre className="whitespace-pre-wrap text-xs">{selectedRun.error}</pre>
                    </Alert>
                  </div>
                )}
              </Card>

              {canTrain && selectedRun.status === "succeeded" && (
                <Card title="Evaluate in simulation">
                  <div className="flex flex-wrap items-end gap-3">
                    <Field label="Task">
                      <Select
                        value={evalForm.task_id}
                        onChange={(e) => setEvalForm({ ...evalForm, task_id: e.target.value })}
                        className="w-56"
                      >
                        {tasks.map((task) => (
                          <option key={task.id} value={task.id}>
                            {task.title}
                          </option>
                        ))}
                      </Select>
                    </Field>
                    <Field label="Episodes">
                      <Input
                        type="number"
                        className="w-28"
                        value={evalForm.num_episodes}
                        onChange={(e) =>
                          setEvalForm({ ...evalForm, num_episodes: Number(e.target.value) })
                        }
                      />
                    </Field>
                    <Button
                      variant="primary"
                      disabled={busy}
                      onClick={async () => {
                        setBusy(true);
                        setError(null);
                        try {
                          await api.createEval({
                            training_run_id: selectedRun.id,
                            ...evalForm,
                          });
                          await load();
                        } catch (exc) {
                          setError(exc instanceof Error ? exc.message : "Failed to start");
                        } finally {
                          setBusy(false);
                        }
                      }}
                    >
                      Run evaluation
                    </Button>
                    <span className="text-xs text-ink-400">
                      Seeds start at 10 000 — object layouts the policy never saw in training.
                    </span>
                  </div>
                </Card>
              )}

              <Card title="Evaluations">
                {selectedEvals.length === 0 ? (
                  <Empty>No evaluations for this run yet.</Empty>
                ) : (
                  <div className="space-y-4">
                    {selectedEvals.map((item) => (
                      <div
                        key={item.id}
                        className="rounded-lg border border-ink-700/60 bg-ink-850/50 p-4"
                      >
                        <div className="flex flex-wrap items-center justify-between gap-3">
                          <div>
                            <div className="text-sm font-medium">{item.task_id}</div>
                            <div className="text-xs text-ink-400">
                              {item.num_episodes} episodes · {timeAgo(item.created_at)}
                            </div>
                          </div>
                          <div className="flex items-center gap-3">
                            {item.status === "succeeded" && (
                              <div className="text-right">
                                <div className="text-2xl font-semibold tabular text-ok-400">
                                  {percent(item.success_rate, 1)}
                                </div>
                                <div className="text-[11px] text-ink-400">
                                  success rate
                                  {Array.isArray(item.details?.success_rate_ci95)
                                    ? ` · 95% CI ${percent(item.details.success_rate_ci95[0])}–${percent(item.details.success_rate_ci95[1])}`
                                    : ""}
                                </div>
                              </div>
                            )}
                            <Badge tone={TONES[item.status]}>{item.status}</Badge>
                          </div>
                        </div>

                        {item.status === "succeeded" && (
                          <>
                            <div className="mt-3 flex flex-wrap gap-4 text-xs text-ink-400">
                              <span>
                                mean episode {item.mean_episode_length.toFixed(0)} steps
                              </span>
                              {item.details?.mean_success_length && (
                                <span>
                                  mean success {Number(item.details.mean_success_length).toFixed(0)}{" "}
                                  steps
                                </span>
                              )}
                              <span>
                                seeds {String(item.details?.seed_range?.[0])}–
                                {String(item.details?.seed_range?.[1])}
                              </span>
                            </div>
                            {Array.isArray(item.details?.episodes) && (
                              <div className="mt-2 flex flex-wrap gap-1">
                                {item.details.episodes.map((episode: any, index: number) => (
                                  <span
                                    key={index}
                                    title={`seed ${episode.seed} · ${episode.steps} steps`}
                                    className={`h-3 w-3 rounded-sm ${
                                      episode.success ? "bg-ok-400" : "bg-bad-600/70"
                                    }`}
                                  />
                                ))}
                              </div>
                            )}
                            <EvalVideos evalId={item.id} details={item.details} />
                          </>
                        )}
                        {item.error && (
                          <pre className="mt-2 whitespace-pre-wrap text-xs text-bad-400">
                            {item.error}
                          </pre>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </Card>

              {log && (
                <Card title="Job log">
                  <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-lg border border-ink-700 bg-ink-950 p-3 text-[11px] leading-relaxed text-ink-300">
                    {log}
                  </pre>
                </Card>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function EvalVideos({ evalId, details }: { evalId: string; details: Record<string, any> }) {
  const episodes = Array.isArray(details?.episodes) ? details.episodes : [];
  const recorded = episodes.slice(0, Number(details?.record_videos ?? 3));
  if (recorded.length === 0) return null;
  return (
    <div className="mt-3 grid gap-2 sm:grid-cols-3">
      {recorded.map((episode: any, index: number) => (
        <video
          key={index}
          controls
          muted
          loop
          className="w-full rounded-md border border-ink-700 bg-black"
          src={mediaUrl(
            `/api/training/evals/${evalId}/video/episode_${String(index).padStart(3, "0")}_${
              episode.success ? "success" : "fail"
            }.mp4`,
          )}
        />
      ))}
    </div>
  );
}
