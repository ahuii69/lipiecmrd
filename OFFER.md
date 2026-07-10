# Oferta produktowa (szkic)

Skrót pod klienta B2B i handoff VPS: [COMMERCIAL.md](COMMERCIAL.md).

## Dla kogo

- **Zespoły operatorskie / DevOps AI** — potrzebują jednego API czatu z narzędziami, pamięcią i audytem trace.
- **Firmy budujące asystentów wewnętrznych** — self-host, kontrola danych, vault na sekrety.
- **Integratorzy** — Cockpit jako UI referencyjny + BFF Next pod własny branding.

## Co dostarcza produkt

- Backend **FastAPI** z czatem, pamięcią, agentem, web/research, cockpit HTTP.
- Frontend **Cockpit** (Next.js) — panele diagnostyczne i czat operatorski.
- **Vault** na poświadczenia użytkownika końcowego (nie zamiast HSM ani enterprise KMS).
- Skrypty smoke i brama runtime (`scripts/smoke_runtime.sh`, `python -m aihub.scripts.final_runtime_gate`).

## Model licencji

Oprogramowanie w repozytorium podlega **własnościowej licencji** — patrz [LICENSE](LICENSE). Nie jest to open source (brak MIT/GPL). Komercyjne użycie wymaga **osobnej umowy** z właścicielem praw.

## Co NIE jest obietnicą „z pudełka”

- SLA 99,99% bez wdrożenia infrastruktury klienta.
- Zgodność HIPAA/GDPR bez analizy DPA i architektury danych.
- Nielimitowany koszt tokenów LLM i API zewnętrznych.

## Kontakt handlowy

Dane właściciela praw: [LICENSE](LICENSE). Kanał kontaktu uzupełnij we własnej dokumentacji sprzedażowej (nie wersjonujemy publicznego e-maila w repo, jeśli nie został podany).
