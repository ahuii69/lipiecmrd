# AI-Hub Cockpit (Next.js)

Desktop-first cockpit operatorski dla backendu AI-Hub.

## Stack

- Next.js (App Router)
- TypeScript
- Tailwind CSS
- shadcn/ui style components
- TanStack Query
- Zustand
- lucide-react

## Co robi aplikacja

- realny czat przez `/chat/turn`
- capability explorer przez `/chat/capabilities`
- planner preview przez capability `planner.build_task_graph`
- reasoning preview przez capability `reasoning.run_preview`
- goals: list/create/update/complete/fail + trace
- runtime status/trace
- system health
- right inspector: trace/tools/goal/usage/debug dla wiadomości

## Wymagania

- Node.js 20+
- działający backend AI-Hub

## Konfiguracja

Skopiuj `.env.example` do `.env.local` i ustaw wartości.

```bash
cp .env.example .env.local
```

## Uruchomienie

```bash
npm install
npm run dev
```

Domyślnie frontend działa pod `http://localhost:3000`.

## Architektura endpointów

Frontend używa serwerowego proxy Next (`/api/aihub/*`) i forwarduje do backendu AI-Hub:

- `/chat/turn` — chat runtime
- `/chat/capabilities` — capability registry
- `/chat/capabilities/execute` — deterministyczne wykonanie capability
- `/agent/run`, `/agent/loop`, `/agent/status/{user_id}` — runtime cycle/status
- `/agent/goals/{user_id}/{goal_id}/trace` — trace celu
- `/system/ping`, `/system/health/{user_id}`, `/cognitive/health` — health/diagnostyka

Proxy wstrzykuje nagłówek `x-api-key` z `AIHUB_API_KEY` lub z UI override.
