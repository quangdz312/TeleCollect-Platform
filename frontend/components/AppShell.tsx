"use client";

/**
 * Chrome around every page: the left bar, and the content column beside it.
 *
 * The left bar is one fixed 288px column that never moves or resizes. What it
 * *contains* can change: Review swaps its own rail — review status, then
 * filters — in once a batch is open, because inside a batch that rail is the
 * navigation and the app links are not. Swapping the contents rather than
 * hiding the bar is the point: the column keeps the same width and position
 * across the transition, so nothing under it reflows.
 *
 * The page supplies that rail rather than the shell deriving it from the path,
 * because whether Review has one depends on the `collection_batch_id` query
 * parameter, and that page reads the query string directly rather than through
 * `useSearchParams`.
 */

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { useAuth } from "@/components/AuthProvider";
import { Nav } from "@/components/Nav";

/** Width of the left bar, and the matching gutter the content column leaves. */
export const LEFT_BAR_WIDTH = "15rem";

const RailContext = createContext<((rail: ReactNode | null) => void) | null>(null);

/** Nhãn và hành động của nút quay lại mà `Nav` vẽ ở đầu thanh mượn. */
export type RailBack = { label: string; onClick?: () => void; href?: string };

const RailBackContext = createContext<((back: RailBack | null) => void) | null>(null);

/**
 * Khai báo nút quay lại cho thanh trái đang mượn.
 *
 * Nút do `Nav` vẽ chứ không do trang tự đặt: chỉ có một chỗ vẽ thì không trang
 * nào đặt lệch đi được. Trước đây Review vẽ ở đầu vùng nội dung còn Training vẽ
 * trên thanh, nên đổi trang là phải tìm lại nút.
 */
export function useRailBack(back: RailBack | null): void {
  const setBack = useContext(RailBackContext);
  useEffect(() => {
    if (!setBack) return;
    setBack(back);
    return () => setBack(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [back?.label, back?.href, setBack]);
}

/**
 * Put `rail` in the left bar in place of the app links, for as long as the
 * calling component is mounted. Restores the links on unmount, so a page
 * cannot leave the bar in a borrowed state.
 */
export function useLeftBar(rail: ReactNode | null): void {
  const setRail = useContext(RailContext);
  useEffect(() => {
    if (!setRail) return;
    setRail(rail);
    return () => setRail(null);
  }, [rail, setRail]);
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const [rail, setRail] = useState<ReactNode | null>(null);
  const [railBack, setRailBack] = useState<RailBack | null>(null);
  // `Nav` renders nothing until someone is signed in, so the gutter it reserves
  // has to disappear with it — otherwise the login screen sits 288px right of
  // a bar that is not there.
  const { user } = useAuth();

  return (
    <RailContext.Provider value={setRail}>
      <RailBackContext.Provider value={setRailBack}>
      <Nav rail={rail} railBack={railBack} />
      {/*
        The left bar is fixed, so the content column reserves its width rather
        than sliding underneath it.

        Padding belongs here, not on each page: every page relies on it, and
        `/login` cancels it with a negative margin to go full-bleed. Moving it
        into the pages left the rest flush against the window edge.
      */}
      <main className={user ? "app-main min-w-0 w-full py-6 lg:pl-72 xl:py-7" : "w-full px-5 py-6"}>
        <div className={user ? "mx-auto w-full max-w-[1600px] px-5 xl:px-8" : "mx-auto w-full max-w-[1500px]"}>{children}</div>
      </main>
      </RailBackContext.Provider>
    </RailContext.Provider>
  );
}
