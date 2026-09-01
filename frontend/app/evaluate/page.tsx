"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useLeftBar, useRailBack } from "@/components/AppShell";
import { useAuth } from "@/components/AuthProvider";
import { PageHeader } from "@/components/PageHeader";
import { Alert, Badge, Button, Card, cx, Empty, Field, Input, Select, Stat } from "@/components/ui";
import {
  api,
  mediaUrl,
  type DatasetExport,
  type EvaluationRequest,
  type EvaluationRun,
  type RunStatus,
  type TrainingCheckpoint,
  type TrainingRun,
} from "@/lib/api";
import { percent, timeAgo } from "@/lib/format";

/** Ngoai component: object moi moi lan render se khien hook chay lai mai. */
const BACK_TO_SECTIONS = { label: "All sections", href: "/" };

const TONES: Record<RunStatus, "ok" | "warn" | "bad" | "info" | "neutral"> = {
  succeeded: "ok",
  running: "info",
  pending: "warn",
  failed: "bad",
  cancelled: "neutral",
};

const INITIAL_FORM: EvaluationRequest = {
  training_run_id: "",
  checkpoint_id: "",
  num_rollouts: 20,
  horizon: 250,
  seed: 5000,
  record_videos: 3,
  success_hold_steps: 10,
};

type DetailTab = "summary" | "seeds" | "videos" | "log";
type VideoModalState = { evaluation: EvaluationRun; index: number };

/** Ô số cho thanh trái: hẹp và không chú thích, vì cột chỉ rộng 288px. */
function RailNumber({
  label,
  value,
  min,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  onChange: (value: number) => void;
}) {
  return (
    <Field label={label}>
      <Input
        type="number"
        className="px-2 py-1 text-xs"
        min={min}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </Field>
  );
}

function preferredCheckpoint(checkpoints: TrainingCheckpoint[]) {
  return checkpoints.find((item) => item.is_best_validation)
    ?? checkpoints.find((item) => item.is_latest)
    ?? checkpoints[0];
}

