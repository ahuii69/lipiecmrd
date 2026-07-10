"use client";

import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatJson } from "@/lib/utils";

export function JsonView({
    title,
    value,
    compact = false,
}: {
    title: string;
    value: unknown;
    compact?: boolean;
}) {
    const [raw, setRaw] = useState(false);
    const pretty = useMemo(() => formatJson(value), [value]);

    return (
        <Card className={compact ? "shadow-none" : undefined}>
            <CardHeader className="pb-2">
                <div className="flex items-center justify-between gap-2">
                    <CardTitle>{title}</CardTitle>
                    <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setRaw((v) => !v)}
                    >
                        {raw ? "Pretty" : "Raw"}
                    </Button>
                </div>
            </CardHeader>
            <CardContent>
                {raw ? (
                    <pre>{String(value)}</pre>
                ) : (
                    <pre className="max-h-[320px]">{pretty}</pre>
                )}
            </CardContent>
        </Card>
    );
}
