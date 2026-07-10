"use client";

import { useEffect, useState } from "react";

/**
 * Hook to prevent hydration mismatches when using client-side storage.
 * Returns false during SSR/hydration to ensure server and client consistency.
 */
export function useIsHydrated() {
    const [isHydrated, setIsHydrated] = useState(false);

    useEffect(() => {
        setIsHydrated(true);
    }, []);

    return isHydrated;
}

/**
 * Component that only renders its children after hydration is complete.
 * Use this to wrap components that depend on client-side state (localStorage, etc.)
 */
export function HydrationGuard({ children }: { children: React.ReactNode }) {
    const isHydrated = useIsHydrated();

    if (!isHydrated) {
        return <div className="min-h-screen bg-background opacity-0">Loading...</div>;
    }

    return <>{children}</>;
}
