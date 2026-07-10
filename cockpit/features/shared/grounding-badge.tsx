import { Badge } from "@/components/ui/badge";
import type { GroundingMode } from "@/lib/types/ui";

const modeMap: Record<
    GroundingMode,
    { label: string; variant: "secondary" | "success" | "warning" | "danger" }
> = {
    model_only: { label: "Model-only", variant: "secondary" },
    tool_verified: { label: "Tool-verified", variant: "success" },
    fallback: { label: "Fallback", variant: "warning" },
    unknown_not_verified: { label: "Niezweryfikowane", variant: "danger" },
};

export function GroundingBadge({ mode }: { mode?: string }) {
    if (!mode || !(mode in modeMap)) {
        return <Badge variant="outline">Brak klasyfikacji</Badge>;
    }

    const cfg = modeMap[mode as GroundingMode];
    return <Badge variant={cfg.variant}>{cfg.label}</Badge>;
}
