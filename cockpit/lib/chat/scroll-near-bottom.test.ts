import { describe, expect, it } from "vitest";

import {
    CHAT_NEAR_BOTTOM_PX,
    isScrollContainerNearBottom,
    nearBottomThresholdPx,
} from "@/lib/chat/scroll-near-bottom";

function mockEl(opts: {
    scrollTop: number;
    scrollHeight: number;
    clientHeight: number;
}): HTMLElement {
    return opts as unknown as HTMLElement;
}

describe("isScrollContainerNearBottom", () => {
    it("true gdy przy dnie w progu", () => {
        const el = mockEl({
            scrollHeight: 1000,
            clientHeight: 500,
            scrollTop: 1000 - 500 - 50,
        });
        expect(isScrollContainerNearBottom(el)).toBe(true);
    });

    it("false gdy user przewinął wyżej", () => {
        const el = mockEl({
            scrollHeight: 1000,
            clientHeight: 500,
            scrollTop: 0,
        });
        expect(isScrollContainerNearBottom(el)).toBe(false);
    });

    it("próg ma sensowną wartość", () => {
        expect(CHAT_NEAR_BOTTOM_PX).toBeGreaterThanOrEqual(48);
    });

    it("na wysokim viewportcie próg rośnie z wysokością (mobile)", () => {
        const tall = mockEl({
            scrollHeight: 2000,
            clientHeight: 900,
            scrollTop: 2000 - 900 - 100,
        });
        expect(nearBottomThresholdPx(tall)).toBeGreaterThanOrEqual(
            CHAT_NEAR_BOTTOM_PX,
        );
        expect(isScrollContainerNearBottom(tall)).toBe(true);
    });
});
