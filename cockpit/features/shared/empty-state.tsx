import { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

export function EmptyState({
    icon: Icon,
    title,
    description,
    className,
}: {
    icon?: LucideIcon;
    title: string;
    description: string;
    /** Nadpisanie tła/ramki (np. ciemny chat). */
    className?: string;
}) {
    return (
        <div
            className={cn(
                "flex h-full min-h-[160px] w-full flex-col items-center justify-center rounded-2xl border border-dashed border-border bg-card/60 p-6 text-center",
                className,
            )}
        >
            {Icon ? (
                <Icon className="mb-3 h-6 w-6 text-muted-foreground" />
            ) : null}
            <p className="text-sm font-semibold">{title}</p>
            <p className="mt-1 max-w-[520px] text-xs text-muted-foreground">
                {description}
            </p>
        </div>
    );
}
