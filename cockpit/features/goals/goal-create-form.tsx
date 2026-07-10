"use client";

import { FormEvent, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { GoalCreateRequest } from "@/lib/api/types";

const goalTypes = [
    "task",
    "information_need",
    "research_goal",
    "maintenance_goal",
    "learning_goal",
    "user_intent_goal",
    "system_goal",
    "long_term_goal",
] as const;

interface CreateFormState {
    title: string;
    description: string;
    goal_type: string;
    source: string;
    priority: string;
    urgency: string;
    importance: string;
    confidence: string;
    tags: string;
    success_criteria: string;
    failure_criteria: string;
    metadata: string;
}

const defaults: CreateFormState = {
    title: "",
    description: "",
    goal_type: "task",
    source: "cockpit",
    priority: "0.50",
    urgency: "0.50",
    importance: "0.60",
    confidence: "0.65",
    tags: "",
    success_criteria: "",
    failure_criteria: "",
    metadata: "{}",
};

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

function toLines(value: string): string[] {
    return value
        .split("\n")
        .map((v) => v.trim())
        .filter((v) => v.length > 0);
}

function toTags(value: string): string[] {
    return value
        .split(",")
        .map((v) => v.trim())
        .filter((v) => v.length > 0);
}

export function GoalCreateForm({
    isSubmitting,
    onSubmit,
}: {
    isSubmitting: boolean;
    onSubmit: (input: GoalCreateRequest) => Promise<void>;
}) {
    const [open, setOpen] = useState(false);
    const [form, setForm] = useState<CreateFormState>(defaults);
    const [error, setError] = useState<string | null>(null);

    const canSubmit = useMemo(
        () =>
            form.title.trim().length > 0 && form.description.trim().length > 0,
        [form.description, form.title],
    );

    const handleSubmit = async (e: FormEvent) => {
        e.preventDefault();
        setError(null);

        try {
            if (!form.title.trim()) {
                throw new Error("Tytuł jest wymagany");
            }
            if (!form.description.trim()) {
                throw new Error("Opis jest wymagany");
            }

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

            const payload: GoalCreateRequest = {
                title: form.title.trim(),
                description: form.description.trim(),
                goal_type: form.goal_type,
                source: form.source.trim() || "cockpit",
                priority: parseRatio("priority", form.priority),
                urgency: parseRatio("urgency", form.urgency),
                importance: parseRatio("importance", form.importance),
                confidence: parseRatio("confidence", form.confidence),
                tags: toTags(form.tags),
                success_criteria: toLines(form.success_criteria),
                failure_criteria: toLines(form.failure_criteria),
                metadata: metadataParsed as Record<string, unknown>,
            };

            await onSubmit(payload);
            setForm(defaults);
            setOpen(false);
        } catch (err) {
            setError(
                err instanceof Error
                    ? err.message
                    : "Błąd walidacji formularza",
            );
        }
    };

    return (
        <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
                <Button size="sm">Utwórz goal</Button>
            </DialogTrigger>
            <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
                <DialogHeader>
                    <DialogTitle>Nowy goal</DialogTitle>
                    <DialogDescription>
                        Formularz zapisuje realny cel przez capability
                        `goal.create`.
                    </DialogDescription>
                </DialogHeader>

                <form className="space-y-3" onSubmit={handleSubmit}>
                    <div className="grid gap-2 lg:grid-cols-2">
                        <Input
                            placeholder="Tytuł"
                            value={form.title}
                            onChange={(e) =>
                                setForm((s) => ({
                                    ...s,
                                    title: e.target.value,
                                }))
                            }
                        />
                        <Input
                            placeholder="Źródło (np. cockpit)"
                            value={form.source}
                            onChange={(e) =>
                                setForm((s) => ({
                                    ...s,
                                    source: e.target.value,
                                }))
                            }
                        />
                    </div>

                    <Textarea
                        className="min-h-[90px]"
                        placeholder="Opis celu"
                        value={form.description}
                        onChange={(e) =>
                            setForm((s) => ({
                                ...s,
                                description: e.target.value,
                            }))
                        }
                    />

                    <div className="grid gap-2 lg:grid-cols-2">
                        <Select
                            value={form.goal_type}
                            onValueChange={(v: string) =>
                                setForm((s) => ({ ...s, goal_type: v }))
                            }
                        >
                            <SelectTrigger>
                                <SelectValue placeholder="goal_type" />
                            </SelectTrigger>
                            <SelectContent>
                                {goalTypes.map((type) => (
                                    <SelectItem key={type} value={type}>
                                        {type}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>

                        <Input
                            placeholder="tag1, tag2, tag3"
                            value={form.tags}
                            onChange={(e) =>
                                setForm((s) => ({ ...s, tags: e.target.value }))
                            }
                        />
                    </div>

                    <div className="grid gap-2 lg:grid-cols-4">
                        <Input
                            type="number"
                            min={0}
                            max={1}
                            step={0.01}
                            value={form.priority}
                            onChange={(e) =>
                                setForm((s) => ({
                                    ...s,
                                    priority: e.target.value,
                                }))
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
                                setForm((s) => ({
                                    ...s,
                                    urgency: e.target.value,
                                }))
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
                                setForm((s) => ({
                                    ...s,
                                    importance: e.target.value,
                                }))
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
                                setForm((s) => ({
                                    ...s,
                                    confidence: e.target.value,
                                }))
                            }
                            placeholder="confidence"
                        />
                    </div>

                    <div className="grid gap-2 lg:grid-cols-2">
                        <Textarea
                            className="min-h-[90px]"
                            placeholder="success_criteria (po jednej linii)"
                            value={form.success_criteria}
                            onChange={(e) =>
                                setForm((s) => ({
                                    ...s,
                                    success_criteria: e.target.value,
                                }))
                            }
                        />
                        <Textarea
                            className="min-h-[90px]"
                            placeholder="failure_criteria (po jednej linii)"
                            value={form.failure_criteria}
                            onChange={(e) =>
                                setForm((s) => ({
                                    ...s,
                                    failure_criteria: e.target.value,
                                }))
                            }
                        />
                    </div>

                    <Textarea
                        className="min-h-[90px]"
                        placeholder='metadata JSON, np. {"channel":"operator"}'
                        value={form.metadata}
                        onChange={(e) =>
                            setForm((s) => ({ ...s, metadata: e.target.value }))
                        }
                    />

                    {error ? (
                        <div className="rounded border border-red-800/60 bg-red-950/50 p-2 text-xs text-red-300">
                            {error}
                        </div>
                    ) : null}

                    <DialogFooter>
                        <Button
                            type="submit"
                            disabled={isSubmitting || !canSubmit}
                        >
                            {isSubmitting ? "Tworzenie…" : "Utwórz goal"}
                        </Button>
                    </DialogFooter>
                </form>
            </DialogContent>
        </Dialog>
    );
}
