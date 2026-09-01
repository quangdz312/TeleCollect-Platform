"use client";

import { useCallback, useEffect, useState, type ReactNode, type SVGProps } from "react";
import Link from "next/link";
import { useAuth } from "@/components/AuthProvider";
import { PageHeader } from "@/components/PageHeader";
import {
  Badge,
  Button,
  Card,
  Empty,
  Field,
  Input,
  Select,
  Stat,
} from "@/components/ui";
import { api, type DatasetExport, type DatasetPage } from "@/lib/api";
import { labeling, type TaskOption } from "@/lib/labeling";
import { bytes, timeAgo } from "@/lib/format";
import { Icon } from "@/components/icons";

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

  const readyExports = exports.filter((item) => item.status === "ready");
  const totalEpisodes = readyExports.reduce((sum, item) => sum + item.num_episodes, 0);
  const totalSize = readyExports.reduce((sum, item) => sum + item.size_bytes, 0);

  return (
    <div className="space-y-4">
      <PageHeader eyebrow="Robot learning assets" title="Datasets" description="Browse and inspect immutable datasets after conversion." />

      <div className="grid gap-3 sm:grid-cols-3">
        <Stat label="Dataset exports" value={datasetPage?.total ?? exports.length} hint={`${readyExports.length} ready on this page`} sparkline={false} icon={<Icon name="datasets" className="h-7 w-7" />} />
        <Stat label="Stored payload" value={bytes(totalSize)} hint="Ready dataset storage" sparkline={false} icon={<StorageIcon className="h-7 w-7" />} />
        <Stat label="Episodes" value={totalEpisodes.toLocaleString()} hint="Ready for robot learning" tone="ok" sparkline={false} icon={<EpisodeIcon className="h-7 w-7" />} />
      </div>

      <Card title="Exports" subtitle={datasetPage ? `${datasetPage.total} datasets` : undefined} actions={datasetPage ? <span className="text-xs text-ink-400">Showing {exports.length} of {datasetPage.total} datasets</span> : undefined}>
        <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Search"><div className="relative"><SearchIcon className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-400" /><Input className="pl-9" value={search} onChange={(event) => { setSearch(event.target.value); setListPage(1); }} placeholder="Search dataset name" /></div></Field>
          <Field label="Status"><Select value={statusFilter} onChange={(event) => { setStatusFilter(event.target.value); setListPage(1); }}><option value="">All statuses</option><option value="building">Building</option><option value="ready">Ready</option><option value="failed">Failed</option></Select></Field>
          <Field label="Source"><Select value={sourceFilter} onChange={(event) => { setSourceFilter(event.target.value); setListPage(1); }}><option value="">All sources</option><option value="teleop">Teleop</option><option value="scripted">Scripted</option><option value="both">Mixed</option><option value="unknown">Unknown (legacy)</option></Select></Field>
          <Field label="Task"><Select value={listTaskFilter} onChange={(event) => { setListTaskFilter(event.target.value); setListPage(1); }}><option value="">All tasks</option>{tasks.map((task) => <option key={task.task} value={task.task}>{task.tool_label ?? task.task}</option>)}</Select></Field>
        </div>
        {exports.length === 0 ? (
          <Empty>No datasets exported yet.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1000px] text-sm">
              <thead className="text-left text-xs uppercase tracking-wider text-ink-400">
                <tr>
                  <th className="pb-2">Name</th>
                  <th className="pb-2">Format</th>
                  <th className="pb-2">Task</th>
                  <th className="pb-2">Status</th>
                  <th className="pb-2 text-right">Episodes</th>
                  <th className="pb-2 text-right">Frames</th>
                  <th className="pb-2 pr-5 text-right">Size</th>
                  <th className="min-w-20 pb-2">Created</th>
                  <th className="pb-2 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="tabular">
                {exports.map((item) => (
                  <tr key={item.id} className="border-t border-ink-700/50">
                    <td className="py-2.5 pr-4 font-medium"><Link href={`/datasets/${item.id}`} className="text-accent-500 hover:underline">{item.name}</Link><div className="mt-0.5 text-[11px] font-normal capitalize text-ink-400">{item.data_source}</div></td>
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
                    <td className="whitespace-nowrap py-2 pr-5 text-right">{item.status === "ready" ? bytes(item.size_bytes) : "—"}</td>
                    <td className="py-2 text-xs text-ink-400">{timeAgo(item.created_at)}</td>
                    <td className="py-2 text-right"><div className="flex items-center justify-end gap-1.5">
                        <Link href={`/datasets/${item.id}`}><Button variant="subtle">Open</Button></Link>
                        {item.status === "ready" && <a href={api.datasetDownloadUrl(item.id)} aria-label={`Download ${item.name}`} title="Download dataset" className="inline-grid h-9 w-9 place-items-center rounded-lg border border-ink-700 bg-ink-850 text-accent-500 transition-colors hover:bg-ink-800"><DownloadIcon /></a>}
                        {item.status === "failed" && <Button variant="subtle" onClick={async () => { await api.retryExport(item.id); await load(); }}>Retry</Button>}
                        {user.role === "admin" && <CompactAction
                          label="Delete dataset"
                          danger
                          align="right"
                          onClick={async () => {
                            if (!confirm(`Delete export ${item.name}?`)) return;
                            await api.deleteExport(item.id);
                            await load();
                          }}
                        >
                          <DeleteIcon />
                        </CompactAction>}
                      </div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {datasetPage && datasetPage.total_pages > 1 && <div className="mt-4 flex items-center justify-between"><Button variant="subtle" disabled={listPage <= 1} onClick={() => setListPage((value) => value - 1)}>Previous</Button><span className="text-xs text-ink-400">Page {listPage} / {datasetPage.total_pages}</span><Button variant="subtle" disabled={listPage >= datasetPage.total_pages} onClick={() => setListPage((value) => value + 1)}>Next</Button></div>}
      </Card>

    </div>
  );
}

function CompactAction({ label, danger = false, align = "center", onClick, children }: { label: string; danger?: boolean; align?: "center" | "right"; onClick: () => void; children: ReactNode }) {
  return (
    <span className="group relative inline-flex">
      <button
        type="button"
        aria-label={label}
        onClick={onClick}
        className={`inline-grid h-9 w-9 place-items-center rounded-lg border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/50 ${danger ? "border-bad-600/25 text-bad-600 hover:border-bad-600/50 hover:bg-bad-600/10" : "border-ink-700 text-ink-400 hover:bg-ink-850 hover:text-accent-500"}`}
      >
        {children}
      </button>
      <span role="tooltip" className={`pointer-events-none absolute bottom-full z-20 mb-1.5 whitespace-nowrap rounded-md bg-ink-950 px-2 py-1 text-[11px] font-medium text-ink-100 opacity-0 shadow-lg transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 ${align === "right" ? "right-0" : "left-1/2 -translate-x-1/2"}`}>{label}</span>
    </span>
  );
}

function SearchIcon(props: SVGProps<SVGSVGElement>) {
  return <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" aria-hidden="true" {...props}><circle cx="8.5" cy="8.5" r="5"/><path d="m12.3 12.3 4 4"/></svg>;
}

function StorageIcon(props: SVGProps<SVGSVGElement>) {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}><path d="M5 4h14l2 7v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-7l2-7Z"/><path d="M3 11h18M16 16h.01M12 16h.01"/></svg>;
}

function EpisodeIcon(props: SVGProps<SVGSVGElement>) {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" aria-hidden="true" {...props}><circle cx="5" cy="6" r="1.5"/><circle cx="5" cy="12" r="1.5"/><circle cx="5" cy="18" r="1.5"/><path d="M9 6h10M9 12h10M9 18h10"/></svg>;
}

function DownloadIcon() {
  return <svg className="h-4 w-4" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M10 3v9m-3-3 3 3 3-3M4 14v3h12v-3"/></svg>;
}

function DeleteIcon() {
  return <svg className="h-4 w-4" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3.5 5.5h13M8 5.5V3.5h4v2M5.5 5.5l.8 11h7.4l.8-11M8.5 9v4M11.5 9v4"/></svg>;
}
