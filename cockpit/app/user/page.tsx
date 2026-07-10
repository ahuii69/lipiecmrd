import type { Metadata } from "next";
import { ErrorBoundary } from "@/components/layout/error-boundary";
import { UserShell } from "@/features/user-chat/user-shell";

export const metadata: Metadata = { title: "AI-Hub Chat", description: "Profesjonalny frontend AI-Hub z realnym backendem, pamięcią i streamingiem." };
export default function UserPage() { return <ErrorBoundary><UserShell /></ErrorBoundary>; }
