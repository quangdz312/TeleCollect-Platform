/**
 * Which half of the product this build is.
 *
 * One frontend serves both: the packaged desktop app builds this very
 * directory, and the deployed web build is the same code. They differ in one
 * way — the app collects data, the web only receives data that was already
 * collected — so the collection screens are gated rather than duplicated.
 *
 * Mirrors `settings.collection_enabled` on the backend, which decides whether
 * `/teleop` and `/demos` are mounted at all. Both default to on, so a developer
 * running the repo gets everything; the deployed web sets it off.
 *
 * Read at module scope on purpose: `NEXT_PUBLIC_*` is inlined at build time, so
 * this is a constant in the bundle and the dead branches drop out.
 */
export const COLLECTION_ENABLED =
  process.env.NEXT_PUBLIC_COLLECTION_ENABLED !== "0";
