"use client";

import { useMemo } from "react";

import { RuntimePanel } from "@/components/cockpit/runtime-panel";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { EmptyState } from "@/features/shared/empty-state";
import { JsonView } from "@/features/shared/json-view";
import { ChatUIMessage, useCockpitStore } from "@/lib/store/cockpit-store";

export function RightInspector() {
    const { sessions, activeSessionId, selectedMessageId } = useCockpitStore();
    const session =
        sessions.find((s) => s.id === activeSessionId) ?? sessions[0];

    const selected = useMemo<ChatUIMessage | undefined>(
        () =>
            (session?.messages ?? []).find(
                (m: ChatUIMessage) => m.id === selectedMessageId,
            ),
        [session?.messages, selectedMessageId],
    );

    const diagnostics = selected?.diagnostics;

    return (
        <Card className="h-full">
            <CardHeader>
                <CardTitle>Inspector</CardTitle>
            </CardHeader>
            <CardContent className="h-[calc(100%-72px)] overflow-auto">
                {!selected || !diagnostics ? (
                    <EmptyState
                        title="Brak wybranej odpowiedzi"
                        description="Kliknij odpowiedź asystenta w czacie, żeby zobaczyć trace, usage i debug tej konkretnej tury."
                    />
                ) : (
                    <Tabs defaultValue="runtime" className="h-full">
                        <TabsList className="grid w-full grid-cols-6 mb-4">
                            <TabsTrigger value="runtime">Runtime</TabsTrigger>
                            <TabsTrigger value="trace">Trace</TabsTrigger>
                            <TabsTrigger value="tools">Tools</TabsTrigger>
                            <TabsTrigger value="goal">Goal</TabsTrigger>
                            <TabsTrigger value="usage">Usage</TabsTrigger>
                            <TabsTrigger value="debug">Debug</TabsTrigger>
                        </TabsList>

                        <TabsContent value="runtime" className="space-y-4">
                            <RuntimePanel diagnostics={diagnostics} />
                        </TabsContent>
                        <TabsContent value="trace" className="space-y-2">
                            <JsonView
                                title="trace"
                                value={diagnostics.trace ?? {}}
                                compact
                            />
                        </TabsContent>
                        <TabsContent value="tools" className="space-y-2">
                            <JsonView
                                title="tool_calls"
                                value={diagnostics.tool_calls ?? []}
                                compact
                            />
                            <JsonView
                                title="tool_results"
                                value={diagnostics.tool_results ?? []}
                                compact
                            />
                        </TabsContent>
                        <TabsContent value="goal" className="space-y-2">
                            <JsonView
                                title="selected_goal"
                                value={diagnostics.trace?.selected_goal ?? null}
                                compact
                            />
                            <JsonView
                                title="goal_progress"
                                value={
                                    diagnostics.trace?.goal_progress_update ??
                                    null
                                }
                                compact
                            />
                        </TabsContent>
                        <TabsContent value="usage" className="space-y-2">
                            <JsonView
                                title="usage"
                                value={diagnostics.usage ?? {}}
                                compact
                            />
                            <JsonView
                                title="errors"
                                value={diagnostics.errors ?? []}
                                compact
                            />
                        </TabsContent>
                        <TabsContent value="debug" className="space-y-2">
                            <JsonView
                                title="debug"
                                value={diagnostics.debug ?? {}}
                                compact
                            />
                            <JsonView
                                title="full payload"
                                value={diagnostics}
                                compact
                            />
                        </TabsContent>
                    </Tabs>
                )}
            </CardContent>
        </Card>
    );
}
