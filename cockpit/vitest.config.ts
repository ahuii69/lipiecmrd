import path from "path";
import { defineConfig } from "vitest/config";

/**
 * Kolejność wpisów w `include` ustawia kolejność odkrywania plików: najpierw
 * warstwa o wyższym znaczeniu regresji dla BFF i kontraktu z hubem, potem szersze scenariusze.
 *
 * Priorytet wysoki: `lib/api/**` (allowlista proxy, klucze), `lib/chat/**` (payload czatu),
 * `lib/store/**` (transkrypt / stan UI).
 * Priorytet niższy: `tests/**` (np. upload — szerszy harness w Node).
 */
export default defineConfig({
    test: {
        environment: "node",
        setupFiles: ["./vitest.setup.ts"],
        include: [
            "lib/api/**/*.test.ts",
            "lib/chat/**/*.test.ts",
            "lib/store/**/*.test.ts",
            "tests/**/*.test.ts",
        ],
    },
    resolve: {
        alias: {
            "@": path.resolve(__dirname, "."),
        },
    },
});
