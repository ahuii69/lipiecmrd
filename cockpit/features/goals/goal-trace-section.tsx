import { Activity } from "lucide-react";

import {
    Accordion,
    AccordionContent,
    AccordionItem,
    AccordionTrigger,
} from "@/components/ui/accordion";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/features/shared/empty-state";
import { JsonView } from "@/features/shared/json-view";
import { formatTs, shortId } from "@/lib/utils";

import { GoalTraceViewModel } from "./goals-parser";

export function GoalTraceSection({
    trace,
    isLoading,
    error,
}: {
    trace: GoalTraceViewModel;
    isLoading: boolean;
    error: string | null;
}) {
    return (
        <Card className="shadow-none">
            <CardHeader className="pb-2">
                <CardTitle className="text-sm">Goal trace timeline</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
                {isLoading ? (
                    <p className="text-xs text-muted-foreground">
                        Ładowanie trace dla wybranego goala…
                    </p>
                ) : null}

                {error ? (
                    <div className="rounded border border-red-800/60 bg-red-950/50 p-2 text-xs text-red-300">
                        {error}
                    </div>
                ) : null}

                {!isLoading && !error && trace.events.length === 0 ? (
                    <EmptyState
                        icon={Activity}
                        title="Brak eventów trace"
                        description="Dla tego goala backend nie zwrócił jeszcze historii zdarzeń."
                    />
                ) : null}

                <div className="space-y-2">
                    {trace.events.map((event) => (
                        <div
                            key={event.id}
                            className="rounded-md border border-border p-2"
                        >
                            <div className="mb-1 flex items-center justify-between gap-2">
                                <p className="text-xs font-semibold">
                                    {event.title}
                                </p>
                                <span className="text-[11px] text-muted-foreground">
                                    {formatTs(event.ts)}
                                </span>
                            </div>
                            <p className="text-xs text-muted-foreground">
                                {event.description}
                            </p>
                            <div className="mt-2 flex flex-wrap gap-1">
                                {event.badges.map((badge) => (
                                    <Badge
                                        key={`${event.id}_${badge}`}
                                        variant="outline"
                                    >
                                        {badge}
                                    </Badge>
                                ))}
                            </div>
                        </div>
                    ))}
                </div>

                {!!trace.linkTypeCounts.length ? (
                    <div>
                        <p className="mb-1 text-xs font-semibold">Link map</p>
                        <div className="flex flex-wrap gap-1">
                            {trace.linkTypeCounts.map((row) => (
                                <Badge
                                    key={`${row.linkType}_${row.count}`}
                                    variant="secondary"
                                >
                                    {row.linkType}: {row.count}
                                </Badge>
                            ))}
                        </div>
                    </div>
                ) : null}

                <Accordion type="single" collapsible>
                    <AccordionItem value="trace-raw-json">
                        <AccordionTrigger>
                            Szczegóły techniczne trace (JSON)
                        </AccordionTrigger>
                        <AccordionContent>
                            <JsonView
                                title="goal.trace events raw"
                                value={trace.events.map((event) => ({
                                    id: event.id,
                                    ts: event.ts,
                                    event_type: event.eventType,
                                    data: event.data,
                                }))}
                                compact
                            />
                            <JsonView
                                title="goal.trace links raw"
                                value={trace.links.map((link) => ({
                                    id: link.id,
                                    link_type: link.link_type,
                                    entity_type: link.entity_type,
                                    entity_id: shortId(link.entity_id),
                                    ts: link.ts,
                                    payload: link.payload,
                                }))}
                                compact
                            />
                        </AccordionContent>
                    </AccordionItem>
                </Accordion>
            </CardContent>
        </Card>
    );
}
