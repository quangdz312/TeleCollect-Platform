"use client";

import { useEffect, useRef, useState } from "react";

type SceneModule = {
  mountPanda: (
    canvas: HTMLCanvasElement,
    handlers: { onReady?: () => void; onError?: (error: unknown) => void },
  ) => () => void;
};

/**
 * The Franka Panda pick-and-place loop on the login screen.
 *
 * Three.js and the ten GLB meshes total 3.6 MB. They are served from
 * /public/panda and pulled in with a runtime `import()` of a plain URL, so
 * `next build` never sees them: the login form paints and is usable before any
 * of it arrives, and someone who only wants to sign in never waits on it.
 *
 * Failure is silent by design. This is decoration on a login page, so a WebGL
 * context that will not allocate or a mesh that will not load leaves the
 * static fallback in place rather than an error in front of someone trying to
 * get in.
 */
export function PandaHero() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    let dispose: (() => void) | undefined;
    let cancelled = false;

    void (async () => {
      try {
        // The URL is built at runtime so neither tsc nor the bundler treats it
        // as a module to resolve -- the file lives in /public and is fetched by
        // the browser, and a literal here fails typecheck with TS2307.
        const url = `${window.location.origin}/panda/scene.js`;
        const module = (await import(/* webpackIgnore: true */ url)) as SceneModule;
        if (cancelled) return;
        dispose = module.mountPanda(canvas, {
          onReady: () => !cancelled && setReady(true),
          onError: () => !cancelled && setFailed(true),
        });
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();

    return () => {
      cancelled = true;
      dispose?.();
    };
  }, []);

  return (
    <div className="relative h-full w-full">
      <canvas
        ref={canvasRef}
        aria-label="Franka Panda arm picking a block from A to B and back"
        className={`h-full w-full transition-opacity duration-700 ${
          ready ? "opacity-100" : "opacity-0"
        }`}
        style={{ filter: "drop-shadow(0 24px 22px rgb(30 58 138 / 0.12))" }}
      />
      {!ready && !failed && (
        <p className="pointer-events-none absolute inset-0 grid place-items-center text-[11px] font-bold uppercase tracking-[0.14em] text-accent-500">
          <span className="pulse-subtle">Loading 7-axis arm…</span>
        </p>
      )}
    </div>
  );
}
