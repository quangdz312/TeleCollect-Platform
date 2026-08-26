"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { Alert, Badge, Button, Card, Empty, Skeleton, Stat } from "@/components/ui";
import { api, type DatasetExport, type EvaluationRun, type TrainingRun } from "@/lib/api";
import { bytes, percent, timeAgo } from "@/lib/format";
import { rawApi, type RawEpisodePage } from "@/lib/raw";

const RUNNING = new Set(["pending", "running"]);

function Bar({ value, max }: { value: number; max: number }) {
  return <div className="h-2 overflow-hidden rounded-full bg-ink-700/70"><div className="h-full rounded-full bg-accent-500" style={{ width: `${max ? Math.max(2, value / max * 100) : 0}%` }} /></div>;
}

export default function OverviewPage() {
  const { user } = useAuth();
  const [raw, setRaw] = useState<RawEpisodePage | null>(null);
  const [datasets, setDatasets] = useState<DatasetExport[]>([]);
  const [runs, setRuns] = useState<TrainingRun[]>([]);
  const [evaluations, setEvaluations] = useState<EvaluationRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!user) return;
    let mounted = true;
    setLoading(true);
    void (async () => {
      try {
        const canReview = user.role === "reviewer" || user.role === "admin";
        const [rawPage, exports, trainingRuns, evaluationRuns] = await Promise.all([
          canReview ? rawApi.episodes({ page: 1, page_size: 8 }) : Promise.resolve(null),
          api.exports(), api.runs(), api.evaluations(),
        ]);
        if (!mounted) return;
        setRaw(rawPage); setDatasets(exports); setRuns(trainingRuns); setEvaluations(evaluationRuns);
      } catch (exc) {
        if (mounted) setError(exc instanceof Error ? exc.message : "Could not load dashboard");
      } finally {
        if (mounted) setLoading(false);
      }
    })();
    return () => { mounted = false; };
  }, [user]);

  const usedDatasetIds = useMemo(() => new Set(runs.map((run) => run.dataset_id).filter(Boolean)), [runs]);
  const taskRows = Object.entries(raw?.summary.by_task ?? {}).sort((a, b) => b[1] - a[1]);
  const taskMax = Math.max(1, ...taskRows.map(([, count]) => count));
  const batchRows = Object.entries(raw?.summary.by_batch ?? {}).sort((a, b) => b[1] - a[1]).slice(0, 6);
  const trendRows = Object.entries(raw?.summary.by_day ?? {}).sort(([a], [b]) => a.localeCompare(b)).slice(-10);
  const pending = raw?.summary.pending ?? 0;
  const reviewed = (raw?.summary.approved ?? 0) + (raw?.summary.rejected ?? 0);
  const activeRuns = runs.filter((run) => RUNNING.has(run.status));
  const failedRuns = runs.filter((run) => run.status === "failed").length;
  const failedEvaluations = evaluations.filter((item) => item.status === "failed").length;
  const unusedDatasets = datasets.filter((dataset) => !usedDatasetIds.has(dataset.id));
  const completedEvaluations = evaluations.filter((item) => item.status === "succeeded" && item.success_rate != null);
  const bestEvaluation = [...completedEvaluations].sort((a, b) => (b.success_rate ?? 0) - (a.success_rate ?? 0))[0];
  const totalDatasetBytes = datasets.reduce((sum, dataset) => sum + dataset.size_bytes, 0);

  if (!user) return null;
  const pipeline: [string, number, string][] = [
    ["Collected", raw?.summary.total ?? 0, "/raw"],
    ["Reviewed", reviewed, "/review"],
    ["Approved", raw?.summary.approved ?? 0, "/raw?review_status=approved"],
    ["Datasets", datasets.length, "/datasets"],
    ["Trained", runs.filter((run) => run.status === "succeeded").length, "/training"],
    ["Evaluated", completedEvaluations.length, "/evaluate"],
  ];

  return <div className="space-y-5">
    <div className="flex flex-wrap items-end justify-between gap-4 border-b-2 border-ink-700 pb-3">
      <div><p className="text-[11px] font-bold uppercase tracking-[0.18em] text-accent-500">TeleCollect command center</p><h1 className="mt-1 font-heading text-[24px] font-bold">Overview</h1><p className="mt-1 text-sm text-ink-400">Follow data from collection through review, conversion, training and evaluation.</p></div>
      <div className="flex gap-2"><Link href="/collect"><Button variant="primary">Collect data</Button></Link><Link href="/review"><Button variant="ghost">Review queue</Button></Link></div>
    </div>
    {error && <Alert>{error}</Alert>}

    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-6" aria-busy={loading}>
      {loading ? Array.from({ length: 6 }).map((_, index) => <Skeleton key={index} className="h-[116px]" />) : <>
        <Link href="/raw"><Stat label="Raw episodes" value={raw?.summary.total ?? "—"} hint={`${raw?.summary.teleop ?? 0} recordings · ${raw?.summary.scripted ?? 0} scripted`} /></Link>
        <Link href="/review?status=pending"><Stat label="Pending review" value={pending} hint={`${reviewed} already reviewed`} tone={pending ? "warn" : "ok"} /></Link>
        <Link href="/raw?review_status=approved"><Stat label="Approved" value={raw?.summary.approved ?? "—"} hint={raw ? `${percent(raw.summary.approved / Math.max(1, raw.summary.total), 1)} of raw` : undefined} tone="ok" /></Link>
        <Link href="/datasets"><Stat label="Datasets" value={datasets.length} hint={`${bytes(totalDatasetBytes)} exported`} /></Link>
        <Link href="/training"><Stat label="Training runs" value={runs.length} hint={`${activeRuns.length} active · ${failedRuns} failed`} tone={failedRuns ? "warn" : undefined} /></Link>
        <Link href="/evaluate"><Stat label="Evaluations" value={evaluations.length} hint={bestEvaluation ? `best ${percent(bestEvaluation.success_rate ?? 0, 1)}` : "no completed result"} tone={failedEvaluations ? "warn" : "ok"} /></Link>
      </>}
    </div>

    <Card title="Data pipeline" subtitle="Click a stage to continue where attention is needed."><div className="grid gap-2 md:grid-cols-6">
      {pipeline.map(([label, value, href], index) => <Link key={label} href={href} className="group relative rounded-xl border border-ink-700 bg-ink-850/60 px-4 py-3 hover:border-accent-500/50"><div className="text-[10px] font-bold uppercase tracking-wider text-ink-400">{label}</div><div className="mt-1 font-heading text-2xl font-bold tabular">{value}</div>{index < 5 && <span className="absolute -right-2 top-1/2 z-10 hidden -translate-y-1/2 text-ink-400 md:block">→</span>}</Link>)}
    </div></Card>

    <Card title="Collection trend" subtitle={`Episodes by source date${raw?.summary.undated ? ` · ${raw.summary.undated} legacy episodes without a date are excluded` : ""}.`} actions={<div className="flex gap-3 text-[11px] text-ink-400"><span><i className="mr-1 inline-block h-2.5 w-2.5 rounded-sm bg-accent-500" />Scripted</span><span><i className="mr-1 inline-block h-2.5 w-2.5 rounded-sm bg-ok-600" />Teleop</span></div>}>
      {trendRows.length ? <TrendChart rows={trendRows} /> : <Empty>No collection dates are available yet.</Empty>}
    </Card>

    <div className="grid gap-5 xl:grid-cols-[1.15fr_.85fr]">
      <Card title="Data balance by task" subtitle="All raw episodes, including teleop and scripted sources." actions={<Link href="/raw"><Button variant="subtle">Open raw</Button></Link>}>
        {taskRows.length ? <div className="space-y-3">{taskRows.map(([task, count]) => <div key={task} className="grid grid-cols-[minmax(110px,1fr)_3fr_70px] items-center gap-3 text-sm"><span className="truncate font-medium">{task}</span><Bar value={count} max={taskMax} /><span className="text-right tabular">{count}</span></div>)}</div> : <Empty>No raw episode statistics available.</Empty>}
      </Card>
      <Card title="Review health" subtitle="Review status and recorded outcomes across raw data.">
        {raw ? <div className="space-y-5"><div><div className="mb-2 flex justify-between text-xs"><span>Approval coverage</span><span>{percent(raw.summary.approved / Math.max(1, raw.summary.total), 1)}</span></div><div className="flex h-3 overflow-hidden rounded-full bg-ink-700"><div className="bg-ok-600" style={{ width: `${raw.summary.approved / Math.max(1, raw.summary.total) * 100}%` }} /><div className="bg-bad-600" style={{ width: `${raw.summary.rejected / Math.max(1, raw.summary.total) * 100}%` }} /><div className="bg-warn-400" style={{ width: `${raw.summary.pending / Math.max(1, raw.summary.total) * 100}%` }} /></div><div className="mt-2 flex flex-wrap gap-2"><Badge tone="ok">{raw.summary.approved} approved</Badge><Badge tone="bad">{raw.summary.rejected} rejected</Badge><Badge tone="warn">{raw.summary.pending} pending</Badge></div></div><div className="grid grid-cols-2 gap-3 border-t border-ink-700 pt-4"><Metric label="Recorded successes" value={raw.summary.successes} tone="text-ok-400" /><Metric label="Recorded failures" value={raw.summary.failures} tone="text-bad-400" /></div></div> : <Empty>Reviewer access is required for raw statistics.</Empty>}
      </Card>
    </div>

    <div className="grid gap-5 xl:grid-cols-2">
      <Card title="Collection batches" subtitle="Largest batches currently present in raw storage." actions={<Link href="/collect"><Button variant="subtle">New collection</Button></Link>}>
        {batchRows.length ? <div className="divide-y divide-ink-700/60">{batchRows.map(([batch, count]) => <div key={batch} className="flex items-center justify-between gap-3 py-2.5"><div className="min-w-0"><div className="truncate text-sm font-medium">{batch}</div><div className="text-xs text-ink-400">{count} episodes</div></div><div className="flex gap-2"><Link href={`/raw?collection_batch_id=${encodeURIComponent(batch)}`}><Button variant="subtle">View raw</Button></Link><Link href={`/convert?batch=${encodeURIComponent(batch)}`}><Button variant="ghost">Convert</Button></Link></div></div>)}</div> : <Empty>No collection batch metadata yet.</Empty>}
      </Card>
      <Card title="Action required" subtitle="Items worth checking before the next training cycle."><div className="space-y-2"><Action href="/review?status=pending" count={pending} label="episodes waiting for review" tone={pending ? "warn" : "ok"} /><Action href="/datasets" count={unusedDatasets.length} label="datasets have never been trained" tone={unusedDatasets.length ? "warn" : "ok"} /><Action href="/training" count={failedRuns} label="training runs failed" tone={failedRuns ? "bad" : "ok"} /><Action href="/evaluate?status=failed" count={failedEvaluations} label="evaluations failed" tone={failedEvaluations ? "bad" : "ok"} /></div></Card>
    </div>

    <Card title="Dataset usage" subtitle="Exports connected to training runs." actions={<Link href="/datasets"><Button variant="subtle">View all</Button></Link>}>
      {datasets.length ? <div className="overflow-x-auto"><table className="w-full text-sm"><thead className="text-left text-[10px] uppercase tracking-wider text-ink-400"><tr><th className="pb-2">Dataset</th><th className="pb-2 text-right">Episodes</th><th className="pb-2 text-right">Frames</th><th className="pb-2 text-right">Size</th><th className="pb-2 text-center">Usage</th><th className="pb-2 text-right">Created</th></tr></thead><tbody>{datasets.slice(0, 8).map((dataset) => <tr key={dataset.id} className="border-t border-ink-700/60"><td className="py-2.5"><Link href={`/datasets/${dataset.id}`} className="font-medium text-accent-500 hover:underline">{dataset.name}</Link><div className="text-xs text-ink-400">{dataset.tasks.join(", ")}</div></td><td className="py-2.5 text-right">{dataset.num_episodes}</td><td className="py-2.5 text-right">{dataset.num_frames.toLocaleString()}</td><td className="py-2.5 text-right">{bytes(dataset.size_bytes)}</td><td className="py-2.5 text-center">{usedDatasetIds.has(dataset.id) ? <Badge tone="ok">trained</Badge> : <Badge tone="warn">never trained</Badge>}</td><td className="py-2.5 text-right text-xs text-ink-400">{timeAgo(dataset.created_at)}</td></tr>)}</tbody></table></div> : <Empty>No converted dataset yet.</Empty>}
    </Card>
  </div>;
}

