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
    <div className="space-y-4">
      <form
        className={scoped ? "flex flex-col gap-2" : "flex flex-col gap-2 sm:flex-row"}
        onSubmit={(event) => {
          event.preventDefault();
          onApplySearch();
        }}
      >
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
      </form>

      <div className={scoped ? "grid gap-3" : "grid gap-3 sm:grid-cols-2 xl:grid-cols-6"}>
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
