"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { StatusBadge } from "@/components/StatusBadge";
import { Button, Card, Empty, Skeleton, Stat, Thumbnail } from "@/components/ui";
import { api, thumbnailUrl, type Demo, type Summary, type Task } from "@/lib/api";
import { bytes, percent, timeAgo } from "@/lib/format";

export default function OverviewPage() {
  const { user } = useAuth();
  const [summary, setSummary] = useState<Summary | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [recent, setRecent] = useState<Demo[]>([]);
  const [health, setHealth] = useState<Record<string, any> | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!user) return;
    let mounted = true;
    setLoading(true);
    void (async () => {
      try {
        const [s, t, d, h] = await Promise.all([
          api.summary(),
          api.tasks(),
          api.demos({ limit: 8 }),
          api.health(),
        ]);
        if (!mounted) return;
        setSummary(s);
        setTasks(t);
        setRecent(d.items);
        setHealth(h);
      } catch {
        // ignored — Stat/Card sections fall back to their empty state
      } finally {
        if (mounted) setLoading(false);
      }
    })();
    return () => {
      mounted = false;
    };
  }, [user]);

  if (!user) return null;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-5 border-b-2 border-ink-700 pb-2">
        <div>
          <h1 className="font-heading text-[22px] font-bold tracking-tight">
            Welcome back, {user.display_name || user.username}
          </h1>
          <p className="mt-0.5 text-sm text-ink-400">
            {user.role === "reviewer"
              ? "Recordings waiting on your review gate the training set."
              : user.role === "operator"
                ? "Record demonstrations; a reviewer approves them before training."
                : "Full access to collection, review, datasets and training."}
          </p>
        </div>
        <div className="flex gap-2">
          {(user.role === "operator" || user.role === "admin") && (
            <Link href="/teleop">
              <Button variant="primary">Start teleoperating</Button>
            </Link>
          )}
          <Link href="/review">
            <Button variant="ghost">Review queue</Button>
          </Link>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" aria-busy={loading}>
        {loading ? (
          Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-[74px]" />)
        ) : (
          <>
            <Stat
              label="Valid demonstrations"
              value={summary?.valid_demo_count ?? "—"}
              hint="approved and labelled success"
              tone="ok"
            />
            <Stat
              label="Demonstration success rate"
              value={summary ? percent(summary.success_rate, 1) : "—"}
              hint={`${summary?.by_label.success ?? 0} of ${
                (summary?.by_label.success ?? 0) + (summary?.by_label.failure ?? 0)
              } labelled`}
            />
            <Stat
              label="Approval rate"
              value={summary ? percent(summary.approval_rate, 1) : "—"}
              hint={`${summary?.by_status.recorded ?? 0} awaiting review`}
            />
            <Stat
              label="Teleop latency (p50)"
              value={summary ? `${summary.median_latency_ms.toFixed(0)} ms` : "—"}
              hint={summary ? `p95 ${summary.p95_latency_ms.toFixed(0)} ms` : undefined}
              tone={summary && summary.median_latency_ms > 90 ? "warn" : "ok"}
            />
          </>
        )}
      </div>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <Card title="Collection by task">
          {loading ? (
            <div className="space-y-2" aria-busy="true">
              <Skeleton className="h-6 w-full" />
              <Skeleton className="h-6 w-full" />
              <Skeleton className="h-6 w-full" />
            </div>
          ) : summary && Object.keys(summary.by_task).length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-xs uppercase tracking-wider text-ink-400">
                  <tr>
                    <th className="pb-2">Task</th>
                    <th className="pb-2 text-right">Recorded</th>
                    <th className="pb-2 text-right">Success</th>
                    <th className="pb-2 text-right">Approved</th>
                    <th className="pb-2 text-right">Rejected</th>
                    <th className="pb-2 pl-4">Progress</th>
                  </tr>
                </thead>
                <tbody className="tabular">
                  {Object.entries(summary.by_task).map(([task, counts]) => {
                    const label = tasks.find((t) => t.id === task)?.title ?? task;
                    const ratio = counts.total ? counts.approved / counts.total : 0;
                    return (
                      <tr key={task} className="border-t border-ink-700/50">
                        <td className="py-2">{label}</td>
                        <td className="py-2 text-right">{counts.total}</td>
                        <td className="py-2 text-right text-ok-400">{counts.success}</td>
                        <td className="py-2 text-right">{counts.approved}</td>
                        <td className="py-2 text-right text-bad-400">{counts.rejected}</td>
                        <td className="py-2 pl-4">
                          <div className="h-1.5 w-full overflow-hidden rounded-full bg-ink-700">
                            <div
                              className="h-full rounded-full bg-accent-500"
                              style={{ width: `${ratio * 100}%` }}
                            />
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty>No demonstrations recorded yet.</Empty>
          )}

          {summary && (
            <div className="mt-4 flex flex-wrap gap-4 border-t border-ink-700/50 pt-3 text-xs text-ink-400">
              <span>{summary.total_frames.toLocaleString()} frames</span>
              <span>{summary.total_hours.toFixed(2)} hours</span>
              <span>{bytes(summary.total_size_bytes)} on disk</span>
              <span>
                {summary.total_frames > 0
                  ? `${bytes(summary.total_size_bytes / summary.total_frames)}/frame`
                  : ""}
              </span>
            </div>
          )}
        </Card>

        <Card title="Recent recordings">
          {loading ? (
            <div className="space-y-2" aria-busy="true">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : recent.length === 0 ? (
            <Empty>Nothing recorded yet.</Empty>
          ) : (
            <ul className="space-y-2">
              {recent.map((demo) => (
                <li key={demo.id}>
                  <Link
                    href={`/review/${demo.id}`}
                    className="flex items-center gap-3 rounded-lg border border-ink-700/50 px-3 py-2 text-sm hover:border-accent-500/40 hover:bg-ink-850"
                  >
                    <Thumbnail src={thumbnailUrl(demo.id)} alt="" className="h-10 w-16 shrink-0" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate">{demo.task_id}</span>
                      <span className="text-xs text-ink-400">
                        {demo.operator_name} · {timeAgo(demo.created_at)} ·{" "}
                        {demo.duration_s.toFixed(1)}s
                      </span>
                    </span>
                    <StatusBadge demo={demo} />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {health && (
        <Card title="System">
          <div className="flex flex-wrap gap-x-6 gap-y-2 text-xs text-ink-400">
            <span>API v{health.version}</span>
            <span>control loop {health.control_hz} Hz</span>
            <span>
              sessions {health.active_sessions}/{health.max_sessions}
            </span>
            <span>tasks: {(health.tasks as string[]).join(", ")}</span>
            <span>face anonymisation {health.anonymize_faces ? "on" : "off"}</span>
          </div>
        </Card>
      )}
    </div>
  );
}
