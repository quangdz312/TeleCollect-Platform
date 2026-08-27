"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/components/AuthProvider";
import { BatchGallery } from "@/components/raw/BatchGallery";
import { RawEpisodeFilters, type RawFiltersValue } from "@/components/raw/RawEpisodeFilters";
import { RawEpisodeTable } from "@/components/raw/RawEpisodeTable";
import { RawSummaryCards } from "@/components/raw/RawSummaryCards";
import { ExportDialog } from "@/components/raw/ExportDialog";
import { BatchImportDialog } from "@/components/raw/BatchImportDialog";
import { BatchDiversity } from "@/components/raw/BatchDiversity";
import { DIVERSITY_VIEW, ReviewStatusTabs } from "@/components/raw/ReviewStatusTabs";
import { Alert, Button, Card, Select } from "@/components/ui";
import { rawApi, type CollectionBatch, type RawEpisode, type RawEpisodeFilters as ApiFilters, type RawEpisodeSummary } from "@/lib/raw";

/** Sentinel for "browse everything", distinct from "no batch chosen yet". */
const ALL_BATCHES = "__all__";

/** Backend caps page_size at 100; select-all walks pages at that size. */
const PAGE_FETCH_SIZE = 100;

/** Ceiling on the walk so a mis-set filter cannot fire hundreds of requests. */
const MAX_SELECT_ALL_PAGES = 20;

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
  const [batchList, setBatchList] = useState<CollectionBatch[]>([]);
  const [batchesLoading, setBatchesLoading] = useState(true);
  const [batchError, setBatchError] = useState<string | null>(null);
  const [selectingAll, setSelectingAll] = useState(false);
  const [statusCounts, setStatusCounts] = useState<RawEpisodeSummary | null>(null);
  const [exporting, setExporting] = useState(false);
  const [view, setView] = useState<"episodes" | "diversity">("episodes");
  const [importing, setImporting] = useState(false);

  const loadBatches = useCallback(async () => {
    if (!user || user.role === "operator") return;
    setBatchesLoading(true);
    setBatchError(null);
    try {
      setBatchList(await rawApi.batches());
    } catch (problem) {
      setBatchError(problem instanceof Error ? problem.message : "Could not load collection batches");
    } finally {
      setBatchesLoading(false);
    }
  }, [user]);

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
    // `__all__` means "browse everything", not a real batch id; sending it to
    // the backend would filter down to nothing.
    if (filters.collectionBatch && filters.collectionBatch !== ALL_BATCHES) {
      params.collection_batch_id = filters.collectionBatch;
    }
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

  useEffect(() => {
    void loadBatches();
  }, [loadBatches]);

  /**
   * Counts for the status tabs come from a second request that leaves
   * `review_status` out. Reusing the table's own summary made "All" drop to the
   * size of the active tab, so the numbers moved every time one was clicked.
   */
  useEffect(() => {
    if (!urlReady || !user || user.role === "operator") return;
    if (!filters.collectionBatch) return;
    const params: ApiFilters = { page: 1, page_size: 1 };
    if (filters.source) params.source = filters.source;
    if (filters.task.trim()) params.task = filters.task.trim();
    if (filters.quality) params.quality = filters.quality;
    if (filters.outcome) params.outcome = filters.outcome;
    if (filters.collectionBatch !== ALL_BATCHES) {
      params.collection_batch_id = filters.collectionBatch;
    }
    if (filters.search.trim()) params.search = filters.search.trim();
    let cancelled = false;
    void rawApi
      .episodes(params)
      .then((result) => {
        if (!cancelled) setStatusCounts(result.summary);
      })
      .catch(() => {
        if (!cancelled) setStatusCounts(null);
      });
    return () => {
      cancelled = true;
    };
  }, [
    filters.collectionBatch,
    filters.source,
    filters.task,
    filters.quality,
    filters.outcome,
    filters.search,
    urlReady,
    user,
  ]);

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

  const exportWholeBatch = async (batchId: string) => {
    setBatchError(null);
    try {
      const collected = new Map<string, RawEpisode>();
      let pageNumber = 1;
      let totalPagesToRead = 1;
      while (pageNumber <= totalPagesToRead && pageNumber <= MAX_SELECT_ALL_PAGES) {
        const result = await rawApi.episodes({
          collection_batch_id: batchId,
          review_status: "approved",
          page: pageNumber,
          page_size: PAGE_FETCH_SIZE,
        });
        for (const episode of result.items) collected.set(episode.episode_id, episode);
        totalPagesToRead = result.total_pages;
        pageNumber += 1;
      }
      setSelected(collected);
      setExporting(true);
    } catch (problem) {
      setBatchError(
        problem instanceof Error ? problem.message : "Could not load the batch for export",
      );
    }
  };

  const toggleEpisode = (episode: RawEpisode) => {
    setSelected((current) => {
      const next = new Map(current);
      if (next.has(episode.episode_id)) next.delete(episode.episode_id);
      else next.set(episode.episode_id, episode);
      return next;
    });
  };

  /**
   * Select every episode matching the current filters, not just this page.
   * Exporting a whole batch is the common case, and ticking 20 rows at a time
   * across 22 pages is not a workflow.
   */
  const selectAllMatching = async () => {
    setSelectingAll(true);
    setError(null);
    const base: ApiFilters = { page_size: PAGE_FETCH_SIZE };
    if (filters.source) base.source = filters.source;
    if (filters.task.trim()) base.task = filters.task.trim();
    if (filters.quality) base.quality = filters.quality;
    if (filters.outcome) base.outcome = filters.outcome;
    if (filters.reviewStatus) base.review_status = filters.reviewStatus;
    if (filters.collectionBatch && filters.collectionBatch !== ALL_BATCHES) {
      base.collection_batch_id = filters.collectionBatch;
    }
    if (filters.search.trim()) base.search = filters.search.trim();
    try {
      // The backend caps page_size at 100, so a batch of several hundred needs
      // several calls. Walk the pages rather than silently selecting a prefix.
      const collected = new Map<string, RawEpisode>();
      let pageNumber = 1;
      let totalPagesToRead = 1;
      while (pageNumber <= totalPagesToRead && pageNumber <= MAX_SELECT_ALL_PAGES) {
        const result = await rawApi.episodes({ ...base, page: pageNumber });
        for (const episode of result.items) collected.set(episode.episode_id, episode);
        totalPagesToRead = result.total_pages;
        pageNumber += 1;
      }
      setSelected(collected);
      if (totalPagesToRead > MAX_SELECT_ALL_PAGES) {
        setError(
          `Selected ${collected.size.toLocaleString()} episodes, the most one action covers. Narrow the filters to reach the rest.`,
        );
      }
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "Could not select all episodes");
    } finally {
      setSelectingAll(false);
    }
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
  const convertibleEpisodes = selectedEpisodes.filter(
    (episode) => episode.review_status === "approved",
  );

  const activeBatch = batchList.find((item) => item.id === filters.collectionBatch) ?? null;

  // With no batch chosen, stop at the picker. A flat table over the whole
  // inventory hides which collection run produced what, and reviewers almost
  // always work inside one batch at a time.
  if (!filters.collectionBatch) {
    return (
      <div className="space-y-5">
        <div>
          <h1 className="font-heading text-[22px] font-bold tracking-tight">Raw episodes</h1>
          <p className="mt-0.5 text-sm text-ink-400">
            Teleop and scripted captures grouped by collection batch. Raw artifacts remain unchanged.
          </p>
        </div>

        <RawSummaryCards summary={summary} />

        <BatchGallery
          batches={batchList}
          loading={batchesLoading}
          error={batchError}
          onOpen={(batchId) => updateFilters({ collectionBatch: batchId })}
          onConvert={(batch) => void exportWholeBatch(batch.id)}
          onCreate={async (input) => {
            await rawApi.createBatch(input);
            await loadBatches();
          }}
          onUpdate={async (batchId, changes) => {
            await rawApi.updateBatch(batchId, changes);
            await loadBatches();
          }}
          onImport={() => setImporting(true)}
        />

        {importing && (
          <BatchImportDialog
            batches={batchList}
            onClose={() => setImporting(false)}
            onImported={() => {
              // The import rescores the whole corpus, so the episode counts on
              // every card can move, not just the batch that was uploaded.
              void loadBatches();
              void load();
            }}
          />
        )}

        {summary.total > 0 && (
          <Card
            title="Episodes without a batch"
            subtitle="Captures recorded before batches existed are still browsable and reviewable."
          >
            <Button variant="subtle" onClick={() => updateFilters({ collectionBatch: ALL_BATCHES })}>
              Browse all episodes ({summary.total})
            </Button>
          </Card>
        )}

        {exporting && (
          <ExportDialog
            episodes={selectedEpisodes}
            onClose={() => setExporting(false)}
            onExported={() => setSelected(new Map())}
          />
        )}
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div>
        <button
          type="button"
          onClick={() => updateFilters({ collectionBatch: "" })}
          className="mb-1 rounded text-sm text-ink-400 hover:text-ink-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/60"
        >
          ← All batches
        </button>
        <h1 className="font-heading text-[22px] font-bold tracking-tight">
          {filters.collectionBatch === ALL_BATCHES
            ? "All episodes"
            : (activeBatch?.name ?? filters.collectionBatch)}
        </h1>
        <p className="mt-0.5 text-sm text-ink-400">
          {activeBatch?.description ||
            "Filter, then select episodes to review or send to conversion."}
        </p>
      </div>

      <RawSummaryCards summary={summary} />

      {/*
        Filters live in a rail rather than a full-width block above the table:
        inside one batch there are only a handful of them, and a wide row of
        mostly-empty selects pushed the episodes themselves below the fold.
      */}
      <div className="grid gap-5 lg:grid-cols-[248px_minmax(0,1fr)] lg:items-start">
        <aside className="space-y-4 lg:sticky lg:top-4">
          <Card title="Review status">
            <ReviewStatusTabs
              value={view === "diversity" ? DIVERSITY_VIEW : filters.reviewStatus}
              counts={statusCounts ?? summary}
              onChange={(next) => {
                if (next === DIVERSITY_VIEW) {
                  setView("diversity");
                  return;
                }
                setView("episodes");
                updateFilters({ reviewStatus: next });
              }}
            />
          </Card>

          <Card title="Filters">
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
              scoped={filters.collectionBatch !== ALL_BATCHES}
            />
          </Card>
        </aside>

        <div className="space-y-5">
      {view === "diversity" ? (
        <BatchDiversity
          task={activeBatch?.task_name ?? items[0]?.task ?? null}
          collectionBatchId={
            filters.collectionBatch === ALL_BATCHES ? undefined : filters.collectionBatch
          }
        />
      ) : (
      <>
      {error && <Alert>{error}</Alert>}

      {selectedEpisodes.length > 0 && (
        <Card
          title="Conversion selection"
          subtitle={
            convertibleEpisodes.length === 0
              ? "Datasets are built from approved episodes only — none of this selection is approved yet."
              : "Your selection is kept while you move between pages in this table."
          }
        >
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="text-sm text-ink-300">
              <strong className="text-ink-100">{selectedEpisodes.length}</strong> selected ·{" "}
              {selectedFrames.toLocaleString()} frames
              {convertibleEpisodes.length < selectedEpisodes.length && (
                <span className="ml-2 text-warn-400">
                  {convertibleEpisodes.length} approved · the export drops the other{" "}
                  {selectedEpisodes.length - convertibleEpisodes.length}
                </span>
              )}
            </div>
            <div className="flex gap-2">
              <Button variant="subtle" onClick={() => setSelected(new Map())}>Clear selection</Button>
              <Button
                variant="primary"
                disabled={convertibleEpisodes.length === 0}
                title={
                  convertibleEpisodes.length === 0
                    ? "Datasets are built from approved episodes only. Accept some in the Verdict panel first."
                    : undefined
                }
                onClick={() => setExporting(true)}
              >
                Export {convertibleEpisodes.length} approved
              </Button>
            </div>
          </div>
        </Card>
      )}

      <Card
        title="Episode inventory"
        subtitle={`${total.toLocaleString()} matching episode${total === 1 ? "" : "s"}`}
        actions={
          <>
            <Button
              variant="subtle"
              disabled={total === 0 || selectingAll}
              onClick={() => void selectAllMatching()}
            >
              {selectingAll ? "Selecting…" : `Select all ${total.toLocaleString()}`}
            </Button>
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
          </>
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
      </>
      )}
        </div>
      </div>

      {exporting && (
        <ExportDialog
          episodes={selectedEpisodes}
          onClose={() => setExporting(false)}
          onExported={() => setSelected(new Map())}
        />
      )}
    </div>
  );
}
