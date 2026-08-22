"use client";

import { useEffect, useMemo, useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { Alert, Badge, Card, Empty, Select, Stat } from "@/components/ui";
import {
  labeling,
  type DiversityReport,
  type DiversityScope,
  type TaskOption,
} from "@/lib/labeling";

const COLORS: Record<string, string> = {
  clean: "#2dd4bf", good: "#38bdf8", medium: "#fbbf24", poor: "#fb7185", unknown: "#94a3b8",
};

function QualityChart({ rows }: { rows: DiversityReport["quality"] }) {
  const max = Math.max(1, ...rows.map((row) => row.total));
  return (
    <div className="space-y-3">
      {rows.map((row) => (
        <div key={row.quality} className="grid grid-cols-[70px_1fr_42px] items-center gap-3 text-xs">
          <span className="capitalize text-ink-300">{row.quality}</span>
          <div className="flex h-6 overflow-hidden rounded bg-ink-800" title={`${row.success} simulator successes, ${row.failure} failures`}>
            <div className="bg-ok-600" style={{ width: `${(row.success / max) * 100}%` }} />
            <div className="bg-bad-600" style={{ width: `${(row.failure / max) * 100}%` }} />
          </div>
          <span className="text-right tabular text-ink-300">{row.total}</span>
        </div>
      ))}
      <div className="flex gap-4 text-[11px] text-ink-400">
        <span><i className="mr-1 inline-block h-2 w-2 rounded-sm bg-ok-600" />sim success</span>
        <span><i className="mr-1 inline-block h-2 w-2 rounded-sm bg-bad-600" />sim failure</span>
      </div>
    </div>
  );
}

function Scatter({ sets }: { sets: DiversityReport["position_sets"] }) {
  const [selected, setSelected] = useState(sets[0]?.key ?? "");
  useEffect(() => setSelected(sets[0]?.key ?? ""), [sets]);
  const points = sets.find((set) => set.key === selected)?.points ?? [];
  if (!points.length) return <Empty>No initial object positions are available.</Empty>;
  const xs = points.map((point) => point.x);
  const ys = points.map((point) => point.y);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const scale = (value: number, low: number, high: number, start: number, span: number) =>
    start + ((value - low) / Math.max(1e-6, high - low)) * span;
  return (
    <div>
      {sets.length > 1 && (
        <Select className="mb-3 max-w-40" value={selected} onChange={(event) => setSelected(event.target.value)}>
          {sets.map((set) => <option key={set.key} value={set.key}>{set.label}</option>)}
        </Select>
      )}
      <svg viewBox="0 0 560 250" className="w-full min-w-[360px] rounded-lg border border-ink-700 bg-ink-850">
        {[0, 1, 2, 3, 4].map((index) => <g key={index}>
          <line x1={45} x2={545} y1={20 + index * 52} y2={20 + index * 52} stroke="#dce3ec" />
          <line x1={45 + index * 125} x2={45 + index * 125} y1={20} y2={228} stroke="#dce3ec" />
        </g>)}
        {points.map((point) => (
          <circle key={point.episode_id} cx={scale(point.x, x0, x1, 55, 480)} cy={228 - scale(point.y, y0, y1, 10, 198)} r="4.5" fill={COLORS[point.quality] ?? COLORS.unknown} opacity="0.9">
            <title>{`${point.episode_id}\n${point.quality} · ${point.decision}\nX ${point.x.toFixed(3)}, Y ${point.y.toFixed(3)}`}</title>
          </circle>
        ))}
        <text x="280" y="246" fill="#64748b" fontSize="11">initial X (m)</text>
        <text x="8" y="125" fill="#64748b" fontSize="11" transform="rotate(-90 8 125)">initial Y (m)</text>
      </svg>
      <div className="mt-2 flex flex-wrap gap-3 text-[11px] text-ink-400">
        {Object.entries(COLORS).filter(([key]) => key !== "unknown").map(([key, color]) => (
          <span key={key}><i className="mr-1 inline-block h-2 w-2 rounded-full" style={{ backgroundColor: color }} />{key}</span>
        ))}
      </div>
    </div>
  );
}

function Histogram({ histogram }: { histogram: DiversityReport["length_histogram"] }) {
  if (!histogram.counts.length) return <Empty>No episode lengths are available.</Empty>;
  const max = Math.max(1, ...histogram.counts);
  return (
    <div className="flex h-52 items-end gap-2 border-b border-ink-600 px-2 pt-3">
      {histogram.counts.map((count, index) => (
        <div key={index} className="flex h-full flex-1 flex-col justify-end text-center">
          <span className="mb-1 text-[10px] tabular text-ink-400">{count}</span>
          <div className="min-h-1 rounded-t bg-accent-500/75" style={{ height: `${(count / max) * 82}%` }} title={`${histogram.edges[index]}–${histogram.edges[index + 1]} frames: ${count}`} />
          <span className="mt-1 truncate text-[9px] text-ink-400">{Math.round(histogram.edges[index])}</span>
        </div>
      ))}
    </div>
  );
}

export default function DiversityPage() {
  const { user } = useAuth();
  const [tasks, setTasks] = useState<TaskOption[]>([]);
  const [task, setTask] = useState("");
  const [scope, setScope] = useState<DiversityScope>("approved");
  const [report, setReport] = useState<DiversityReport | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!user) return;
    labeling.config().then((config) => {
      setTasks(config.tasks);
      setTask((current) => current || config.tasks[0]?.task || "");
    }).catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)));
  }, [user]);

  useEffect(() => {
    if (!task) return;
    setLoading(true); setError("");
    labeling.diversity(task, scope).then(setReport)
      .catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setLoading(false));
  }, [task, scope]);

  const statusTone = useMemo(() => report?.status.code === "healthy" ? "ok" : report?.status.code === "no_data" ? "bad" : "warn", [report]);
  if (!user) return null;
  return (
    <div className="space-y-5">
      <div>
        <h1 className="font-heading text-[22px] font-bold tracking-tight">Data diversity</h1>
        <p className="mt-0.5 text-sm text-ink-400">Track quality balance, initial-state coverage, trajectory length and phase risk before training.</p>
      </div>
      <Card>
        <div className="grid gap-4 md:grid-cols-2">
          <label className="text-xs text-ink-300">Task<Select className="mt-1" value={task} onChange={(event) => setTask(event.target.value)}>{tasks.map((item) => <option key={item.task} value={item.task}>{item.tool_label ?? item.tool}</option>)}</Select></label>
          <label className="text-xs text-ink-300">Data scope<Select className="mt-1" value={scope} onChange={(event) => setScope(event.target.value as DiversityScope)}><option value="approved">Approved only</option><option value="reviewed">All reviewed</option><option value="all">All collected</option></Select></label>
        </div>
      </Card>
      {error && <Alert>{error}</Alert>}
      {loading && <Card><div className="py-12 text-center text-sm text-ink-400">Reading diversity metrics…</div></Card>}
      {!loading && report && <>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Stat label="Episodes" value={report.episodes} hint={`${scope} scope`} />
          <Stat label="Simulator success" value={report.success_rate === null ? "—" : `${report.success_rate}%`} hint="Recorded task completion" />
          <Stat label="Reviewed XY coverage" value={report.coverage.overall === null ? "—" : `${report.coverage.overall}%`} hint={`vs ${report.coverage.reference_episodes} collected poses · X ${report.coverage.x ?? "—"}% · Y ${report.coverage.y ?? "—"}%`} />
          <Stat label="MVP diversity status" value={<Badge tone={statusTone}>{report.status.label}</Badge>} hint={report.status.detail} />
        </div>
        <div className="grid gap-5 lg:grid-cols-2">
          <Card title="Quality distribution" subtitle="Requested collection quality, split by simulator outcome"><QualityChart rows={report.quality} /></Card>
          <Card title="Initial-position coverage" subtitle="Each point is one episode; hover for details"><Scatter sets={report.position_sets} /></Card>
          <Card title="Episode length" subtitle="Histogram in frames"><Histogram histogram={report.length_histogram} /></Card>
          <Card title="Phase diagnostics" subtitle="Configured action-noise multiplier and failure attribution when recorded">
            <div className="max-h-64 overflow-auto"><table className="w-full text-sm"><thead className="sticky top-0 bg-ink-900 text-left text-xs uppercase text-ink-400"><tr><th className="pb-2">Phase</th><th className="pb-2 text-right">Noise scale</th><th className="pb-2 text-right">Failures</th></tr></thead><tbody>{report.phases.map((phase) => <tr key={phase.phase} className="border-t border-ink-700/50"><td className="py-1.5 font-mono text-xs">{phase.phase}</td><td className="py-1.5 text-right tabular">{phase.action_scale.toFixed(2)}×</td><td className="py-1.5 text-right tabular text-bad-400">{phase.failures}</td></tr>)}</tbody></table></div>
          </Card>
        </div>
        <Alert tone="info">Coverage is relative to the full set of collected initial XY positions for this task. It is not the simulator&apos;s theoretical spawn-area coverage.</Alert>
      </>}
    </div>
  );
}
