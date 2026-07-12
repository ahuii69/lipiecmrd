"use client";

import { useEffect, useState } from "react";

type BackendStatus = "checking" | "online" | "offline";

export function LoginBackendStatus() {
    const [status, setStatus] = useState<BackendStatus>("checking");

    useEffect(() => {
        let cancelled = false;
        void (async () => {
            try {
                const response = await fetch("/api/aihub/system/ping", {
                    cache: "no-store",
                    headers: { accept: "application/json" },
                });
                if (!cancelled) {
                    setStatus(response.ok ? "online" : "offline");
                }
            } catch {
                if (!cancelled) {
                    setStatus("offline");
                }
            }
        })();
        return () => {
            cancelled = true;
        };
    }, []);

    const label =
        status === "online"
            ? "Backend połączony"
            : status === "offline"
              ? "Backend niedostępny"
              : "Sprawdzam połączenie…";

    return (
        <p className="flex items-center justify-center gap-2 text-center text-xs text-neutral-500">
            <span
                className={
                    status === "online"
                        ? "h-1.5 w-1.5 rounded-full bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.55)]"
                        : status === "offline"
                          ? "h-1.5 w-1.5 rounded-full bg-red-400/90"
                          : "h-1.5 w-1.5 animate-pulse rounded-full bg-neutral-500"
                }
                aria-hidden
            />
            <span>{label}</span>
        </p>
    );
}
