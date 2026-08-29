"use client";

/**
 * Data-diversity report for one collection batch.
 *
 * Diversity used to live on its own page scoped to a whole task, which mixed
 * every collection run together. Reviewers decide per batch — whether this run
 * is balanced enough to train on — so the same report is scoped to the batch
 * being reviewed.
 *
 * Coverage stays measured against the task's full set of collected positions
 * on purpose: comparing a batch only to itself would always read 100%.
 */

import { useEffect, useState } from "react";
import { Histogram, QualityChart, Scatter } from "@/components/DiversityCharts";
import { Alert, Badge, Card, Empty, Select, Stat } from "@/components/ui";
import { labeling, type DiversityReport, type DiversityScope } from "@/lib/labeling";

function statusTone(code: string): "ok" | "warn" | "bad" | "neutral" {
  if (code === "ready") return "ok";
  if (code === "no_data") return "neutral";
  if (code === "imbalanced" || code === "low_coverage") return "warn";
  return "bad";
}

/**
 * The episode list reports a task by its display name, but the diversity
 * endpoint only accepts the simulator's own task names and 400s on anything
 * else. `lift` is the one that differs — episodes come back as `lift_cube` —
 * so a lift batch could never load its report until this mapped back.
 */
const REPORTED_TASK: Record<string, string> = { lift_cube: "lift" };

function reportedTask(task: string): string {
  return REPORTED_TASK[task] ?? task;
}

export function BatchDiversity({
  task,
  collectionBatchId,
}: {
  task: string | null;
  collectionBatchId?: string;
}) {
  const [scope, setScope] = useState<DiversityScope>("all");
  const [report, setReport] = useState<DiversityReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!task) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    labeling
      .diversity(reportedTask(task), scope, collectionBatchId)
      .then((result) => {
        if (!cancelled) setReport(result);
      })
      .catch((problem: unknown) => {
        if (!cancelled) {
          setReport(null);
          setError(problem instanceof Error ? problem.message : "Could not load diversity");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [task, scope, collectionBatchId]);

  if (!task) {
    return (
      <Card title="Data diversity">
        <Empty>
          This batch has no task recorded yet, so its diversity cannot be scoped. Add a task on
          the batch card.
        </Empty>
      </Card>
    );
  }

  return (
    <div className="space-y-5">
      <Card
        title="Data diversity"
        subtitle={`Quality balance, initial-state coverage and episode length for ${task}`}
        actions={
          <Select
            aria-label="Data scope"
            className="max-w-40"
            value={scope}
            onChange={(event) => setScope(event.target.value as DiversityScope)}
          >
            <option value="all">All collected</option>
            <option value="reviewed">All reviewed</option>
            <option value="approved">Approved only</option>
          </Select>
        }
      >
        {loading ? (
          <div className="py-12 text-center text-sm text-ink-400">Reading diversity metrics…</div>
        ) : error ? (
          <Alert tone="bad">{error}</Alert>
        ) : report ? (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Stat label="Episodes" value={report.episodes} hint={`${scope} scope`} />
            <Stat
              label="Simulator success"
              value={report.success_rate === null ? "—" : `${report.success_rate}%`}
              hint="Recorded task completion"
            />
            <Stat
              label="XY coverage"
              value={report.coverage.overall === null ? "—" : `${report.coverage.overall}%`}
              hint={`vs ${report.coverage.reference_episodes} collected poses for this task`}
            />
            <Stat
              label="Diversity status"
              value={<Badge tone={statusTone(report.status.code)}>{report.status.label}</Badge>}
              hint={report.status.detail}
            />
          </div>
        ) : (
          <Empty>No diversity data for this batch.</Empty>
        )}
      </Card>

      {!loading && report && report.episodes > 0 && (
        <div className="grid gap-5 xl:grid-cols-2">
          <Card
            title="Quality distribution"
            subtitle="Requested collection quality, split by simulator outcome"
          >
            <QualityChart rows={report.quality} />
          </Card>
          <Card title="Initial-position coverage" subtitle="Each point is one episode">
            <Scatter sets={report.position_sets} />
          </Card>
          <Card title="Episode length" subtitle="Histogram in frames">
            <Histogram histogram={report.length_histogram} />
          </Card>
          <Card title="Phase diagnostics" subtitle="Action-noise multiplier and failure attribution">
            <div className="max-h-64 overflow-auto">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-ink-900 text-left text-xs uppercase text-ink-400">
                  <tr>
                    <th className="pb-2">Phase</th>
                    <th className="pb-2 text-right">Noise scale</th>
                    <th className="pb-2 text-right">Failures</th>
                  </tr>
                </thead>
                <tbody>
                  {report.phases.map((phase) => (
                    <tr key={phase.phase} className="border-t border-ink-700/50">
                      <td className="py-1.5 font-mono text-xs">{phase.phase}</td>
                      <td className="py-1.5 text-right tabular">{phase.action_scale.toFixed(2)}×</td>
                      <td className="py-1.5 text-right tabular text-bad-400">{phase.failures}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}
