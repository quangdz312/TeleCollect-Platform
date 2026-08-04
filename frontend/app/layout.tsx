import type { Metadata } from "next";
import "./globals.css";
import { AuthProvider } from "@/components/AuthProvider";
import { Nav } from "@/components/Nav";

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
          <Nav />
          <main className="mx-auto w-full max-w-[1500px] px-5 py-6">{children}</main>
        </AuthProvider>
      </body>
    </html>
  );
}
