"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

interface ProgressProps extends React.HTMLAttributes<HTMLDivElement> {
    value?: number;
}

/** Width via transform scaleX — avoids CSP-blocked inline style attributes. */
const Progress = React.forwardRef<HTMLDivElement, ProgressProps>(
    ({ className, value = 0, ...props }, ref) => {
        const percent = Math.min(Math.max(value, 0), 100);
        const step = Math.round(percent / 5) * 5;
        return (
            <div
                ref={ref}
                className={cn(
                    "relative h-2 w-full overflow-hidden rounded-full bg-secondary",
                    className,
                )}
                {...props}
            >
                <div
                    className={cn(
                        "h-full origin-left bg-primary transition-transform",
                        step === 0 && "scale-x-0",
                        step === 5 && "scale-x-[0.05]",
                        step === 10 && "scale-x-[0.10]",
                        step === 15 && "scale-x-[0.15]",
                        step === 20 && "scale-x-[0.20]",
                        step === 25 && "scale-x-[0.25]",
                        step === 30 && "scale-x-[0.30]",
                        step === 35 && "scale-x-[0.35]",
                        step === 40 && "scale-x-[0.40]",
                        step === 45 && "scale-x-[0.45]",
                        step === 50 && "scale-x-[0.50]",
                        step === 55 && "scale-x-[0.55]",
                        step === 60 && "scale-x-[0.60]",
                        step === 65 && "scale-x-[0.65]",
                        step === 70 && "scale-x-[0.70]",
                        step === 75 && "scale-x-[0.75]",
                        step === 80 && "scale-x-[0.80]",
                        step === 85 && "scale-x-[0.85]",
                        step === 90 && "scale-x-[0.90]",
                        step === 95 && "scale-x-[0.95]",
                        step >= 100 && "scale-x-100",
                    )}
                />
            </div>
        );
    },
);
Progress.displayName = "Progress";

export { Progress };
