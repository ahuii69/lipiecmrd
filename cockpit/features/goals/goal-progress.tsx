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
            <div className="h-2 w-full overflow-hidden rounded bg-muted">
                <div
                    className="h-full bg-primary transition-all"
                    style={{ width: `${clamped * 100}%` }}
                />
            </div>
            <p className="text-[11px] text-muted-foreground">
                progress {(clamped * 100).toFixed(0)}%
            </p>
        </div>
    );
}
