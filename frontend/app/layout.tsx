import type { Metadata } from "next";
import "./globals.css";
import { AuthProvider } from "@/components/AuthProvider";
import { Nav } from "@/components/Nav";
import { ToastProvider } from "@/components/Toast";

export const metadata: Metadata = {
  title: "TeleCollect",
  description:
    "Teleoperation and demonstration-collection platform for imitation learning",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">
        <AuthProvider>
          <ToastProvider>
            <Nav />
            <main className="mx-auto w-full max-w-[1500px] px-5 py-6">{children}</main>
          </ToastProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
