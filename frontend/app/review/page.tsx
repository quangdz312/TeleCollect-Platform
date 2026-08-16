"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
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
import { labeling, type WorkspaceSummary } from "@/lib/labeling";
import { timeAgo } from "@/lib/format";

const PAGE_SIZE = 25;
const SMOKE_TRAIN_TARGET = 20;

function ReviewQueueContent() {
  const { user } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();
  const [demos, setDemos] = useState<Demo[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [taskFilter, setTaskFilter] = useState(() => searchParams.get("task") ?? "");
  const [statusFilter, setStatusFilter] = useState<DemoStatus | "">(() => {
    const value = searchParams.get("status");
    if (value === "all") return "";
    if (value === "recorded" || value === "labeled" || value === "approved" || value === "rejected") {
      return value;
    }
    return "recorded";
  });
  const [labelFilter, setLabelFilter] = useState<LabelValue | "">(() => {
    const value = searchParams.get("label");
    return value === "success" || value === "failure" ? value : "";
  });
  const [loading, setLoading] = useState(true);
  const [scriptedTotal, setScriptedTotal] = useState(0);
  const [scriptedSummary, setScriptedSummary] = useState<WorkspaceSummary | null>(null);
  const [scriptedTasks, setScriptedTasks] = useState<Array<{ id: string; title: string }>>([]);
  const [applyingGate, setApplyingGate] = useState(false);
  const [gateMessage, setGateMessage] = useState("");

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
    if (user.role === "reviewer" || user.role === "admin") {
      void labeling.config().then((config) => {
        setScriptedSummary(config.workspace);
        setScriptedTasks(config.tasks.map((task) => ({
          id: task.task,
          title: task.tool_label ?? task.task,
        })));
      });
    }
  }, [user]);

  if (!user) return null;
  const canReviewScripted = user.role === "reviewer" || user.role === "admin";
  const setQueueFilters = (next: {
    status?: DemoStatus | "";
    task?: string;
    label?: LabelValue | "";
  }) => {
    const status = next.status ?? statusFilter;
    const task = next.task ?? taskFilter;
    const label = next.label ?? labelFilter;
    const query = new URLSearchParams();
    query.set("status", status || "all");
    if (task) query.set("task", task);
    if (label) query.set("label", label);
    router.replace(`/review?${query.toString()}`, { scroll: false });
  };
  const returnToParams = new URLSearchParams();
  returnToParams.set("status", statusFilter || "all");
  if (taskFilter) returnToParams.set("task", taskFilter);
  if (labelFilter) returnToParams.set("label", labelFilter);
  const returnTo = `/review?${returnToParams.toString()}`;
  const taskOptions = [
    ...tasks.map((task) => ({ id: task.id, title: task.title })),
    ...scriptedTasks.filter((scripted) => !tasks.some((task) => task.id === scripted.id)),
  ];
  const pendingCount = (summary?.by_status.recorded ?? 0)
    + (summary?.by_status.labeled ?? 0)
    + (scriptedSummary?.pending ?? 0);
  const approvedCount = (summary?.by_status.approved ?? 0) + (scriptedSummary?.approved ?? 0);
  const rejectedCount = (summary?.by_status.rejected ?? 0) + (scriptedSummary?.rejected ?? 0);

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
            <Badge tone="neutral">{pendingCount} pending</Badge>
            <Badge tone="ok">{approvedCount} approved</Badge>
            <Badge tone="bad">{rejectedCount} rejected</Badge>
            {canReviewScripted && <Badge tone="info">{scriptedSummary?.episodes ?? scriptedTotal} scripted</Badge>}
          </div>
        )}
      </div>

      {canReviewScripted && scriptedSummary && (
        <Card
          title="Automatic review gate"
          subtitle="Strict clean/good passes are approved, hard failures are rejected, and uncertain cases remain for people. Human decisions are never overwritten."
          actions={
            <Button
              variant="subtle"
              disabled={applyingGate}
              onClick={() => {
                setApplyingGate(true);
                setGateMessage("");
                void labeling.applyAutoGate().then(({ result, workspace }) => {
                  setScriptedSummary(workspace);
                  const disabled = result.disabled_tasks.length ? ` Auto-approve disabled for: ${result.disabled_tasks.join(", ")}.` : "";
                  setGateMessage(`Auto-approved ${result.approved}, auto-rejected ${result.rejected}, audit ${result.audit}, still needs review ${result.review}.${disabled}`);
                }).catch((problem) => {
                  setGateMessage(problem instanceof Error ? problem.message : String(problem));
                }).finally(() => setApplyingGate(false));
              }}
            >
              {applyingGate ? "Applying…" : "Apply to pending"}
            </Button>
          }
        >
          <div className="flex flex-wrap gap-2 text-xs">
            <Badge tone="ok">{scriptedSummary.auto_approved ?? 0} auto approved</Badge>
            <Badge tone="bad">{scriptedSummary.auto_rejected ?? 0} auto rejected</Badge>
            <Badge tone="info">{scriptedSummary.human_reviewed ?? scriptedSummary.reviewed} human reviewed</Badge>
            <Badge tone={scriptedSummary.audit_failed ? "bad" : "warn"}>
              {scriptedSummary.audit_pending ?? 0} audit pending · {scriptedSummary.audit_failed ?? 0}/{scriptedSummary.audit_reviewed ?? 0} rejected
            </Badge>
            {gateMessage && <span className="self-center text-ink-300">{gateMessage}</span>}
          </div>
        </Card>
      )}

      {canReviewScripted && scriptedSummary && (
        <Card
          title="Scripted review progress by task"
          subtitle={`Approved successes are eligible for BC export · smoke target ${SMOKE_TRAIN_TARGET} per task`}
        >
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wider text-ink-400">
                <tr>
                  <th className="pb-2">Task</th>
                  <th className="pb-2 text-right">Total</th>
                  <th className="pb-2 text-right">Pending</th>
                  <th className="pb-2 text-right">Approved</th>
                  <th className="pb-2 text-right">Rejected</th>
                  <th className="pb-2 text-right">Train eligible</th>
                  <th className="pb-2 pl-5">Smoke target</th>
                </tr>
              </thead>
              <tbody className="tabular">
                {scriptedTasks.map((task) => {
                  const stats = scriptedSummary.per_task[task.id] ?? {
                    total: 0,
                    reviewed: 0,
                    pending: 0,
                    approved: 0,
                    approved_successes: 0,
                    rejected: 0,
                  };
                  const ready = stats.approved_successes >= SMOKE_TRAIN_TARGET;
                  return (
                    <tr key={task.id} className="border-t border-ink-700/50">
                      <td className="py-2 font-medium">{task.title}</td>
                      <td className="py-2 text-right">{stats.total}</td>
                      <td className="py-2 text-right text-warn-400">{stats.pending}</td>
                      <td className="py-2 text-right text-ok-400">{stats.approved}</td>
                      <td className="py-2 text-right text-bad-400">{stats.rejected}</td>
                      <td className="py-2 text-right font-semibold text-ok-400">
                        {stats.approved_successes}
                      </td>
                      <td className="py-2 pl-5">
                        <Badge tone={ready ? "ok" : "warn"}>
                          {ready
                            ? "ready"
                            : `${SMOKE_TRAIN_TARGET - stats.approved_successes} needed`}
                        </Badge>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <Card>
        <div className="flex flex-wrap gap-3">
          <Select
            className="w-48"
            value={statusFilter}
            onChange={(e) => {
              setOffset(0);
              const status = e.target.value as DemoStatus | "";
              setStatusFilter(status);
              setQueueFilters({ status });
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
              const task = e.target.value;
              setTaskFilter(task);
              setQueueFilters({ task });
            }}
          >
            <option value="">All tasks</option>
            {taskOptions.map((task) => (
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
              const label = e.target.value as LabelValue | "";
              setLabelFilter(label);
              setQueueFilters({ label });
            }}
          >
            <option value="">Any label</option>
            <option value="success">Success</option>
            <option value="failure">Failure</option>
          </Select>
          <div className="ml-auto flex items-center gap-2 text-xs text-ink-400">
            <span>{total === 0
              ? `${scriptedTotal} scripted matches`
              : `${offset + 1}–${Math.min(offset + PAGE_SIZE, total)} of ${total} teleop`}</span>
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
        ) : demos.length === 0 && !canReviewScripted ? (
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
                      <Link href={`/review/${demo.id}?returnTo=${encodeURIComponent(returnTo)}`}>
                        <Button variant="subtle">Open</Button>
                      </Link>
                    </td>
                  </tr>
                ))}
                {canReviewScripted && (
                  <ScriptedReviewRows
                    task={taskFilter || undefined}
                    status={statusFilter || undefined}
                    label={labelFilter || undefined}
                    returnTo={returnTo}
                    onCount={setScriptedTotal}
                  />
                )}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

export default function ReviewQueuePage() {
  return (
    <Suspense fallback={<div className="py-12 text-center text-sm text-ink-400">Loading review queue…</div>}>
      <ReviewQueueContent />
    </Suspense>
  );
}
