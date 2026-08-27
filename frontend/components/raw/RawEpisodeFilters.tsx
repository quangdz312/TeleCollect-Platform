import { Button, Field, Input, Select } from "@/components/ui";
import type {
  RawEpisodeQuality,
  RawEpisodeReviewStatus,
  RawEpisodeSource,
} from "@/lib/raw";

export interface RawFiltersValue {
  source: RawEpisodeSource | "";
  task: string;
  quality: RawEpisodeQuality | "";
  outcome: "success" | "failure" | "";
  reviewStatus: RawEpisodeReviewStatus | "";
  collectionBatch: string;
  search: string;
}

export function RawEpisodeFilters({
  value,
  searchDraft,
  onSearchDraftChange,
  onChange,
  onApplySearch,
  onReset,
  tasks,
  batches,
  scoped = false,
}: {
  value: RawFiltersValue;
  searchDraft: string;
  onSearchDraftChange: (value: string) => void;
  onChange: (patch: Partial<RawFiltersValue>) => void;
  onApplySearch: () => void;
  onReset: () => void;
  tasks: string[];
  batches: string[];
  /**
   * True while browsing inside one collection batch. The batch is already
   * chosen on the previous screen, and a batch targets a single task, so both
   * selects would be dead controls here.
   */
  scoped?: boolean;
}) {
  return (
    <div className={scoped ? "space-y-2.5" : "space-y-4"}>
      <form
        className={scoped ? "flex flex-col gap-2" : "flex flex-col gap-2 sm:flex-row"}
        onSubmit={(event) => {
          event.preventDefault();
          onApplySearch();
        }}
      >
        {scoped ? (
          /*
           * In the left bar there is no room for a pair of buttons beside the
           * field, and none is needed: the form submits on Enter, so the icon
           * is an affordance rather than the only way to search.
           */
          <>
            <div className="relative">
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-400"
                aria-hidden="true"
              >
                <circle cx="11" cy="11" r="7" />
                <path d="m20 20-3.5-3.5" />
              </svg>
              <Input
                style={{ paddingLeft: "2rem", paddingRight: value.search ? "2rem" : undefined }}
                aria-label="Search episode"
                value={searchDraft}
                onChange={(event) => onSearchDraftChange(event.target.value)}
                placeholder="Search episodes"
              />
              {/*
                Keyed off the applied term, not the draft: a search is only
                escapable if the way out is visible while it is in force. The
                draft can differ from what the table is filtered by, and it is
                the latter the reviewer needs to undo.
              */}
              {value.search ? (
                <button
                  type="button"
                  aria-label="Clear search"
                  onClick={() => {
                    onSearchDraftChange("");
                    onChange({ search: "" });
                  }}
                  className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-1 text-ink-400 transition-colors hover:text-ink-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/60"
                >
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true">
                    <path d="M18 6 6 18M6 6l12 12" />
                  </svg>
                </button>
              ) : null}
            </div>
            {value.search ? (
              <p className="px-0.5 text-[11px] text-ink-400">
                Filtering every status by “{value.search}”.
              </p>
            ) : null}
            <button
              type="button"
              onClick={onReset}
              className="self-start rounded text-xs text-ink-400 hover:text-ink-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/60"
            >
              Reset filters
            </button>
          </>
        ) : (
          <>
            <Field label="Search episode" className="min-w-0 flex-1">
              <Input
                value={searchDraft}
                onChange={(event) => onSearchDraftChange(event.target.value)}
                placeholder="Episode ID or display name"
              />
            </Field>
            <div className="flex items-end gap-2">
              <Button type="submit" variant="primary">Search</Button>
              <Button type="button" variant="subtle" onClick={onReset}>Reset</Button>
            </div>
          </>
        )}
      </form>

      <div className={scoped ? "grid gap-2.5" : "grid gap-3 sm:grid-cols-2 xl:grid-cols-6"}>
        <Field label="Source">
          <Select
            value={value.source}
            onChange={(event) => onChange({
              source: event.target.value as RawFiltersValue["source"],
              task: "",
              collectionBatch: event.target.value === "teleop" ? "" : value.collectionBatch,
            })}
          >
            <option value="">All sources</option>
            <option value="teleop">Teleop</option>
            <option value="scripted">Scripted</option>
          </Select>
        </Field>
        {scoped ? null : (
          <Field label="Task">
            <Select
              value={value.task}
              onChange={(event) => onChange({ task: event.target.value })}
            >
              <option value="">All tasks</option>
              {tasks.map((task) => <option key={task} value={task}>{task}</option>)}
            </Select>
          </Field>
        )}
        {scoped ? null : (
          <Field label="Collection batch">
            <Select
              value={value.collectionBatch}
              onChange={(event) => onChange({ collectionBatch: event.target.value })}
            >
              <option value="">All collection batches</option>
              {batches.map((batch) => <option key={batch} value={batch}>{batch}</option>)}
            </Select>
          </Field>
        )}
        <Field label="Quality">
          <Select
            value={value.quality}
            onChange={(event) => onChange({ quality: event.target.value as RawFiltersValue["quality"] })}
          >
            <option value="">All qualities</option>
            <option value="clean">Clean</option>
            <option value="good">Good</option>
            <option value="medium">Medium</option>
            <option value="poor">Poor</option>
          </Select>
        </Field>
        <Field label="Outcome">
          <Select
            value={value.outcome}
            onChange={(event) => onChange({ outcome: event.target.value as RawFiltersValue["outcome"] })}
          >
            <option value="">All outcomes</option>
            <option value="success">Success</option>
            <option value="failure">Failure</option>
          </Select>
        </Field>
        <Field label="Review status">
          <Select
            value={value.reviewStatus}
            onChange={(event) => onChange({ reviewStatus: event.target.value as RawFiltersValue["reviewStatus"] })}
          >
            <option value="">All statuses</option>
            <option value="pending">Pending</option>
            <option value="approved">Approved</option>
            <option value="rejected">Rejected</option>
            <option value="archived">Archived</option>
          </Select>
        </Field>
      </div>
    </div>
  );
}
