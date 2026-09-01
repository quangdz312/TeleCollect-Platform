"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { COLLECTION_ENABLED } from "@/lib/features";
import { Alert, Badge, Button, Card, Empty, Skeleton, Stat } from "@/components/ui";
import { api, type DatasetExport, type EvaluationRun, type TrainingRun } from "@/lib/api";
import { bytes, percent, timeAgo } from "@/lib/format";
import { rawApi, type RawEpisodePage } from "@/lib/raw";
import { Icon, type IconName } from "@/components/icons";

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
    ["Reviewed", reviewed, "/raw?collection_batch_id=__all__&review_status=approved"],
    ["Approved", raw?.summary.approved ?? 0, "/raw?collection_batch_id=__all__&review_status=approved"],
    ["Datasets", datasets.length, "/datasets"],
    ["Trained", runs.filter((run) => run.status === "succeeded").length, "/training"],
    ["Evaluated", completedEvaluations.length, "/evaluate"],
  ];
  const pipelineIcons: IconName[] = ["collect", "review", "review", "datasets", "training", "evaluate"];

  return <div className="space-y-4">
    <div className="flex flex-wrap items-end justify-between gap-4 border-b-2 border-ink-700 pb-3">
      <div><p className="text-[11px] font-bold uppercase tracking-[0.18em] text-accent-500">TeleCollect command center</p><h1 className="mt-1 font-heading text-[24px] font-bold">Overview</h1><p className="mt-1 text-sm text-ink-400">Follow data from collection through review, conversion, training and evaluation.</p></div>
      <div className="flex gap-2">{COLLECTION_ENABLED && <Link href="/collect"><Button variant="primary">Collect data</Button></Link>}<Link href="/raw"><Button variant={COLLECTION_ENABLED ? "ghost" : "primary"}>Review queue</Button></Link></div>
    </div>
    {error && <Alert>{error}</Alert>}

    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-6" aria-busy={loading}>
      {loading ? Array.from({ length: 6 }).map((_, index) => <Skeleton key={index} className="h-[116px]" />) : <>
        <Link href="/raw"><Stat label="Raw episodes" value={raw?.summary.total ?? "—"} hint={`${raw?.summary.teleop ?? 0} recordings · ${raw?.summary.scripted ?? 0} scripted`} /></Link>
        <Link href="/raw?collection_batch_id=__all__&review_status=pending"><Stat label="Pending review" value={pending} hint={`${reviewed} already reviewed`} tone={pending ? "warn" : "ok"} /></Link>
        <Link href="/raw?collection_batch_id=__all__&review_status=approved"><Stat label="Approved" value={raw?.summary.approved ?? "—"} hint={raw ? `${percent(raw.summary.approved / Math.max(1, raw.summary.total), 1)} of raw` : undefined} tone="ok" /></Link>
        <Link href="/datasets"><Stat label="Datasets" value={datasets.length} hint={`${bytes(totalDatasetBytes)} exported`} /></Link>
        <Link href="/training"><Stat label="Training runs" value={runs.length} hint={`${activeRuns.length} active · ${failedRuns} failed`} tone={failedRuns ? "warn" : undefined} /></Link>
        <Link href="/evaluate"><Stat label="Evaluations" value={evaluations.length} hint={bestEvaluation ? `best ${percent(bestEvaluation.success_rate ?? 0, 1)}` : "no completed result"} tone={failedEvaluations ? "warn" : "ok"} /></Link>
      </>}
    </div>

    <Card title="Data pipeline" subtitle="Click a stage to continue where attention is needed."><div className="grid gap-2 md:grid-cols-6">
      {pipeline.map(([label, value, href], index) => <Link key={label} href={href} className="group relative flex flex-col items-center justify-center rounded-xl border border-transparent px-2 py-2 text-center hover:border-accent-500/20 hover:bg-accent-500/5"><span className="grid h-10 w-10 place-items-center rounded-full bg-accent-500/10 text-accent-500"><Icon name={pipelineIcons[index]} className="h-5 w-5" /></span><div className="mt-1.5 text-[10px] font-medium text-ink-300">{label}</div><div className="font-heading text-sm font-bold tabular text-ink-100">{value}</div>{index < 5 && <span className="absolute -right-2 top-7 z-10 hidden text-accent-500 md:block">→</span>}</Link>)}
    </div></Card>

    <div className="grid items-stretch gap-3 xl:grid-cols-[1.5fr_1fr]">
      <div className="space-y-3">
        <Card title="Collection trend" subtitle={`Episodes by source date${raw?.summary.undated ? ` · ${raw.summary.undated} legacy episodes without a date are excluded` : ""}.`} actions={<div className="flex gap-3 text-[11px] text-ink-400"><span><i className="mr-1 inline-block h-2.5 w-2.5 rounded-sm bg-accent-500" />Scripted</span><span><i className="mr-1 inline-block h-2.5 w-2.5 rounded-sm bg-ok-600" />Teleop</span></div>}>
          {trendRows.length ? <TrendChart rows={trendRows} /> : <Empty>No collection dates are available yet.</Empty>}
        </Card>
        <div className="grid gap-3 md:grid-cols-2">
          <Card title="Data balance by task" subtitle="All raw episodes.">
            {taskRows.length ? <div className="space-y-2.5">{taskRows.slice(0, 5).map(([task, count]) => <div key={task} className="grid grid-cols-[minmax(90px,1fr)_2fr_48px] items-center gap-2 text-xs"><span className="truncate font-medium">{task}</span><Bar value={count} max={taskMax} /><span className="text-right tabular">{count}</span></div>)}</div> : <Empty>No raw episode statistics available.</Empty>}
          </Card>
          <Card title="Review health" subtitle="Approval coverage.">
            {raw ? <div className="grid grid-cols-[92px_1fr] items-center gap-4"><div className="grid aspect-square place-items-center rounded-full bg-[conic-gradient(#059669_var(--coverage),#e2e8f0_0)] p-3" style={{ "--coverage": `${raw.summary.approved / Math.max(1, raw.summary.total) * 100}%` } as React.CSSProperties}><div className="grid h-full w-full place-items-center rounded-full bg-white font-heading text-xl font-bold">{percent(raw.summary.approved / Math.max(1, raw.summary.total), 0)}</div></div><div className="space-y-1.5 text-xs"><div className="flex justify-between"><span className="text-ok-600">Approved</span><strong>{raw.summary.approved}</strong></div><div className="flex justify-between"><span className="text-warn-400">Pending</span><strong>{raw.summary.pending}</strong></div><div className="flex justify-between"><span className="text-bad-600">Rejected</span><strong>{raw.summary.rejected}</strong></div><Link href="/raw" className="mt-2 inline-block text-accent-500 hover:underline">View review queue →</Link></div></div> : <Empty>Reviewer access is required.</Empty>}
          </Card>
        </div>
      </div>
      <FleetSignal activeRuns={activeRuns.length} datasets={datasets.length} />
    </div>

    <div className="grid gap-5 xl:grid-cols-2">
      <Card title="Collection batches" subtitle="Largest batches currently present in raw storage." actions={COLLECTION_ENABLED ? <Link href="/collect"><Button variant="subtle">New collection</Button></Link> : undefined}>
        {batchRows.length ? <div className="divide-y divide-ink-700/60">{batchRows.map(([batch, count]) => <div key={batch} className="flex items-center justify-between gap-3 py-2.5"><div className="min-w-0"><div className="truncate text-sm font-medium">{batch}</div><div className="text-xs text-ink-400">{count} episodes</div></div><div className="flex gap-2"><Link href={`/raw?collection_batch_id=${encodeURIComponent(batch)}`}><Button variant="subtle">View raw</Button></Link><Link href={`/raw?collection_batch_id=${encodeURIComponent(batch)}`}><Button variant="ghost">Convert</Button></Link></div></div>)}</div> : <Empty>No collection batch metadata yet.</Empty>}
      </Card>
      <Card title="Action required" subtitle="Items worth checking before the next training cycle."><div className="space-y-2"><Action href="/raw?collection_batch_id=__all__&review_status=pending" count={pending} label="episodes waiting for review" tone={pending ? "warn" : "ok"} /><Action href="/datasets" count={unusedDatasets.length} label="datasets have never been trained" tone={unusedDatasets.length ? "warn" : "ok"} /><Action href="/training" count={failedRuns} label="training runs failed" tone={failedRuns ? "bad" : "ok"} /><Action href="/evaluate?status=failed" count={failedEvaluations} label="evaluations failed" tone={failedEvaluations ? "bad" : "ok"} /></div></Card>
    </div>

    <Card title="Dataset usage" subtitle="Exports connected to training runs." actions={<Link href="/datasets"><Button variant="subtle">View all</Button></Link>}>
      {datasets.length ? <div className="overflow-x-auto"><table className="w-full text-sm"><thead className="text-left text-[10px] uppercase tracking-wider text-ink-400"><tr><th className="pb-2">Dataset</th><th className="pb-2 text-right">Episodes</th><th className="pb-2 text-right">Frames</th><th className="pb-2 text-right">Size</th><th className="pb-2 text-center">Usage</th><th className="pb-2 text-right">Created</th></tr></thead><tbody>{datasets.slice(0, 8).map((dataset) => <tr key={dataset.id} className="border-t border-ink-700/60"><td className="py-2.5"><Link href={`/datasets/${dataset.id}`} className="font-medium text-accent-500 hover:underline">{dataset.name}</Link><div className="text-xs text-ink-400">{dataset.tasks.join(", ")}</div></td><td className="py-2.5 text-right">{dataset.num_episodes}</td><td className="py-2.5 text-right">{dataset.num_frames.toLocaleString()}</td><td className="py-2.5 text-right">{bytes(dataset.size_bytes)}</td><td className="py-2.5 text-center">{usedDatasetIds.has(dataset.id) ? <Badge tone="ok">trained</Badge> : <Badge tone="warn">never trained</Badge>}</td><td className="py-2.5 text-right text-xs text-ink-400">{timeAgo(dataset.created_at)}</td></tr>)}</tbody></table></div> : <Empty>No converted dataset yet.</Empty>}
    </Card>
  </div>;
}

