import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";

export function GoalProgress({
    value,
    className,
}: {
    value: number;
    className?: string;
}) {
    const clamped = Math.max(
        0,
        Math.min(1, Number.isFinite(value) ? value : 0),
    );

    return (
        <div className={cn("space-y-1", className)}>
            <Progress value={clamped * 100} />
            <p className="text-[11px] text-muted-foreground">
                progress {(clamped * 100).toFixed(0)}%
            </p>
        </div>
    );
}
