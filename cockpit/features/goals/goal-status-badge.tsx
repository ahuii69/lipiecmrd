import { Badge } from "@/components/ui/badge";
import { GoalStatus } from "@/lib/api/types";

import { goalStatusTone } from "./goals-parser";

export function GoalStatusBadge({ status }: { status: GoalStatus | string }) {
    return <Badge variant={goalStatusTone(status)}>{status}</Badge>;
}
