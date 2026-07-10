"use client";

import type { CapabilityDescriptor } from "@/lib/api/types";

export interface CapabilityView {
    name: string;
    group: string;
    description: string;
    mode: "read" | "write";
    enabled: boolean;
    timeout: number;
    confirmsRequired: boolean;
    visibility: string[];
    inputSchema: Record<string, unknown>;
    outputSchema: Record<string, unknown>;
}

export interface CapabilitiesGroupView {
    group: string;
    count: number;
    items: CapabilityView[];
    readOnlyCount: number;
    mutatableCount: number;
}

function safeString(value: unknown): string {
    if (typeof value === "string") return value;
    if (typeof value === "number") return String(value);
    return "";
}

function normalizeCapability(cap: CapabilityDescriptor): CapabilityView {
    return {
        name: safeString(cap.name),
        group: safeString(cap.capability_group) || "andere",
        description: safeString(cap.description),
        mode: cap.read_only ? "read" : "write",
        enabled: cap.enabled === true,
        timeout:
            typeof cap.timeout_seconds === "number" ? cap.timeout_seconds : 30,
        confirmsRequired: cap.requires_confirmation === true,
        visibility: Array.isArray(cap.visibility)
            ? cap.visibility.map((v) => safeString(v))
            : [],
        inputSchema:
            cap.input_schema &&
            typeof cap.input_schema === "object" &&
            !Array.isArray(cap.input_schema)
                ? (cap.input_schema as Record<string, unknown>)
                : {},
        outputSchema:
            cap.output_schema &&
            typeof cap.output_schema === "object" &&
            !Array.isArray(cap.output_schema)
                ? (cap.output_schema as Record<string, unknown>)
                : {},
    };
}

export function normalizeCapabilities(
    capabilities: CapabilityDescriptor[] | undefined,
): CapabilitiesGroupView[] {
    if (!capabilities || !Array.isArray(capabilities)) {
        return [];
    }

    const normalized = capabilities.map(normalizeCapability);

    const grouped = new Map<string, CapabilityView[]>();
    for (const cap of normalized) {
        const { group } = cap;
        const items = grouped.get(group) || [];
        items.push(cap);
        grouped.set(group, items);
    }

    return [...grouped.entries()]
        .sort((a, b) => a[0].localeCompare(b[0]))
        .map(([group, items]) => ({
            group,
            count: items.length,
            items: items.sort((a, b) => a.name.localeCompare(b.name)),
            readOnlyCount: items.filter((i) => i.mode === "read").length,
            mutatableCount: items.filter((i) => i.mode === "write").length,
        }));
}

export function filterCapabilities(
    groups: CapabilitiesGroupView[],
    query: string,
): CapabilitiesGroupView[] {
    if (!query.trim()) return groups;

    const needle = query.toLowerCase();
    return groups
        .map((g) => ({
            ...g,
            items: g.items.filter(
                (c) =>
                    c.name.toLowerCase().includes(needle) ||
                    c.description.toLowerCase().includes(needle) ||
                    c.group.toLowerCase().includes(needle),
            ),
        }))
        .filter((g) => g.items.length > 0);
}
