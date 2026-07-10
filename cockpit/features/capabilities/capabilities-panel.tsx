"use client";

import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useMemo, useState } from "react";

import {
    Accordion,
    AccordionContent,
    AccordionItem,
    AccordionTrigger,
} from "@/components/ui/accordion";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { EmptyState } from "@/features/shared/empty-state";
import { JsonView } from "@/features/shared/json-view";
import { apiClient } from "@/lib/api/client";
import { useCockpitStore } from "@/lib/store/cockpit-store";
import {
    filterCapabilities,
    normalizeCapabilities,
    type CapabilitiesGroupView,
    type CapabilityView,
} from "./capabilities-parser";

export function CapabilitiesPanel() {
    const { sessions, activeSessionId, apiKeyOverride } = useCockpitStore();
    const session =
        sessions.find((s) => s.id === activeSessionId) ?? sessions[0];
    const [q, setQ] = useState("");

    const capQuery = useQuery({
        queryKey: ["capabilities", session.mode, session.id, apiKeyOverride],
        queryFn: () =>
            apiClient.capabilities(
                session.mode,
                session.mode === "debug",
                apiKeyOverride || undefined,
            ),
    });

    const grouped = useMemo(() => {
        const normalized = normalizeCapabilities(capQuery.data?.capabilities);
        return filterCapabilities(normalized, q);
    }, [capQuery.data?.capabilities, q]);

    const totalCount = capQuery.data?.count ?? 0;
    const filteredCount = grouped.reduce((sum, g) => sum + g.count, 0);

    return (
        <Card className="h-full">
            <CardHeader className="space-y-3">
                <div className="flex items-center justify-between">
                    <CardTitle>Capabilities Browser</CardTitle>
                    <Badge variant="outline">
                        {filteredCount}/{totalCount}
                    </Badge>
                </div>
                <div className="relative">
                    <Search className="pointer-events-none absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
                    <Input
                        className="pl-8"
                        placeholder="Search capability name, group, or description..."
                        value={q}
                        onChange={(e) => setQ(e.target.value)}
                    />
                </div>
            </CardHeader>
            <CardContent className="space-y-3">
                {capQuery.isLoading ? (
                    <p className="text-sm text-muted-foreground">
                        Loading capabilities…
                    </p>
                ) : null}
                {capQuery.isError ? (
                    <div className="rounded-md border border-red-800/60 bg-red-950/50 p-3 text-sm text-red-300">
                        Failed to load capabilities:{" "}
                        {(capQuery.error as Error).message}
                    </div>
                ) : null}

                {!capQuery.isLoading && !grouped.length ? (
                    <EmptyState
                        title="No capabilities found"
                        description={
                            q.trim()
                                ? "Try a different search term."
                                : "No capabilities available for this mode."
                        }
                    />
                ) : null}

                {grouped.map((group: CapabilitiesGroupView) => (
                    <CapabilityGroup key={group.group} group={group} />
                ))}
            </CardContent>
        </Card>
    );
}

function CapabilityGroup({ group }: { group: CapabilitiesGroupView }) {
    return (
        <Card className="shadow-none">
            <CardHeader className="pb-2">
                <div className="flex items-center justify-between">
                    <CardTitle className="text-xs uppercase tracking-wide text-muted-foreground">
                        {group.group}
                    </CardTitle>
                    <div className="flex gap-1">
                        <Badge variant="secondary" className="text-xs">
                            {group.count}
                        </Badge>
                        <Badge
                            variant={
                                group.readOnlyCount > 0
                                    ? "outline"
                                    : "secondary"
                            }
                            className="text-xs"
                        >
                            {group.readOnlyCount} RO
                        </Badge>
                        <Badge
                            variant={
                                group.mutatableCount > 0
                                    ? "warning"
                                    : "secondary"
                            }
                            className="text-xs"
                        >
                            {group.mutatableCount} RW
                        </Badge>
                    </div>
                </div>
            </CardHeader>
            <CardContent>
                <Accordion type="multiple" className="w-full">
                    {group.items.map((cap) => (
                        <CapabilityItem key={cap.name} capability={cap} />
                    ))}
                </Accordion>
            </CardContent>
        </Card>
    );
}

function CapabilityItem({ capability }: { capability: CapabilityView }) {
    return (
        <AccordionItem value={capability.name}>
            <AccordionTrigger>
                <div className="flex w-full items-center justify-between pr-4">
                    <span className="text-left text-xs font-semibold">
                        {capability.name}
                    </span>
                    <div className="flex items-center gap-1">
                        <Badge
                            variant={
                                capability.mode === "read"
                                    ? "success"
                                    : "warning"
                            }
                            className="text-xs"
                        >
                            {capability.mode === "read" ? "RO" : "RW"}
                        </Badge>
                        <Badge variant="outline" className="text-xs">
                            {capability.timeout}s
                        </Badge>
                        {capability.confirmsRequired && (
                            <Badge variant="danger" className="text-xs">
                                ⚠ Confirm required
                            </Badge>
                        )}
                    </div>
                </div>
            </AccordionTrigger>
            <AccordionContent>
                <div className="space-y-3 text-xs">
                    <p className="text-muted-foreground">
                        {capability.description}
                    </p>

                    <div className="flex flex-wrap gap-1">
                        {capability.enabled ? (
                            <Badge variant="default">Enabled</Badge>
                        ) : (
                            <Badge variant="secondary">Disabled</Badge>
                        )}
                        {capability.visibility.map((v: string) => (
                            <Badge key={v} variant="outline">
                                {v}
                            </Badge>
                        ))}
                    </div>

                    <div className="grid gap-2 lg:grid-cols-2">
                        <JsonView
                            title="Input schema"
                            value={capability.inputSchema}
                            compact
                        />
                        <JsonView
                            title="Output schema"
                            value={capability.outputSchema}
                            compact
                        />
                    </div>
                </div>
            </AccordionContent>
        </AccordionItem>
    );
}