function Metric({ label, value, tone }: { label: string; value: number; tone: string }) {
  return <div><div className="text-xs text-ink-400">{label}</div><div className={`mt-1 text-xl font-bold ${tone}`}>{value}</div></div>;
}

function FleetSignal({ activeRuns, datasets }: { activeRuns: number; datasets: number }) {
  return <Card className="h-full" title="Fleet signal" subtitle="Robotics cell topology and service status.">
    <div className="relative flex h-[370px] min-h-0 flex-col overflow-hidden rounded-xl border border-accent-500/20 bg-accent-500/[0.035] p-3">
      <div className="absolute inset-0 bg-[linear-gradient(rgba(37,99,235,.08)_1px,transparent_1px),linear-gradient(90deg,rgba(37,99,235,.08)_1px,transparent_1px)] bg-[size:24px_24px]" />
      <div className="relative z-10 h-[292px] min-h-0 flex-none overflow-hidden rounded-lg py-1">
        <img src="/robot-arm-blueprint.png" alt="Technical blueprint of a six-axis industrial robot arm" className="h-full w-full object-contain mix-blend-multiply" />
      </div>
      <svg viewBox="0 0 420 270" className="hidden" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M8 28V8h20M392 8h20v20M8 242v20h20M392 262h20v-20" strokeWidth="2"/>
        <path d="M34 232h352M70 241h280" opacity=".35"/>

        <path d="M139 224h142l-12-25H151l-12 25Z"/><path d="M158 199v-20h104v20M171 179l9-30h60l10 30"/>
        <ellipse cx="210" cy="148" rx="35" ry="18"/><ellipse cx="210" cy="148" rx="19" ry="9"/>

        <path d="m192 136-18-69 22-6 24 71M216 132l25-68 22 8-31 70"/>
        <circle cx="183" cy="63" r="22"/><circle cx="183" cy="63" r="10"/><path d="M162 58 111 81l9 22 55-22M170 76l-42 40 16 17 49-47"/>
        <circle cx="119" cy="93" r="19"/><circle cx="119" cy="93" r="8"/>
        <path d="m105 80-41-30 13-18 47 36M108 104l-55-13 5-21 58 16"/>
        <circle cx="68" cy="45" r="17"/><circle cx="68" cy="45" r="7"/>
        <path d="m53 53-24 27 14 13 29-29M77 58l-17 37 16 8 22-40"/>
        <circle cx="48" cy="91" r="13"/><circle cx="48" cy="91" r="5"/>
        <path d="m42 102 8 25M55 101l12 22M50 127l-13 15M50 127l2 20M67 123l2 18M67 123l13 12"/>

        <path d="M181 165h58M190 187h40M199 210h22" opacity=".55"/>
        <path d="M283 52h88M283 62h61M283 72h42" opacity=".35"/>
        <circle cx="291" cy="52" r="2" fill="currentColor" stroke="none"/><circle cx="291" cy="62" r="2" fill="currentColor" stroke="none"/><circle cx="291" cy="72" r="2" fill="currentColor" stroke="none"/>
      </svg>
      <div className="relative z-10 mt-2 grid grid-cols-3 gap-2">
        <StatusChip label="Robot cell" value="Nominal" tone="ok" />
        <StatusChip label="Active training" value={String(activeRuns)} tone="info" />
        <StatusChip label="Datasets" value={String(datasets)} tone="ok" />
      </div>
    </div>
  </Card>;
}

