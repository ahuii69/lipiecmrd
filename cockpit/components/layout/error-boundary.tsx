"use client";

import { AlertTriangle, RefreshCw } from "lucide-react";
import React, { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

interface ErrorBoundaryProps {
    children: ReactNode;
    fallback?: ReactNode;
}

interface ErrorBoundaryState {
    hasError: boolean;
    error: Error | null;
}

export class ErrorBoundary extends React.Component<
    ErrorBoundaryProps,
    ErrorBoundaryState
> {
    constructor(props: ErrorBoundaryProps) {
        super(props);
        this.state = { hasError: false, error: null };
    }

    static getDerivedStateFromError(error: Error): ErrorBoundaryState {
        return { hasError: true, error };
    }

    componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
        console.error("ErrorBoundary caught error:", error, errorInfo);
    }

    reset = () => {
        this.setState({ hasError: false, error: null });
        window.location.reload();
    };

    render() {
        if (this.state.hasError) {
            return (
                <div className="flex h-screen items-center justify-center p-4">
                    <Card className="max-w-md border-red-700/50 bg-red-950/50">
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2 text-red-300">
                                <AlertTriangle className="h-5 w-5" />
                                Coś się nie powiodło
                            </CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <div className="rounded bg-red-950/70 p-3 text-sm text-red-200">
                                <p className="mb-2 font-mono text-xs">
                                    {this.state.error?.message ||
                                        "Nieznany błąd"}
                                </p>
                                {process.env.NODE_ENV === "development" && (
                                    <details className="text-xs text-red-300/70">
                                        <summary>Szczegóły</summary>
                                        <pre className="mt-2 overflow-auto whitespace-pre-wrap break-words">
                                            {this.state.error?.stack}
                                        </pre>
                                    </details>
                                )}
                            </div>
                            <Button
                                onClick={this.reset}
                                variant="outline"
                                className="w-full"
                            >
                                <RefreshCw className="mr-2 h-4 w-4" />
                                Odśwież
                            </Button>
                        </CardContent>
                    </Card>
                </div>
            );
        }

        return this.props.children;
    }
}
