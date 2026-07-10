import { CapabilityExecuteResponse } from "@/lib/api/types";

export function unwrapCapabilityResult<T = Record<string, unknown>>(
    response: CapabilityExecuteResponse,
): T {
    const out = response.tool_result?.output as
        | { ok?: boolean; result?: T }
        | undefined;
    if (out?.result !== undefined) return out.result;
    return {} as T;
}

export function capabilityError(
    response: CapabilityExecuteResponse,
): string | null {
    if (response.ok) return null;
    return response.tool_result?.error || "Capability execution failed";
}
