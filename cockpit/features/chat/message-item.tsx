"use client";

import { AlertTriangle, Copy } from "lucide-react";
import { memo } from "react";
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import {
    AssistantTruthStatus,
    parseDiagnosticsSummary,
} from "@/features/chat/diagnostics/diagnostics-parser";
import { MessageDiagnosticsDrawer } from "@/features/chat/diagnostics/message-diagnostics-drawer";
import { RuntimeInsightPanel } from "@/features/chat/runtime-insight";
import { UsedMemoryPanel } from "@/features/chat/used-memory-panel";
import { GroundingBadge } from "@/features/shared/grounding-badge";
import { ChatUIMessage, useCockpitStore } from "@/lib/store/cockpit-store";
import { formatTs } from "@/lib/utils";

type RenderRole = "user" | "assistant" | AssistantTruthStatus;

function classifyRole(message: ChatUIMessage): RenderRole {
    if (message.role === "user") return "user";
    if (!message.diagnostics && !message.error) return "assistant";
    return parseDiagnosticsSummary(message.diagnostics, message.error).status;
}

function roleBadgeVariant(
    role: RenderRole,
): "default" | "secondary" | "warning" | "danger" | "success" {
    switch (role) {
        case "user":
            return "secondary";
        case "assistant":
            return "default";
        case "error":
            return "danger";
        case "fallback":
            return "warning";
        case "tool-verified":
            return "success";
        case "tool-failed":
            return "danger";
        case "model-only":
            return "secondary";
        default:
            return "secondary";
    }
}

function MessageItemInner({ message }: { message: ChatUIMessage }) {
    const selectMessage = useCockpitStore((s) => s.selectMessage);

    const renderRole = classifyRole(message);
    const summary = parseDiagnosticsSummary(message.diagnostics, message.error);

    const copy = async () => {
        await navigator.clipboard.writeText(message.content);
    };

    return (
        <Card
            className={
                message.role === "assistant"
                    ? "border-primary/20"
                    : "border-border"
            }
            onClick={() => selectMessage(message.id)}
        >
            <CardHeader className="flex flex-row items-center justify-between gap-2 pb-2">
                <div className="flex items-center gap-2 text-xs">
                    <Badge variant={roleBadgeVariant(renderRole)}>
                        {renderRole}
                    </Badge>
                    <Badge variant="outline">
                        {message.role === "assistant" ? "AI-Hub" : "Ty"}
                    </Badge>
                    <span className="text-muted-foreground">
                        {formatTs(message.createdAt)}
                    </span>
                    {message.role === "assistant" ? (
                        <GroundingBadge mode={summary.groundingMode} />
                    ) : null}
                    {summary.status === "tool-verified" ? (
                        <Badge variant="success">Tool success</Badge>
                    ) : null}
                    {summary.status === "tool-failed" ? (
                        <Badge variant="danger">Tool failed</Badge>
                    ) : null}
                    {summary.status === "fallback" ? (
                        <Badge variant="warning">Fallback</Badge>
                    ) : null}
                    {summary.status === "model-only" ? (
                        <Badge variant="secondary">Model-only</Badge>
                    ) : null}
                    {message.error ? (
                        <Badge variant="danger">Błąd</Badge>
                    ) : null}
                </div>
                <div className="flex items-center gap-1">
                    {message.role === "assistant" ? (
                        <MessageDiagnosticsDrawer
                            message={message}
                            summary={summary}
                        />
                    ) : null}
                    <Button
                        size="icon"
                        variant="ghost"
                        onClick={(e) => {
                            e.stopPropagation();
                            void copy();
                        }}
                        aria-label="Kopiuj odpowiedź"
                    >
                        <Copy className="h-4 w-4" />
                    </Button>
                </div>
            </CardHeader>

            <CardContent className="space-y-2">
                {message.role === "assistant" ? (
                    message.streaming ? (
                        <p className="whitespace-pre-wrap text-sm leading-relaxed">
                            {message.content}
                        </p>
                    ) : (
                        <div className="prose prose-invert max-w-none min-w-0 break-words text-sm leading-relaxed [&_pre]:max-w-full [&_pre]:overflow-x-auto">
                            <ReactMarkdown
                                remarkPlugins={[remarkGfm]}
                                rehypePlugins={[rehypeSanitize]}
                            >
                                {message.content}
                            </ReactMarkdown>
                        </div>
                    )
                ) : (
                    <p className="break-words text-sm leading-relaxed">
                        {message.content}
                    </p>
                )}

                {message.error ? (
                    <div className="rounded-md border border-red-700/60 bg-red-950/60 p-2 text-xs text-red-300">
                        <div className="mb-1 flex items-center gap-1 font-semibold">
                            <AlertTriangle className="h-3.5 w-3.5" />
                            Błąd runtime
                        </div>
                        {message.error}
                    </div>
                ) : null}
                {message.role === "assistant" ? (
                    <>
                        <UsedMemoryPanel
                            messageId={message.id}
                            diagnostics={message.diagnostics}
                        />
                        <RuntimeInsightPanel diagnostics={message.diagnostics} />
                    </>
                ) : null}
            </CardContent>
        </Card>
    );
}

export const MessageItem = memo(
    MessageItemInner,
    (a, b) =>
        a.message.id === b.message.id &&
        a.message.role === b.message.role &&
        a.message.content === b.message.content &&
        a.message.streaming === b.message.streaming &&
        a.message.error === b.message.error &&
        a.message.diagnostics === b.message.diagnostics,
);
