import { ErrorBoundary } from "@/components/layout/error-boundary";
import { UserShell } from "@/features/user-chat/user-shell";

export default function Page() {
    return (
        <ErrorBoundary>
            <UserShell />
        </ErrorBoundary>
    );
}
