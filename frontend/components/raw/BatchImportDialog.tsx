"use client";

/**
 * Bring a collection batch across from the desktop app.
 *
 * The app writes every batch to `<workspace>/batches/<name>/` as ordinary
 * folders, so the transfer people actually reach for is a zip of that folder.
 * It carries the rendered `review.mp4` beside each recording, which is why the
 * zip is worth preferring over the raw collection files: episodes are watchable
 * the moment the import finishes, with no render queue in between.
 *
 * A batch can be several gigabytes, so this reports upload progress rather than
 * leaving a spinner running.
 */

import { useEffect, useRef, useState } from "react";
import { Alert, Button, Field, Input } from "@/components/ui";
import { rawApi, type CollectionBatch, type CollectionBatchImport } from "@/lib/raw";

/** Matches `BATCH_ID_PATTERN` in `src/api/raw.py`. */
const BATCH_ID = /^[A-Za-z0-9._-]{1,64}$/;

function suggestId(fileName: string): string {
  const base = fileName.replace(/\.zip$/i, "");
  return base.replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 64);
}

export function BatchImportDialog({
  batches,
  onClose,
  onImported,
}: {
  batches: CollectionBatch[];
  onClose: () => void;
  onImported: (result: CollectionBatchImport) => void;
}) {
  const [archive, setArchive] = useState<File | null>(null);
  const [batchId, setBatchId] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<CollectionBatchImport | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) onClose();
    };
    window.addEventListener("keydown", onKey);
    dialogRef.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  const existing = batches.find((batch) => batch.id === batchId.trim());
  const validId = BATCH_ID.test(batchId.trim());
  const canSubmit = archive !== null && validId && !busy;

  async function run() {
    if (!archive) return;
    setBusy(true);
    setError(null);
    setProgress(0);
    try {
      const imported = await rawApi.importBatch(batchId.trim(), {
        archive,
        name: name.trim() || undefined,
        onProgress: setProgress,
      });
      setResult(imported);
      onImported(imported);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "Import failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) onClose();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label="Import a batch from the desktop app"
        tabIndex={-1}
        className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-2xl border border-ink-700 bg-ink-900 p-5 shadow-xl focus:outline-none"
      >
        <h2 className="font-heading text-lg font-bold text-ink-100">Import batch</h2>

        {result ? (
          <div className="mt-4 space-y-4">
            <Alert tone="ok">
              {result.batch.name} · {result.episodes.toLocaleString()} episodes and{" "}
              {result.videos.toLocaleString()} videos imported.
            </Alert>
            <p className="text-xs text-ink-400">
              Rebuilt {result.sources.length} collection{" "}
              {result.sources.length === 1 ? "run" : "runs"}:{" "}
              <span className="font-mono">{result.sources.join(", ")}</span>
            </p>
            {result.skipped.length > 0 ? (
              <div className="space-y-1">
                <Alert tone="info">
                  {result.skipped.length} recording
                  {result.skipped.length === 1 ? " was" : "s were"} left out.
                </Alert>
                <ul className="max-h-40 overflow-auto text-xs text-ink-400">
                  {result.skipped.map((item) => (
                    <li key={item.episode} className="py-0.5">
                      <span className="font-mono">{item.episode}</span> — {item.reason}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            <Button variant="primary" onClick={onClose}>
              Done
            </Button>
          </div>
        ) : (
          <div className="mt-4 space-y-4">
            <p className="text-sm text-ink-300">
              Zip a batch folder from the app workspace — the one holding{" "}
              <span className="font-mono text-ink-100">batch.json</span> beside{" "}
              <span className="font-mono text-ink-100">episodes/</span> — and upload it here.
            </p>

            <Field label="Batch archive" hint="A .zip of one batch folder">
              <Input
                type="file"
                accept=".zip,application/zip"
                disabled={busy}
                onChange={(event) => {
                  const file = event.target.files?.[0] ?? null;
                  setArchive(file);
                  if (file && !batchId) setBatchId(suggestId(file.name));
                }}
              />
            </Field>

            <Field
              label="Import into batch"
              hint={
                existing
                  ? `Adds to the existing batch "${existing.name}".`
                  : "A new batch is created with this id."
              }
            >
              <Input
                value={batchId}
                placeholder="lift-scripted-v2"
                disabled={busy}
                onChange={(event) => setBatchId(event.target.value)}
              />
            </Field>

            {batchId.trim() !== "" && !validId ? (
              <Alert tone="bad">
                A batch id may only contain letters, digits and . _ - because it becomes
                part of a dataset filename.
              </Alert>
            ) : null}

            {existing ? null : (
              <Field label="Display name" hint="Optional — taken from batch.json when blank.">
                <Input
                  value={name}
                  placeholder="Lift v2"
                  disabled={busy}
                  onChange={(event) => setName(event.target.value)}
                />
              </Field>
            )}

            {busy ? (
              <div>
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-ink-700">
                  <div
                    className="h-full bg-accent-500 transition-[width]"
                    style={{ width: `${progress}%` }}
                  />
                </div>
                <p className="mt-1.5 text-xs text-ink-400">
                  {progress < 100
                    ? `Uploading… ${progress}%`
                    : "Rebuilding collection runs and rescoring the corpus…"}
                </p>
              </div>
            ) : null}

            {error ? <Alert tone="bad">{error}</Alert> : null}

            <div className="flex justify-end gap-2 border-t border-ink-700 pt-4">
              <Button onClick={onClose} disabled={busy}>
                Cancel
              </Button>
              <Button variant="primary" disabled={!canSubmit} onClick={() => void run()}>
                {busy ? "Importing…" : "Import"}
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
