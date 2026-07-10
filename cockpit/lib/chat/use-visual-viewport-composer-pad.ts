"use client";

import { useEffect, useRef } from "react";

const CSS_VAR = "--cockpit-composer-vv-pad";

/**
 * Klawiatura mobilna: oblicza „wcięcie” od dołu layoutu względem visualViewport,
 * żeby pole kompozycji nie znikało pod klawiaturą (Safari/Chrome Android).
 */
export function useVisualViewportComposerPad(): void {
    const rafRef = useRef<number | null>(null);

    useEffect(() => {
        const vv = window.visualViewport;
        if (!vv) {
            return;
        }

        const apply = () => {
            if (rafRef.current !== null) {
                cancelAnimationFrame(rafRef.current);
            }
            rafRef.current = requestAnimationFrame(() => {
                rafRef.current = null;
                const innerH = window.innerHeight;
                const bottom = vv.offsetTop + vv.height;
                const pad = Math.max(0, Math.round(innerH - bottom));
                document.documentElement.style.setProperty(CSS_VAR, `${pad}px`);
            });
        };

        vv.addEventListener("resize", apply);
        vv.addEventListener("scroll", apply);
        apply();

        return () => {
            vv.removeEventListener("resize", apply);
            vv.removeEventListener("scroll", apply);
            if (rafRef.current !== null) {
                cancelAnimationFrame(rafRef.current);
            }
            document.documentElement.style.removeProperty(CSS_VAR);
        };
    }, []);
}
