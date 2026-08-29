"use client";

import { useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { Alert, Button, Card, Field, Input } from "@/components/ui";
import { api } from "@/lib/api";

const SEEDS: [string, string, string][] = [
  ["admin", "Admin12345", "Everything, plus user management"],
  ["seed_reviewer1", "seedpassword1", "Trim, label and approve recordings"],
  ["seed_operator1", "seedpassword1", "View and manage operator recordings"],
];

// The seed accounts exist so a developer running the repo can sign in without
// setting anything up. The deployed web serves real accounts, so printing
// working passwords on its front door would hand the site to anyone.
const SHOW_SEEDS = process.env.NODE_ENV !== "production";

export default function LoginPage() {
  const { login } = useAuth();
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [username, setUsername] = useState(SHOW_SEEDS ? "admin" : "");
  const [password, setPassword] = useState(SHOW_SEEDS ? "Admin12345" : "");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function switchMode(next: "signin" | "signup") {
    setMode(next);
    setError(null);
    setNotice(null);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      if (mode === "signup") {
        await api.register({ username, password, display_name: displayName });
        setNotice("Account created. An administrator has to approve it before you can sign in.");
        setMode("signin");
        setPassword("");
        setDisplayName("");
      } else {
        await login(username, password);
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : mode === "signup" ? "Sign up failed" : "Login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="-mx-5 -my-6 grid min-h-screen lg:grid-cols-[1.1fr_0.9fr]">
      <div className="relative hidden items-center justify-center overflow-hidden bg-gradient-to-br from-[#edf6ff] to-[#edf9f7] p-10 lg:flex">
        <div
          aria-hidden="true"
          className="pointer-events-none absolute -right-24 -top-32 h-[540px] w-[540px] rounded-full bg-accent-500/[0.08]"
        />
        <div
          aria-hidden="true"
          className="pointer-events-none absolute -bottom-36 -left-28 h-[430px] w-[430px] rounded-full bg-cyan-600/[0.06]"
        />
        <div className="relative z-10 flex min-h-[420px] w-full max-w-[520px] flex-col items-center justify-center gap-4 text-center">
          <div
            aria-hidden="true"
            className="pulse-glow grid h-40 w-40 place-items-center rounded-full bg-white/70 shadow-[0_10px_24px_rgba(37,99,235,0.16)]"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="#2563eb" strokeWidth="1.5" className="h-20 w-20">
              <rect x="4" y="9" width="16" height="10" rx="2" />
              <path d="M9 9V6a3 3 0 0 1 6 0v3" />
              <circle cx="9" cy="14" r="1.4" fill="#2563eb" />
              <circle cx="15" cy="14" r="1.4" fill="#0891b2" />
            </svg>
          </div>
          <div
            aria-label="Data pipeline"
            className="mt-2 flex flex-wrap items-center justify-center gap-2 text-[11px] font-bold uppercase tracking-wider text-ink-300"
          >
            {[
              ["Collect", "#2563eb"],
              ["Review", "#0891b2"],
              ["Dataset", "#059669"],
              ["Train", "#7c3aed"],
            ].map(([label, color]) => (
              <span
                key={label}
                className="inline-flex items-center gap-1.5 rounded-full border border-black/10 bg-white/80 px-2.5 py-1.5"
              >
                <span
                  aria-hidden="true"
                  className="h-2 w-2 rounded-full"
                  style={{ background: color, boxShadow: `0 0 0 5px ${color}1f` }}
                />
                {label}
              </span>
            ))}
          </div>
        </div>
      </div>

      <div className="flex flex-col justify-center bg-ink-900/40 px-6 py-16 sm:px-14">
        <div className="mx-auto w-full max-w-[420px]">
          <div className="mb-6 text-center">
            <div className="mx-auto mb-3 grid h-12 w-12 place-items-center rounded-xl bg-gradient-to-br from-accent-500 to-cyan-600 text-lg font-bold text-white shadow-[0_10px_24px_rgba(37,99,235,0.22)]">
              TC
            </div>
            <h1 className="text-2xl font-bold tracking-tight text-ink-100">TeleCollect</h1>
            <p className="mx-auto mt-2 max-w-[380px] text-sm text-ink-400">
              Teleoperation &amp; demonstration collection for imitation learning
            </p>
          </div>

          <Card className="slide-up">
            <form onSubmit={submit} className="space-y-4 p-5">
              <Field label="Username">
                <Input
                  value={username}
                  autoComplete="username"
                  onChange={(e) => setUsername(e.target.value)}
                  required
                />
              </Field>
              {mode === "signup" && (
                <Field label="Display name" hint="Optional — how your name appears to reviewers">
                  <Input
                    value={displayName}
                    autoComplete="name"
                    onChange={(e) => setDisplayName(e.target.value)}
                  />
                </Field>
              )}
              <Field label="Password" hint={mode === "signup" ? "At least 8 characters" : undefined}>
                <Input
                  type="password"
                  value={password}
                  autoComplete={mode === "signup" ? "new-password" : "current-password"}
                  minLength={mode === "signup" ? 8 : undefined}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                />
              </Field>
              {notice && <Alert tone="info">{notice}</Alert>}
              {error && <Alert>{error}</Alert>}
              <Button
                type="submit"
                variant="primary"
                className="w-full"
                disabled={busy || (mode === "signup" && password.length < 8)}
              >
                {busy
                  ? mode === "signup"
                    ? "Creating account…"
                    : "Signing in…"
                  : mode === "signup"
                    ? "Create account"
                    : "Sign in"}
              </Button>
              <button
                type="button"
                onClick={() => switchMode(mode === "signup" ? "signin" : "signup")}
                className="w-full text-center text-xs text-ink-400 underline-offset-2 hover:text-accent-500 hover:underline"
              >
                {mode === "signup" ? "Already have an account? Sign in" : "Create an account"}
              </button>
            </form>

            {SHOW_SEEDS && (
            <div className="border-t border-ink-700 px-5 pb-5 pt-4">
              <p className="mb-3 text-[11px] font-bold uppercase tracking-wider text-ink-400">
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
                    className="flex w-full items-center justify-between rounded-lg border border-ink-700 bg-ink-900 px-3.5 py-2.5 text-left text-xs transition-all hover:translate-x-0.5 hover:border-accent-500 hover:bg-accent-500/5"
                  >
                    <span className="font-bold text-accent-500">{name}</span>
                    <span className="text-ink-400">{description}</span>
                  </button>
                ))}
              </div>
            </div>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