export default function EvaluatePage() {
  const { user } = useAuth();
  const router = useRouter();
  const [runs, setRuns] = useState<TrainingRun[]>([]);
  const [datasets, setDatasets] = useState<DatasetExport[]>([]);
  const [evaluations, setEvaluations] = useState<EvaluationRun[]>([]);
  const [form, setForm] = useState<EvaluationRequest>(INITIAL_FORM);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [detailTab, setDetailTab] = useState<DetailTab>("summary");
  const [videoModal, setVideoModal] = useState<VideoModalState | null>(null);
  const [log, setLog] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const eligibleRuns = useMemo(
    () => runs.filter((run) => run.status === "succeeded" && Boolean(run.checkpoints?.length)),
    [runs],
  );
  const selectedRun = eligibleRuns.find((run) => run.id === form.training_run_id) ?? null;
  const checkpoints = selectedRun?.checkpoints ?? [];
  const datasetById = useMemo(() => new Map(datasets.map((item) => [item.id, item])), [datasets]);
  const selectedDataset = selectedRun
    ? datasetById.get(selectedRun.dataset_id ?? selectedRun.config.dataset_id)
    : undefined;
  const selectedEvaluations = evaluations.filter((item) => item.training_run_id === form.training_run_id);

  const load = useCallback(async (preserveSelection = true) => {
    const [allRuns, allDatasets, allEvaluations] = await Promise.all([
      api.runs(),
      api.exports(),
      api.evaluations(),
    ]);
    const eligible = allRuns.filter((run) => run.status === "succeeded" && Boolean(run.checkpoints?.length));
    setRuns(allRuns);
    setDatasets(allDatasets);
    setEvaluations(allEvaluations);
    setForm((current) => {
      if (preserveSelection && eligible.some((run) => run.id === current.training_run_id)) return current;
      const query = new URLSearchParams(window.location.search);
      const requestedRun = eligible.find((run) => run.id === query.get("run"));
      const run = requestedRun ?? eligible[0];
      if (!run) return INITIAL_FORM;
      const requestedCheckpoint = run.checkpoints?.find((item) => item.id === query.get("checkpoint"));
      const checkpoint = requestedCheckpoint ?? preferredCheckpoint(run.checkpoints ?? []);
      return { ...current, training_run_id: run.id, checkpoint_id: checkpoint?.id ?? "" };
    });
  }, []);

  useEffect(() => {
    if (!user) return;
    void load(false).catch((problem) => setError(problem instanceof Error ? problem.message : "Could not load evaluations"));
  }, [load, user]);

  useEffect(() => {
    const active = evaluations.some((item) => item.status === "pending" || item.status === "running");
    if (!active) return;
    const timer = window.setInterval(() => void load(), 2500);
    return () => window.clearInterval(timer);
  }, [evaluations, load]);

  useEffect(() => {
    if (!expandedId || detailTab !== "log") {
      setLog("");
      return;
    }
    void api.evaluationLog(expandedId).then(setLog).catch(() => setLog(""));
  }, [detailTab, expandedId]);

  /**
   * Nhận checkpoint từ trang Training qua `?run=…&checkpoint=…`.
   *
   * Người dùng đang xem một lần train, bấm Evaluate ở đúng checkpoint đó —
   * sang đây mà phải chọn lại run rồi chọn lại checkpoint là bắt làm lại thứ
   * họ vừa chỉ. Chạy một lần cho mỗi tham số, sau đó form là của người dùng.
   */
  useEffect(() => {
    // `window.location.search` chứ không phải `useSearchParams`: hook đó buộc
    // trang phải nằm trong một <Suspense>, và thiếu nó thì `next build` hỏng ở
    // bước prerender. Trang Training đọc query đúng theo cách này.
    const query = new URLSearchParams(window.location.search);
    const runId = query.get("run");
    const checkpointId = query.get("checkpoint");
    if (!runId || !eligibleRuns.some((run) => run.id === runId)) return;
    setForm((current) =>
      current.training_run_id === runId && current.checkpoint_id === checkpointId
        ? current
        : {
            ...current,
            training_run_id: runId,
            checkpoint_id:
              checkpointId
              ?? preferredCheckpoint(
                eligibleRuns.find((run) => run.id === runId)?.checkpoints ?? [],
              )?.id
              ?? "",
          },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [eligibleRuns.length]);

  useEffect(() => {
    if (!videoModal) return;
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") setVideoModal(null);
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [videoModal]);

  const invalidVideos = form.record_videos > form.num_rollouts;

  /**
   * Thanh trái chỉ giữ phần cấu hình.
   *
   * Cấu hình là thứ người dùng đụng vào mỗi lần chạy, nên nó đứng yên một chỗ
   * thay vì nằm trong một thẻ bị đẩy xuống khi kết quả dài ra. Danh sách các
   * lần đã chạy từng nằm dưới đây, nhưng Evaluation history giữa trang đã có
   * đúng danh sách đó — nhắc lại chỉ làm thanh dài quá màn hình rồi mọc thêm
   * một thanh cuộn riêng.
   */
  const evaluateRail = useMemo(
    () => (
      <div className="flex flex-col gap-3">
        <div className="space-y-2.5">
          <p className="px-1 text-[10px] font-semibold uppercase tracking-wider text-ink-400">
            Configuration
          </p>
          <Field label="Training run">
            <Select
              className="px-2 py-1 text-xs"
              value={form.training_run_id}
              onChange={(event) => selectRun(event.target.value)}
            >
              {eligibleRuns.map((run) => <option key={run.id} value={run.id}>{run.name}</option>)}
            </Select>
          </Field>
          <Field label="Checkpoint">
            <Select
              className="px-2 py-1 text-xs"
              value={form.checkpoint_id}
              onChange={(event) => setForm({ ...form, checkpoint_id: event.target.value })}
            >
              {checkpoints.map((checkpoint) => (
                <option key={checkpoint.id} value={checkpoint.id}>
                  epoch {checkpoint.epoch}{checkpoint.is_best_validation ? " · best" : ""}{checkpoint.is_latest ? " · latest" : ""}
                </option>
              ))}
            </Select>
          </Field>
          <div className="grid grid-cols-2 gap-2">
            <RailNumber label="Rollouts" value={form.num_rollouts} min={1} onChange={(num_rollouts) => setForm({ ...form, num_rollouts })} />
            <RailNumber label="Horizon" value={form.horizon ?? 250} min={1} onChange={(horizon) => setForm({ ...form, horizon })} />
            <RailNumber label="Start seed" value={form.seed} min={0} onChange={(seed) => setForm({ ...form, seed })} />
            <RailNumber label="Videos" value={form.record_videos} min={0} onChange={(record_videos) => setForm({ ...form, record_videos })} />
            <RailNumber label="Hold steps" value={form.success_hold_steps} min={1} onChange={(success_hold_steps) => setForm({ ...form, success_hold_steps })} />
          </div>
          <Button
            variant="primary"
            className="w-full justify-center"
            disabled={busy || !form.checkpoint_id || invalidVideos}
            onClick={() => void startEvaluation()}
          >
            {busy ? "Starting…" : "Run evaluation"}
          </Button>
          {invalidVideos && <p className="text-[11px] text-bad-400">Videos cannot exceed rollouts.</p>}
        </div>

      </div>
    ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [form, eligibleRuns, checkpoints, busy, invalidVideos],
  );

  // Trên các early return: hook phải chạy ở mọi lần render.
  useLeftBar(evaluateRail);
  useRailBack(BACK_TO_SECTIONS);

  if (!user) return null;
  if (user.role === "operator") return <Alert tone="info">Evaluation is available to reviewers and administrators.</Alert>;

  function selectRun(runId: string) {
    const run = eligibleRuns.find((item) => item.id === runId);
    const checkpoint = preferredCheckpoint(run?.checkpoints ?? []);
    setForm((current) => ({ ...current, training_run_id: runId, checkpoint_id: checkpoint?.id ?? "" }));
  }

  async function startEvaluation() {
    setBusy(true);
    setError(null);
    try {
      const created = await api.createEvaluation(form.training_run_id, form);
      setDetailTab("summary");
      setExpandedId(created.id);
      await load();
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "Could not start evaluation");
    } finally {
      setBusy(false);
    }
  }

  async function cancelEvaluation(id: string) {
    setBusy(true);
    setError(null);
    try {
      await api.cancelEvaluation(id);
      await load();
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "Could not cancel evaluation");
    } finally {
      setBusy(false);
    }
  }

  async function retryEvaluation(id: string) {
    setBusy(true);
    setError(null);
    try {
      const created = await api.retryEvaluation(id);
      setDetailTab("summary");
      setExpandedId(created.id);
      await load();
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "Could not retry evaluation");
    } finally {
      setBusy(false);
    }
  }

  async function deleteEvaluation(id: string) {
    if (!window.confirm("Delete this evaluation, including its log and rollout videos? This cannot be undone.")) return;
    setBusy(true);
    setError(null);
    try {
      await api.deleteEvaluation(id);
      if (expandedId === id) setExpandedId(null);
      await load();
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "Could not delete evaluation");
    } finally {
      setBusy(false);
    }
  }

  function toggleDetails(id: string) {
    if (expandedId === id) {
      setExpandedId(null);
      return;
    }
    setDetailTab("summary");
    setExpandedId(id);
  }

  const comparisonRows = latestByCheckpoint(selectedEvaluations).filter((item) => item.status === "succeeded");
  const summaryEvaluation = [...comparisonRows]
    .filter((item) => item.success_rate != null)
    .sort((a, b) => (b.success_rate ?? 0) - (a.success_rate ?? 0))[0] ?? null;
  const summarySuccesses = summaryEvaluation?.episodes.filter((episode) => episode.success).length ?? 0;

  return (
    <div className="space-y-5">
      {/* Đường về đúng lần train đang đánh giá. Kết quả đánh giá gần như luôn
          dẫn tới một câu hỏi về chính lần train đó — loss, cấu hình, các
          checkpoint khác — nên bắt người dùng sang Training rồi tìm lại đúng
          run vừa xem là bắt đi vòng. */}
      <PageHeader
        eyebrow="Policy validation lab"
        title="Evaluate checkpoints"
        description="Run simulator rollouts and compare checkpoint success rates."
        actions={
          selectedRun ? (
            <Button
              variant="subtle"
              onClick={() => router.push(`/training?run=${encodeURIComponent(selectedRun.id)}`)}
            >
              Open training run
            </Button>
          ) : undefined
        }
      />

      {error && <Alert>{error}</Alert>}

      {eligibleRuns.length === 0 && (
        <Empty>Complete a training run with at least one checkpoint first.</Empty>
      )}

      {summaryEvaluation && <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Success rate" value={percent(summaryEvaluation.success_rate ?? 0, 0)} hint={`${summarySuccesses} / ${summaryEvaluation.episodes.length} rollouts succeeded`} tone="ok" />
        <Stat label="Mean steps" value={summaryEvaluation.mean_episode_length?.toFixed(1) ?? "—"} hint="Completed rollout average" />
        <Stat label="Rollouts" value={summaryEvaluation.episodes.length} hint={`${summaryEvaluation.num_episodes} requested`} />
        <SuccessDonut successes={summarySuccesses} total={summaryEvaluation.episodes.length} />
      </div>}

      <Card title="Checkpoint comparison" subtitle="Latest completed result for each checkpoint in this training run.">
        {selectedEvaluations.length === 0 ? <Empty>No evaluation results for this training run yet.</Empty> : (
          <div className="grid gap-5 xl:grid-cols-[1.05fr_.95fr]">
            <CheckpointChart evaluations={latestByCheckpoint(selectedEvaluations)} checkpoints={checkpoints} />
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-sm">
              <thead className="text-left text-xs uppercase text-ink-400"><tr><th className="pb-2">Checkpoint</th><th className="pb-2">Status</th><th className="pb-2 text-right">Success rate</th><th className="pb-2 text-right">Rollouts</th><th className="pb-2 text-right">Mean steps</th><th className="pb-2 text-right">Created</th></tr></thead>
              <tbody>{latestByCheckpoint(selectedEvaluations).map((evaluation) => {
                const checkpoint = checkpoints.find((item) => item.id === evaluation.checkpoint_id);
                return <tr key={evaluation.id} className="border-t border-ink-700/60"><td className="py-2">{checkpoint ? `Epoch ${checkpoint.epoch}` : evaluation.checkpoint_id}</td><td className="py-2"><Badge tone={TONES[evaluation.status]}>{evaluation.status}</Badge></td><td className="py-2 text-right">{evaluation.success_rate == null ? "—" : percent(evaluation.success_rate, 1)}</td><td className="py-2 text-right">{evaluation.episodes.length}/{evaluation.num_episodes}</td><td className="py-2 text-right">{evaluation.mean_episode_length?.toFixed(1) ?? "—"}</td><td className="py-2 text-right text-xs text-ink-400">{timeAgo(evaluation.created_at)}</td></tr>;
              })}</tbody>
            </table>
          </div></div>
        )}
      </Card>

      <Card title="Evaluation history">
        {selectedEvaluations.length === 0 ? <Empty>No evaluation has run for this training run yet.</Empty> : (
          <div className="space-y-2">
            {selectedEvaluations.map((evaluation) => {
              const checkpoint = checkpoints.find((item) => item.id === evaluation.checkpoint_id);
              const expanded = expandedId === evaluation.id;
              const successes = evaluation.episodes.filter((episode) => episode.success).length;
              return (
                <div key={evaluation.id} className="rounded-lg border border-ink-700/60 bg-ink-850/50 p-3">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div><div className="flex items-center gap-2"><strong>{checkpoint ? `Epoch ${checkpoint.epoch}` : evaluation.checkpoint_id}</strong><Badge tone={TONES[evaluation.status]}>{evaluation.status}</Badge></div><div className="mt-1 text-xs text-ink-400">{evaluation.task_name} · {timeAgo(evaluation.created_at)}</div></div>
                    <div className="flex flex-wrap items-center justify-end gap-2">
                      {evaluation.success_rate != null && <div className="mr-1 text-right"><div className="text-lg font-semibold text-ok-400">{percent(evaluation.success_rate, 1)}</div><div className="text-[11px] text-ink-400">{successes}/{evaluation.episodes.length} successful</div></div>}
                      {(evaluation.status === "pending" || evaluation.status === "running") ? (
                        <Button variant="danger" disabled={busy} onClick={() => void cancelEvaluation(evaluation.id)}>Cancel</Button>
                      ) : (
                        <>
                          {(evaluation.status === "failed" || evaluation.status === "cancelled") && <Button variant="primary" disabled={busy} onClick={() => void retryEvaluation(evaluation.id)}>Retry</Button>}
                          <Button variant="danger" disabled={busy} onClick={() => void deleteEvaluation(evaluation.id)}>Delete</Button>
                        </>
                      )}
                      <Button variant="subtle" onClick={() => toggleDetails(evaluation.id)}>{expanded ? "Hide" : "View details"}</Button>
                    </div>
                  </div>
                  {expanded && (
                    <div className="mt-3 border-t border-ink-700/60 pt-3">
                      <DetailTabs value={detailTab} onChange={setDetailTab} />
                      {detailTab === "summary" && <EvaluationSummary evaluation={evaluation} />}
                      {detailTab === "seeds" && <SeedResults evaluation={evaluation} />}
                      {detailTab === "videos" && <EvaluationVideos evaluation={evaluation} onOpen={(index) => setVideoModal({ evaluation, index })} />}
                      {detailTab === "log" && (log ? <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-lg border border-ink-700 bg-ink-950 p-3 text-[11px] text-ink-300">{log}</pre> : <Empty>No log available.</Empty>)}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Card>
      {videoModal && <VideoModal state={videoModal} onChange={setVideoModal} onClose={() => setVideoModal(null)} />}
    </div>
  );
}

function latestByCheckpoint(items: EvaluationRun[]) {
  const latest = new Map<string, EvaluationRun>();
  for (const item of items) {
    if (!latest.has(item.checkpoint_id)) latest.set(item.checkpoint_id, item);
  }
  return Array.from(latest.values());
}

function NumberField({ label, value, min, onChange }: { label: string; value: number; min: number; onChange: (value: number) => void }) {
  return <Field label={label}><Input type="number" min={min} value={value} onChange={(event) => onChange(Number(event.target.value))} /></Field>;
}

function SuccessDonut({ successes, total }: { successes: number; total: number }) {
  const rate = total ? Math.round((successes / total) * 100) : 0;
  return <div className="flex items-center justify-center gap-5 rounded-lg border border-ink-700/90 bg-ink-900 px-4 py-3 shadow-[0_4px_14px_rgba(15,23,42,0.035)]">
    <div className="grid h-20 w-20 shrink-0 place-items-center rounded-full p-2" style={{ background: `conic-gradient(#059669 ${rate}%, #ef4444 0)` }}><div className="grid h-full w-full place-items-center rounded-full bg-white font-heading text-lg font-bold text-ok-600">{rate}%</div></div>
    <div className="min-w-24 space-y-2 text-xs"><div className="flex items-center justify-between gap-4"><span className="flex items-center gap-2"><i className="h-2 w-2 rounded-full bg-ok-600" />Success</span><strong>{successes}</strong></div><div className="flex items-center justify-between gap-4"><span className="flex items-center gap-2"><i className="h-2 w-2 rounded-full bg-bad-600" />Fail</span><strong>{Math.max(0, total - successes)}</strong></div></div>
  </div>;
}

function CheckpointChart({ evaluations, checkpoints }: { evaluations: EvaluationRun[]; checkpoints: TrainingCheckpoint[] }) {
  const rows = evaluations
    .filter((item) => item.status === "succeeded" && item.success_rate != null)
    .map((item) => ({
      epoch: checkpoints.find((checkpoint) => checkpoint.id === item.checkpoint_id)?.epoch ?? 0,
      success: (item.success_rate ?? 0) * 100,
      steps: item.mean_episode_length ?? 0,
    }))
    .sort((a, b) => a.epoch - b.epoch);
  if (!rows.length) return <Empty>No completed checkpoint data to chart.</Empty>;
  const width = 520;
  const height = 190;
  const maxSteps = Math.max(1, ...rows.map((item) => item.steps));
  const slot = width / rows.length;
  const linePoints = rows.map((item, index) => `${slot * index + slot / 2},${height - 28 - (item.steps / maxSteps) * 120}`).join(" ");
  return <div className="rounded-lg border border-ink-700/70 bg-ink-850/45 p-3">
    <div className="mb-2 flex gap-4 text-[10px] text-ink-400"><span><i className="mr-1.5 inline-block h-2.5 w-2.5 rounded-sm bg-accent-500/70" />Success rate (%)</span><span><i className="mr-1.5 inline-block h-0.5 w-4 align-middle bg-accent-600" />Mean steps</span></div>
    <svg viewBox={`0 0 ${width} ${height}`} className="h-48 w-full" role="img" aria-label="Checkpoint success rate and mean steps comparison">
      {[0, 25, 50, 75, 100].map((tick) => <g key={tick}><line x1="32" x2={width} y1={height - 28 - tick * 1.2} y2={height - 28 - tick * 1.2} stroke="rgb(203 213 225 / .65)" strokeDasharray="3 4"/><text x="26" y={height - 24 - tick * 1.2} textAnchor="end" fontSize="9" fill="#64748b">{tick}</text></g>)}
      {rows.map((item, index) => { const barHeight = item.success * 1.2; const x = slot * index + slot * .28; return <g key={`${item.epoch}-${index}`}><rect x={x} y={height - 28 - barHeight} width={slot * .44} height={barHeight} rx="4" fill="#60a5fa"/><text x={x + slot * .22} y={height - 34 - barHeight} textAnchor="middle" fontSize="9" fontWeight="600" fill="#334155">{item.success.toFixed(0)}%</text><text x={x + slot * .22} y={height - 10} textAnchor="middle" fontSize="9" fill="#64748b">{item.epoch}</text></g>; })}
      <polyline points={linePoints} fill="none" stroke="#1e40af" strokeWidth="2"/>
      {rows.map((item, index) => <circle key={index} cx={slot * index + slot / 2} cy={height - 28 - (item.steps / maxSteps) * 120} r="3.5" fill="#1e40af"/>)}
    </svg>
    <div className="text-center text-[10px] text-ink-400">Checkpoint epoch</div>
  </div>;
}

function DetailTabs({ value, onChange }: { value: DetailTab; onChange: (value: DetailTab) => void }) {
  const tabs: { value: DetailTab; label: string }[] = [
    { value: "summary", label: "Summary" },
    { value: "seeds", label: "Seeds" },
    { value: "videos", label: "Videos" },
    { value: "log", label: "Log" },
  ];
  return <div className="mb-3 flex flex-wrap gap-1 border-b border-ink-700/60">{tabs.map((tab) => <button key={tab.value} type="button" onClick={() => onChange(tab.value)} className={`border-b-2 px-3 py-2 text-xs font-medium ${value === tab.value ? "border-accent-500 text-accent-500" : "border-transparent text-ink-400 hover:text-ink-200"}`}>{tab.label}</button>)}</div>;
}

function EvaluationSummary({ evaluation }: { evaluation: EvaluationRun }) {
  const successes = evaluation.episodes.filter((episode) => episode.success).length;
  const failures = evaluation.episodes.filter((episode) => !episode.success);
  return <div className="space-y-3">
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <Stat label="Success rate" value={evaluation.success_rate == null ? "—" : percent(evaluation.success_rate, 1)} />
      <Stat label="Successful" value={`${successes} / ${evaluation.episodes.length}`} />
      <Stat label="Mean steps" value={evaluation.mean_episode_length?.toFixed(1) ?? "—"} />
      <Stat label="Recorded videos" value={evaluation.episodes.filter((episode) => episode.video).length.toString()} />
    </div>
    {failures.length > 0 && <div><div className="mb-1.5 text-xs font-medium text-ink-300">Failed seeds</div><div className="flex flex-wrap gap-1.5">{failures.map((episode) => <span key={episode.seed} className="rounded bg-bad-600/20 px-2 py-1 text-[11px] text-bad-400">{episode.seed} · {episode.steps} steps</span>)}</div></div>}
    {evaluation.error && <Alert><pre className="whitespace-pre-wrap text-xs">{evaluation.error}</pre></Alert>}
  </div>;
}

function SeedResults({ evaluation }: { evaluation: EvaluationRun }) {
  if (evaluation.episodes.length === 0) return <Empty>No completed rollouts yet.</Empty>;
  return <div className="flex max-h-72 flex-wrap gap-1.5 overflow-auto pr-1">{evaluation.episodes.map((episode) => <span key={episode.seed} title={`${episode.steps} steps`} className={`rounded px-2 py-1 text-[11px] ${episode.success ? "bg-ok-600/20 text-ok-400" : "bg-bad-600/20 text-bad-400"}`}>{episode.seed}: {episode.success ? "success" : "fail"}{episode.held_steps != null && episode.required_hold_steps != null ? ` · held ${episode.held_steps}/${episode.required_hold_steps}` : " · legacy rule"}</span>)}</div>;
}

function EvaluationVideos({ evaluation, onOpen }: { evaluation: EvaluationRun; onOpen: (index: number) => void }) {
  const recorded = evaluation.episodes.filter((episode) => episode.video);
  if (recorded.length === 0) return <Empty>No videos were recorded for this evaluation.</Empty>;
  return <div className="flex gap-3 overflow-x-auto pb-2">{recorded.map((episode, index) => <button type="button" key={episode.seed} onClick={() => onOpen(index)} className="w-56 shrink-0 overflow-hidden rounded-lg border border-ink-700 bg-ink-900 text-left transition hover:border-accent-500/70"><video className="pointer-events-none h-32 w-full bg-black object-cover" muted preload="metadata" src={mediaUrl(`/training/evaluations/${evaluation.id}/videos/${episode.video}`)} /><div className="flex items-center justify-between gap-2 px-2.5 py-2"><span className="text-xs font-medium">Seed {episode.seed}</span><Badge tone={episode.success ? "ok" : "bad"}>{episode.success ? "success" : "fail"}</Badge></div><div className="px-2.5 pb-2 text-[11px] text-ink-400">{episode.steps} steps{episode.held_steps != null && episode.required_hold_steps != null ? ` · held ${episode.held_steps}/${episode.required_hold_steps}` : ""} · click to play</div></button>)}</div>;
}

function VideoModal({ state, onChange, onClose }: { state: VideoModalState; onChange: (state: VideoModalState) => void; onClose: () => void }) {
  const recorded = state.evaluation.episodes.filter((episode) => episode.video);
  const episode = recorded[state.index];
  if (!episode?.video) return null;
  const move = (offset: number) => onChange({ ...state, index: (state.index + offset + recorded.length) % recorded.length });
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4" role="dialog" aria-modal="true" aria-label={`Evaluation video seed ${episode.seed}`} onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><div className="w-full max-w-5xl rounded-xl border border-ink-700 bg-ink-900 p-4 shadow-2xl"><div className="mb-3 flex flex-wrap items-center justify-between gap-3"><div><div className="flex items-center gap-2"><strong>Seed {episode.seed}</strong><Badge tone={episode.success ? "ok" : "bad"}>{episode.success ? "success" : "fail"}</Badge></div><div className="mt-1 text-xs text-ink-400">{episode.steps} steps · video {state.index + 1} of {recorded.length}</div></div><Button variant="subtle" onClick={onClose}>Close</Button></div><video key={episode.video} className="max-h-[70vh] w-full rounded-lg bg-black" controls autoPlay preload="auto" src={mediaUrl(`/training/evaluations/${state.evaluation.id}/videos/${episode.video}`)} /><div className="mt-3 flex justify-between"><Button variant="subtle" onClick={() => move(-1)}>← Previous</Button><Button variant="subtle" onClick={() => move(1)}>Next →</Button></div></div></div>;
}
