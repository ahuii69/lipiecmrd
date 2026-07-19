"use client";

import {
    Archive,
    ArchiveRestore,
    Brain,
    FileText,
    LogOut,
    MoreHorizontal,
    PanelLeftClose,
    PanelLeftOpen,
    Pin,
    Plus,
    Search,
    Settings,
    Trash2,
} from "lucide-react";
import { useMemo, useState } from "react";

import { groupSessionsByDate } from "@/features/chat/chat-session-groups";
import { useChatUiStore } from "@/features/chat/chat-ui-store";
import { Input } from "@/components/ui/input";
import { apiClient } from "@/lib/api/client";
import { chatSessionRuntime } from "@/lib/chat/chat-session-runtime";
import { filterSessionsForSidebar } from "@/lib/chat/session-list-filter";
import { lastUserVisiblePreview } from "@/lib/chat/session-title";
import { publishSessionsSync } from "@/lib/chat/sessions-sync";
import { logoutAndRedirect } from "@/lib/hooks/use-auth-principal";
import { useCockpitStore } from "@/lib/store/cockpit-store";
import { cn } from "@/lib/utils";

export function ChatSidebar({
    username,
    sessionsSyncing = false,
    onNewChat,
    onSelectSession,
    onOpenMemory,
    onOpenFiles,
}: {
    username?: string;
    sessionsSyncing?: boolean;
    onNewChat: () => void;
    onSelectSession: (sessionId: string) => void;
    onOpenMemory: () => void;
    onOpenFiles: () => void;
}) {
    const sessions = useCockpitStore((s) => s.sessions);
    const activeSessionId = useCockpitStore((s) => s.activeSessionId);
    const deleteSession = useCockpitStore((s) => s.deleteSession);
    const updateSessionTitle = useCockpitStore((s) => s.updateSessionTitle);
    const authUserId = useCockpitStore((s) => s.authUserId);
    const apiKeyOverride = useCockpitStore((s) => s.apiKeyOverride);

    const {
        sidebarCollapsed,
        sidebarMobileOpen,
        setSidebarMobileOpen,
        toggleSidebarCollapsed,
        searchQuery,
        setSearchQuery,
        pinnedSessionIds,
        togglePinSession,
        archivedSessionIds,
        archiveSession,
        unarchiveSession,
    } = useChatUiStore();
    const [menuSessionId, setMenuSessionId] = useState<string | null>(null);
    const [renameId, setRenameId] = useState<string | null>(null);
    const [renameDraft, setRenameDraft] = useState("");
    const [showArchived, setShowArchived] = useState(false);

    const filtered = useMemo(() => {
        return filterSessionsForSidebar({
            sessions: [...sessions],
            archivedSessionIds,
            showArchived,
            searchQuery,
            previewOf: (s) => lastUserVisiblePreview(s.messages),
        }).sort((a, b) => b.updatedAt - a.updatedAt);
    }, [sessions, searchQuery, archivedSessionIds, showArchived]);

    const pinned = filtered.filter((s) => pinnedSessionIds.includes(s.id));
    const unpinned = filtered.filter((s) => !pinnedSessionIds.includes(s.id));
    const groups = groupSessionsByDate(unpinned);
    const expanded = sidebarMobileOpen || !sidebarCollapsed;

    const persistArchive = async (sessionId: string, archived: boolean) => {
        if (archived) {
            archiveSession(sessionId);
        } else {
            unarchiveSession(sessionId);
        }
        const uid =
            authUserId ||
            sessions.find((s) => s.id === sessionId)?.userId ||
            "";
        if (!uid || uid === "default") return;
        try {
            if (archived) {
                await apiClient.archiveSession(
                    { user_id: uid, session_id: sessionId },
                    apiKeyOverride || undefined,
                );
            } else {
                await apiClient.unarchiveSession(
                    { user_id: uid, session_id: sessionId },
                    apiKeyOverride || undefined,
                );
            }
        } catch (err) {
            console.error("[sidebar] archive sync failed", err);
            return;
        }
        publishSessionsSync({
            type: "archive-changed",
            userId: uid,
            sessionId,
            archived,
        });
        publishSessionsSync({ type: "sessions-changed", userId: uid });
    };

    const persistRename = async (sessionId: string, title: string) => {
        const prev = sessions.find((s) => s.id === sessionId)?.title;
        updateSessionTitle(sessionId, title);
        const uid =
            authUserId ||
            sessions.find((s) => s.id === sessionId)?.userId ||
            "";
        if (!uid || uid === "default") return;
        try {
            await apiClient.renameSession(
                { user_id: uid, session_id: sessionId, title },
                apiKeyOverride || undefined,
            );
        } catch (err) {
            console.error("[sidebar] rename failed", err);
            if (prev) updateSessionTitle(sessionId, prev);
        }
    };

    const persistDelete = async (sessionId: string) => {
        if (sessionId === activeSessionId) {
            chatSessionRuntime.abortAll();
        }
        const snapshot = sessions.find((s) => s.id === sessionId);
        deleteSession(sessionId);
        unarchiveSession(sessionId);
        const uid = authUserId || snapshot?.userId || "";
        if (!uid || uid === "default") return;
        try {
            await apiClient.deleteSession(
                { user_id: uid, session_id: sessionId },
                apiKeyOverride || undefined,
            );
            publishSessionsSync({ type: "sessions-changed", userId: uid });
        } catch (err) {
            console.error("[sidebar] delete failed", err);
        }
    };

    return (
        <>
            {sidebarMobileOpen ? (
                <button
                    type="button"
                    className="fixed inset-0 z-30 bg-black/60 md:hidden"
                    aria-label="Zamknij menu"
                    onClick={() => setSidebarMobileOpen(false)}
                />
            ) : null}

            <aside
                data-testid="user-sidebar"
                data-sidebar-state={expanded ? "open" : "closed"}
                className={cn(
                    "chat-sidebar z-40 flex flex-col border-r border-[var(--chat-border)] bg-[#0D0F12] pt-[env(safe-area-inset-top)] transition-[width,transform] duration-200",
                    "fixed inset-y-0 left-0 md:static",
                    sidebarCollapsed && !sidebarMobileOpen
                        ? "md:w-[68px]"
                        : "md:w-[260px]",
                    sidebarMobileOpen
                        ? "w-[min(86vw,320px)] translate-x-0"
                        : "w-[min(86vw,320px)] -translate-x-full md:translate-x-0",
                )}
            >
                <div
                    className={cn(
                        "flex h-14 items-center gap-2 px-3",
                        sidebarCollapsed && !sidebarMobileOpen && "md:justify-center",
                    )}
                >
                    {expanded ? (
                        <span className="text-sm font-semibold text-[var(--chat-text)]">
                            AI-Hub
                        </span>
                    ) : (
                        <span className="hidden text-xs font-bold text-[var(--chat-accent)] md:inline">
                            AI
                        </span>
                    )}
                    <button
                        type="button"
                        className="ml-auto hidden text-[var(--chat-text-muted)] hover:text-[var(--chat-text)] md:inline-flex"
                        onClick={toggleSidebarCollapsed}
                        aria-label={sidebarCollapsed ? "Rozwiń" : "Zwiń"}
                    >
                        {sidebarCollapsed ? (
                            <PanelLeftOpen className="h-4 w-4" />
                        ) : (
                            <PanelLeftClose className="h-4 w-4" />
                        )}
                    </button>
                </div>

                <div className={cn("px-3 pb-2", !expanded && "md:px-2")}>
                    <button
                        type="button"
                        className={cn(
                            "flex h-[38px] w-full items-center gap-2 bg-white/[0.06] px-3 text-sm font-medium text-[var(--chat-text)] hover:bg-white/[0.08]",
                            !expanded && "md:justify-center md:px-0",
                        )}
                        onClick={onNewChat}
                        data-testid="user-new-session"
                    >
                        <Plus className="h-4 w-4 shrink-0" />
                        {expanded ? <span>Nowa rozmowa</span> : null}
                    </button>
                </div>

                {expanded ? (
                    <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-2 [scrollbar-width:thin]">
                        <div className="relative mb-3">
                            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--chat-text-muted)]" />
                            <Input
                                value={searchQuery}
                                onChange={(e) => setSearchQuery(e.target.value)}
                                placeholder="Szukaj rozmów…"
                                className="h-9 border-[var(--chat-border)] bg-white/[0.04] pl-8 text-sm"
                            />
                        </div>

                        <div className="mb-3 flex gap-1">
                            <button
                                type="button"
                                className={cn(
                                    "flex-1 rounded px-2 py-1 text-[11px]",
                                    !showArchived
                                        ? "bg-white/[0.08] text-[var(--chat-text)]"
                                        : "text-[var(--chat-text-muted)]",
                                )}
                                onClick={() => setShowArchived(false)}
                            >
                                Aktywne
                            </button>
                            <button
                                type="button"
                                className={cn(
                                    "flex-1 rounded px-2 py-1 text-[11px]",
                                    showArchived
                                        ? "bg-white/[0.08] text-[var(--chat-text)]"
                                        : "text-[var(--chat-text-muted)]",
                                )}
                                onClick={() => setShowArchived(true)}
                            >
                                Archiwum
                            </button>
                        </div>

                        {sessionsSyncing && filtered.length === 0 ? (
                            <div className="space-y-2 px-1" aria-busy>
                                {[0, 1, 2, 3].map((i) => (
                                    <div
                                        key={i}
                                        className="h-9 animate-pulse rounded bg-white/[0.05]"
                                    />
                                ))}
                            </div>
                        ) : null}

                        {pinned.length > 0 && !showArchived ? (
                            <SessionGroup label="Przypięte">
                                {pinned.map((s) => (
                                    <SessionRow
                                        key={s.id}
                                        session={s}
                                        active={s.id === activeSessionId}
                                        menuOpen={menuSessionId === s.id}
                                        renaming={renameId === s.id}
                                        renameDraft={renameDraft}
                                        onRenameDraft={setRenameDraft}
                                        onSelect={() => onSelectSession(s.id)}
                                        onMenuToggle={() =>
                                            setMenuSessionId(
                                                menuSessionId === s.id
                                                    ? null
                                                    : s.id,
                                            )
                                        }
                                        onRenameStart={() => {
                                            setRenameId(s.id);
                                            setRenameDraft(s.title);
                                            setMenuSessionId(null);
                                        }}
                                        onRenameSave={() => {
                                            if (renameDraft.trim()) {
                                                void persistRename(
                                                    s.id,
                                                    renameDraft.trim(),
                                                );
                                            }
                                            setRenameId(null);
                                        }}
                                        onRenameCancel={() => setRenameId(null)}
                                        onPin={() => togglePinSession(s.id)}
                                        onArchive={() => {
                                            void persistArchive(s.id, true);
                                            setMenuSessionId(null);
                                        }}
                                        onUnarchive={() => {
                                            void persistArchive(s.id, false);
                                            setMenuSessionId(null);
                                        }}
                                        onDelete={() => {
                                            void persistDelete(s.id);
                                            setMenuSessionId(null);
                                        }}
                                        pinned
                                        archived={false}
                                    />
                                ))}
                            </SessionGroup>
                        ) : null}

                        {groups.map((g) => (
                            <SessionGroup key={g.key} label={g.label}>
                                {g.items.map((s) => (
                                    <SessionRow
                                        key={s.id}
                                        session={s}
                                        active={s.id === activeSessionId}
                                        menuOpen={menuSessionId === s.id}
                                        renaming={renameId === s.id}
                                        renameDraft={renameDraft}
                                        onRenameDraft={setRenameDraft}
                                        onSelect={() => onSelectSession(s.id)}
                                        onMenuToggle={() =>
                                            setMenuSessionId(
                                                menuSessionId === s.id
                                                    ? null
                                                    : s.id,
                                            )
                                        }
                                        onRenameStart={() => {
                                            setRenameId(s.id);
                                            setRenameDraft(s.title);
                                            setMenuSessionId(null);
                                        }}
                                        onRenameSave={() => {
                                            if (renameDraft.trim()) {
                                                void persistRename(
                                                    s.id,
                                                    renameDraft.trim(),
                                                );
                                            }
                                            setRenameId(null);
                                        }}
                                        onRenameCancel={() => setRenameId(null)}
                                        onPin={() => togglePinSession(s.id)}
                                        onArchive={() => {
                                            void persistArchive(s.id, true);
                                            setMenuSessionId(null);
                                        }}
                                        onUnarchive={() => {
                                            void persistArchive(s.id, false);
                                            setMenuSessionId(null);
                                        }}
                                        onDelete={() => {
                                            void persistDelete(s.id);
                                            setMenuSessionId(null);
                                        }}
                                        pinned={pinnedSessionIds.includes(s.id)}
                                        archived={archivedSessionIds.includes(s.id)}
                                    />
                                ))}
                            </SessionGroup>
                        ))}
                    </div>
                ) : null}

                <div className="mt-auto border-t border-[var(--chat-border)] p-3">
                    {expanded ? (
                        <>
                            <NavBtn icon={Brain} label="Pamięć" onClick={onOpenMemory} />
                            <NavBtn icon={FileText} label="Pliki" onClick={onOpenFiles} />
                            <NavBtn
                                icon={Settings}
                                label="Ustawienia"
                                onClick={toggleSidebarCollapsed}
                            />
                            <div className="mt-2 px-2 py-1 text-xs text-[var(--chat-text-muted)]">
                                {username || "Użytkownik"}
                            </div>
                        </>
                    ) : null}
                    <button
                        type="button"
                        className={cn(
                            "mt-1 flex h-9 w-full items-center gap-2 px-2 text-sm text-[var(--chat-text-muted)] hover:text-[var(--chat-text)]",
                            !expanded && "md:justify-center",
                        )}
                        onClick={() => void logoutAndRedirect()}
                        data-testid="user-logout"
                    >
                        <LogOut className="h-4 w-4 shrink-0" />
                        {expanded ? "Wyloguj" : null}
                    </button>
                </div>
            </aside>
        </>
    );
}

