"use client";

import { Menu, PanelRightOpen } from "lucide-react";

import { BackendHealthCheck } from "@/components/layout/backend-health-check";
import { LeftSidebar } from "@/components/layout/left-sidebar";
import { RightInspector } from "@/components/layout/right-inspector";
import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
} from "@/components/ui/dialog";
import { AgentControlPanel } from "@/features/agent-control/agent-control-panel";
import { CalibrationPanel } from "@/features/calibration/calibration-panel";
import { CapabilitiesPanel } from "@/features/capabilities/capabilities-panel";
import { ChatPanel } from "@/features/chat/chat-panel";
import { ConsistencyPanel } from "@/features/diagnostics/consistency-panel";
import { PolicyPanel } from "@/features/diagnostics/policy-panel";
import { ReflectionsPanel } from "@/features/diagnostics/reflections-panel";
import { SimulationsPanel } from "@/features/diagnostics/simulations-panel";
import { GoalsPanel } from "@/features/goals/goals-panel";
import { MemoryV2Panel } from "@/features/memory-v2/memory-v2-panel";
import { OverviewPanel } from "@/features/overview/overview-panel";
import { PlannerPanel } from "@/features/planner/planner-panel";
import { ProceduresPanel } from "@/features/procedures/procedures-panel";
import { PsycheV2Panel } from "@/features/psyche-v2/psyche-v2-panel";
import { ReasoningPanel } from "@/features/reasoning/reasoning-panel";
import { RuntimePanel } from "@/features/runtime/runtime-panel";
import { SystemPanel } from "@/features/system/system-panel";
import { useCockpitStore } from "@/lib/store/cockpit-store";
import { ContradictionsPanel } from "../../features/contradictions/contradictions-panel";
import { IdentityPanel } from "../../features/identity/identity-panel";
import { MemoryPanel } from "../../features/memory/memory-panel";
import { PsychePanel } from "../../features/psyche/psyche-panel";
import { ResearchPanel } from "../../features/research/research-panel";

function MainPanel() {
    const section = useCockpitStore((s) => s.currentSection);
    const activeSessionId = useCockpitStore((s) => s.activeSessionId);
    const sessions = useCockpitStore((s) => s.sessions);
    const activeSession = sessions.find((s) => s.id === activeSessionId);

    switch (section) {
        case "chat":
            return <ChatPanel />;
        case "memory":
            return <MemoryPanel />;
        case "psyche":
            return <PsychePanel />;
        case "research":
            return <ResearchPanel />;
        case "planner":
            return <PlannerPanel />;
        case "reasoning":
            return <ReasoningPanel />;
        case "goals":
            return <GoalsPanel />;
        case "runtime":
            return <RuntimePanel />;
        case "capabilities":
            return <CapabilitiesPanel />;
        case "system":
            return <SystemPanel />;
        case "agent-control":
            return <AgentControlPanel />;
        case "overview":
            return <OverviewPanel />;
        case "consistency":
            return <ConsistencyPanel />;
        case "reflections":
            return <ReflectionsPanel />;
        case "policy":
            return <PolicyPanel />;
        case "simulations":
            return <SimulationsPanel />;
        case "memory-v2":
            return <MemoryV2Panel />;
        case "psyche-v2":
            return <PsycheV2Panel />;
        case "identity":
            return <IdentityPanel />;
        case "contradictions":
            return <ContradictionsPanel />;
        case "procedures":
            return <ProceduresPanel />;
        case "calibration":
            return <CalibrationPanel userId={activeSession?.userId || ""} />;
        default:
            return <ChatPanel />;
    }
}

export function AppShell() {
    return (
        <div className="flex h-[100dvh] max-h-[100dvh] w-full min-w-0 flex-col overflow-hidden p-2 pt-[max(0.5rem,env(safe-area-inset-top))] sm:p-3">
            <div className="mb-2 shrink-0 space-y-2 rounded-xl border border-border bg-card/70 px-2 py-2 shadow-panel sm:mb-3 sm:px-3">
                <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                        <Dialog>
                            <DialogTrigger asChild>
                                <Button
                                    variant="outline"
                                    size="icon"
                                    className="lg:hidden"
                                >
                                    <Menu className="h-4 w-4" />
                                </Button>
                            </DialogTrigger>
                            <DialogContent className="max-w-[90vw]">
                                <DialogHeader>
                                    <DialogTitle>Nawigacja AI-Hub</DialogTitle>
                                </DialogHeader>
                                <div className="h-[75vh]">
                                    <LeftSidebar />
                                </div>
                            </DialogContent>
                        </Dialog>
                        <p className="text-sm font-bold">AI-Hub Cockpit</p>
                    </div>

                    <Dialog>
                        <DialogTrigger asChild>
                            <Button
                                variant="outline"
                                size="sm"
                                className="xl:hidden"
                            >
                                <PanelRightOpen className="mr-1 h-4 w-4" />
                                Inspector
                            </Button>
                        </DialogTrigger>
                        <DialogContent className="max-w-[95vw]">
                            <DialogHeader>
                                <DialogTitle>Inspector runtime</DialogTitle>
                            </DialogHeader>
                            <div className="h-[75vh]">
                                <RightInspector />
                            </div>
                        </DialogContent>
                    </Dialog>
                </div>
                <div className="px-1">
                    <BackendHealthCheck />
                </div>
            </div>

            <div className="grid min-h-0 flex-1 grid-cols-1 gap-2 overflow-hidden sm:gap-3 lg:grid-cols-[minmax(0,280px)_1fr] xl:grid-cols-[minmax(0,280px)_1fr_minmax(0,360px)]">
                <div className="hidden min-h-0 lg:block">
                    <LeftSidebar />
                </div>
                <div className="flex min-h-0 min-w-0 flex-col overflow-hidden rounded-xl border border-border bg-card/40 p-2 pb-[max(0.5rem,env(safe-area-inset-bottom))] shadow-panel sm:p-3 sm:pb-3">
                    <MainPanel />
                </div>
                <div className="hidden xl:block">
                    <RightInspector />
                </div>
            </div>
        </div>
    );
}
