"use client";

import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

export type ChatDrawerTab = "pamiec" | "zrodla" | "szczegoly";

interface ChatUiState {
    sidebarCollapsed: boolean;
    sidebarMobileOpen: boolean;
    drawerOpen: boolean;
    drawerTab: ChatDrawerTab;
    searchQuery: string;
    pinnedSessionIds: string[];
    setSidebarCollapsed: (v: boolean) => void;
    toggleSidebarCollapsed: () => void;
    setSidebarMobileOpen: (v: boolean) => void;
    setDrawerOpen: (v: boolean) => void;
    setDrawerTab: (tab: ChatDrawerTab) => void;
    openDrawer: (tab: ChatDrawerTab) => void;
    setSearchQuery: (q: string) => void;
    togglePinSession: (sessionId: string) => void;
}

export const useChatUiStore = create<ChatUiState>()(
    persist(
        (set, get) => ({
            sidebarCollapsed: false,
            sidebarMobileOpen: false,
            drawerOpen: false,
            drawerTab: "pamiec",
            searchQuery: "",
            pinnedSessionIds: [],
            setSidebarCollapsed: (v) => set({ sidebarCollapsed: v }),
            toggleSidebarCollapsed: () =>
                set({ sidebarCollapsed: !get().sidebarCollapsed }),
            setSidebarMobileOpen: (v) => set({ sidebarMobileOpen: v }),
            setDrawerOpen: (v) => set({ drawerOpen: v }),
            setDrawerTab: (tab) => set({ drawerTab: tab, drawerOpen: true }),
            openDrawer: (tab) => set({ drawerTab: tab, drawerOpen: true }),
            setSearchQuery: (q) => set({ searchQuery: q }),
            togglePinSession: (sessionId) => {
                const pinned = get().pinnedSessionIds;
                set({
                    pinnedSessionIds: pinned.includes(sessionId)
                        ? pinned.filter((id) => id !== sessionId)
                        : [...pinned, sessionId],
                });
            },
        }),
        {
            name: "aihub-chat-ui-v3",
            storage: createJSONStorage(() => localStorage),
            partialize: (s) => ({
                sidebarCollapsed: s.sidebarCollapsed,
                pinnedSessionIds: s.pinnedSessionIds,
            }),
        },
    ),
);
