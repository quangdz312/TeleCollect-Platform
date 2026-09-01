"use client";

/**
 * Batch picker shown before the episode list.
 *
 * Same layout as the desktop app's review screen: one card per collection
 * batch with its task, episode counts split by source, and review progress.
 * Before this, a batch was one filter dropdown among many, so there was no
 * place that showed how many batches existed or how far each one had been
 * reviewed.
 */

import { useEffect, useMemo, useState } from "react";
import { Alert, Badge, Button, Card, cx, Empty, Field, Input, Select, TextArea } from "@/components/ui";
import { COLLECTION_ENABLED } from "@/lib/features";
import { labeling, type TaskOption } from "@/lib/labeling";
import { rawApi, type CollectionBatch } from "@/lib/raw";

/**
 * Turn a display name into a batch id.
 *
 * The id is stamped into every episode collected into the batch and can never
 * change, while the name is just a label. Asking for both up front made people
 * invent a second name for something they had already named, so the id is
 * derived here and only shown for confirmation. The pattern matches the
 * server's `BATCH_ID_PATTERN` — letters, digits, and `. _ -`, up to 64 chars.
 */
function idFromName(name: string): string {
  return name
    .normalize("NFD")
    .replace(new RegExp("[\\u0300-\\u036f]", "g"), "") // strip Vietnamese diacritics
    .replace(/đ/g, "d")
    .replace(/Đ/g, "D")
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, "-")
    .replace(/^[-.]+|[-.]+$/g, "")
    .slice(0, 64);
}

function Count({ value, label }: { value: number; label: string }) {
  return (
    <div>
      <p className="text-2xl font-bold tabular-nums text-ink-100">{value}</p>
      <p className="text-xs text-ink-400">{label}</p>
    </div>
  );
}

/**
 * Review progress as one fixed-height row.
 *
 * Every card reserves all three slots even at zero, so a batch with nothing
 * rejected still lines up with one that has rejections — previously the counts
 * wrapped to a second line on some cards and not others, and the cards in a row
 * stopped matching each other. Each state keeps its colour at zero too: dimming
 * the zeroes made the same label read as two different things across cards.
 */
function Progress({ batch }: { batch: CollectionBatch }) {
  const parts = [
    { value: batch.approved, label: "approved", tone: "text-ok-600" },
    { value: batch.pending, label: "pending", tone: "text-warn-400" },
    { value: batch.rejected, label: "rejected", tone: "text-bad-600" },
  ];
  return (
    <div className="mt-4 flex items-baseline gap-3 overflow-hidden text-sm font-semibold">
      {parts.map((part) => (
        <span key={part.label} className={cx("truncate tabular-nums", part.tone)}>
          {part.value} {part.label}
        </span>
      ))}
    </div>
  );
}

/**
 * Icon button with a hover/focus tooltip.
 *
 * The card has four actions and spelling them all out either greyed them into
 * illegibility or wrapped the row. `title` alone is not enough — it never
 * appears on keyboard focus — so the label is rendered and `aria-label` carries
 * the same text for screen readers.
 */
function IconAction({
  label,
  onClick,
  disabled,
  hint,
  danger,
  children,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  hint?: string;
  danger?: boolean;
  children: React.ReactNode;
}) {
  return (
    <span className="group relative inline-flex">
      <button
        type="button"
        onClick={onClick}
        disabled={disabled}
        aria-label={label}
        className={cx(
          "rounded-lg p-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/60",
          disabled
            ? "cursor-not-allowed text-ink-600"
            : danger
              ? "text-ink-300 hover:bg-bad-600/10 hover:text-bad-600"
              : "text-ink-300 hover:bg-ink-800 hover:text-ink-100",
        )}
      >
        {children}
      </button>
      <span
        role="tooltip"
        className="pointer-events-none absolute bottom-full left-1/2 z-10 mb-1.5 -translate-x-1/2 whitespace-nowrap rounded-md bg-ink-950 px-2 py-1 text-xs font-medium text-ink-100 opacity-0 shadow-lg transition-opacity group-hover:opacity-100 group-focus-within:opacity-100"
      >
        {hint ?? label}
      </span>
    </span>
  );
}

const ICON = "h-4 w-4";

