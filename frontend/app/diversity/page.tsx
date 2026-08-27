"use client";

import { useEffect, useMemo, useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { Alert, Badge, Card, Empty, Select, Stat } from "@/components/ui";
import { Histogram, QualityChart, Scatter } from "@/components/DiversityCharts";
import {
  labeling,
  type DiversityReport,
  type DiversityScope,
  type TaskOption,
} from "@/lib/labeling";

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
