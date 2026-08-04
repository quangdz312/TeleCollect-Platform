"use client";

import { useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { Alert, Button, Card, Field, Input } from "@/components/ui";

const SEEDS: [string, string, string][] = [
  ["operator", "operator", "Drive the robot and record demonstrations"],
  ["reviewer", "reviewer", "Trim, label and approve recordings"],
  ["admin", "admin", "Everything, plus user management"],
];

export default function LoginPage() {
  const { login } = useAuth();
  const [username, setUsername] = useState("operator");
  const [password, setPassword] = useState("operator");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username, password);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto flex min-h-[80vh] max-w-md flex-col justify-center">
      <div className="mb-6 text-center">
        <div className="mx-auto mb-3 grid h-12 w-12 place-items-center rounded-xl bg-accent-500 text-lg font-bold text-white">
          TC
        </div>
        <h1 className="text-xl font-semibold">TeleCollect</h1>
        <p className="mt-1 text-sm text-ink-400">
          Teleoperation &amp; demonstration collection for imitation learning
        </p>
      </div>

      <Card>
        <form onSubmit={submit} className="space-y-4">
          <Field label="Username">
            <Input
              value={username}
              autoComplete="username"
              onChange={(e) => setUsername(e.target.value)}
              required
            />
          </Field>
          <Field label="Password">
            <Input
              type="password"
              value={password}
              autoComplete="current-password"
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </Field>
          {error && <Alert>{error}</Alert>}
          <Button type="submit" variant="primary" className="w-full" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
          </Button>
        </form>

        <div className="mt-5 border-t border-ink-700/60 pt-4">
          <p className="mb-2 text-[11px] uppercase tracking-wider text-ink-400">
            Development accounts
          </p>
          <div className="space-y-1.5">
            {SEEDS.map(([name, pass, description]) => (
              <button
                key={name}
                type="button"
                onClick={() => {
                  setUsername(name);
                  setPassword(pass);
                }}
                className="flex w-full items-center justify-between rounded-lg border border-ink-700/60 px-3 py-2 text-left text-xs hover:border-accent-500/50 hover:bg-ink-850"
              >
                <span className="font-medium">{name}</span>
                <span className="text-ink-400">{description}</span>
              </button>
            ))}
          </div>
        </div>
      </Card>
    </div>
  );
}
