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

import { useMemo, useState } from "react";
import { Alert, Badge, Button, Card, Empty, Field, Input, TextArea } from "@/components/ui";
import type { CollectionBatch } from "@/lib/raw";

function Count({ value, label }: { value: number; label: string }) {
  return (
    <div>
      <p className="text-2xl font-bold tabular-nums text-ink-100">{value}</p>
      <p className="text-xs text-ink-400">{label}</p>
    </div>
  );
}

function BatchCard({
  batch,
  onOpen,
  onRename,
  onConvert,
}: {
  batch: CollectionBatch;
  onOpen: (batchId: string) => void;
  onRename: (batch: CollectionBatch) => void;
  onConvert: (batch: CollectionBatch) => void;
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
        {batch.named ? (
          batch.archived ? <Badge tone="neutral">archived</Badge> : null
        ) : (
          <Badge tone="warn">unnamed</Badge>
        )}
      </div>

      {batch.description ? (
        <p className="mt-2 line-clamp-2 text-sm text-ink-300">{batch.description}</p>
      ) : null}

      <div className="mt-4 grid grid-cols-3 gap-3 border-t border-ink-700 pt-4">
        <Count value={batch.episodes} label="episodes" />
        <Count value={batch.teleop} label="teleop" />
        <Count value={batch.scripted} label="scripted" />
      </div>

      <div className="mt-4 flex flex-wrap gap-x-4 gap-y-1 text-sm font-semibold">
        <span className="text-ok-600">{batch.approved} approved</span>
        <span className="text-warn-400">{batch.pending} pending</span>
        <span className="text-bad-600">{batch.rejected} rejected</span>
      </div>

      <div className="mt-auto flex flex-wrap items-center gap-3 border-t border-ink-700 pt-4">
        <button
          type="button"
          onClick={() => onOpen(batch.id)}
          className="rounded text-sm font-semibold text-accent-500 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/60"
        >
          Review episodes →
        </button>
        <button
          type="button"
          onClick={() => onConvert(batch)}
          className="rounded text-sm text-ink-400 hover:text-ink-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/60"
          disabled={batch.approved === 0}
          title={batch.approved === 0 ? "No approved episodes to convert yet" : undefined}
        >
          Convert
        </button>
        <button
          type="button"
          onClick={() => onRename(batch)}
          className="rounded text-sm text-ink-400 hover:text-ink-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/60"
        >
          {batch.named ? "Edit" : "Name it"}
        </button>
      </div>
    </Card>
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
  onImport,
}: {
  batches: CollectionBatch[];
  loading: boolean;
  error: string | null;
  onOpen: (batchId: string) => void;
  onConvert: (batch: CollectionBatch) => void;
  onCreate: (input: { id: string; name: string; task_name?: string; description?: string }) => Promise<void>;
  onUpdate: (batchId: string, changes: { name?: string; description?: string; archived?: boolean }) => Promise<void>;
  onImport: () => void;
}) {
  const [editing, setEditing] = useState<CollectionBatch | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ id: "", name: "", task_name: "", description: "" });
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

  function startCreate() {
    setForm({ id: "", name: "", task_name: "", description: "" });
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
  const canSubmit =
    form.name.trim().length > 0 && (editing !== null || form.id.trim().length > 0);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-xl font-bold text-ink-100">Collection batches</h2>
          <p className="text-sm text-ink-400">
            Choose a batch to review or convert its episodes, or import one from the app.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Input
            type="search"
            aria-label="Search batches"
            placeholder="Search batches"
            className="w-56"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <Button onClick={onImport}>Import batch</Button>
          <Button variant="primary" onClick={startCreate}>
            New batch
          </Button>
        </div>
      </div>

      {error ? <Alert tone="bad">{error}</Alert> : null}

      {open ? (
        <Card title={editing ? `Edit batch · ${editing.id}` : "New batch"}>
          <div className="grid gap-4 sm:grid-cols-2">
            {editing ? null : (
              <Field label="Batch id" hint="Stored with every episode collected into this batch.">
                <Input
                  value={form.id}
                  placeholder="lift-scripted-v2"
                  onChange={(event) => setForm({ ...form, id: event.target.value })}
                />
              </Field>
            )}
            <Field label="Display name">
              <Input
                value={form.name}
                placeholder="Lift — round 2"
                onChange={(event) => setForm({ ...form, name: event.target.value })}
              />
            </Field>
            {editing && editing.named ? null : (
              <Field label="Task">
                <Input
                  value={form.task_name}
                  placeholder="lift"
                  onChange={(event) => setForm({ ...form, task_name: event.target.value })}
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
            />
          ))}
        </div>
      )}
    </div>
  );
}
