"use client";

import { useEffect } from "react";

import { useCockpitStore } from "@/lib/store/cockpit-store";

/**
 * Zustand `persist` + SSR: bez tego pierwszy render klienta może od razu wczytać
 * localStorage i różnić się od HTML z serwera → hydration mismatch.
 * `skipHydration` w store + `rehydrate()` tutaj po montażu wyrównuje cykl.
 */
export function StoreRehydrator({ children }: { children: React.ReactNode }) {
    useEffect(() => {
        void useCockpitStore.persist.rehydrate();
    }, []);

    return <>{children}</>;
}
