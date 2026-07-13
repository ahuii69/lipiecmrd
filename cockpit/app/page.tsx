import { ErrorBoundary } from "@/components/layout/error-boundary";
import { ChatShell } from "@/features/chat/ChatShell";

export default function Page() {
    return (
        <ErrorBoundary>
            <ChatShell />
        </ErrorBoundary>
    );
}
