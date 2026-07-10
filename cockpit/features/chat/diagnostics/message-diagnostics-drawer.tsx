"use client";

import {
    Activity,
    CheckCircle2,
    CircleDashed,
    Cpu,
    Wrench,
    XCircle,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { JsonView } from "@/features/shared/json-view";
import { ChatUIMessage } from "@/lib/store/cockpit-store";
import { formatTs } from "@/lib/utils";

import { DiagnosticsSummary } from "./diagnostics-parser";

function statusBadge(status: DiagnosticsSummary["status"]): {
    label: string;
    variant: "secondary" | "success" | "warning" | "danger";
} {
    switch (status) {
        case "fallback":
            return { label: "Fallback", variant: "warning" };
        case "tool-verified":
            return { label: "Tool-verified", variant: "success" };
        case "tool-failed":
            return { label: "Tool-failed", variant: "danger" };
        case "error":
            return { label: "Error", variant: "danger" };
        case "model-only":
        default:
            return { label: "Model-only", variant: "secondary" };
    }
}

function outcomeBadge(outcome: DiagnosticsSummary["toolExecutionOutcome"]): {
    label: string;
    variant: "secondary" | "success" | "warning" | "danger";
} {
    switch (outcome) {
        case "success":
            return { label: "Tool execution: success", variant: "success" };
        case "partial":
            return { label: "Tool execution: partial", variant: "warning" };
        case "failed":
            return { label: "Tool execution: failed", variant: "danger" };
        case "none":
        default:
            return { label: "Tool execution: none", variant: "secondary" };
    }
}

function executionIcon(outcome: DiagnosticsSummary["toolExecutionOutcome"]) {
    if (outcome === "success")
        return <CheckCircle2 className="h-4 w-4 text-emerald-400" />;
    if (outcome === "partial")
        return <Activity className="h-4 w-4 text-amber-400" />;
    if (outcome === "failed")
        return <XCircle className="h-4 w-4 text-red-400" />;
    return <CircleDashed className="h-4 w-4 text-muted-foreground" />;
}

export function MessageDiagnosticsDrawer({
    message,
    summary,
}: {
    message: ChatUIMessage;
    summary: DiagnosticsSummary;
}) {
    const { diagnostics, createdAt } = message;
    const status = statusBadge(summary.status);
    const outcome = outcomeBadge(summary.toolExecutionOutcome);

    return (
        <Dialog>
            <DialogTrigger asChild>
                <Button
                    variant="outline"
                    size="sm"
                    onClick={(e) => e.stopPropagation()}
                >
                    <Wrench className="mr-1 h-4 w-4" />
                    Diagnostyka
                </Button>
            </DialogTrigger>

            <DialogContent className="w-[96vw] max-w-5xl p-0">
                <DialogHeader className="border-b border-border px-4 py-3">
                    <DialogTitle className="flex items-center gap-2">
                        Diagnostics drawer
                        <Badge variant={status.variant}>{status.label}</Badge>
                        <Badge variant={outcome.variant}>{outcome.label}</Badge>
                    </DialogTitle>
                    <DialogDescription>
                        Odpowiedź assistant z {formatTs(createdAt)} — full
                        trace, bez zgadywania i bez upiększania runtime.
                    </DialogDescription>
                </DialogHeader>

                <ScrollArea className="max-h-[80vh] px-4 py-3">
                    <div className="space-y-3 text-xs">
                        <div className="grid gap-2 md:grid-cols-3">
                            <div className="rounded border border-border p-2">
                                <div className="mb-1 flex items-center gap-1 text-muted-foreground">
                                    <Cpu className="h-3.5 w-3.5" /> Provider /
                                    model
                                </div>
                                <p>{summary.provider}</p>
                                <p>{summary.model}</p>
                            </div>

                            <div className="rounded border border-border p-2">
                                <div className="mb-1 text-muted-foreground">
                                    Usage
                                </div>
                                <p>prompt: {summary.usage.promptTokens}</p>
                                <p>
                                    completion: {summary.usage.completionTokens}
                                </p>
                                <p>total: {summary.usage.totalTokens}</p>
                            </div>

                            <div className="rounded border border-border p-2">
                                <div className="mb-1 flex items-center gap-1 text-muted-foreground">
                                    {executionIcon(
                                        summary.toolExecutionOutcome,
                                    )}{" "}
                                    Execution
                                </div>
                                <p>grounding: {summary.groundingMode}</p>
                                <p>fallback: {String(summary.fallback)}</p>
                                <p>errors: {summary.errorsCount}</p>
                            </div>
                        </div>

                        <div className="rounded border border-border p-2">
                            <div className="mb-1 text-muted-foreground">
                                Tool status
                            </div>
                            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                                <div>
                                    <p className="text-muted-foreground">
                                        requested
                                    </p>
                                    <p className="text-sm font-semibold">
                                        {summary.toolsRequested}
                                    </p>
                                </div>
                                <div>
                                    <p className="text-muted-foreground">
                                        attempted
                                    </p>
                                    <p className="text-sm font-semibold">
                                        {summary.toolsAttempted}
                                    </p>
                                </div>
                                <div>
                                    <p className="text-muted-foreground">
                                        succeeded
                                    </p>
                                    <p className="text-sm font-semibold text-emerald-400">
                                        {summary.toolsSucceeded}
                                    </p>
                                </div>
                                <div>
                                    <p className="text-muted-foreground">
                                        failed
                                    </p>
                                    <p className="text-sm font-semibold text-red-400">
                                        {summary.toolsFailed}
                                    </p>
                                </div>
                            </div>
                            {summary.toolsRequested > 0 &&
                            summary.toolsAttempted === 0 ? (
                                <p className="mt-2 rounded border border-amber-700/60 bg-amber-950/40 p-2 text-amber-200">
                                    Model poprosił o narzędzia, ale backend nie
                                    zwrócił próby wykonania (brak attempt).
                                </p>
                            ) : null}
                            {summary.toolExecutionOutcome === "failed" ? (
                                <p className="mt-2 rounded border border-red-700/60 bg-red-950/40 p-2 text-red-200">
                                    Wszystkie próby narzędzi zakończyły się
                                    niepowodzeniem — to nie jest tool-verified.
                                </p>
                            ) : null}
                            {summary.toolExecutionOutcome === "partial" ? (
                                <p className="mt-2 rounded border border-amber-700/60 bg-amber-950/40 p-2 text-amber-200">
                                    Częściowy sukces narzędzi: część wywołań
                                    zakończona błędem.
                                </p>
                            ) : null}
                        </div>

                        {!diagnostics ? (
                            <div className="rounded border border-border p-2 text-muted-foreground">
                                Brak payloadu diagnostycznego dla tej
                                wiadomości.
                            </div>
                        ) : (
                            <>
                                <JsonView
                                    title="errors"
                                    value={diagnostics.errors}
                                    compact
                                />
                                <JsonView
                                    title="tool_calls"
                                    value={diagnostics.tool_calls}
                                    compact
                                />
                                <JsonView
                                    title="tool_results"
                                    value={diagnostics.tool_results}
                                    compact
                                />
                                <JsonView
                                    title="trace"
                                    value={diagnostics.trace}
                                    compact
                                />
                            </>
                        )}
                    </div>
                </ScrollArea>
            </DialogContent>
        </Dialog>
    );
}
