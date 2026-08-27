"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useAuth } from "@/components/AuthProvider";
import { useLeftBar } from "@/components/AppShell";
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

/** One count in the sticky batch header — the card set, flattened to a line. */
function HeaderStat({
  label,
  value,
  tone,
}: {
  label: string;
  value: number | string;
  tone?: "warn";
}) {
  return (
    <span className="flex items-baseline gap-1.5">
      <span className="text-[11px] uppercase tracking-wider text-ink-400">{label}</span>
      <strong className={tone === "warn" ? "font-bold text-warn-400" : "font-bold text-ink-100"}>
        {typeof value === "number" ? value.toLocaleString() : value}
      </strong>
    </span>
  );
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
  /**
   * The batch's task, held apart from the table.
   *
   * Diversity is scoped by task, and an unnamed batch carries no `task_name`,
   * so the task has to come from the episodes. Reading it off the current page
   * meant a status with no rows — Archived, usually — left it null and the
   * report claimed the batch had no task at all.
   */
  const [batchTask, setBatchTask] = useState<string | null>(null);
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
        if (cancelled) return;
        setStatusCounts(result.summary);
        // `summary.by_task` counts the filtered set, so it is scoped to this
        // batch — unlike `available_tasks`, which the backend derives from
        // every episode in the system and would name another batch's task.
        // The request omits `review_status`, so this survives an empty tab.
        setBatchTask(Object.keys(result.summary.by_task)[0] ?? null);
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

  const updateFilters = (patch: Partial<RawFiltersValue>) => {
    setPage(1);
    const next = { ...filters, ...patch };
    setFilters(next);
    const query = filtersToQuery(next);
    router.replace(query.size ? `/raw?${query}` : "/raw", { scroll: false });
  };

  const insideBatch = Boolean(filters.collectionBatch);

  // Diversity is a view of one batch, so leaving the batch — or opening a
  // different one — has to drop it. Otherwise the next batch opens straight
  // into a report while the status rail shows nothing selected.
  useEffect(() => {
    setView("episodes");
  }, [filters.collectionBatch]);

  // One continuous rail: status above, filters below, no cards or gap between
  // them, so reaching the search box is a glance rather than a scroll.
  const reviewRail = useMemo(
    () =>
      insideBatch ? (
        <div className="space-y-3">
          <div>
            <p className="px-2.5 pb-1 text-[10px] font-semibold uppercase tracking-wider text-ink-400">
              Review status
            </p>
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
          </div>

          <div className="border-t border-ink-700 pt-3">
            <p className="px-2.5 pb-1.5 text-[10px] font-semibold uppercase tracking-wider text-ink-400">
              Filters
            </p>
            <RawEpisodeFilters
              value={filters}
              searchDraft={searchDraft}
              onSearchDraftChange={setSearchDraft}
              onChange={updateFilters}
              onApplySearch={() => updateFilters({ search: searchDraft })}
              onReset={() => {
                // Clearing the filters must not also leave the batch: this
                // button lives inside one, and dropping to `/raw` threw the
                // reviewer back out to the batch picker.
                setSearchDraft("");
                updateFilters({
                  ...EMPTY_FILTERS,
                  collectionBatch: filters.collectionBatch,
                });
              }}
              tasks={tasks}
              batches={batches}
              scoped={filters.collectionBatch !== ALL_BATCHES}
            />
          </div>
        </div>
      ) : null,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [insideBatch, view, filters, statusCounts, summary, searchDraft, tasks, batches],
  );

  // Above the early returns below: a hook has to run on every render.
  useLeftBar(reviewRail);

  if (authLoading || !user) return null;
  if (user.role === "operator") {
    return <Alert tone="info">Raw episode management is available to reviewers and administrators.</Alert>;
  }

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
      <div className="-mx-5 -my-6">
        <div className="sticky top-0 z-20 border-b border-ink-700 bg-ink-950/95 px-5 py-3 backdrop-blur-md">
          <h1 className="font-heading text-lg font-bold tracking-tight">Raw episodes</h1>
          <p className="text-xs text-ink-400">
            Captures grouped by collection batch.
          </p>
        </div>

        <div className="space-y-5 px-5 pb-6 pt-5">
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

  return (
    <div className="-mx-5 -my-6">
      {/*
        Header and counts ride together in one sticky strip. The counts are the
        reference a reviewer checks against while working down a long table, so
        scrolling them away is what made them worth pinning; folding them into
        the title row keeps that strip shallow enough to be worth the space.
      */}
      <div className="sticky top-0 z-20 flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-ink-700 bg-ink-950/95 px-5 py-2.5 backdrop-blur-md">
        <div className="min-w-0">
          <button
            type="button"
            onClick={() => updateFilters({ collectionBatch: "" })}
            className="rounded text-xs text-ink-400 hover:text-ink-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/60"
          >
            ← All batches
          </button>
          <h1 className="truncate font-heading text-lg font-bold tracking-tight">
            {filters.collectionBatch === ALL_BATCHES
              ? "All episodes"
              : (activeBatch?.name ?? filters.collectionBatch)}
          </h1>
        </div>

        <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-sm tabular-nums">
          <HeaderStat label="Episodes" value={summary.total} />
          <HeaderStat label="Teleop" value={summary.teleop} />
          <HeaderStat label="Scripted" value={summary.scripted} />
          <HeaderStat
            label="Success / failure"
            value={`${summary.successes} / ${summary.failures}`}
            tone="warn"
          />
          <HeaderStat label="Pending" value={summary.pending} />
        </div>
      </div>

      <div className="grid px-5 pb-6 pt-5">
        <div className="space-y-5">
      {view === "diversity" ? (
        <BatchDiversity
          task={activeBatch?.task_name ?? batchTask ?? items[0]?.task ?? null}
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
