"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { cx } from "@/components/ui";

type ToastTone = "info" | "ok" | "bad";

interface ToastItem {
  id: number;
  tone: ToastTone;
  message: string;
}

const DURATION_MS = 4000;

const ToastContext = createContext<((message: string, tone?: ToastTone) => void) | null>(null);

export function useToast() {
  const push = useContext(ToastContext);
  if (!push) throw new Error("useToast must be used inside ToastProvider");
  return push;
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const nextId = useRef(0);
  const timers = useRef<Record<number, ReturnType<typeof setTimeout>>>({});

  const dismiss = useCallback((id: number) => {
    setItems((prev) => prev.filter((it) => it.id !== id));
    clearTimeout(timers.current[id]);
    delete timers.current[id];
  }, []);

  const push = useCallback(
    (message: string, tone: ToastTone = "info") => {
      const id = nextId.current++;
      setItems((prev) => [...prev, { id, tone, message }]);
      timers.current[id] = setTimeout(() => dismiss(id), DURATION_MS);
    },
    [dismiss],
  );

  useEffect(
    () => () => {
      Object.values(timers.current).forEach(clearTimeout);
      timers.current = {};
    },
    [],
  );

  const tones: Record<ToastTone, string> = {
    info: "border-accent-500/40 bg-ink-900 text-accent-400",
    ok: "border-ok-600/40 bg-ink-900 text-ok-400",
    bad: "border-bad-600/40 bg-ink-900 text-bad-400",
  };

  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-full max-w-sm flex-col gap-2">
        {items.map((item) => (
          <div
            key={item.id}
            role={item.tone === "bad" ? "alert" : "status"}
            aria-live={item.tone === "bad" ? "assertive" : "polite"}
            className={cx(
              "fade-in pointer-events-auto flex items-start gap-2 rounded-lg border px-3 py-2 text-sm shadow-lg",
              tones[item.tone],
            )}
          >
            <span className="flex-1">{item.message}</span>
            <button
              type="button"
              aria-label="Dismiss notification"
              onClick={() => dismiss(item.id)}
              className="text-ink-400 hover:text-ink-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/60"
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
