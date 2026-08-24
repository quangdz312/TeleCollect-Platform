"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { RawEpisodeFilters, type RawFiltersValue } from "@/components/raw/RawEpisodeFilters";
import { RawEpisodeTable } from "@/components/raw/RawEpisodeTable";
import { RawSummaryCards } from "@/components/raw/RawSummaryCards";
import { Alert, Button, Card, Select } from "@/components/ui";
import { rawApi, type RawEpisode, type RawEpisodeFilters as ApiFilters, type RawEpisodeSummary } from "@/lib/raw";

const EMPTY_FILTERS: RawFiltersValue = {
  source: "",
  task: "",
  quality: "",
  outcome: "",
  reviewStatus: "",
  search: "",
};

export default function RawEpisodesPage() {
  const { user, loading: authLoading } = useAuth();
  const [filters, setFilters] = useState<RawFiltersValue>(EMPTY_FILTERS);
  const [searchDraft, setSearchDraft] = useState("");
  const [items, setItems] = useState<RawEpisode[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [totalPages, setTotalPages] = useState(0);
  const [summary, setSummary] = useState<RawEpisodeSummary>({
    total: 0, teleop: 0, scripted: 0, successes: 0, failures: 0,
    pending: 0, approved: 0, rejected: 0, archived: 0,
  });
  const [tasks, setTasks] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!user || user.role === "operator") return;
    setLoading(true);
    setError(null);
    const params: ApiFilters = { page, page_size: pageSize };
    if (filters.source) params.source = filters.source;
    if (filters.task.trim()) params.task = filters.task.trim();
    if (filters.quality) params.quality = filters.quality;
    if (filters.outcome) params.outcome = filters.outcome;
    if (filters.reviewStatus) params.review_status = filters.reviewStatus;
    if (filters.search.trim()) params.search = filters.search.trim();
    try {
      const result = await rawApi.episodes(params);
      setItems(result.items);
      setTotal(result.total);
      setTotalPages(result.total_pages);
      setSummary(result.summary);
      setTasks(result.available_tasks);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "Failed to load raw episodes");
    } finally {
      setLoading(false);
    }
  }, [filters, page, pageSize, user]);

  useEffect(() => {
    void load();
  }, [load]);

  if (authLoading || !user) return null;
  if (user.role === "operator") {
    return <Alert tone="info">Raw episode management is available to reviewers and administrators.</Alert>;
  }

  const updateFilters = (patch: Partial<RawFiltersValue>) => {
    setPage(1);
    setFilters((current) => ({ ...current, ...patch }));
  };

  return (
    <div className="space-y-5">
      <div>
        <h1 className="font-heading text-[22px] font-bold tracking-tight">Raw episodes</h1>
        <p className="mt-0.5 text-sm text-ink-400">
          Browse teleop and scripted captures through one normalized inventory. Raw artifacts remain unchanged.
        </p>
      </div>

      <RawSummaryCards summary={summary} />

      <Card title="Filters" subtitle="Filters run on the backend and apply across the complete raw inventory.">
        <RawEpisodeFilters
          value={filters}
          searchDraft={searchDraft}
          onSearchDraftChange={setSearchDraft}
          onChange={updateFilters}
          onApplySearch={() => updateFilters({ search: searchDraft })}
          onReset={() => {
            setSearchDraft("");
            setPage(1);
            setFilters(EMPTY_FILTERS);
          }}
          tasks={tasks}
        />
      </Card>

      {error && <Alert>{error}</Alert>}

      <Card
        title="Episode inventory"
        subtitle={`${total.toLocaleString()} matching episode${total === 1 ? "" : "s"}`}
        actions={
          <Select
            aria-label="Rows per page"
            value={pageSize}
            onChange={(event) => {
              setPage(1);
              setPageSize(Number(event.target.value));
            }}
          >
            <option value={10}>10 rows</option>
            <option value={20}>20 rows</option>
            <option value={50}>50 rows</option>
            <option value={100}>100 rows</option>
          </Select>
        }
      >
        <RawEpisodeTable items={items} loading={loading} />
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-ink-700 pt-4">
          <span className="text-xs text-ink-400">
            Page {totalPages === 0 ? 0 : page} of {totalPages}
          </span>
          <div className="flex gap-2">
            <Button variant="subtle" disabled={loading || page <= 1} onClick={() => setPage((value) => value - 1)}>
              Previous
            </Button>
            <Button variant="subtle" disabled={loading || page >= totalPages} onClick={() => setPage((value) => value + 1)}>
              Next
            </Button>
          </div>
        </div>
      </Card>
    </div>
  );
}
