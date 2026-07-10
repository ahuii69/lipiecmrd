"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatTs } from "@/lib/utils";
import { AlertCircle, Loader2 } from "lucide-react";
import {
    getEventStyle,
    type TraceViewModel
} from "./goals-parser";

interface GoalTraceViewProps {
    traceData: TraceViewModel | null;
    isLoading: boolean;
    error: Error | null;
    goalId: string | null;
}

export function GoalTraceView({
    traceData,
    isLoading,
    error,
    goalId,
}: GoalTraceViewProps) {
    if (!goalId) {
        return (
            <Card className="h-full flex flex-col">
                <CardHeader>
                    <CardTitle className="text-base">Trace celu</CardTitle>
                </CardHeader>
                <CardContent className="flex-1 flex items-center justify-center text-center">
                    <p className="text-xs text-muted-foreground">
                        Wybierz cel aby zobaczyć jego trace…
                    </p>
                </CardContent>
            </Card>
        );
    }

    return (
        <Card className="h-full flex flex-col">
            <CardHeader>
                <CardTitle className="text-base">Trace celu</CardTitle>
            </CardHeader>
            <CardContent className="flex-1 overflow-auto space-y-3">
                {isLoading ? (
                    <div className="flex items-center justify-center py-8">
                        <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
                        <span className="ml-2 text-xs text-muted-foreground">
                            Ładowanie trace…
                        </span>
                    </div>
                ) : null}

                {error ? (
                    <div className="rounded border border-red-800/60 bg-red-950/50 p-2 text-xs text-red-300 flex items-start gap-2">
                        <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
                        <div>{error.message}</div>
                    </div>
                ) : null}

                {traceData && !isLoading ? (
                    <>
                        {/* Events Timeline */}
                        {traceData.events.length > 0 ? (
                            <div className="space-y-2">
                                <p className="text-xs font-semibold text-muted-foreground">
                                    Zdarzenia ({traceData.events.length})
                                </p>
                                <div className="space-y-2">
                                    {traceData.events.map((event, idx) => {
                                        const style = getEventStyle(
                                            event.event_type,
                                        );
                                        return (
                                            <div
                                                key={event.event_id}
                                                className="rounded border border-border/50 bg-card/50 p-2"
                                            >
                                                <div className="flex items-start gap-2">
                                                    <span
                                                        className={`text-lg ${style.color}`}
                                                    >
                                                        {style.icon}
                                                    </span>
                                                    <div className="flex-1 min-w-0">
                                                        <p className="text-xs font-semibold">
                                                            {event.event_type}
                                                        </p>
                                                        <p className="text-xs text-muted-foreground">
                                                            {formatTs(event.ts)}
                                                        </p>
                                                        {Object.keys(event.data)
                                                            .length > 0 ? (
                                                            <details className="mt-1 text-xs">
                                                                <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
                                                                    Dane…
                                                                </summary>
                                                                <pre className="mt-1 bg-background p-1 rounded text-xs overflow-auto max-h-20">
                                                                    {JSON.stringify(
                                                                        event.data,
                                                                        null,
                                                                        2,
                                                                    )}
                                                                </pre>
                                                            </details>
                                                        ) : null}
                                                    </div>
                                                </div>
                                            </div>
                                        );
                                    })}
                                </div>
                            </div>
                        ) : (
                            <p className="text-xs text-muted-foreground">
                                Brak zdarzeń dla tego celu.
                            </p>
                        )}

                        {/* Links */}
                        {traceData.links.length > 0 ? (
                            <div className="space-y-2 pt-2 border-t border-border">
                                <p className="text-xs font-semibold text-muted-foreground">
                                    Wykryte powiązania ({traceData.links.length}
                                    )
                                </p>
                                <div className="space-y-1">
                                    {traceData.links.map((link) => (
                                        <div
                                            key={link.link_id}
                                            className="rounded border border-border/30 bg-card/30 p-2 text-xs"
                                        >
                                            <p className="font-semibold">
                                                {link.link_type} —{" "}
                                                {link.entity_type}
                                            </p>
                                            <p className="text-muted-foreground">
                                                ID: {link.entity_id}
                                            </p>
                                            <p className="text-muted-foreground">
                                                {formatTs(link.ts)}
                                            </p>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        ) : null}

                        {traceData.events.length === 0 &&
                        traceData.links.length === 0 ? (
                            <p className="text-xs text-muted-foreground py-8 text-center">
                                Ten cel nie ma jeszcze zdarzeń ani powiązań.
                            </p>
                        ) : null}
                    </>
                ) : null}
            </CardContent>
        </Card>
    );
}
