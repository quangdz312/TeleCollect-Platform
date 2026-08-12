"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { ScriptedReviewRows } from "@/components/ScriptedReviewPanel";
import { AutoLabelBadge } from "@/components/AutoLabelBadge";
import { StatusBadge } from "@/components/StatusBadge";
import { Badge, Button, Card, Empty, Select } from "@/components/ui";
import {
  api,
  type Demo,
  type DemoStatus,
  type LabelValue,
  type Summary,
  type Task,
} from "@/lib/api";
import { timeAgo } from "@/lib/format";

const PAGE_SIZE = 25;

export default function ReviewQueuePage() {
  const { user } = useAuth();
  const [demos, setDemos] = useState<Demo[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [taskFilter, setTaskFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<DemoStatus | "">("");
  const [labelFilter, setLabelFilter] = useState<LabelValue | "">("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const page = await api.demos({
        task_id: taskFilter || undefined,
        status: (statusFilter || undefined) as DemoStatus | undefined,
        label: (labelFilter || undefined) as LabelValue | undefined,
        limit: PAGE_SIZE,
        offset,
      });
      setDemos(page.items);
      setTotal(page.total);
    } finally {
      setLoading(false);
    }
  }, [taskFilter, statusFilter, labelFilter, offset]);

  useEffect(() => {
    if (!user) return;
    void load();
  }, [user, load]);

  useEffect(() => {
    if (!user) return;
    void api.tasks().then(setTasks);
    void api.summary().then(setSummary);
  }, [user]);

  if (!user) return null;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold">Review queue</h1>
          <p className="mt-0.5 text-sm text-ink-400">
            {user.role === "operator"
              ? "Your recordings and their review status."
              : "Trim, label and approve recordings. Only approved successes reach a training export."}
          </p>
        </div>
        {summary && (
          <div className="flex gap-2 text-xs">
            <Badge tone="neutral">{summary.by_status.recorded ?? 0} pending</Badge>
            <Badge tone="ok">{summary.by_status.approved ?? 0} approved</Badge>
            <Badge tone="bad">{summary.by_status.rejected ?? 0} rejected</Badge>
          </div>
        )}
      </div>

      <Card>
        <div className="flex flex-wrap gap-3">
          <Select
            className="w-48"
            value={statusFilter}
            onChange={(e) => {
              setOffset(0);
              setStatusFilter(e.target.value as DemoStatus | "");
            }}
          >
            <option value="">All statuses</option>
            <option value="recorded">Needs review</option>
            <option value="labeled">Labeled · needs review</option>
            <option value="approved">Approved</option>
            <option value="rejected">Rejected</option>
          </Select>
          <Select
            className="w-48"
            value={taskFilter}
            onChange={(e) => {
              setOffset(0);
              setTaskFilter(e.target.value);
            }}
          >
            <option value="">All tasks</option>
            {tasks.map((task) => (
              <option key={task.id} value={task.id}>
                {task.title}
              </option>
            ))}
          </Select>
          <Select
            className="w-48"
            value={labelFilter}
            onChange={(e) => {
              setOffset(0);
              setLabelFilter(e.target.value as LabelValue | "");
            }}
          >
            <option value="">Any label</option>
            <option value="success">Success</option>
            <option value="failure">Failure</option>
          </Select>
          <div className="ml-auto flex items-center gap-2 text-xs text-ink-400">
            <span>
              {total === 0 ? 0 : offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}
            </span>
            <Button
              variant="ghost"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            >
              Prev
            </Button>
            <Button
              variant="ghost"
              disabled={offset + PAGE_SIZE >= total}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              Next
            </Button>
          </div>
        </div>
      </Card>

      <Card>
        {loading ? (
          <Empty>Loading…</Empty>
        ) : demos.length === 0 ? (
          <Empty>Nothing matches these filters.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wider text-ink-400">
                <tr>
                  <th className="pb-2">Task / Source</th>
                  <th className="pb-2">Operator</th>
                  <th className="pb-2">Recorded</th>
                  <th className="pb-2 text-right">Length</th>
                  <th className="pb-2 text-right">Frames</th>
                  <th className="pb-2 whitespace-nowrap pl-4 text-right">Latency p50</th>
                  <th className="pb-2 whitespace-nowrap pl-5">Auto label</th>
                  <th className="pb-2 whitespace-nowrap pl-5">Status</th>
                  <th className="pb-2" />
                </tr>
              </thead>
              <tbody className="tabular">
                {demos.map((demo) => (
                  <tr key={demo.id} className="border-t border-ink-700/50 hover:bg-ink-850/50">
                    <td className="py-2">
                      <div className="flex items-center gap-2">
                        <span>{demo.task_id}</span>
                        <Badge tone="info">Teleop</Badge>
                      </div>
                    </td>
                    <td className="py-2 text-ink-300">{demo.operator_name}</td>
                    <td className="py-2 text-ink-400">{timeAgo(demo.created_at)}</td>
                    <td className="py-2 text-right">{demo.duration_s.toFixed(1)}s</td>
                    <td className="py-2 text-right">
                      {demo.trim_end !== null && demo.trim_end - demo.trim_start !== demo.num_frames
                        ? `${demo.trim_end - demo.trim_start}/${demo.num_frames}`
                        : demo.num_frames}
                    </td>
                    <td className="py-2 pl-4 text-right">{demo.latency_p50_ms.toFixed(0)} ms</td>
                    <td className="py-2 pl-5">
                      <AutoLabelBadge label={demo.auto_label ?? "review"} reason={demo.auto_label_reason} />
                    </td>
                    <td className="py-2 pl-5">
                      <StatusBadge demo={demo} />
                    </td>
                    <td className="py-2 text-right">
                      <Link href={`/review/${demo.id}`}>
                        <Button variant="subtle">Open</Button>
                      </Link>
                    </td>
                  </tr>
                ))}
                {(user.role === "reviewer" || user.role === "admin") && <ScriptedReviewRows />}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