function SessionGroup({
    label,
    children,
}: {
    label: string;
    children: React.ReactNode;
}) {
    return (
        <div className="mb-4">
            <p className="mb-1 px-2 text-[10px] font-medium uppercase tracking-wider text-[var(--chat-text-muted)]">
                {label}
            </p>
            <div>{children}</div>
        </div>
    );
}

function SessionRow({
    session,
    active,
    menuOpen,
    renaming,
    renameDraft,
    onRenameDraft,
    onSelect,
    onMenuToggle,
    onRenameStart,
    onRenameSave,
    onRenameCancel,
    onPin,
    onArchive,
    onUnarchive,
    onDelete,
    pinned,
    archived,
}: {
    session: { id: string; title: string };
    active: boolean;
    menuOpen: boolean;
    renaming: boolean;
    renameDraft: string;
    onRenameDraft: (v: string) => void;
    onSelect: () => void;
    onMenuToggle: () => void;
    onRenameStart: () => void;
    onRenameSave: () => void;
    onRenameCancel: () => void;
    onPin: () => void;
    onArchive: () => void;
    onUnarchive: () => void;
    onDelete: () => void;
    pinned: boolean;
    archived: boolean;
}) {
    if (renaming) {
        return (
            <div className="px-2 py-1">
                <input
                    value={renameDraft}
                    onChange={(e) => onRenameDraft(e.target.value)}
                    onKeyDown={(e) => {
                        if (e.key === "Enter") onRenameSave();
                        if (e.key === "Escape") onRenameCancel();
                    }}
                    onBlur={onRenameSave}
                    className="h-9 w-full border border-[var(--chat-border)] bg-[#15181D] px-2 text-sm text-[var(--chat-text)]"
                    autoFocus
                />
            </div>
        );
    }

    return (
        <div
            data-testid="user-session-item"
            data-active={active ? "true" : "false"}
            className={cn(
                "group relative flex h-[40px] items-center",
                active && "bg-[rgba(255,255,255,0.06)]",
                !active && "hover:bg-white/[0.04]",
            )}
        >
            <button
                type="button"
                data-testid="user-session-select"
                className="min-w-0 flex-1 truncate px-3 text-left text-sm text-[var(--chat-text)]"
                onClick={onSelect}
            >
                {session.title}
            </button>
            {pinned ? (
                <Pin className="mr-1 h-3 w-3 shrink-0 text-[var(--chat-accent)]" />
            ) : null}
            <button
                type="button"
                className="shrink-0 px-2 text-[var(--chat-text-muted)] opacity-0 hover:text-[var(--chat-text)] group-hover:opacity-100"
                onClick={onMenuToggle}
                aria-label="Menu sesji"
            >
                <MoreHorizontal className="h-4 w-4" />
            </button>
            {menuOpen ? (
                <div className="absolute right-0 top-full z-20 min-w-[9rem] border border-[var(--chat-border)] bg-[#15181D] py-1 shadow-lg">
                    <MenuItem onClick={onRenameStart}>Zmień nazwę</MenuItem>
                    {!archived ? (
                        <MenuItem onClick={onPin}>
                            {pinned ? "Odepnij" : "Przypnij"}
                        </MenuItem>
                    ) : null}
                    {archived ? (
                        <MenuItem onClick={onUnarchive}>
                            <span className="inline-flex items-center gap-1.5">
                                <ArchiveRestore className="h-3 w-3" />
                                Przywróć
                            </span>
                        </MenuItem>
                    ) : (
                        <MenuItem onClick={onArchive}>
                            <span className="inline-flex items-center gap-1.5">
                                <Archive className="h-3 w-3" />
                                Archiwizuj
                            </span>
                        </MenuItem>
                    )}
                    <MenuItem
                        onClick={onDelete}
                        className="text-red-300 hover:text-red-200"
                        data-testid="user-session-delete"
                    >
                        <span className="inline-flex items-center gap-1.5">
                            <Trash2 className="h-3 w-3" />
                            Usuń
                        </span>
                    </MenuItem>
                </div>
            ) : null}
        </div>
    );
}

function MenuItem({
    children,
    onClick,
    className,
    ...rest
}: {
    children: React.ReactNode;
    onClick: () => void;
    className?: string;
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
    return (
        <button
            type="button"
            className={cn(
                "block w-full px-3 py-1.5 text-left text-xs text-[var(--chat-text)] hover:bg-white/[0.06]",
                className,
            )}
            onClick={onClick}
            {...rest}
        >
            {children}
        </button>
    );
}

function NavBtn({
    icon: Icon,
    label,
    onClick,
    disabled,
}: {
    icon: typeof Brain;
    label: string;
    onClick: () => void;
    disabled?: boolean;
}) {
    return (
        <button
            type="button"
            disabled={disabled}
            onClick={onClick}
            className="flex h-9 w-full items-center gap-2 px-2 text-sm text-[var(--chat-text-muted)] hover:bg-white/[0.04] hover:text-[var(--chat-text)] disabled:opacity-50"
        >
            <Icon className="h-4 w-4 shrink-0" />
            {label}
        </button>
    );
}