function ConvertIcon() {
  return (
    <svg className={ICON} viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
      <path d="M3 7h11M11 4l3 3-3 3" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M17 13H6M9 16l-3-3 3-3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function RenameIcon() {
  return (
    <svg className={ICON} viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
      <path d="M13.5 3.5l3 3L7 16H4v-3l9.5-9.5z" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/**
 * Hai viec nay khac nhau ve huong, nen hai icon phai khac nhau ve huong.
 *
 * Push la day RA mot may khac: dam may, mui ten roi khoi may nay. Add episodes
 * la nap VAO cho dang mo: mui ten di xuong mot cai khay. Ban dau ca hai cung la
 * "mui ten len tren mot cai khay", lech nhau vai pixel — nhin khong the phan
 * biet, va hai nut canh nhau lam cung mot viec la giao dien noi doi.
 */
function PushIcon() {
  return (
    <svg className={ICON} viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
      <path
        d="M5.5 14.5a3 3 0 01-.4-5.97 4 4 0 017.74-1.06A3.25 3.25 0 0116 14.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path d="M10 17v-6.5M7.5 13L10 10.5l2.5 2.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function AddEpisodesIcon() {
  return (
    <svg className={ICON} viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
      <path d="M10 3v8.5M7 8.5l3 3 3-3" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M3.5 12.5v3a1 1 0 001 1h11a1 1 0 001-1v-3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function DeleteIcon() {
  return (
    <svg className={ICON} viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
      <path d="M3.5 5.5h13M8 5.5V3.5h4v2M5.5 5.5l.8 11h7.4l.8-11" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function BatchCard({
  batch,
  onOpen,
  onRename,
  onConvert,
  onDelete,
  onAddEpisodes,
  onPush,
  pushState,
}: {
  batch: CollectionBatch;
  onOpen: (batchId: string) => void;
  onRename: (batch: CollectionBatch) => void;
  onConvert: (batch: CollectionBatch) => void;
  onDelete: (batch: CollectionBatch) => void;
  onAddEpisodes: (batch: CollectionBatch) => void;
  /** `null` khi bản dựng này không phải app — khi đó không có nút Push. */
  onPush: ((batch: CollectionBatch) => void) | null;
  pushState: { busy: boolean; message: string | null } | undefined;
}) {
  return (
    <Card className="flex min-h-[210px] flex-col">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate text-lg font-bold text-ink-100">{batch.name}</h3>
          <p className="truncate font-mono text-xs text-ink-400">
            {batch.task_name ?? batch.id}
          </p>
        </div>
        {batch.archived ? <Badge tone="neutral">archived</Badge> : null}
      </div>

      {batch.description ? (
        <p className="mt-2 line-clamp-2 text-sm text-ink-300">{batch.description}</p>
      ) : null}

      <div className="mt-4 grid grid-cols-3 gap-3 border-t border-ink-700 pt-4">
        <Count value={batch.episodes} label="episodes" />
        <Count value={batch.teleop} label="teleop" />
        <Count value={batch.scripted} label="scripted" />
      </div>

      <Progress batch={batch} />

      {/* Bon icon canh mot nhan chu: "Review episodes →" dai toi muc mui ten bi
          day xuong dong va de len hang icon o the hep. Rut con "Review" — the
          da mang ten dot thu va so tap ngay tren, nen chu "episodes" khong noi
          them gi. `shrink-0` de nhan khong bao gio bi ep xuong dong nua. */}
      {/* Ket qua day len nam ngay tren the vua bam, khong phai mot thong bao
          chung o dau trang: day mot luc vai dot thu thi phai biet cai nao xong. */}
      {pushState?.message && (
        <p className="mt-2 truncate text-[11px] text-ink-400" title={pushState.message}>
          {pushState.message}
        </p>
      )}

      <div className="mt-auto flex items-center justify-between gap-2 border-t border-ink-700 pt-3">
        <button
          type="button"
          onClick={() => onOpen(batch.id)}
          className="shrink-0 whitespace-nowrap rounded text-sm font-semibold text-accent-500 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/60"
        >
          Review →
        </button>
        <div className="flex shrink-0 items-center gap-0.5">
          <IconAction
            label="Convert"
            hint={batch.approved === 0 ? "No approved episodes yet" : "Convert to a dataset"}
            onClick={() => onConvert(batch)}
            disabled={batch.approved === 0}
          >
            <ConvertIcon />
          </IconAction>
          {onPush && (
            <IconAction
              label="Push to server"
              hint={
                pushState?.busy
                  ? "Uploading…"
                  : batch.episodes === 0
                    ? "Nothing to push yet"
                    : "Send this batch to the shared server"
              }
              disabled={pushState?.busy || batch.episodes === 0}
              onClick={() => onPush(batch)}
            >
              <PushIcon />
            </IconAction>
          )}
          <IconAction
            label="Add episodes"
            hint="Upload more episodes into this batch"
            onClick={() => onAddEpisodes(batch)}
          >
            <AddEpisodesIcon />
          </IconAction>
          <IconAction label="Rename" onClick={() => onRename(batch)}>
            <RenameIcon />
          </IconAction>
          <IconAction label="Delete" danger onClick={() => onDelete(batch)}>
            <DeleteIcon />
          </IconAction>
        </div>
      </div>
    </Card>
  );
}

/**
 * Confirmation before a batch goes.
 *
 * Deleting the batch record only drops its name and description — the episodes
 * stay and reappear as an unnamed batch. The checkbox opts into also deleting
 * the teleop captures and their files, which is the irreversible half, so it
 * starts off and says exactly how many episodes it would take.
 */
function DeleteDialog({
  batch,
  onCancel,
  onConfirm,
}: {
  batch: CollectionBatch;
  onCancel: () => void;
  onConfirm: (purgeEpisodes: boolean) => Promise<void>;
}) {
  const [purge, setPurge] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      await onConfirm(purge);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete the batch");
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) onCancel();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`Delete batch ${batch.name}`}
        className="w-full max-w-md rounded-2xl border border-ink-700 bg-ink-900 p-5 shadow-xl"
      >
        <h2 className="font-heading text-lg font-bold text-ink-100">Delete batch</h2>
        <p className="mt-2 text-sm text-ink-300">
          <span className="font-semibold text-ink-100">{batch.name}</span> — its name and
          description go. The {batch.episodes.toLocaleString()} episodes stay and reappear
          as an unnamed batch.
        </p>

        {/* Only teleop captures can be purged — scripted episodes live in the
            labelling workspace as files, with no safe delete path from here. A
            batch with no teleop therefore has nothing to offer, so it says so
            instead of showing a checkbox that would delete nothing. */}
        {batch.teleop > 0 ? (
          <label className="mt-4 flex cursor-pointer gap-3 rounded-lg border border-ink-700 p-3 hover:border-bad-600/50">
            <input
              type="checkbox"
              checked={purge}
              onChange={(event) => setPurge(event.target.checked)}
              className="mt-0.5 h-4 w-4 shrink-0 accent-bad-600"
            />
            <span className="text-sm">
              <span className="font-semibold text-bad-600">
                Also delete {batch.teleop.toLocaleString()} teleop{" "}
                {batch.teleop === 1 ? "episode" : "episodes"} and their files
              </span>
              <span className="mt-1 block text-xs text-ink-400">
                Cannot be undone.
                {batch.scripted > 0
                  ? ` The ${batch.scripted.toLocaleString()} scripted episodes are kept either way.`
                  : ""}
              </span>
            </span>
          </label>
        ) : (
          <p className="mt-4 rounded-lg border border-ink-700 bg-ink-850 p-3 text-xs text-ink-400">
            All {batch.scripted.toLocaleString()} episodes here are scripted, so they
            stay in the labelling workspace — only the batch record is deleted.
          </p>
        )}

        {error ? (
          <div className="mt-3">
            <Alert tone="bad">{error}</Alert>
          </div>
        ) : null}

        <div className="mt-4 flex justify-end gap-2">
          <Button onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button variant="danger" onClick={() => void confirm()} disabled={busy}>
            {busy ? "Deleting…" : purge ? "Delete batch and episodes" : "Delete batch"}
          </Button>
        </div>
      </div>
    </div>
  );
}

export function BatchGallery({
  batches,
  loading,
  error,
  onOpen,
  onConvert,
  onCreate,
  onUpdate,
  onDelete,
  onImport,
}: {
  batches: CollectionBatch[];
  loading: boolean;
  error: string | null;
  onOpen: (batchId: string) => void;
  onConvert: (batch: CollectionBatch) => void;
  onCreate: (input: { id: string; name: string; task_name?: string; description?: string }) => Promise<void>;
  onUpdate: (batchId: string, changes: { name?: string; description?: string; archived?: boolean }) => Promise<void>;
  onDelete: (batchId: string, purgeEpisodes: boolean) => Promise<void>;
  // `batch` trong khi nap them tap vao mot dot thu co san, `null` khi nap ca
  // mot dot thu moi tu file zip.
  onImport: (batch: CollectionBatch | null) => void;
}) {
  const [editing, setEditing] = useState<CollectionBatch | null>(null);
  const [deleting, setDeleting] = useState<CollectionBatch | null>(null);
  /**
   * Trạng thái đẩy lên, theo từng đợt thu.
   *
   * Một cờ chung không đủ: đẩy một đợt thu chạy vài phút, người dùng bấm tiếp
   * cái thứ hai, và khi đó "đang đẩy" phải nói về đúng cái thẻ đang đẩy.
   */
  const [pushing, setPushing] = useState<Record<string, { busy: boolean; message: string | null }>>({});
  /** `null` cho tới khi biết: nút Push chỉ có nghĩa khi app đã đăng nhập máy chủ. */
  const [signedIn, setSignedIn] = useState<boolean | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ id: "", name: "", task_name: "", description: "" });
  // The id follows the name until someone edits it directly; after that it is
  // theirs to control, because silently rewriting an id they typed would lose
  // the one field that is stamped into every episode.
  const [idEdited, setIdEdited] = useState(false);
  const [tasks, setTasks] = useState<TaskOption[]>([]);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  // Filtering happens in the browser: the batch list is one row per collection
  // run, so it stays small enough that a round trip per keystroke would cost
  // more than it saves.
  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return batches;
    return batches.filter((batch) =>
      [batch.name, batch.id, batch.task_name ?? "", batch.description]
        .join(" ")
        .toLowerCase()
        .includes(needle),
    );
  }, [batches, query]);

  // The task list comes from the simulator, so the picker can never offer a
  // task the collector would then refuse.
  useEffect(() => {
    let cancelled = false;
    labeling
      .config()
      .then((config) => {
        if (!cancelled) setTasks(config.tasks);
      })
      .catch(() => {
        // A missing list only costs the dropdown its options; the field still
        // accepts a typed task, so this is not worth an error banner.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Chỉ hỏi khi bản dựng này là app; trên web đã dựng, đường `/local` không tồn
  // tại và một lần 404 mỗi lần mở trang là tiếng ồn vô ích trong console.
  useEffect(() => {
    if (!COLLECTION_ENABLED) return;
    let cancelled = false;
    rawApi
      .syncSession()
      .then((session) => {
        if (!cancelled) setSignedIn(session !== null);
      })
      .catch(() => {
        if (!cancelled) setSignedIn(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function pushBatch(batch: CollectionBatch) {
    setPushing((current) => ({ ...current, [batch.id]: { busy: true, message: "Uploading…" } }));
    try {
      const result = await rawApi.pushBatch(batch.id);
      setPushing((current) => ({
        ...current,
        [batch.id]: {
          busy: false,
          message: `Pushed ${result.episodes.toLocaleString()} episode${result.episodes === 1 ? "" : "s"}`,
        },
      }));
    } catch (problem) {
      setPushing((current) => ({
        ...current,
        [batch.id]: {
          busy: false,
          message: problem instanceof Error ? problem.message : "Push failed",
        },
      }));
    }
  }

  function startCreate() {
    setForm({ id: "", name: "", task_name: "", description: "" });
    setIdEdited(false);
    setFormError(null);
    setEditing(null);
    setCreating(true);
  }

  function startRename(batch: CollectionBatch) {
    setForm({
      id: batch.id,
      name: batch.named ? batch.name : "",
      task_name: batch.task_name ?? "",
      description: batch.description,
    });
    setFormError(null);
    setCreating(false);
    setEditing(batch);
  }

  function close() {
    setCreating(false);
    setEditing(null);
    setFormError(null);
  }

  async function submit() {
    setSaving(true);
    setFormError(null);
    try {
      if (editing && editing.named) {
        await onUpdate(editing.id, {
          name: form.name.trim(),
          description: form.description.trim(),
        });
      } else {
        // An unnamed batch already has its id from episode provenance, so
        // "name it" is the same create call — no separate code path needed.
        await onCreate({
          id: (editing ? editing.id : form.id).trim(),
          name: form.name.trim(),
          task_name: form.task_name.trim() || undefined,
          description: form.description.trim() || undefined,
        });
      }
      close();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Could not save the batch");
    } finally {
      setSaving(false);
    }
  }

  const open = creating || editing !== null;
  // Mirrors the server's BATCH_ID_PATTERN so a name that derives to nothing
  // usable — all punctuation, say — is caught here rather than by a 422.
  const idValid = /^[A-Za-z0-9._-]{1,64}$/.test(form.id.trim());
  const canSubmit = form.name.trim().length > 0 && (editing !== null || idValid);

  return (
    <div className="space-y-4">
      {/* One row: heading left, controls right, both aligned to the heading's
          first line. `items-end` used to hang the controls off the bottom of a
          two-line heading, which read as a floating block rather than a header
          row. The controls never wrap among themselves — search shrinks first,
          and only the whole cluster drops to its own line on a narrow screen. */}
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-3">
        <div className="min-w-0">
          <h2 className="text-xl font-bold text-ink-100">Collection batches</h2>
          <p className="text-sm text-ink-400">
            Choose a batch to review or convert its episodes, or import one from the app.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Input
            type="search"
            aria-label="Search batches"
            placeholder="Search batches"
            className="w-48 sm:w-56"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <Button onClick={() => onImport(null)}>Import batch</Button>
          <Button variant="primary" onClick={startCreate}>
            New batch
          </Button>
        </div>
      </div>

      {/* Chua dang nhap thi khong the co nut Push tren the nao. Im lang o day
          khien nguoi dung tuong tinh nang khong ton tai, thay vi biet minh con
          thieu mot buoc. */}
      {COLLECTION_ENABLED && signedIn === false ? (
        <Alert tone="info">
          Connect to the shared server to push these batches. Use Connect at the bottom of the
          sidebar; your batches stay on this computer until you do.
        </Alert>
      ) : null}

      {error ? <Alert tone="bad">{error}</Alert> : null}

      {open ? (
        <Card title={editing ? `Edit batch · ${editing.id}` : "New batch"}>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Name">
              <Input
                value={form.name}
                placeholder="Lift — round 2"
                onChange={(event) => {
                  const name = event.target.value;
                  setForm((current) => ({
                    ...current,
                    name,
                    id: editing || idEdited ? current.id : idFromName(name),
                  }));
                }}
              />
            </Field>
            {editing && editing.named ? null : (
              <Field label="Task">
                <Select
                  value={form.task_name}
                  onChange={(event) => setForm({ ...form, task_name: event.target.value })}
                >
                  <option value="">Choose a task</option>
                  {tasks.map((option) => (
                    <option key={option.task} value={option.task}>
                      {option.tool_label ?? option.task}
                    </option>
                  ))}
                </Select>
              </Field>
            )}
            {editing ? null : (
              <Field
                label="Batch id"
                className="sm:col-span-2"
                hint={
                  form.id.trim().length > 0 && !idValid
                    ? "Only letters, digits, and . _ - are allowed, up to 64 characters."
                    : "Derived from the name and stamped into every episode collected into this batch. It never changes, so renaming later leaves it alone."
                }
              >
                <Input
                  value={form.id}
                  placeholder="lift-round-2"
                  onChange={(event) => {
                    setIdEdited(true);
                    setForm({ ...form, id: event.target.value });
                  }}
                />
              </Field>
            )}
            <Field label="Description" className="sm:col-span-2">
              <TextArea
                rows={2}
                value={form.description}
                onChange={(event) => setForm({ ...form, description: event.target.value })}
              />
            </Field>
          </div>
          {formError ? (
            <div className="mt-3">
              <Alert tone="bad">{formError}</Alert>
            </div>
          ) : null}
          <div className="mt-4 flex gap-2">
            <Button variant="primary" disabled={!canSubmit || saving} onClick={() => void submit()}>
              {saving ? "Saving…" : "Save"}
            </Button>
            <Button onClick={close} disabled={saving}>
              Cancel
            </Button>
          </div>
        </Card>
      ) : null}

      {loading ? (
        <p className="text-sm text-ink-400">Loading batches…</p>
      ) : batches.length === 0 ? (
        <Empty>No collection batches yet. Create one to start grouping captures.</Empty>
      ) : visible.length === 0 ? (
        <Empty>No batch matches “{query}”.</Empty>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {visible.map((batch) => (
            <BatchCard
              key={batch.id}
              batch={batch}
              onOpen={onOpen}
              onRename={startRename}
              onConvert={onConvert}
              onDelete={setDeleting}
              onAddEpisodes={onImport}
              onPush={COLLECTION_ENABLED && signedIn ? pushBatch : null}
              pushState={pushing[batch.id]}
            />
          ))}
        </div>
      )}

      {deleting ? (
        <DeleteDialog
          batch={deleting}
          onCancel={() => setDeleting(null)}
          onConfirm={async (purgeEpisodes) => {
            await onDelete(deleting.id, purgeEpisodes);
            setDeleting(null);
          }}
        />
      ) : null}
    </div>
  );
}
