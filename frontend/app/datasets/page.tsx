"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/components/AuthProvider";
import {
  Badge,
  Button,
  Card,
  Empty,
  Field,
  Input,
  Select,
} from "@/components/ui";
import { api, type DatasetExport, type DatasetPage } from "@/lib/api";
import { labeling, type TaskOption } from "@/lib/labeling";
import { bytes, timeAgo } from "@/lib/format";

export default function DatasetsPage() {
  const { user } = useAuth();
  const [exports, setExports] = useState<DatasetExport[]>([]);
  const [datasetPage, setDatasetPage] = useState<DatasetPage | null>(null);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [listTaskFilter, setListTaskFilter] = useState("");
  const [listPage, setListPage] = useState(1);
  const [tasks, setTasks] = useState<TaskOption[]>([]);

  const load = useCallback(async () => {
    const [e, scripted] = await Promise.all([
      api.datasetPage({ search, status: statusFilter, source: sourceFilter, task: listTaskFilter, page: listPage, page_size: 20 }),
      labeling.config().catch(() => null),
    ]);
    setDatasetPage(e);
    setExports(e.items);
    setTasks(scripted?.tasks ?? []);
  }, [listPage, listTaskFilter, search, sourceFilter, statusFilter]);

  useEffect(() => {
    if (!user) return;
    void load();
  }, [user, load]);

  useEffect(() => {
    if (!user || !exports.some((item) => item.status === "building")) return;
    const timer = window.setInterval(() => { void load(); }, 1500);
    return () => window.clearInterval(timer);
  }, [exports, load, user]);

  if (!user) return null;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="font-heading text-[22px] font-bold tracking-tight">Datasets</h1>
        <p className="mt-0.5 text-sm text-ink-400">
          Browse and inspect immutable datasets after conversion.
        </p>
      </div>

      <Card title="Exports" subtitle={datasetPage ? `${datasetPage.total} datasets` : undefined}>
        <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Search"><Input value={search} onChange={(event) => { setSearch(event.target.value); setListPage(1); }} placeholder="Dataset name" /></Field>
          <Field label="Status"><Select value={statusFilter} onChange={(event) => { setStatusFilter(event.target.value); setListPage(1); }}><option value="">All statuses</option><option value="building">Building</option><option value="ready">Ready</option><option value="failed">Failed</option></Select></Field>
          <Field label="Source"><Select value={sourceFilter} onChange={(event) => { setSourceFilter(event.target.value); setListPage(1); }}><option value="">All sources</option><option value="teleop">Teleop</option><option value="scripted">Scripted</option><option value="both">Mixed</option><option value="unknown">Unknown (legacy)</option></Select></Field>
          <Field label="Task"><Select value={listTaskFilter} onChange={(event) => { setListTaskFilter(event.target.value); setListPage(1); }}><option value="">All tasks</option>{tasks.map((task) => <option key={task.task} value={task.task}>{task.tool_label ?? task.task}</option>)}</Select></Field>
        </div>
        {exports.length === 0 ? (
          <Empty>No datasets exported yet.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wider text-ink-400">
                <tr>
                  <th className="pb-2">Name</th>
                  <th className="pb-2">Format</th>
                  <th className="pb-2">Tasks</th>
                  <th className="pb-2">Status</th>
                  <th className="pb-2 text-right">Episodes</th>
                  <th className="pb-2 text-right">Frames</th>
                  <th className="pb-2 text-right">Size</th>
                  <th className="pb-2">DVC hash</th>
                  <th className="pb-2">Created</th>
                  <th className="pb-2" />
                </tr>
              </thead>
              <tbody className="tabular">
                {exports.map((item) => (
                  <tr key={item.id} className="border-t border-ink-700/50">
                    <td className="py-2 font-medium"><Link href={`/datasets/${item.id}`} className="text-accent-500 hover:underline">{item.name}</Link><div className="text-[11px] font-normal text-ink-400">{item.data_source}</div></td>
                    <td className="py-2">
                      <Badge tone="info">{item.format}</Badge>
                    </td>
                    <td className="py-2 text-xs text-ink-400">{item.tasks.join(", ")}</td>
                    <td className="py-2">
                      <Badge tone={item.status === "ready" ? "ok" : item.status === "failed" ? "bad" : "warn"}>
                        {item.status}
                      </Badge>
                    </td>
                    <td className="py-2 text-right">{item.status === "ready" ? item.num_episodes : "—"}</td>
                    <td className="py-2 text-right">{item.status === "ready" ? item.num_frames.toLocaleString() : "—"}</td>
                    <td className="py-2 text-right">{item.status === "ready" ? bytes(item.size_bytes) : "—"}</td>
                    <td className="py-2 font-mono text-xs text-ink-400">
                      {item.dvc_hash ? item.dvc_hash.slice(0, 16) : "—"}
                    </td>
                    <td className="py-2 text-xs text-ink-400">{timeAgo(item.created_at)}</td>
                    <td className="py-2 text-right"><div className="flex justify-end gap-1">
                        <Link href={`/datasets/${item.id}`}><Button variant="subtle">Open</Button></Link>
                        {item.status === "ready" && <a href={api.datasetDownloadUrl(item.id)}><Button variant="subtle">Download</Button></a>}
                        {item.status === "failed" && <Button variant="subtle" onClick={async () => { await api.retryExport(item.id); await load(); }}>Retry</Button>}
                        {user.role === "admin" && <Button
                          variant="ghost"
                          onClick={async () => {
                            if (!confirm(`Delete export ${item.name}?`)) return;
                            await api.deleteExport(item.id);
                            await load();
                          }}
                        >
                          Delete
                        </Button>}
                      </div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {datasetPage && datasetPage.total_pages > 1 && <div className="mt-4 flex items-center justify-between"><Button variant="subtle" disabled={listPage <= 1} onClick={() => setListPage((value) => value - 1)}>Previous</Button><span className="text-xs text-ink-400">Page {listPage} / {datasetPage.total_pages}</span><Button variant="subtle" disabled={listPage >= datasetPage.total_pages} onClick={() => setListPage((value) => value + 1)}>Next</Button></div>}
      </Card>

      <Card title="Version control">
        <p className="text-sm text-tech-text">
          Datasets are tracked by DVC rather than committed to git: the payload lives in the DVC
          cache and remote, while a small hash pointer goes into git next to the code. The hash
          recorded on each export is also stored on every training run, so any policy can be
          traced back to the exact bytes it learned from.
        </p>
        <pre className="mt-3 overflow-x-auto rounded-lg border border-tech-border bg-tech-bg p-3 font-mono text-xs text-tech-text">
{`dvc push                      # upload the dataset payload to the remote
git add -A && git commit -m "dataset v1"
dvc checkout                  # restore the exact dataset for this commit`}
        </pre>
      </Card>
    </div>
  );
}
