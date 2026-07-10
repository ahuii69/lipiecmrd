"use client";

import { AlertTriangle, CheckCircle2, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiClient } from "@/lib/api/client";

interface BackendHealthCheckProps {
    onHealthy?: () => void;
    onUnhealthy?: () => void;
}

export function BackendHealthCheck({
    onHealthy,
    onUnhealthy,
}: BackendHealthCheckProps) {
    const [status, setStatus] = useState<"loading" | "healthy" | "unhealthy">(
        "loading",
    );
    const [message, setMessage] = useState(
        "Sprawdzam połączenie z backendem...",
    );

    useEffect(() => {
        let mounted = true;

        const checkBackend = async () => {
            try {
                await Promise.race([
                    // /system/ping jest bez auth — mylące przy włączonym API_KEY. /cognitive/health wymaga klucza.
                    apiClient.cognitiveHealth(),
                    new Promise<never>((_, reject) =>
                        setTimeout(
                            () =>
                                reject(
                                    new Error("Backend health check timeout"),
                                ),
                            5000,
                        ),
                    ),
                ]);

                if (mounted) {
                    setStatus("healthy");
                    setMessage("Backend dostępny");
                    onHealthy?.();
                }
            } catch (err) {
                if (mounted) {
                    const errorMsg =
                        err instanceof Error ? err.message : "Unknown error";
                    setStatus("unhealthy");
                    setMessage(
                        `Backend niedostępny: ${errorMsg}. Sprawdź czy backend na ${process.env.AIHUB_BASE_URL || "http://127.0.0.1:8080"} żyje.`,
                    );
                    onUnhealthy?.();
                }
            }
        };

        void checkBackend();

        return () => {
            mounted = false;
        };
    }, [onHealthy, onUnhealthy]);

    if (status === "healthy") {
        return (
            <div className="flex items-center gap-2 rounded p-2 text-xs text-green-300">
                <CheckCircle2 className="h-4 w-4" />
                {message}
            </div>
        );
    }

    if (status === "unhealthy") {
        return (
            <Card className="border-red-700/50 bg-red-950/50">
                <CardHeader className="pb-2">
                    <CardTitle className="flex items-center gap-2 text-xs text-red-300">
                        <AlertTriangle className="h-4 w-4" />
                        Backend niedostępny
                    </CardTitle>
                </CardHeader>
                <CardContent className="text-xs text-red-200">
                    {message}
                </CardContent>
            </Card>
        );
    }

    return (
        <div className="flex items-center gap-2 rounded p-2 text-xs text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            {message}
        </div>
    );
}
