import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";

// @ts-ignore
export function RuntimePanel({ diagnostics }: { diagnostics: any }) {
    if (!diagnostics) return null;

    return (
        <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4 text-sm">
                <div className="flex flex-col gap-1">
                    <span className="text-muted-foreground font-semibold">
                        Mode
                    </span>
                    <Badge
                        variant={
                            diagnostics.selected_mode === "AGENTIC"
                                ? "danger"
                                : "default"
                        }
                        className="w-fit"
                    >
                        {diagnostics.selected_mode || "UNKNOWN"}
                    </Badge>
                </div>
                <div className="flex flex-col gap-1">
                    <span className="text-muted-foreground font-semibold">
                        Duration
                    </span>
                    <span>
                        {diagnostics.trace?.duration_ms
                            ? (diagnostics.trace.duration_ms / 1000).toFixed(
                                  2,
                              ) + "s"
                            : "-"}{" "}
                        /{" "}
                        {diagnostics.time_budget
                            ? diagnostics.time_budget + "s"
                            : "N/A"}
                    </span>
                </div>
                <div className="flex flex-col gap-1">
                    <span className="text-muted-foreground font-semibold">
                        LLM Usage
                    </span>
                    <span>{diagnostics.usage?.total_tokens || 0} tokens</span>
                </div>
                <div className="flex flex-col gap-1">
                    <span className="text-muted-foreground font-semibold">
                        Web Needs
                    </span>
                    <span>{diagnostics.web_eligibility || "N/A"}</span>
                </div>
            </div>

            <Separator />

            <div className="flex flex-col gap-2 text-sm">
                <span className="text-muted-foreground font-semibold">
                    Active Layers
                </span>
                <div className="flex flex-wrap gap-2">
                    {diagnostics.layers_active &&
                    diagnostics.layers_active.length > 0 ? (
                        diagnostics.layers_active.map((layer: string) => (
                            <Badge key={layer} variant="secondary">
                                {layer}
                            </Badge>
                        ))
                    ) : (
                        <span className="text-muted-foreground italic">
                            None reported
                        </span>
                    )}
                </div>
            </div>

            <Separator />

            <div className="flex flex-col gap-2 text-sm">
                <span className="text-muted-foreground font-semibold">
                    Routing Reasons
                </span>
                <ul className="list-disc pl-5">
                    {diagnostics.reason_codes &&
                    diagnostics.reason_codes.length > 0 ? (
                        diagnostics.reason_codes.map((reason: string) => (
                            <li key={reason} className="text-muted-foreground">
                                {reason}
                            </li>
                        ))
                    ) : (
                        <li className="text-muted-foreground italic">
                            No reasons provided
                        </li>
                    )}
                </ul>
            </div>

            <Separator />

            <div className="grid grid-cols-2 gap-2 text-xs">
                <div>
                    Memory Lookup:{" "}
                    <Badge
                        variant={
                            diagnostics.trace?.memory_lookup_happened
                                ? "default"
                                : "outline"
                        }
                    >
                        {diagnostics.trace?.memory_lookup_happened
                            ? "Yes"
                            : "No"}
                    </Badge>
                </div>
                <div>
                    Web Required:{" "}
                    <Badge
                        variant={
                            diagnostics.trace?.research_was_required
                                ? "default"
                                : "outline"
                        }
                    >
                        {diagnostics.trace?.research_was_required
                            ? "Yes"
                            : "No"}
                    </Badge>
                </div>
                <div>
                    Psyche Snap:{" "}
                    <Badge
                        variant={
                            diagnostics.trace?.psyche_snapshot_happened
                                ? "default"
                                : "outline"
                        }
                    >
                        {diagnostics.trace?.psyche_snapshot_happened
                            ? "Yes"
                            : "No"}
                    </Badge>
                </div>
                <div>
                    Experience Write:{" "}
                    <Badge
                        variant={
                            diagnostics.trace?.experience_write_back_succeeded
                                ? "default"
                                : "outline"
                        }
                    >
                        {diagnostics.trace?.experience_write_back_succeeded
                            ? "Yes"
                            : "No"}
                    </Badge>
                </div>
            </div>
        </div>
    );
}