function StatusChip({ label, value, tone }: { label: string; value: string; tone: "ok" | "info" }) {
  return <div className="rounded-lg border border-ink-700/80 bg-white/85 px-2.5 py-2"><div className="flex items-center gap-1.5 text-[9px] font-bold uppercase tracking-wider text-ink-400"><span className={`h-1.5 w-1.5 rounded-full ${tone === "ok" ? "bg-ok-600" : "bg-accent-500"}`} />{label}</div><div className={`mt-1 text-sm font-bold ${tone === "ok" ? "text-ok-600" : "text-accent-500"}`}>{value}</div></div>;
}

function TrendChart({ rows }: { rows: [string, { teleop: number; scripted: number }][] }) {
  const peak = Math.max(1, ...rows.map(([, value]) => value.teleop + value.scripted));
  const magnitude = 10 ** Math.floor(Math.log10(peak));
  const max = Math.ceil(peak / magnitude) * magnitude;
  const scriptedTotal = rows.reduce((sum, [, value]) => sum + value.scripted, 0);
  const teleopTotal = rows.reduce((sum, [, value]) => sum + value.teleop, 0);
  const ticks = [max, Math.round(max * 0.75), Math.round(max * 0.5), Math.round(max * 0.25), 0];
  return <div className="rounded-lg border border-ink-700/60 bg-gradient-to-b from-ink-850/50 to-ink-900 p-2.5">
    <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
      <div><div className="text-[9px] font-bold uppercase tracking-wider text-ink-400">Last {rows.length} collection days</div><div className="text-xs text-ink-300"><strong className="text-ink-100">{scriptedTotal + teleopTotal}</strong> episodes represented</div></div>
      <div className="flex gap-2"><Badge tone="info">{scriptedTotal} scripted</Badge><Badge tone="ok">{teleopTotal} teleop</Badge></div>
    </div>
    <div className="grid grid-cols-[38px_minmax(0,1fr)]">
      <div className="relative h-28 text-[9px] tabular text-ink-400">{ticks.map((tick, index) => <span key={index} className="absolute right-2 -translate-y-1/2" style={{ top: `${index * 25}%` }}>{tick}</span>)}</div>
      <div className="relative h-28 border-b border-l border-ink-700">
        {ticks.slice(0, -1).map((_, index) => <div key={index} className="pointer-events-none absolute inset-x-0 border-t border-dashed border-ink-700/60" style={{ top: `${index * 25}%` }} />)}
        <div className="absolute inset-0 grid items-end gap-2 px-3 sm:gap-5" style={{ gridTemplateColumns: `repeat(${rows.length}, minmax(24px, 1fr))` }}>
          {rows.map(([day, value]) => {
            const total = value.teleop + value.scripted;
            const height = Math.max(3, total / max * 88);
            return <div key={day} className="group relative flex h-full flex-col items-center justify-end pt-3">
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
