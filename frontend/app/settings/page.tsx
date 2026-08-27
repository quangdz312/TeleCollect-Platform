"use client";

/**
 * Personal integrations for the signed-in user.
 *
 * A W&B key belongs to one person's account — their quota, their private
 * projects — so each person supplies their own rather than the server holding
 * one key for everybody. That also avoids the failure this page exists to fix:
 * a deployment configured with somebody else's key, where every run either
 * lands in a stranger's account or fails to authenticate.
 *
 * The key is write-only here. Once saved it can be replaced or removed, never
 * read back, so the field starts empty even when a key is stored and the four
 * trailing characters are all the page shows.
 */

import { useEffect, useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { Alert, Button, Card, Field, Input } from "@/components/ui";
import { api, type WandbSettings } from "@/lib/api";

export default function SettingsPage() {
  const { user } = useAuth();
  const [settings, setSettings] = useState<WandbSettings | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [entity, setEntity] = useState("");
  const [busy, setBusy] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (!user) return;
    void api
      .wandbSettings()
      .then((loaded) => {
        setSettings(loaded);
        setEntity(loaded.entity);
      })
      .catch((problem: unknown) => {
        setError(problem instanceof Error ? problem.message : "Could not load settings");
      });
  }, [user]);

  if (!user) return null;

  async function save() {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const body: { api_key?: string; entity: string } = { entity: entity.trim() };
      // Omitted rather than sent empty: an empty field means "keep the stored
      // key and only change the entity", which the page cannot express any
      // other way because it never holds the key.
      if (apiKey.trim()) body.api_key = apiKey.trim();
      const saved = await api.saveWandbSettings(body);
      setSettings(saved);
      setApiKey("");
      setNotice("Saved.");
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "Could not save");
    } finally {
      setBusy(false);
    }
  }

  async function verify() {
    setVerifying(true);
    setError(null);
    setNotice(null);
    try {
      // Prefers whatever is typed, so a key can be checked before it is saved.
      const result = await api.verifyWandbKey(
        apiKey.trim() ? { api_key: apiKey.trim() } : {},
      );
      if (result.ok) {
        setNotice(
          result.entity
            ? `Key works — default entity is ${result.entity}.`
            : "Key works.",
        );
      } else {
        setError(result.detail || "W&B rejected this key.");
      }
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "Could not reach W&B");
    } finally {
      setVerifying(false);
    }
  }

  async function clear() {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      setSettings(await api.clearWandbSettings());
      setApiKey("");
      setEntity("");
      setNotice("Removed.");
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "Could not remove");
    } finally {
      setBusy(false);
    }
  }

  const configured = settings?.configured ?? false;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="font-heading text-[22px] font-bold tracking-tight">Settings</h1>
        <p className="mt-0.5 text-sm text-ink-400">
          Accounts you connect here belong to you alone — nobody else, including
          administrators, can read them back.
        </p>
      </div>

      <Card
        title="Weights &amp; Biases"
        subtitle="Training runs log to your own W&B account. Without a key here, runs with W&B enabled are refused rather than logged somewhere unexpected."
      >
        <div className="max-w-xl space-y-4">
          {configured ? (
            <Alert tone="ok">
              A key ending {settings?.key_preview} is stored.
            </Alert>
          ) : (
            <Alert tone="info">No key stored yet.</Alert>
          )}

          <Field
            label={configured ? "Replace API key" : "API key"}
            hint="From wandb.ai → User settings → API keys. Stored encrypted and never shown again."
          >
            <Input
              type="password"
              autoComplete="off"
              value={apiKey}
              placeholder={configured ? "Leave blank to keep the stored key" : "40-character key"}
              disabled={busy}
              onChange={(event) => setApiKey(event.target.value)}
            />
          </Field>

          <Field
            label="Entity"
            hint="W&B team or username runs are logged under. Blank uses your account default."
          >
            <Input
              value={entity}
              placeholder="my-team"
              disabled={busy}
              onChange={(event) => setEntity(event.target.value)}
            />
          </Field>

          {error ? <Alert tone="bad">{error}</Alert> : null}
          {notice ? <Alert tone="ok">{notice}</Alert> : null}

          <div className="flex flex-wrap gap-2 border-t border-ink-700 pt-4">
            <Button variant="primary" disabled={busy} onClick={() => void save()}>
              {busy ? "Saving…" : "Save"}
            </Button>
            <Button
              disabled={verifying || busy || (!configured && !apiKey.trim())}
              onClick={() => void verify()}
            >
              {verifying ? "Checking…" : "Test connection"}
            </Button>
            {configured ? (
              <Button variant="subtle" disabled={busy} onClick={() => void clear()}>
                Remove
              </Button>
            ) : null}
          </div>
        </div>
      </Card>
    </div>
  );
}
