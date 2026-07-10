/** Zustand persist w środowisku node — bez tego middleware loguje „storage unavailable”. */
const mem: Record<string, string> = {};
const ls = {
    getItem: (k: string) => (k in mem ? mem[k] : null),
    setItem: (k: string, v: string) => {
        mem[k] = v;
    },
    removeItem: (k: string) => {
        delete mem[k];
    },
    clear: () => {
        for (const k of Object.keys(mem)) delete mem[k];
    },
};
Object.defineProperty(globalThis, "localStorage", {
    value: ls,
    configurable: true,
});
