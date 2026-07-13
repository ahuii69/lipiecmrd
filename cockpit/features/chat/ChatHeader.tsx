"use client";

import {
    Brain,
    Menu,
    MoreVertical,
    Trash2,
    Zap,
} from "lucide-react";
import { useState } from "react";

import { ChatConnectionStatus } from "@/features/chat/chat-connection-status";
import { useChatUiStore } from "@/features/chat/chat-ui-store";
import { useCockpitStore } from "@/lib/store/cockpit-store";

export function ChatHeader({
    title,
    apiKeyOverride,
    insightDisabled,
    onClearSession,
}: {
    title: string;
    apiKeyOverride?: string;
    insightDisabled?: boolean;
    onClearSession?: () => void;
}) {
    const { setSidebarMobileOpen, openDrawer } = useChatUiStore();
    const { clearSessionMessages, activeSessionId } = useCockpitStore();
    const [menuOpen, setMenuOpen] = useState(false);

    return (
        <header className="chat-header flex h-14 shrink-0 items-center gap-3 border-b border-[var(--chat-border)] px-4 max-md:h-[58px] max-md:sticky max-md:top-0 max-md:z-20 max-md:bg-[var(--chat-bg)]">
            <button
                type="button"
                className="flex h-11 w-11 items-center justify-center text-[var(--chat-text-muted)] hover:text-[var(--chat-text)] md:hidden"
                data-testid="user-sidebar-toggle"
                onClick={() => setSidebarMobileOpen(true)}
                aria-label="Menu"
            >
                <Menu className="h-5 w-5" />
            </button>

            <h1
                className="min-w-0 flex-1 truncate text-sm font-medium text-[var(--chat-text)] sm:text-base"
                data-testid="user-header-title"
            >
                {title}
            </h1>

            <ChatConnectionStatus apiKeyOverride={apiKeyOverride} />

            <button
                type="button"
                className="flex h-11 w-11 items-center justify-center text-[var(--chat-text-muted)] hover:text-[var(--chat-text)]"
                onClick={() => openDrawer("pamiec")}
                disabled={insightDisabled}
                data-testid="open-memory-drawer"
                aria-label="Pamięć"
            >
                <Brain className="h-4 w-4" />
            </button>

            <button
                type="button"
                className="flex h-11 w-11 items-center justify-center text-[var(--chat-text-muted)] hover:text-[var(--chat-text)]"
                onClick={() => openDrawer("zrodla")}
                disabled={insightDisabled}
                aria-label="Źródła"
            >
                <Zap className="h-4 w-4" />
            </button>

            <div className="relative">
                <button
                    type="button"
                    className="flex h-11 w-11 items-center justify-center text-[var(--chat-text-muted)] hover:text-[var(--chat-text)]"
                    onClick={() => setMenuOpen((v) => !v)}
                    aria-label="Menu rozmowy"
                >
                    <MoreVertical className="h-4 w-4" />
                </button>
                {menuOpen ? (
                    <div className="absolute right-0 top-full z-30 min-w-[10rem] border border-[var(--chat-border)] bg-[#15181D] py-1 shadow-lg">
                        <button
                            type="button"
                            className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-[var(--chat-text)] hover:bg-white/[0.06]"
                            onClick={() => {
                                if (activeSessionId) {
                                    clearSessionMessages(activeSessionId);
                                    onClearSession?.();
                                }
                                setMenuOpen(false);
                            }}
                        >
                            <Trash2 className="h-3.5 w-3.5" />
                            Wyczyść rozmowę
                        </button>
                    </div>
                ) : null}
            </div>
        </header>
    );
}
