import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { AppQueryProvider } from "@/lib/query/query-provider";
import "@/styles/globals.css";

export const metadata: Metadata = { title: "AI-Hub Morda", description: "Nowoczesny frontend AI-Hub: streaming chat, sesje, upload, STT i Memory V2." };
export const viewport: Viewport = { width: "device-width", initialScale: 1, maximumScale: 1, viewportFit: "cover", themeColor: "#0a0a0a" };
export default function RootLayout({ children }: { children: ReactNode }) { return <html lang="pl" className="dark"><body><AppQueryProvider>{children}</AppQueryProvider></body></html>; }
