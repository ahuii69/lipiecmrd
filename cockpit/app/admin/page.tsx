import { AppShell } from "@/components/layout/app-shell";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

export const dynamic = "force-dynamic";

export default async function AdminPage() {
    const cookieStore = await cookies();
    const sessionCookie = cookieStore.get("aihub_session");
    if (!sessionCookie?.value) redirect("/login");

    const backend = (
        process.env.AIHUB_BASE_URL || "http://127.0.0.1:8080"
    ).replace(/\/+$/, "");
    const response = await fetch(`${backend}/auth/me`, {
        headers: {
            cookie: `${sessionCookie.name}=${sessionCookie.value}`,
            accept: "application/json",
        },
        cache: "no-store",
    });
    if (!response.ok) redirect("/login");

    const body = (await response.json()) as { principal?: { role?: string } };
    if (body.principal?.role !== "admin") redirect("/");
    return <AppShell />;
}
