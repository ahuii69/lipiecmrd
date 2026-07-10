"use client";

import { FormEvent, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { GoalUpdateRequest } from "@/lib/api/types";

import { GoalListItemViewModel } from "./goals-parser";

interface UpdateFormState {
    status: string;
    priority: string;
    urgency: string;
    importance: string;
    confidence: string;
    progress: string;
    metadata: string;
    reason: string;
}

interface GoalUpdateFormProps {
    goal: GoalListItemViewModel;
    isSubmitting: boolean;
    onSubmit: (input: GoalUpdateRequest) => Promise<void>;
}

function toState(goal: GoalListItemViewModel): UpdateFormState {
    return {
        status: goal.status,
        priority: goal.priority.toFixed(2),
        urgency: goal.urgency.toFixed(2),
        importance: goal.importance.toFixed(2),
        confidence: goal.confidence.toFixed(2),
        progress: goal.progress.toFixed(2),
        metadata: JSON.stringify(goal.raw.metadata ?? {}, null, 2),
        reason: "cockpit_update",
    };
}

function parseRatio(name: string, value: string): number {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) {
        throw new Error(`Pole ${name} musi być liczbą`);
    }
    if (parsed < 0 || parsed > 1) {
        throw new Error(`Pole ${name} musi być w zakresie 0..1`);
    }
    return parsed;
}

export function GoalUpdateForm({
    goal,
    isSubmitting,
    onSubmit,
}: GoalUpdateFormProps) {
    const [form, setForm] = useState<UpdateFormState>(() => toState(goal));
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        setForm(toState(goal));
        setError(null);
    }, [goal.goalId]);

    const submit = async (e: FormEvent) => {
        e.preventDefault();
        setError(null);

        try {
            const metadataRaw = form.metadata.trim();
            const metadataParsed = metadataRaw
                ? JSON.parse(metadataRaw)
                : ({} as Record<string, unknown>);

            if (
                metadataParsed === null ||
                typeof metadataParsed !== "object" ||
                Array.isArray(metadataParsed)
            ) {
                throw new Error("Metadata musi być obiektem JSON");
            }

            await onSubmit({
                goal_id: goal.goalId,
                status: form.status,
                priority: parseRatio("priority", form.priority),
                urgency: parseRatio("urgency", form.urgency),
                importance: parseRatio("importance", form.importance),
                confidence: parseRatio("confidence", form.confidence),
                progress: parseRatio("progress", form.progress),
                metadata: metadataParsed as Record<string, unknown>,
                reason: form.reason.trim() || "cockpit_update",
            });
        } catch (err) {
            setError(
                err instanceof Error ? err.message : "Błąd walidacji update",
            );
        }
    };

    return (
        <form className="space-y-3" onSubmit={submit}>
            <div className="grid gap-2 lg:grid-cols-[180px_1fr]">
                <Select
                    value={form.status}
                    onValueChange={(status) =>
                        setForm((s) => ({ ...s, status }))
                    }
                >
                    <SelectTrigger>
                        <SelectValue placeholder="status" />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="proposed">proposed</SelectItem>
                        <SelectItem value="active">active</SelectItem>
                        <SelectItem value="scheduled">scheduled</SelectItem>
                        <SelectItem value="blocked">blocked</SelectItem>
                        <SelectItem value="completed">completed</SelectItem>
                        <SelectItem value="failed">failed</SelectItem>
                        <SelectItem value="cancelled">cancelled</SelectItem>
                        <SelectItem value="expired">expired</SelectItem>
                    </SelectContent>
                </Select>

                <Input
                    value={form.reason}
                    onChange={(e) =>
                        setForm((s) => ({ ...s, reason: e.target.value }))
                    }
                    placeholder="reason"
                />
            </div>

            <div className="grid gap-2 lg:grid-cols-3">
                <Input
                    type="number"
                    min={0}
                    max={1}
                    step={0.01}
                    value={form.priority}
                    onChange={(e) =>
                        setForm((s) => ({ ...s, priority: e.target.value }))
                    }
                    placeholder="priority"
                />
                <Input
                    type="number"
                    min={0}
                    max={1}
                    step={0.01}
                    value={form.urgency}
                    onChange={(e) =>
                        setForm((s) => ({ ...s, urgency: e.target.value }))
                    }
                    placeholder="urgency"
                />
                <Input
                    type="number"
                    min={0}
                    max={1}
                    step={0.01}
                    value={form.importance}
                    onChange={(e) =>
                        setForm((s) => ({ ...s, importance: e.target.value }))
                    }
                    placeholder="importance"
                />
                <Input
                    type="number"
                    min={0}
                    max={1}
                    step={0.01}
                    value={form.confidence}
                    onChange={(e) =>
                        setForm((s) => ({ ...s, confidence: e.target.value }))
                    }
                    placeholder="confidence"
                />
                <Input
                    type="number"
                    min={0}
                    max={1}
                    step={0.01}
                    value={form.progress}
                    onChange={(e) =>
                        setForm((s) => ({ ...s, progress: e.target.value }))
                    }
                    placeholder="progress"
                />
            </div>

            <Textarea
                className="min-h-[120px]"
                value={form.metadata}
                onChange={(e) =>
                    setForm((s) => ({ ...s, metadata: e.target.value }))
                }
                placeholder="metadata JSON"
            />

            {error ? (
                <div className="rounded border border-red-800/60 bg-red-950/50 p-2 text-xs text-red-300">
                    {error}
                </div>
            ) : null}

            <div className="flex items-center justify-end">
                <Button type="submit" disabled={isSubmitting}>
                    {isSubmitting ? "Zapisywanie…" : "Zapisz update"}
                </Button>
            </div>
        </form>
    );
}
