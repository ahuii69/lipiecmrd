import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { Inter } from "next/font/google";
import { headers } from "next/headers";

import { AppQueryProvider } from "@/lib/query/query-provider";
import { StoreRehydrator } from "@/components/layout/store-rehydrator";
import "@/styles/globals.css";

const inter = Inter({
    subsets: ["latin", "latin-ext"],
    display: "swap",
    variable: "--font-sans",
});

/**
 * CSP nonces are generated per request in middleware and parsed by Next.js from
 * the `Content-Security-Policy` request header during SSR. Static prerender
 * skips that path, leaving framework scripts without `nonce` — blocked by
 * `strict-dynamic`.
 */
export const dynamic = "force-dynamic";

export const metadata: Metadata = {
    title: "AI-Hub Morda",
    description:
        "Nowoczesny frontend AI-Hub: streaming chat, sesje, upload, STT i Memory V2.",
};

export const viewport: Viewport = {
    width: "device-width",
    initialScale: 1,
    maximumScale: 1,
    viewportFit: "cover",
    themeColor: "#0a0a0a",
};

export default async function RootLayout({
    children,
}: {
    children: ReactNode;
}) {
    // Touch request headers so this tree renders dynamically with middleware CSP.
    await headers();

    return (
        <html lang="pl" className={`dark ${inter.variable}`}>
            <body className="min-h-[100dvh] font-sans antialiased">
                <AppQueryProvider>
                    <StoreRehydrator>{children}</StoreRehydrator>
                </AppQueryProvider>
            </body>
        </html>
    );
}