function Metric({ label, value, tone }: { label: string; value: number; tone: string }) {
  return <div><div className="text-xs text-ink-400">{label}</div><div className={`mt-1 text-xl font-bold ${tone}`}>{value}</div></div>;
}

function TrendChart({ rows }: { rows: [string, { teleop: number; scripted: number }][] }) {
  const peak = Math.max(1, ...rows.map(([, value]) => value.teleop + value.scripted));
  const magnitude = 10 ** Math.floor(Math.log10(peak));
  const max = Math.ceil(peak / magnitude) * magnitude;
  const scriptedTotal = rows.reduce((sum, [, value]) => sum + value.scripted, 0);
  const teleopTotal = rows.reduce((sum, [, value]) => sum + value.teleop, 0);
  const ticks = [max, Math.round(max * 0.75), Math.round(max * 0.5), Math.round(max * 0.25), 0];
  return <div className="rounded-xl border border-ink-700/60 bg-gradient-to-b from-ink-850/50 to-ink-900 p-4">
    <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
      <div><div className="text-[10px] font-bold uppercase tracking-wider text-ink-400">Last {rows.length} collection days</div><div className="mt-1 text-sm text-ink-300"><strong className="text-ink-100">{scriptedTotal + teleopTotal}</strong> episodes represented</div></div>
      <div className="flex gap-2"><Badge tone="info">{scriptedTotal} scripted</Badge><Badge tone="ok">{teleopTotal} teleop</Badge></div>
    </div>
    <div className="grid grid-cols-[38px_minmax(0,1fr)]">
      <div className="relative h-60 text-[10px] tabular text-ink-400">{ticks.map((tick, index) => <span key={index} className="absolute right-2 -translate-y-1/2" style={{ top: `${index * 25}%` }}>{tick}</span>)}</div>
      <div className="relative h-60 border-b border-l border-ink-700">
        {ticks.slice(0, -1).map((_, index) => <div key={index} className="pointer-events-none absolute inset-x-0 border-t border-dashed border-ink-700/60" style={{ top: `${index * 25}%` }} />)}
        <div className="absolute inset-0 grid items-end gap-2 px-3 sm:gap-5" style={{ gridTemplateColumns: `repeat(${rows.length}, minmax(24px, 1fr))` }}>
          {rows.map(([day, value]) => {
            const total = value.teleop + value.scripted;
            const height = Math.max(3, total / max * 88);
            return <div key={day} className="group relative flex h-full flex-col items-center justify-end pt-7">
              <div className="absolute z-20 hidden w-max -translate-y-2 rounded-lg border border-ink-700 bg-ink-950 px-3 py-2 text-[11px] shadow-xl group-hover:block" style={{ bottom: `${height}%` }}><div className="font-semibold text-ink-100">{new Date(`${day}T00:00:00`).toLocaleDateString()}</div><div className="mt-1 text-accent-500">{value.scripted} scripted</div><div className="text-ok-600">{value.teleop} teleop</div></div>
              <span className="mb-1 text-[10px] font-semibold tabular text-ink-300">{total}</span>
              <div className="flex w-full max-w-16 flex-col-reverse overflow-hidden rounded-t-lg shadow-[0_5px_15px_rgba(37,99,235,0.16)] transition-all duration-200 group-hover:brightness-110" style={{ height: `${height}%` }}>
                {value.scripted > 0 && <div className="bg-gradient-to-t from-blue-700 to-accent-500" style={{ height: `${value.scripted / total * 100}%` }} />}
                {value.teleop > 0 && <div className="bg-gradient-to-t from-emerald-700 to-ok-600" style={{ height: `${value.teleop / total * 100}%` }} />}
              </div>
            </div>;
          })}
        </div>
      </div>
      <div />
      <div className="grid gap-2 px-3 pt-2 text-center text-[10px] font-medium text-ink-400 sm:gap-5" style={{ gridTemplateColumns: `repeat(${rows.length}, minmax(24px, 1fr))` }}>{rows.map(([day]) => <span key={day}>{new Date(`${day}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" })}</span>)}</div>
    </div>
  </div>;
}

function Action({ href, count, label, tone }: { href: string; count: number; label: string; tone: "ok" | "warn" | "bad" }) {
  return <Link href={href} className="flex items-center justify-between rounded-xl border border-ink-700/70 bg-ink-850/50 px-4 py-3 hover:border-accent-500/50"><span className="text-sm">{label}</span><Badge tone={tone}>{count}</Badge></Link>;
}
