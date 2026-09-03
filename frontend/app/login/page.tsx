"use client";

import { useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { PandaHero } from "@/components/PandaHero";
import { Icon, type IconName } from "@/components/icons";
import { Alert, Button, Field, Input, Modal } from "@/components/ui";
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

// The one account the deployed site does advertise. Signing up puts an account
// in front of an administrator, so someone invited to look around would stop
// at the door. This one reads everything and writes nothing -- the server
// refuses every write it makes, so the password being public costs nothing.
const DEMO: [string, string] = ["demo", "telecollect"];

// Every figure below is measured, not estimated. docs/phase1/slide-can-sua.md
// records where each one comes from and how to re-derive it.
const STATS: [string, string][] = [
  ["1,535", "Episodes"],
  ["97%", "Success rate"],
  ["13", "Training runs"],
  ["17.5 ms", "Control p50"],
];

const PIPELINE: [IconName, string, string][] = [
  ["collect", "Collect", "Teleoperate with webcam hand tracking, mouse or keyboard — or run scripted policies across four tasks."],
  ["review", "Review", "Every episode is scored and labelled automatically. 53% clear the gate without a human ever opening them."],
  ["datasets", "Package", "Export to RoboMimic HDF5 and LeRobot v3 — the two formats the imitation-learning ecosystem actually reads."],
  ["training", "Train", "Behavioural cloning and BC-RNN, on the box or on a rented GPU, with the loss curve streaming live."],
  ["evaluate", "Evaluate", "Rollouts in simulation, not validation loss. Checkpoints are ranked by the success rate they actually score."],
];

const LINKS: [string, string, string][] = [
  ["Watch the demo", "The whole flow, from collection through evaluation", "https://www.youtube.com/watch?v=eWnuH2-vsIE"],
  ["Download the desktop app", "Windows installer — collect straight from your own machine", "https://drive.google.com/drive/folders/1RR_behNMTDSdjDQ_tcsquJ3DFx4yJ_Y6?usp=drive_link"],
];

export default function LoginPage() {
  const { login } = useAuth();
  const [open, setOpen] = useState(false);
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
    <div className="-mx-5 -my-6">
      {/* ---- header ---- */}
      <header className="sticky top-0 z-30 border-b border-ink-700/60 bg-ink-950/80 backdrop-blur-md">
        <div className="mx-auto flex h-16 max-w-[1180px] items-center justify-between px-5 sm:px-8">
          <div className="flex items-center gap-2.5">
            <span className="grid h-9 w-9 place-items-center rounded-xl bg-gradient-to-br from-accent-500 to-cyan-600 text-sm font-bold text-white shadow-[0_8px_18px_rgba(37,99,235,0.22)]">
              TC
            </span>
            <span className="font-heading text-lg font-bold tracking-tight text-ink-100">TeleCollect</span>
          </div>
          <div className="flex items-center gap-2">
            <a
              href="https://www.youtube.com/watch?v=eWnuH2-vsIE"
              target="_blank"
              rel="noreferrer"
              className="hidden text-sm font-semibold text-ink-400 transition-colors hover:text-accent-500 sm:block"
            >
              Watch the demo
            </a>
            <Button variant="primary" onClick={() => setOpen(true)}>
              Sign in
            </Button>
          </div>
        </div>
      </header>

      {/* ---- hero ---- */}
      <section className="relative overflow-hidden bg-gradient-to-br from-[#edf6ff] to-[#edf9f7]">
        <div
          aria-hidden="true"
          className="pointer-events-none absolute -right-24 -top-32 h-[540px] w-[540px] rounded-full bg-accent-500/[0.08]"
        />
        <div
          aria-hidden="true"
          className="pointer-events-none absolute -bottom-36 -left-28 h-[430px] w-[430px] rounded-full bg-cyan-600/[0.06]"
        />
        <div className="relative mx-auto grid max-w-[1180px] items-center gap-6 px-5 py-12 sm:px-8 lg:grid-cols-[0.92fr_1.08fr] lg:py-16">
          <div>
            <span className="inline-flex items-center gap-2 rounded-full border border-accent-500/20 bg-white/70 px-3 py-1.5 text-[11px] font-bold uppercase tracking-[0.14em] text-accent-600">
              <span aria-hidden="true" className="pulse-subtle h-1.5 w-1.5 rounded-full bg-accent-500" />
              Demonstration data · Imitation learning
            </span>
            <h1 className="mt-5 font-heading text-[clamp(38px,5.2vw,60px)] font-bold leading-[1.02] tracking-[-0.035em] text-ink-100">
              Collect once.
              <span className="block bg-gradient-to-r from-accent-500 to-cyan-600 bg-clip-text text-transparent">
                Train anywhere.
              </span>
            </h1>
            <p className="mt-5 max-w-[520px] text-[17px] leading-relaxed text-ink-300">
              A teleoperation and demonstration-collection platform for imitation learning.
              Record with a webcam, a mouse or a scripted policy — then score, package, train
              and evaluate against rollouts in simulation.
            </p>
            <div className="mt-7 flex flex-col gap-3 sm:flex-row sm:flex-wrap">
              <Button variant="primary" className="min-h-11 px-5" onClick={() => setOpen(true)}>
                Sign in
              </Button>
              <Button
                variant="subtle"
                className="min-h-11 px-5"
                onClick={() => {
                  setUsername(DEMO[0]);
                  setPassword(DEMO[1]);
                  switchMode("signin");
                  setOpen(true);
                }}
              >
                Explore with the demo account
              </Button>
            </div>
            <dl className="mt-9 grid max-w-[540px] grid-cols-2 gap-x-6 gap-y-5 sm:grid-cols-4">
              {STATS.map(([value, label]) => (
                <div key={label}>
                  <dt className="sr-only">{label}</dt>
                  <dd className="tabular font-heading text-[26px] font-bold leading-none tracking-tight text-ink-100">
                    {value}
                  </dd>
                  <dd className="mt-1.5 text-[10px] font-bold uppercase leading-tight tracking-[0.1em] text-ink-400">
                    {label}
                  </dd>
                </div>
              ))}
            </dl>
          </div>

          <div className="h-[380px] lg:h-[520px]">
            <PandaHero />
          </div>
        </div>
      </section>

      {/* ---- pipeline ---- */}
      <section className="mx-auto max-w-[1180px] px-5 py-16 sm:px-8">
        <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-accent-500">Pipeline</p>
        <h2 className="mt-2 max-w-[640px] font-heading text-[30px] font-bold tracking-tight text-ink-100">
          From the arm to an evaluated policy, in one system
        </h2>
        <div className="mt-9 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          {PIPELINE.map(([icon, title, body], index) => (
            <div
              key={title}
              className="rounded-xl border border-ink-700 bg-ink-900 p-4 transition-colors hover:border-accent-500/40"
            >
              <div className="flex items-center gap-2.5">
                <span className="grid h-8 w-8 place-items-center rounded-lg bg-accent-500/10 text-accent-500">
                  <Icon name={icon} className="h-4 w-4" />
                </span>
                <span className="tabular text-[11px] font-bold text-ink-400">
                  {String(index + 1).padStart(2, "0")}
                </span>
              </div>
              <h3 className="mt-3 font-heading text-[15px] font-bold text-ink-100">{title}</h3>
              <p className="mt-1.5 text-[13px] leading-relaxed text-ink-400">{body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ---- links + footer ---- */}
      <section className="mx-auto max-w-[1180px] px-5 pb-12 sm:px-8">
        <div className="grid gap-3 sm:grid-cols-2">
          {LINKS.map(([title, body, href]) => (
            <a
              key={title}
              href={href}
              target="_blank"
              rel="noreferrer"
              className="group flex items-center justify-between gap-4 rounded-xl border border-ink-700 bg-ink-900 px-5 py-4 transition-all hover:border-accent-500 hover:bg-accent-500/[0.03]"
            >
              <div>
                <p className="font-heading text-[15px] font-bold text-ink-100">{title}</p>
                <p className="mt-0.5 text-[13px] text-ink-400">{body}</p>
              </div>
              <span
                aria-hidden="true"
                className="shrink-0 text-lg text-ink-400 transition-transform group-hover:translate-x-0.5 group-hover:text-accent-500"
              >
                →
              </span>
            </a>
          ))}
        </div>
        <p className="mt-10 border-t border-ink-700 pt-6 text-center text-[13px] text-ink-400">
          TeleCollect — Team NEURA · VinUniversity AI in Action
        </p>
      </section>

      {/* ---- login modal ---- */}
      {open && (
        <Modal
          title={mode === "signup" ? "Create an account" : "Sign in"}
          subtitle={
            mode === "signup"
              ? "An administrator has to approve the account before you can sign in"
              : "Teleoperation & demonstration collection for imitation learning"
          }
          width="max-w-[420px]"
          onClose={() => setOpen(false)}
        >
          <form onSubmit={submit} className="space-y-4">
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

          <div className="mt-4 border-t border-ink-700 pt-4">
            <p className="mb-3 text-[11px] font-bold uppercase tracking-wider text-ink-400">
              {SHOW_SEEDS ? "Development accounts" : "Try it out"}
            </p>
            <div className="space-y-1.5">
              {(SHOW_SEEDS ? SEEDS : [[...DEMO, "Browse everything, read-only"] as [string, string, string]]).map(
                ([name, pass, description]) => (
                  <button
                    key={name}
                    type="button"
                    onClick={() => {
                      setUsername(name);
                      setPassword(pass);
                    }}
                    className="flex w-full items-center justify-between gap-3 rounded-lg border border-ink-700 bg-ink-900 px-3.5 py-2.5 text-left text-xs transition-all hover:translate-x-0.5 hover:border-accent-500 hover:bg-accent-500/5"
                  >
                    <span className="font-bold text-accent-500">{name}</span>
                    <span className="text-ink-400">{description}</span>
                  </button>
                ),
              )}
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}
