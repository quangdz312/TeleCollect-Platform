"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/components/AuthProvider";
import { RawEpisodeFilters, type RawFiltersValue } from "@/components/raw/RawEpisodeFilters";
import { RawEpisodeTable } from "@/components/raw/RawEpisodeTable";
import { RawSummaryCards } from "@/components/raw/RawSummaryCards";
import { Alert, Button, Card, Select } from "@/components/ui";
import { rawApi, type RawEpisode, type RawEpisodeFilters as ApiFilters, type RawEpisodeSummary } from "@/lib/raw";
import { writeConvertSelection } from "@/lib/convert-selection";

const EMPTY_FILTERS: RawFiltersValue = {
  source: "",
  task: "",
  quality: "",
  outcome: "",
  reviewStatus: "",
  collectionBatch: "",
  search: "",
};

function filtersFromUrl(): RawFiltersValue {
  if (typeof window === "undefined") return EMPTY_FILTERS;
  const query = new URLSearchParams(window.location.search);
  const source = query.get("source");
  const quality = query.get("quality");
  const outcome = query.get("outcome");
  const reviewStatus = query.get("review_status");
  return {
    source: source === "teleop" || source === "scripted" ? source : "",
    task: query.get("task") ?? "",
    quality: quality === "clean" || quality === "good" || quality === "medium" || quality === "poor" ? quality : "",
    outcome: outcome === "success" || outcome === "failure" ? outcome : "",
    reviewStatus: reviewStatus === "pending" || reviewStatus === "approved" || reviewStatus === "rejected" || reviewStatus === "archived" ? reviewStatus : "",
    collectionBatch: query.get("collection_batch_id") ?? "",
    search: query.get("search") ?? "",
  };
}

function filtersToQuery(filters: RawFiltersValue) {
  const query = new URLSearchParams();
  if (filters.source) query.set("source", filters.source);
  if (filters.task) query.set("task", filters.task);
  if (filters.quality) query.set("quality", filters.quality);
  if (filters.outcome) query.set("outcome", filters.outcome);
  if (filters.reviewStatus) query.set("review_status", filters.reviewStatus);
  if (filters.collectionBatch) query.set("collection_batch_id", filters.collectionBatch);
  if (filters.search) query.set("search", filters.search);
  return query;
}

export default function RawEpisodesPage() {
  const router = useRouter();
  const { user, loading: authLoading } = useAuth();
  const [filters, setFilters] = useState<RawFiltersValue>(EMPTY_FILTERS);
  const [urlReady, setUrlReady] = useState(false);
  const [searchDraft, setSearchDraft] = useState("");
  const [items, setItems] = useState<RawEpisode[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [totalPages, setTotalPages] = useState(0);
  const [summary, setSummary] = useState<RawEpisodeSummary>({
    total: 0, teleop: 0, scripted: 0, successes: 0, failures: 0,
    pending: 0, approved: 0, rejected: 0, archived: 0,
    by_task: {}, by_quality: {}, by_batch: {}, by_day: {}, undated: 0,
  });
  const [tasks, setTasks] = useState<string[]>([]);
  const [batches, setBatches] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Map<string, RawEpisode>>(new Map());

  const load = useCallback(async () => {
    if (!urlReady || !user || user.role === "operator") return;
    setLoading(true);
    setError(null);
    const params: ApiFilters = { page, page_size: pageSize };
    if (filters.source) params.source = filters.source;
    if (filters.task.trim()) params.task = filters.task.trim();
    if (filters.quality) params.quality = filters.quality;
    if (filters.outcome) params.outcome = filters.outcome;
    if (filters.reviewStatus) params.review_status = filters.reviewStatus;
    if (filters.collectionBatch) params.collection_batch_id = filters.collectionBatch;
    if (filters.search.trim()) params.search = filters.search.trim();
    try {
      const result = await rawApi.episodes(params);
      setItems(result.items);
      setTotal(result.total);
      setTotalPages(result.total_pages);
      setSummary(result.summary);
      setTasks(result.available_tasks);
      setBatches(result.available_batches);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "Failed to load raw episodes");
    } finally {
      setLoading(false);
    }
  }, [filters, page, pageSize, urlReady, user]);

  useEffect(() => {
    const initial = filtersFromUrl();
    setFilters(initial);
    setSearchDraft(initial.search);
    setUrlReady(true);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (authLoading || !user) return null;
  if (user.role === "operator") {
    return <Alert tone="info">Raw episode management is available to reviewers and administrators.</Alert>;
  }

  const updateFilters = (patch: Partial<RawFiltersValue>) => {
    setPage(1);
    const next = { ...filters, ...patch };
    setFilters(next);
    const query = filtersToQuery(next);
    router.replace(query.size ? `/raw?${query}` : "/raw", { scroll: false });
  };

  const toggleEpisode = (episode: RawEpisode) => {
    setSelected((current) => {
      const next = new Map(current);
      if (next.has(episode.episode_id)) next.delete(episode.episode_id);
      else next.set(episode.episode_id, episode);
      return next;
    });
  };

  const togglePage = (episodes: RawEpisode[], shouldSelect: boolean) => {
    setSelected((current) => {
      const next = new Map(current);
      for (const episode of episodes) {
        if (shouldSelect) next.set(episode.episode_id, episode);
        else next.delete(episode.episode_id);
      }
      return next;
    });
  };

  const selectedEpisodes = Array.from(selected.values());
  const selectedFrames = selectedEpisodes.reduce((totalFrames, episode) => totalFrames + episode.length, 0);

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
            router.replace("/raw", { scroll: false });
          }}
          tasks={tasks}
          batches={batches}
        />
      </Card>

      {error && <Alert>{error}</Alert>}

      {selectedEpisodes.length > 0 && (
        <Card title="Conversion selection" subtitle="Your selection is kept while you move between pages in this table.">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="text-sm text-ink-300">
              <strong className="text-ink-100">{selectedEpisodes.length}</strong> approved episodes · {selectedFrames.toLocaleString()} frames
            </div>
            <div className="flex gap-2">
              <Button variant="subtle" onClick={() => setSelected(new Map())}>Clear selection</Button>
              <Button
                variant="primary"
                onClick={() => {
                  writeConvertSelection(selectedEpisodes);
                  router.push("/convert?selection=raw");
                }}
              >
                Convert selected
              </Button>
            </div>
          </div>
        </Card>
      )}

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
        <RawEpisodeTable
          items={items}
          loading={loading}
          selectedIds={new Set(selected.keys())}
          onToggle={toggleEpisode}
          onTogglePage={togglePage}
        />
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
