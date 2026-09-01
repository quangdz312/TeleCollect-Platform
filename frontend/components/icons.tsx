import type { ReactNode, SVGProps } from "react";

export type IconName = "overview" | "collect" | "review" | "datasets" | "training" | "evaluate" | "settings" | "users" | "logout" | "menu" | "robot" | "teleop" | "scripted" | "outcome" | "pending";

const paths: Record<IconName, ReactNode> = {
  overview: <><path d="M3 11.5 12 4l9 7.5"/><path d="M5.5 10.5V20h13v-9.5M9.5 20v-6h5v6"/></>,
  collect: <><path d="M12 3v12"/><path d="m8 7 4-4 4 4"/><path d="M5 13v6h14v-6"/></>,
  review: <><rect x="4" y="4" width="16" height="16" rx="3"/><path d="M8 9h8M8 13h5"/><path d="m15 16 1.5 1.5L20 14"/></>,
  datasets: <><ellipse cx="12" cy="5" rx="7" ry="3"/><path d="M5 5v6c0 1.7 3.1 3 7 3s7-1.3 7-3V5"/><path d="M5 11v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6"/></>,
  training: <><path d="M4 19V9M10 19V5M16 19v-7M22 19V3"/><path d="m3 15 6-5 6 2 7-7"/></>,
  evaluate: <><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/><path d="M12 1v3M12 20v3M1 12h3M20 12h3"/></>,
  settings: <><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1A1.7 1.7 0 0 0 9 4.6 1.7 1.7 0 0 0 10 3v-.2h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z"/></>,
  users: <><circle cx="9" cy="8" r="3"/><path d="M3.5 20v-2a5.5 5.5 0 0 1 11 0v2M16 5.5a3 3 0 0 1 0 5.8M18 14a5 5 0 0 1 2.5 4.3V20"/></>,
  logout: <><path d="M10 4H5v16h5M14 8l4 4-4 4M8 12h10"/></>,
  menu: <path d="M4 7h16M4 12h16M4 17h16"/>,
  robot: <><circle cx="12" cy="4" r="2"/><circle cx="5" cy="10" r="2"/><circle cx="19" cy="10" r="2"/><circle cx="7" cy="19" r="2"/><circle cx="17" cy="19" r="2"/><path d="m10.5 5.5-4 3M13.5 5.5l4 3M6 12l1 5M18 12l-1 5M7 10h10M8.5 18h7"/></>,
  teleop: <><rect x="3" y="7" width="18" height="11" rx="4"/><path d="M8 11v4M6 13h4M15.5 11.5h.01M18 14h.01M9 7l2-3h2l2 3"/></>,
  scripted: <><rect x="3" y="4" width="18" height="16" rx="3"/><path d="m8 9-2 2 2 2M16 9l2 2-2 2M10.5 16h3"/></>,
  outcome: <><circle cx="12" cy="12" r="9"/><path d="m8 12 2.5 2.5L16.5 8.5"/></>,
  pending: <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
};

export function Icon({ name, ...props }: { name: IconName } & SVGProps<SVGSVGElement>) {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>{paths[name]}</svg>;
}
