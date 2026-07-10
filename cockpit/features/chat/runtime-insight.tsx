"use client";

import { ChevronDown } from "lucide-react";
import { useState, type ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import type { ChatTurnResponse } from "@/lib/api/types";

function hasKey(obj: unknown, key: string): obj is Record<string, unknown> {
    return typeof obj === "object" && obj !== null && key in obj;
}

function InsightBadge({
    tone,
    children,
}: {
    tone: "success" | "fail" | "fallback" | "neutral";
    children: ReactNode;
}) {
    const variant =
        tone === "success"
            ? "success"
            : tone === "fail"
              ? "danger"
              : tone === "fallback"
                ? "warning"
                : "secondary";
    return <Badge variant={variant}>{children}</Badge>;
}

function formatScalar(v: unknown): string {
    if (v === null || v === undefined) return "—";
    if (typeof v === "boolean") return v ? "tak" : "nie";
    if (typeof v === "number" && Number.isFinite(v)) return String(v);
    if (typeof v === "string") return v || "—";
    return JSON.stringify(v);
}

type Row = { label: string; value: ReactNode };

function buildRows(trace: Record<string, unknown>): Row[] {
    const rows: Row[] = [];

    if (hasKey(trace, "strategy_source")) {
        const v = trace.strategy_source;
        const s = typeof v === "string" ? v : String(v);
        const tone =
            s.includes("external") || s === "external"
                ? "success"
                : s.includes("fallback") || s.includes("worker")
                  ? "fallback"
                  : "neutral";
        rows.push({
            label: "strategy_source",
            value: <InsightBadge tone={tone}>{s}</InsightBadge>,
        });
    }

    if (hasKey(trace, "strategy_authority_external")) {
        const ext = trace.strategy_authority_external === true;
        rows.push({
            label: "strategy_authority_external",
            value: (
                <InsightBadge tone={ext ? "success" : "neutral"}>
                    {ext ? "external" : "local"}
                </InsightBadge>
            ),
        });
    }

    const reasoningParts: string[] = [];
    if (hasKey(trace, "planner_executed") && trace.planner_executed === true) {
        reasoningParts.push("planner");
    }
    if (hasKey(trace, "reasoning_executed") && trace.reasoning_executed === true) {
        reasoningParts.push("reasoning loop");
    }
    if (
        hasKey(trace, "escalation_use_reasoning") &&
        trace.escalation_use_reasoning === true
    ) {
        reasoningParts.push("escalation → reasoning");
    }
    if (reasoningParts.length > 0) {
        rows.push({
            label: "reasoning",
            value: (
                <InsightBadge tone="success">{reasoningParts.join(", ")}</InsightBadge>
            ),
        });
    } else if (
        hasKey(trace, "planner_executed") ||
        hasKey(trace, "reasoning_executed") ||
        hasKey(trace, "escalation_use_reasoning")
    ) {
        rows.push({
            label: "reasoning",
            value: <InsightBadge tone="neutral">brak</InsightBadge>,
        });
    }

    if (hasKey(trace, "executive_strategy")) {
        const st = String(trace.executive_strategy ?? "");
        const isReactive = st === "reactive_tick";
        const isCognitive = st === "cognitive_direct";
        rows.push({
            label: "reactive_tick",
            value: (
                <InsightBadge tone={isReactive ? "success" : "neutral"}>
                    {isReactive ? "tak" : "nie"}
                </InsightBadge>
            ),
        });
        rows.push({
            label: "cognitive_direct",
            value: (
                <InsightBadge tone={isCognitive ? "success" : "neutral"}>
                    {isCognitive ? "tak" : "nie"}
                </InsightBadge>
            ),
        });
    }

    if (hasKey(trace, "memory_lookup_happened")) {
        const ok = trace.memory_lookup_happened === true;
        rows.push({
            label: "memory_lookup_happened",
            value: <InsightBadge tone={ok ? "success" : "neutral"}>{formatScalar(ok)}</InsightBadge>,
        });
    }

    if (hasKey(trace, "controlled_web_triggered")) {
        const tr = trace.controlled_web_triggered === true;
        rows.push({
            label: "controlled_web_triggered",
            value: <InsightBadge tone={tr ? "fallback" : "neutral"}>{formatScalar(tr)}</InsightBadge>,
        });
    }

    if (hasKey(trace, "controlled_web_ok")) {
        const v = trace.controlled_web_ok;
        const ok = v === true;
        const bad = v === false;
        rows.push({
            label: "controlled_web_ok",
            value: (
                <InsightBadge tone={ok ? "success" : bad ? "fail" : "neutral"}>
                    {v === null || v === undefined ? "—" : formatScalar(ok)}
                </InsightBadge>
            ),
        });
    }

    if (hasKey(trace, "used_fallback")) {
        const fb = trace.used_fallback === true;
        rows.push({
            label: "used_fallback",
            value: (
                <InsightBadge tone={fb ? "fallback" : "success"}>
                    {formatScalar(fb)}
                </InsightBadge>
            ),
        });
    }

    if (hasKey(trace, "tool_calls_successful")) {
        const n = trace.tool_calls_successful;
        const num = typeof n === "number" ? n : Number(n);
        rows.push({
            label: "tool_calls_successful",
            value: (
                <span className="font-mono text-xs text-muted-foreground">
                    {Number.isFinite(num) ? num : formatScalar(n)}
                </span>
            ),
        });
    }

    if (
        hasKey(trace, "experience_write_back_attempted") ||
        hasKey(trace, "experience_write_back_succeeded")
    ) {
        const att =
            hasKey(trace, "experience_write_back_attempted") &&
            trace.experience_write_back_attempted === true;
        const ok =
            hasKey(trace, "experience_write_back_succeeded") &&
            trace.experience_write_back_succeeded === true;
        let tone: "success" | "fail" | "neutral" | "fallback" = "neutral";
        let text = "—";
        if (att && ok) {
            tone = "success";
            text = "zapisano";
        } else if (att && hasKey(trace, "experience_write_back_succeeded") && !ok) {
            tone = "fail";
            text = "próba, brak sukcesu";
        } else if (!att && ok) {
            tone = "success";
            text = "zapisano";
        } else if (att) {
            tone = "fallback";
            text = "próba";
        } else if (hasKey(trace, "experience_write_back_succeeded") && !ok) {
            tone = "neutral";
            text = "nie";
        }
        rows.push({
            label: "experience_write_back",
            value: <InsightBadge tone={tone}>{text}</InsightBadge>,
        });
    }

    return rows;
}

export function RuntimeInsightPanel({
    diagnostics,
}: {
    diagnostics?: ChatTurnResponse;
}) {
    const [open, setOpen] = useState(false);
    const trace = diagnostics?.trace;
    if (!trace || typeof trace !== "object") return null;

    const rows = buildRows(trace as Record<string, unknown>);
    if (rows.length === 0) return null;

    return (
        <div
            className="mt-2 border-t border-border/60 pt-2"
            onClick={(e) => e.stopPropagation()}
        >
            <button
                type="button"
                className="flex w-full items-center justify-between gap-2 rounded-md px-1 py-1 text-left text-xs font-medium text-muted-foreground hover:bg-muted/40"
                aria-expanded={open}
                onClick={() => setOpen((v) => !v)}
            >
                <span>⚙ Runtime Insight</span>
                <ChevronDown
                    className={`h-4 w-4 shrink-0 transition-transform ${open ? "rotate-180" : ""}`}
                />
            </button>
            {open ? (
                <dl className="mt-2 space-y-1.5 text-xs">
                    {rows.map((r) => (
                        <div
                            key={r.label}
                            className="flex flex-wrap items-center gap-x-2 gap-y-1"
                        >
                            <dt className="min-w-[8rem] shrink-0 text-muted-foreground">
                                {r.label}
                            </dt>
                            <dd className="min-w-0">{r.value}</dd>
                        </div>
                    ))}
                </dl>
            ) : null}
        </div>
    );
}
