/** Piksel od dołu — baza progu „przy dole”. */
export const CHAT_NEAR_BOTTOM_PX = 96;

/** Na wysokich ekranach (mobile) procent wysokości okna lepiej łapie „prawie na dole”. */
const NEAR_BOTTOM_VIEWPORT_RATIO = 0.12;

export function nearBottomThresholdPx(el: HTMLElement): number {
    const h = el.clientHeight;
    const fromRatio = Math.round(h * NEAR_BOTTOM_VIEWPORT_RATIO);
    return Math.max(CHAT_NEAR_BOTTOM_PX, fromRatio);
}

export function isScrollContainerNearBottom(el: HTMLElement): boolean {
    const { scrollTop, scrollHeight, clientHeight } = el;
    const distanceFromBottom = scrollHeight - clientHeight - scrollTop;
    return distanceFromBottom <= nearBottomThresholdPx(el);
}

export function scrollContainerToBottom(
    el: HTMLElement,
    behavior: ScrollBehavior,
): void {
    el.scrollTo({ top: el.scrollHeight, behavior });
}
