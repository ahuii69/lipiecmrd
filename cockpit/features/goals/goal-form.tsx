"use client";

import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
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
import type {
    GoalCreateInput,
    GoalRow,
    GoalUpdateInput,
} from "@/lib/api/types";
import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";

export type GoalFormMode = "create" | "update";

interface GoalFormProps {
    mode: GoalFormMode;
    goal?: GoalRow | null;
    isOpen: boolean;
    onOpenChange: (open: boolean) => void;
    onSubmit: (data: GoalCreateInput | GoalUpdateInput) => Promise<void>;
    isPending: boolean;
    error?: Error | null;
}

const GOAL_TYPES = [
    { value: "task", label: "Zadanie" },
    { value: "information_need", label: "Potrzeba informacji" },
    { value: "research_goal", label: "Cel badawczy" },
    { value: "maintenance_goal", label: "Utrzymanie" },
    { value: "learning_goal", label: "Nauka" },
    { value: "user_intent_goal", label: "Intencja użytkownika" },
    { value: "system_goal", label: "System" },
    { value: "long_term_goal", label: "Długoterminowy" },
];

export function GoalForm({
    mode,
    goal,
    isOpen,
    onOpenChange,
    onSubmit,
    isPending,
    error,
}: GoalFormProps) {
    const [formData, setFormData] = useState<GoalCreateInput | GoalUpdateInput>(
        mode === "create"
            ? {
                  title: "",
                  description: "",
                  goal_type: "task",
                  priority: 0.6,
                  urgency: 0.6,
                  importance: 0.65,
                  confidence: 0.7,
                  tags: [],
                  success_criteria: [],
                  failure_criteria: [],
              }
            : {
                  goal_id: goal?.goal_id ?? "",
                  status: goal?.status,
                  priority: goal?.priority,
                  urgency: goal?.urgency,
                  importance: goal?.importance,
                  confidence: goal?.confidence,
                  progress: goal?.progress,
              },
    );

    useEffect(() => {
        if (mode === "create") {
            setFormData({
                title: "",
                description: "",
                goal_type: "task",
                priority: 0.6,
                urgency: 0.6,
                importance: 0.65,
                confidence: 0.7,
                tags: [],
                success_criteria: [],
                failure_criteria: [],
            });
        } else if (goal) {
            setFormData({
                goal_id: goal.goal_id,
                status: goal.status,
                priority: goal.priority,
                urgency: goal.urgency,
                importance: goal.importance,
                confidence: goal.confidence,
                progress: goal.progress,
            });
        }
    }, [mode, goal, isOpen]);

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        if (
            mode === "create" &&
            "title" in formData &&
            (!formData.title.trim() ||
                !("description" in formData && formData.description?.trim()))
        ) {
            return;
        }
        await onSubmit(formData);
        onOpenChange(false);
    };

    const isCreateForm = mode === "create";

    return (
        <Dialog open={isOpen} onOpenChange={onOpenChange}>
            <DialogContent className="max-w-lg">
                <DialogHeader>
                    <DialogTitle>
                        {isCreateForm ? "Utwórz nowy cel" : "Edytuj cel"}
                    </DialogTitle>
                    <DialogDescription>
                        {isCreateForm
                            ? "Utwórz nowy cel w systemie."
                            : "Zaktualizuj atrybuty wybranego celu."}
                    </DialogDescription>
                </DialogHeader>

                {error ? (
                    <div className="rounded border border-red-800/60 bg-red-950/50 p-2 text-xs text-red-300">
                        {error.message}
                    </div>
                ) : null}

                <form className="space-y-4" onSubmit={handleSubmit}>
                    {isCreateForm ? (
                        <>
                            <div className="space-y-2">
                                <label
                                    htmlFor="title"
                                    className="text-sm block font-medium"
                                >
                                    Tytuł
                                </label>
                                <Input
                                    id="title"
                                    placeholder="Tytuł celu"
                                    value={
                                        ("title" in formData &&
                                            formData.title) ||
                                        ""
                                    }
                                    onChange={(e) =>
                                        setFormData((s) => ({
                                            ...s,
                                            title: e.target.value,
                                        }))
                                    }
                                    className="text-sm"
                                />
                            </div>

                            <div className="space-y-2">
                                <label
                                    htmlFor="description"
                                    className="text-sm block font-medium"
                                >
                                    Opis
                                </label>
                                <Textarea
                                    id="description"
                                    placeholder="Szczegółowy opis celu"
                                    value={
                                        ("description" in formData &&
                                            formData.description) ||
                                        ""
                                    }
                                    onChange={(e) =>
                                        setFormData((s) => ({
                                            ...s,
                                            description: e.target.value,
                                        }))
                                    }
                                    className="text-sm min-h-20"
                                />
                            </div>

                            <div className="space-y-2">
                                <label
                                    htmlFor="goal_type"
                                    className="text-sm block font-medium"
                                >
                                    Typ celu
                                </label>
                                <Select
                                    value={
                                        ("goal_type" in formData &&
                                            formData.goal_type) ||
                                        "task"
                                    }
                                    onValueChange={(v: string) =>
                                        setFormData((s) => ({
                                            ...s,
                                            goal_type: v,
                                        }))
                                    }
                                >
                                    <SelectTrigger className="text-sm">
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {GOAL_TYPES.map((gt) => (
                                            <SelectItem
                                                key={gt.value}
                                                value={gt.value}
                                            >
                                                {gt.label}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>
                        </>
                    ) : null}

                    {/* Shared controls for both create and update */}
                    <div className="grid grid-cols-2 gap-3">
                        <div className="space-y-2">
                            <label
                                htmlFor="priority"
                                className="text-xs block font-medium"
                            >
                                Priority (
                                {("priority" in formData &&
                                    formData.priority?.toFixed(2)) ||
                                    "0.60"}
                                )
                            </label>
                            <input
                                id="priority"
                                type="range"
                                min="0"
                                max="1"
                                step="0.05"
                                value={
                                    ("priority" in formData &&
                                        formData.priority) ||
                                    0.6
                                }
                                onChange={(e) =>
                                    setFormData((s) => ({
                                        ...s,
                                        priority: parseFloat(e.target.value),
                                    }))
                                }
                                className="w-full"
                            />
                        </div>

                        <div className="space-y-2">
                            <label
                                htmlFor="urgency"
                                className="text-xs block font-medium"
                            >
                                Urgency (
                                {("urgency" in formData &&
                                    formData.urgency?.toFixed(2)) ||
                                    "0.60"}
                                )
                            </label>
                            <input
                                id="urgency"
                                type="range"
                                min="0"
                                max="1"
                                step="0.05"
                                value={
                                    ("urgency" in formData &&
                                        formData.urgency) ||
                                    0.6
                                }
                                onChange={(e) =>
                                    setFormData((s) => ({
                                        ...s,
                                        urgency: parseFloat(e.target.value),
                                    }))
                                }
                                className="w-full"
                            />
                        </div>

                        <div className="space-y-2">
                            <label
                                htmlFor="importance"
                                className="text-xs block font-medium"
                            >
                                Importance (
                                {("importance" in formData &&
                                    formData.importance?.toFixed(2)) ||
                                    "0.65"}
                                )
                            </label>
                            <input
                                id="importance"
                                type="range"
                                min="0"
                                max="1"
                                step="0.05"
                                value={
                                    ("importance" in formData &&
                                        formData.importance) ||
                                    0.65
                                }
                                onChange={(e) =>
                                    setFormData((s) => ({
                                        ...s,
                                        importance: parseFloat(e.target.value),
                                    }))
                                }
                                className="w-full"
                            />
                        </div>

                        <div className="space-y-2">
                            <label
                                htmlFor="confidence"
                                className="text-xs block font-medium"
                            >
                                Confidence (
                                {("confidence" in formData &&
                                    formData.confidence?.toFixed(2)) ||
                                    "0.70"}
                                )
                            </label>
                            <input
                                id="confidence"
                                type="range"
                                min="0"
                                max="1"
                                step="0.05"
                                value={
                                    ("confidence" in formData &&
                                        formData.confidence) ||
                                    0.7
                                }
                                onChange={(e) =>
                                    setFormData((s) => ({
                                        ...s,
                                        confidence: parseFloat(e.target.value),
                                    }))
                                }
                                className="w-full"
                            />
                        </div>
                    </div>

                    {!isCreateForm && "progress" in formData ? (
                        <div className="space-y-2">
                            <label
                                htmlFor="progress"
                                className="text-xs block font-medium"
                            >
                                Progress (
                                {(
                                    formData.progress?.toFixed(2) || "0.00"
                                ).padStart(4, "0")}
                                )
                            </label>
                            <input
                                id="progress"
                                type="range"
                                min="0"
                                max="1"
                                step="0.05"
                                value={formData.progress || 0}
                                onChange={(e) =>
                                    setFormData((s) => ({
                                        ...s,
                                        progress: parseFloat(e.target.value),
                                    }))
                                }
                                className="w-full"
                            />
                        </div>
                    ) : null}

                    <DialogFooter>
                        <Button
                            type="button"
                            variant="outline"
                            onClick={() => onOpenChange(false)}
                            className="text-sm"
                        >
                            Anuluj
                        </Button>
                        <Button
                            type="submit"
                            disabled={isPending}
                            className="text-sm"
                        >
                            {isPending ? (
                                <Loader2 className="w-4 h-4 animate-spin" />
                            ) : null}
                            {isCreateForm ? "Utwórz" : "Zaaktualizuj"}
                        </Button>
                    </DialogFooter>
                </form>
            </DialogContent>
        </Dialog>
    );
}
