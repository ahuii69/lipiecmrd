import { LucideIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";

export function CoreLayerNote({
    title,
    description,
    icon: Icon,
}: {
    title: string;
    description: string;
    icon: LucideIcon;
}) {
    return (
        <div className="rounded-md border border-border bg-card/50 p-3 text-xs">
            <div className="mb-1 flex items-center gap-2">
                <Icon className="h-4 w-4 text-primary" />
                <p className="text-sm font-semibold">{title}</p>
                <Badge variant="secondary">core runtime layer</Badge>
            </div>
            <p className="text-muted-foreground">{description}</p>
        </div>
    );
}
