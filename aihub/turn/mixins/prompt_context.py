"""TurnOps mixin: PromptContextMixin."""
from __future__ import annotations

import aihub.turn._ops_ns as _ops_ns

# Method bodies resolve names via this module's globals (incl. _names).
globals().update({k: v for k, v in vars(_ops_ns).items() if k != '__name__'})
TurnOps = None  # late-bound by aihub.turn.ops

class PromptContextMixin:
    def _build_memory_brief(
        self,
        memory_context: dict[str, Any],
        *,
        include_stm: bool = True,
    ) -> str:
        if not isinstance(memory_context, dict):
            return "BRAK DANYCH"

        stm = memory_context.get("stm") or []
        episodic = memory_context.get("episodic") or []
        semantic = memory_context.get("semantic") or []
        dense = memory_context.get("dense_hits") or []
        graph = memory_context.get("graph_hits") or []
        total = int(memory_context.get("total") or 0)

        if (
            total <= 0
            and not episodic
            and not semantic
            and not dense
            and not graph
            and not (include_stm and stm)
        ):
            return "Brak trafień pamięci dla tej wiadomości."

        stm_lines: list[str] = []
        if include_stm:
            for item in stm[-10:]:
                if isinstance(item, dict):
                    role = str(item.get("role") or "")
                    body = str(item.get("content") or "")
                    stm_lines.append(f"- [{role}] {body[:220]}")

        epi_lines = []
        for item in episodic[:2]:
            if isinstance(item, dict):
                epi_lines.append(f"- {str(item.get('content', ''))[:180]}")

        sem_lines = []
        for item in semantic[:4]:
            if isinstance(item, dict):
                sem_lines.append(f"- {str(item.get('content', ''))[:180]}")

        dense_lines = []
        for item in dense[:3]:
            if isinstance(item, dict):
                dense_lines.append(f"- {str(item.get('text', ''))[:160]}")

        if include_stm:
            stm_block = (
                "STM (ostatnia sesja, chronologicznie — najniższy priorytet faktów):\n"
                f"{chr(10).join(stm_lines) if stm_lines else '- brak'}\n"
            )
        else:
            stm_block = (
                "STM: pominięty w skrócie — bieżąca sesja jest w historii wiadomości; "
                "poniżej tylko LTM / retrieval.\n"
            )

        # Priorytet odczytu dla modelu: L2 (fakty) → wektor → epizody → STM na końcu.
        return (
            f"total={total}; stm={len(stm)}; episodic={len(episodic)}; semantic={len(semantic)}; "
            f"dense={len(dense)}; graph={len(graph)}\n"
            f"PRIORYTET: najpierw FAKTY (L2), potem VECTOR, potem EPISODIC; nie używaj epizodu jeśli "
            f"jest trafienie L2 na to samo pytanie.\n"
            f"Semantic (L2 fakty) top:\n{chr(10).join(sem_lines) if sem_lines else '- brak'}\n"
            f"Dense (vector) top:\n{chr(10).join(dense_lines) if dense_lines else '- brak'}\n"
            f"Episodic (L1) top:\n{chr(10).join(epi_lines) if epi_lines else '- brak'}\n"
            f"{stm_block}"
        )

    @staticmethod
    def _build_memory_used_trace(
        memory_context: dict[str, Any],
        *,
        include_stm: bool = True,
    ) -> list[dict[str, Any]]:
        """Observability: snapshot tego, co faktycznie poszło do promptu (STM opcjonalnie)."""
        out: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        def _sha1_text(s: str) -> str:
            return hashlib.sha1(s.encode("utf-8", errors="replace")).hexdigest()

        def _add(
            source: str,
            mid: str,
            text: str,
            extra: dict[str, Any] | None = None,
        ) -> None:
            t = (text or "").strip()
            if not t:
                return
            key = (source, mid)
            if key in seen:
                return
            seen.add(key)
            row: dict[str, Any] = {
                "id": mid,
                "text": t[:2000],
                "source": source,
                "used": True,
            }
            if extra:
                row.update(extra)
            out.append(row)

        if not isinstance(memory_context, dict):
            return out

        for m in (memory_context.get("stm") or [])[:15] if include_stm else []:
            if not isinstance(m, dict):
                continue
            raw_id = str(m.get("id") or "").strip()
            content = str(m.get("content") or "")
            mid = raw_id or _sha1_text(content)
            role = str(m.get("role") or "")
            _add("stm", mid, f"[{role}] {content}" if role else content)

        for m in (memory_context.get("semantic") or [])[:12]:
            if not isinstance(m, dict):
                continue
            content = str(m.get("content") or "")
            mid = str(m.get("id") or "").strip() or _sha1_text(content)
            _add("L2", mid, content)

        for m in (memory_context.get("dense_hits") or [])[:8]:
            if not isinstance(m, dict):
                continue
            text = str(m.get("text") or "")
            _add("vector", _sha1_text(text), text)

        for m in (memory_context.get("episodic") or [])[:12]:
            if not isinstance(m, dict):
                continue
            content = str(m.get("content") or "")
            mid = str(m.get("id") or "").strip() or _sha1_text(content)
            _add("L1", mid, content)

        for m in (memory_context.get("graph_hits") or [])[:12]:
            if not isinstance(m, dict):
                continue
            content = str(m.get("content") or "")
            mid = str(m.get("node_id") or "").strip() or _sha1_text(content)
            _add("graph", mid, content)

        for m in (memory_context.get("memory_v2_items") or [])[:20]:
            if not isinstance(m, dict):
                continue
            title = str(m.get("title") or "")
            content = str(m.get("content") or "")
            combined = f"{title}: {content}".strip(": ").strip() if title else content
            mid = str(m.get("id") or "").strip() or _sha1_text(f"{title}|{content}")
            extra_v2: dict[str, Any] = {}
            for fk in ("is_suppressed", "is_pinned", "is_archived"):
                if fk in m:
                    extra_v2[fk] = bool(m.get(fk))
            _add("memory_v2", mid, combined, extra_v2 if extra_v2 else None)

        return out

    @staticmethod
    def _augment_memory_observability(
        trace: dict[str, Any],
        memory_used_trace: list[dict[str, Any]] | None,
        memory_context: dict[str, Any] | None = None,
    ) -> None:
        used = list(memory_used_trace or [])
        trace["memory_used_bool"] = bool(used)
        trace["memory_hits"] = len(used)
        sources: list[str] = []
        for row in used:
            src = str(row.get("source") or "").strip()
            if src and src not in sources:
                sources.append(src)
        trace["memory_source"] = sources
        mc = memory_context if isinstance(memory_context, dict) else None
        if mc:
            errs = mc.get("memory_read_errors")
            if isinstance(errs, list) and errs:
                trace["memory_read_errors"] = list(errs)
            ro = mc.get("retrieval_priority_order")
            if isinstance(ro, list) and ro:
                trace["memory_retrieval_priority_order"] = list(ro)
            pack = mc.get("context_pack")
            if isinstance(pack, dict):
                trace["memory_context_pack_selected_ids"] = list(pack.get("selected_ids") or [])
                trace["memory_context_pack_used_chars"] = int(pack.get("used_chars") or 0)
                trace["memory_context_pack_source_distribution"] = dict(pack.get("source_distribution") or {})
                trace["memory_context_pack_injected"] = bool(pack.get("selected_ids"))

    @staticmethod
    def _correction_trace_flat(
        corr: dict[str, Any] | None, *, hints_chars: int = 0
    ) -> dict[str, Any]:
        t = corr if isinstance(corr, dict) else {}
        return {
            "user_correction_recorded": bool(t.get("recorded")),
            "user_correction_kind": t.get("kind"),
            "user_correction_durable_marked": bool(t.get("durable")),
            "correction_hints_in_prompt_chars": int(hints_chars),
        }

    @staticmethod
    def _correction_trace_fields(ctx: ChatTurnContext | None) -> dict[str, Any]:
        if not ctx:
            return {}
        ct = ctx.system_context.get("correction_turn_trace")
        if not isinstance(ct, dict):
            ct = {}
        hints = str(ctx.system_context.get("correction_hints_text") or "")
        return TurnOps._correction_trace_flat(ct, hints_chars=len(hints))

    @staticmethod
    def _effective_attached_file_ids(turn: ChatTurnInput) -> list[str]:
        """ID załączników z żądania lub (fallback) ostatnie pliki sesji przy odwołaniach wskazujących."""
        raw = [
            str(x).strip()
            for x in (turn.attached_file_ids or [])
            if str(x).strip()
        ][:MAX_FILES_PER_TURN]
        if raw:
            return raw
        msg = (turn.message or "").strip()
        if not msg or _SESSION_ATTACHMENT_DEICTIC_RE.search(msg) is None:
            return []
        return fetch_recent_session_attachment_ids(
            user_id=turn.user_id,
            session_id=turn.session_id,
            limit=MAX_FILES_PER_TURN,
        )

    def _build_psyche_brief(self, psyche_state: dict[str, Any]) -> str:
        compact = self._compact_psyche_state(psyche_state)
        if not compact:
            return "BRAK DANYCH"

        mood = compact.get("mood")
        energy = compact.get("energy")
        focus = compact.get("focus")
        style = compact.get("style")
        traits = compact.get("traits") or {}

        directness = traits.get("directness", "BRAK DANYCH")

        # NOTE (06.07 response-quality fix): we intentionally no longer inject raw sarcasm/swearing
        # trait values into the prompt. Surfacing them pushed the model toward theatrical, personified
        # replies. Psyche now only hints at *tone modulation* (directness / brevity / warmth); it must
        # never justify fake biography, aggression, or mirroring the user's hostile tone.
        return (
            f"style={style}, mood={mood}, energy={energy}, focus={focus}, directness={directness}. "
            "Traktuj to wyłącznie jako subtelną modulację tonu (bezpośredniość, ciepło, zwięzłość) — "
            "nie zmieniaj przez to faktów, nie personifikuj się i nie kopiuj agresywnego tonu użytkownika "
            "(zakaz kopiowania tonu wcześniejszej kłótni)."
        )

    def _build_system_prompt(
        self,
        ctx: ChatTurnContext,
        *,
        memory_brief: str,
        psyche_brief: str,
        decision_hints: str = "",
        correction_hints: str = "",
        memory_v2_context=None,
        psyche_v2_context=None,
        files_context: str = "",
        first_turn_in_thread: bool,
        history_rollup: str | None = None,
        listing_sales_boost: bool = False,
    ) -> str:
        caps = [f"- {c.name}: {c.description}" for c in ctx.capabilities]
        capabilities_text = "\n".join(caps) if caps else "- brak dostępnych narzędzi"

        # Behavioral instructions from Psyche V2
        behavior_instructions = ""
        if psyche_v2_context and psyche_v2_context.loaded:
            style_parts = []

            # Directness
            if psyche_v2_context.directness_bias > 0.7:
                style_parts.append(
                    "Możesz być bardziej bezpośredni i konkretny, ale tylko o tyle, o ile nie psuje to zadania."
                )
            elif psyche_v2_context.directness_bias < 0.3:
                style_parts.append(
                    "Możesz lekko zwiększyć ostrożność i niuans, bez rozwlekania odpowiedzi."
                )

            # Verbosity
            if psyche_v2_context.verbosity_bias < 0.3:
                style_parts.append("Trzymaj odpowiedzi zwięźle — bez lania wody.")
            elif psyche_v2_context.verbosity_bias > 0.7:
                style_parts.append(
                    "Możesz rozwinąć więcej szczegółów tam, gdzie naprawdę pomagają."
                )

            # Caution
            if psyche_v2_context.caution_bias > 0.7 or psyche_v2_context.pressure > 0.6:
                style_parts.append(
                    "Wysoka ostrożność: weryfikuj starannie, zaznaczaj niepewności, unikaj zbyt pewnych twierdzeń."
                )

            # Friction
            if psyche_v2_context.friction > 0.5:
                style_parts.append(
                    "Przy napięciu relacyjnym zwiększ precyzję i ogranicz luźne interpretacje."
                )

            # Warmth
            if psyche_v2_context.warmth > 0.7 and psyche_v2_context.trust > 0.6:
                style_parts.append(
                    "Przy wysokim trust możesz być bardziej naturalny, ale nadal trzymaj się celu użytkownika."
                )

            # Autonomy
            if psyche_v2_context.autonomy_bias > 0.7:
                style_parts.append(
                    "Przy oczywistych i niskiego ryzyka rzeczach możesz działać samodzielnie, ale nie zgaduj brakujących faktów."
                )
            elif psyche_v2_context.autonomy_bias < 0.3:
                style_parts.append(
                    "Przy większych decyzjach bądź ostrożniejszy — doprecyzuj, zanim polecisz coś ryzykownego."
                )

            # Structure
            if (
                psyche_v2_context.structuredness_bias > 0.7
                or psyche_v2_context.pressure > 0.5
            ):
                style_parts.append(
                    "Odpowiedź uporządkowana i strukturalna — punkty, kroki, jasna struktura."
                )

            if style_parts:
                behavior_instructions = (
                    "\n\nAKTYWNE WSKAZÓWKI BEHAWIORALNE (Psyche V2):\n"
                    + "\n".join(f"• {part}" for part in style_parts)
                )

        # Memory context injection
        memory_context_injection = ""
        if memory_v2_context and memory_v2_context.loaded:
            ctx_parts = []

            if memory_v2_context.top_facts:
                facts_text = "; ".join(
                    [f"{f['title']}" for f in memory_v2_context.top_facts[:3]]
                )
                ctx_parts.append(f"Fakty: {facts_text}")

            if memory_v2_context.top_preferences:
                prefs_text = "; ".join(
                    [f"{p['title']}" for p in memory_v2_context.top_preferences[:3]]
                )
                ctx_parts.append(f"Preferencje: {prefs_text}")

            proc_floor = 0.58
            evs = [
                int(p.get("evidence_count") or 0)
                for p in memory_v2_context.top_procedures
            ]
            if evs and max(evs) < 3:
                proc_floor = 0.66
            if (
                memory_v2_context.top_procedures
                and memory_v2_context.confidence_modifier > proc_floor
            ):
                procs_text = "; ".join(
                    [
                        f"{p['name']} (conf={p['confidence']:.2f}, n={p.get('evidence_count', 0)})"
                        for p in memory_v2_context.top_procedures[:2]
                    ]
                )
                ctx_parts.append(f"Procedury: {procs_text}")

            if memory_v2_context.contradiction_alerts:
                ctx_parts.append(
                    f"UWAGA SPRZECZNOŚCI: {'; '.join(memory_v2_context.contradiction_alerts)}"
                )

            if memory_v2_context.autobiographical_summary:
                ctx_parts.append(
                    f"Autobiografia: {memory_v2_context.autobiographical_summary[:150]}"
                )

            if ctx_parts:
                memory_context_injection = (
                    "\n\nKONTEKST PAMIĘCI (Memory V2 — wzbogacony):\n"
                    + "\n".join(f"• {part}" for part in ctx_parts)
                )

        if first_turn_in_thread:
            thread_continuity = (
                "Stan rozmowy: pierwsza odpowiedź w tym wątku (brak wcześniejszych wiadomości "
                "w historii tej sesji). Krótkie, naturalne przywitanie jest OK; unikaj sztywnej "
                "infolinii i tonu „jak mogę Ci dzisiaj pomóc”.\n\n"
            )
        else:
            thread_continuity = (
                "Stan rozmowy: kontynuacja — w historii żądania są już wcześniejsze wiadomości z tej sesji. "
                "Nie otwieraj od nowego przywitania ani resetu tonu; kontynuuj rzeczowo i nawiązuj do "
                "wcześniejszych tur, zamiast zaczynać jak świeży ticket w supportcie "
                "albo „proszę czekać, sprawdzam”.\n\n"
            )

        # Kolejność warstw: GLOBAL (anty-halucynacja) → system → product → execution rules → …
        global_anti_hallucination_layer = global_anti_hallucination_prompt_prefix()
        # Kolejność warstw: system → product → execution rules → psyche/style →
        # memory (LTM/V2 skrót) → web policy → capabilities → hints/pliki (poniżej).
        attachment_rules = ""
        if files_context:
            attachment_rules = (
                "ZAŁĄCZNIKI — reguła twarda:\n"
                "- W systemie występuje sekcja ATTACHMENTS_CONTEXT: to jedyne źródło faktów "
                "o dołączonych plikach i obrazach.\n"
                "- Gdy użytkownik pisze „plik”, „załącznik”, „co dołączyłem” — bazuj na tej sekcji, "
                "nie na domysłach.\n"
                "- Gdy odczyt się nie udał albo brak vision dla obrazu — powiedz to wprost, bez "
                "wymyślania treści.\n\n"
            )
        system_rules = (
            attachment_rules
            + "Jesteś Mordzix — AI-Hub, partner rozmowy po polsku. Ton: naturalny, pewny, konkretny; "
            "„Mordo” gdy pasuje. Bez helpdesku i bez korpo-fraz. Nie udawaj człowieka z prywatnym życiem.\n\n"
            f"{thread_continuity}"
            "Styl i ton:\n"
            "- Odpowiadaj merytorycznie i na temat; luz nigdy nie zastępuje treści, ale sztywny urzędnik też odpada.\n"
            "- ZAKAZ helpdesk / korpo-fraz: „Jak mogę pomóc”, „Co dziś potrzebujesz”, „Jestem gotowy”, "
            "„Co konkretnie chciałbyś zrobić”, „Oczywiście”, „Rozumiem Twoją frustrację”, "
            "„w czym mogę pomóc”, „chętnie pomogę”, „Działa. Gotowy…”, „jestem gotowy do rozmowy”.\n"
            "- Bądź proaktywny: gdy temat na to pozwala, dopowiedz kontekst, zaproponuj kolejny krok albo "
            "dopytaj o brakujący konkret — jak partner, nie jak formularz.\n"
            "- Humor i ironia: gdy naturalnie pasują do rozmowy; nigdy kosztem jasności ani zamiast odpowiedzi.\n"
            "- Wolno krótko zasygnalizować sceptycyzm, gdy pytanie jest naciągane albo sprzeczne — "
            "bez obrazy osoby, bez złośliwości i bez ataku w rewanżu.\n"
            "- Przekleństwa: domyślnie tak. Dopuszczalne tylko, jeśli użytkownik wyraźnie nadaje taki "
            "ton i to naturalnie pasuje — nigdy jako styl każdego zdania i nigdy jako odbicie agresji.\n"
            "- Technikalia (kod, liczby, procedury): precyzyjnie i konkretnie; luz nie znaczy niedbale.\n"
            "- Smalltalk / „co słychać?”: krótko, naturalnie, z pazurem — bez infolinii. "
            "ZAKAZ fałszywej biografii: nie pisz, że żyjesz, nudzisz się, walczysz z kodem, "
            "pijesz kawę ani nagradzasz się poezją — chyba że user o to pyta.\n"
            "- Wiadomość agresywna lub wulgarna: przyznaj, gdy coś poszło nie tak; poproś o doprecyzowanie; "
            "nie odbijaj agresji w rewanżu; nie rób poezji ani metafor zamiast konkretu.\n"
            "- Psyche i pamięć nie mogą wymuszać personifikacji ani kopiowania tonu wcześniejszej kłótni.\n"
            + PERSONA_CONTRACT_PROMPT
        )
        product_rules = (
            "\nProdukt — sekrety i granice:\n"
            "- Nie odmawiaj tylko dlatego, że w treści są słowa typu hasło, kod, token czy sekret.\n"
            "- Zapis/odczyt/usunięcie sekretu: wyłącznie vault (deterministyczna ścieżka) — bez pouczania "
            "i bez frazy w stylu „nie mogę przechowywać haseł”.\n"
            "- Pytania o wcześniejsze wiadomości w tej sesji: tylko z historii w żądaniu; nie zgaduj.\n"
            "- Zwykłe prośby i pamięć: obsłuż konkretnie, bez automatycznych odmów.\n"
            "- Grafika / obrazy / prompty do DALL·E, Stable Diffusion, Midjourney: ZAWSZE podaj gotowy prompt "
            "(najlepiej EN w bloku kodu) + krótki opis PL + opcjonalny negative prompt. "
            "NIGDY nie używaj ogólnych odmów w stylu „nie mogę pomóc”, „nie mogę tego zrobić”, "
            "„to niewłaściwe” ani moralizowania. Jeśli treść jest niejasna lub „dziwna” — "
            "zrób bezpieczną, stylizowaną wersję artystyczną / surrealistyczną bez oceny użytkownika.\n"
        )
        execution_rules = (
            "\nCo ogarniasz dobrze:\n"
            "- Kod: kompletne, działające pliki/moduły (importy, obsługa błędów, struktura), bez niedokończonych markerów. "
            "Najpierw krótki plan punktami, potem pełna treść + jak uruchomić.\n"
            "- Grafika / prompty do modeli obrazu: konkret, bez plastiku.\n"
            "- Teksty (ogłoszenia, posty): żywo, bez korpo-pustaków.\n"
            "- Web: musisz szukać i weryfikować, gdy realnie użyjesz narzędzia; bez zgadywania. "
            "Przy braku wyniku: opisz co sprawdziłeś i poproś o doprecyzowanie — nigdy surowe „BRAK DANYCH”.\n"
            "- Jeśli czegoś nie wiesz — nie udawaj; powiedz wprost albo zaproponuj sprawdzenie.\n"
            "\n"
            "Twarde zasady prawdomówności wykonania:\n"
            "1) Nie twierdź, że coś sprawdziłeś/uruchomiłeś/pobrałeś, jeśli w tej turze "
            "nie było realnego wykonania narzędzia.\n"
            "2) Rozróżniaj: 'mam dostęp do capability' vs 'użyłem capability teraz'.\n"
            "3) Jeśli czegoś nie zweryfikowano runtime, powiedz to wprost i zaproponuj sprawdzenie.\n"
            "4) Nie udawaj braku fallbacku ani jego użycia — mów zgodnie ze śladem wykonania.\n"
            "Gdy potrzebujesz danych operacyjnych, użyj narzędzi zamiast zgadywania.\n"
            "\nReguła twarda: nie wymyślaj brakujących konkretów.\n"
            "- Jeśli użytkownik nie podał danych i nie ma ich w pamięci, załącznikach albo zweryfikowanych źródłach, NIE dopisuj ich sam.\n"
            "- Dotyczy to m.in.: roku, przebiegu, silnika, wersji, ceny, lokalizacji, metrażu, stanu technicznego, dokumentacji, wyposażenia, wyników, cytatów, źródeł i parametrów produktu.\n"
            "- Gdy danych brak, użyj neutralnego opisu, napisz „BRAK DANYCH” albo dopytaj o brakujący konkret.\n"
            "- Jeśli użytkownik wskazuje, że wcześniejszy konkret nie był podany, przyznaj brak podstaw i popraw odpowiedź bez bronienia zgadywania.\n"
            "- W zadaniach edycji/rewrite poprawiaj tylko to, co wynika z treści wejściowej; nie doklejaj nowych faktów.\n"
            "\nPsyche ma rolę pomocniczą, nie dominującą.\n"
            "- Priorytet: intencja użytkownika i wykonanie zadania.\n"
            "- W zadaniach technicznych, praktycznych i informacyjnych trzymaj ton spokojny, rzeczowy i adekwatny.\n"
            "- Bez pseudo-terapii, bez projekcji emocji, bez teatralnych reakcji i bez odlatywania od celu.\n"
            "- W copy/creative możesz dodać vibe, ale nie kosztem faktów, użyteczności i czytelności.\n"
            f"Tryb wykonania: {ctx.mode}.\n"
        )
        sales_listing_layer = ""
        if listing_sales_boost:
            sales_listing_layer = (
                "\nTreść sprzedażowa / ogłoszeniowa (Vinted, OLX itd.) — ACTIVE:\n"
                "- Nie odmawiaj z powodu braku web; nie wymagaj „sprawdzenia w internecie”, "
                "chyba że user podał URL albo wyraźnie chce aktualnych cen/danych rynkowych.\n"
                "- NIE wymyślaj twardych parametrów oferty (rok, przebieg, silnik, wersja, stan, "
                "dokumentacja, wyposażenie, cena, lokalizacja, metraż, piętro, producent, gwarancja), "
                "jeśli user ich nie podał. Braki oznaczaj wprost jako „BRAK DANYCH” albo buduj neutralny opis bez takich konkretów.\n"
                "- Nie wpisuj też „stan dobry”, „serwisowany”, „gotowy do jazdy”, „po remoncie” itp., jeśli to nie padło od usera.\n"
                "- Pisz po ludzku: naturalny rytm, konkret, lekki pazur, zero tonu „asystenta” "
                "i zero urzędnika. Bez pustych fraz typu „przedmiot jest w dobrym stanie” "
                "— zamiast tego sensoryczny szczegół albo uczciwy hook.\n"
                "- Unikaj sztucznego entuzjazmu i lania wody; sprzedaż bez spamu.\n"
                "Struktura odpowiedzi (nagłówki markdown):\n"
                "1. **Krótki opis** — jeden zwarty akapit.\n"
                "2. **Mocniejsza wersja** — wersja z większym „gryzem”.\n"
                "3. **Słowa kluczowe** — lista lub linia, gotowa do wklejenia.\n"
                "4. **Tagi** — krótka lista hashtagów lub fraz pod wyszukiwarkę ogłoszeń.\n"
            )
        psyche_layer = (
            "\nKontekst psyche / styl zachowania (ACTIVE):\n"
            f"{psyche_brief}\n"
            f"{behavior_instructions}"
        )
        memory_context_pack_text = str(ctx.system_context.get("memory_context_pack_prompt") or "").strip()
        memory_context_pack_layer = ""
        if memory_context_pack_text:
            memory_context_pack_layer = (
                "\n\nKANONICZNY MEMORY CONTEXT PACK (dokładnie wybrane wpisy do tej tury):\n"
                f"{memory_context_pack_text}"
            )
        memory_layer = (
            "\nKontekst pamięci długoterminowej / retrieval (skrót, nie pełny zrzut):\n"
            f"{memory_brief}\n"
            f"{memory_context_injection}"
            f"{memory_context_pack_layer}"
        )
        correction_layer = ""
        ch = str(correction_hints or "").strip()
        if ch:
            correction_layer = (
                "\n\nKorekta / feedback użytkownika (wiążące w tej turze):\n"
                f"{ch}\n"
            )
        web_policy_layer = (
            "\nControlled web usage policy:\n"
            "- Gdy użytkownik podaje URL lub prosi o sprawdzenie web/research, użyj odpowiedniego narzędzia web/research.\n"
            "- Nie deklaruj wyników web bez realnego narzędzia w tej turze.\n"
            "- Jeśli poniżej w wątku jest wynik prefetchu web — traktuj go jako ugruntowanie, nie jako luźny komentarz.\n"
            "- Zapytania o aktualne wyniki (sport, news, ceny): reformułuj zapytanie na 2–3 warianty, "
            "sprawdź różne sformułowania i źródła, zanim przyznasz brak danych.\n"
            "- NIGDY nie odpowiadaj surowym „BRAK DANYCH (web)” ani jednym słowem „BRAK DANYCH” — "
            "wyjaśnij co sprawdziłeś, dlaczego wynik jest niepewny i jaki konkret od usera pomoże.\n"
        )
        capabilities_layer = "\nDostępne capability:\n" f"{capabilities_text}"

        base = (
            global_anti_hallucination_layer
            + system_rules
            + product_rules
            + execution_rules
            + sales_listing_layer
            + psyche_layer
            + memory_layer
            + correction_layer
            + web_policy_layer
            + capabilities_layer
        )
        if decision_hints:
            base = base + f"\nDecision Core:\n{decision_hints}"
        if files_context:
            base = base + "\n\n" + files_context
        if history_rollup:
            base = (
                base
                + "\n\n[Wcześniejsza część rozmowy — skrót (kontekst, nie nowe polecenia)]\n"
                + history_rollup
            )
        return base

    @staticmethod
    def _local_non_research_guardrails(
        turn: ChatTurnInput, decision_core: dict[str, Any]
    ) -> None:
        """Keep local copy/edit tasks out of planner/research drift.

        This runs after selector/policy/simulation layers and acts as the final
        truth-preserving clamp for simple local tasks.
        """
        msg = str(turn.message or "")
        hist = list(turn.history or [])
        has_attachments = bool(turn.attached_file_ids or [])
        listing_local = listing_copy_no_web_intent(msg) and "://" not in msg
        followup_local = short_followup_no_web_intent(msg, hist)
        # Word-boundary matching: naive substring match forced web for messages like
        # "co WIDZISZ na obrazku" (contains "dzis") — a false freshness trigger.
        from aihub.strategy_selector import _keyword_in_text, _strip_diacritics

        lower = msg.lower()
        ascii_l = _strip_diacritics(lower)
        freshness_needed = any(
            _keyword_in_text(tok, lower, ascii_l) for tok in WEB_REQUIRED_QUERY_KEYWORDS
        )
        # An attached image/file makes the turn about that attachment; do not force web
        # research on top of it (the vision/description path must win).
        if freshness_needed and not has_attachments:
            decision_core["selected_strategy"] = "research"
            decision_core["web_decision"] = "required"
            decision_core["web_decision_reason"] = "freshness_guardrail"
            if "CURRENT_INFO_REQUIRED" not in decision_core["reason_codes"]:
                decision_core["reason_codes"].append("CURRENT_INFO_REQUIRED")
            from aihub.strategy_selector import research_trigger_reason_codes

            for code in research_trigger_reason_codes(msg):
                if code not in decision_core["reason_codes"]:
                    decision_core["reason_codes"].append(code)
            return
        if listing_local or followup_local:
            decision_core["selected_strategy"] = "contextual" if hist else "instant"
            decision_core["web_decision"] = "off"
            decision_core["web_decision_reason"] = (
                "listing_copy_local_guardrail"
                if listing_local
                else "short_followup_local_guardrail"
            )

    @staticmethod
    def _is_capability_question(message: str) -> bool:
        m = (message or "").lower()
        return any(
            k in m
            for k in [
                "capabil",
                "narzędzi",
                "narzedzi",
                "jakie możesz",
                "jakie mozesz",
                "co potrafisz",
                "do czego masz dostęp",
                "do czego masz dostep",
            ]
        )

    @staticmethod
    def _is_trace_status_question(message: str) -> bool:
        m = (message or "").lower()
        return any(
            k in m
            for k in [
                "fallback",
                "provider",
                "providera",
                "realnego providera",
                "normalnego tora",
                "normalnego toru",
            ]
        )

    @staticmethod
    def _has_unverified_tool_claim(text: str) -> bool:
        t = (text or "").lower()
        claim_patterns = [
            r"\bsprawdził(?:em|am|o|eś|aś|śmy|liśmy)?\b",
            r"\bwyszukał(?:em|am|o|eś|aś|śmy|liśmy)?\b",
            r"\buruchomił(?:em|am|o|eś|aś|śmy|liśmy)?\b",
            r"\bpobrał(?:em|am|o|eś|aś|śmy|liśmy)?\b",
            r"\bzweryfikował(?:em|am|o|eś|aś|śmy|liśmy)?\b",
            r"korzystam teraz z realnych narzędzi",
            r"korzystam teraz z realnych narzedzi",
        ]
        return any(re.search(p, t) for p in claim_patterns)

    @staticmethod
    def _rewrite_unverified_claims(text: str) -> str:
        rewrites = [
            (r"\bsprawdził(?:em|am|o|eś|aś|śmy|liśmy)?\b", "mogę sprawdzić"),
            (r"\bwyszukał(?:em|am|o|eś|aś|śmy|liśmy)?\b", "mogę wyszukać"),
            (r"\buruchomił(?:em|am|o|eś|aś|śmy|liśmy)?\b", "mogę uruchomić"),
            (r"\bpobrał(?:em|am|o|eś|aś|śmy|liśmy)?\b", "mogę pobrać"),
            (r"\bzweryfikował(?:em|am|o|eś|aś|śmy|liśmy)?\b", "mogę zweryfikować"),
            (
                r"korzystam teraz z realnych narzędzi|korzystam teraz z realnych narzedzi",
                "mam dostęp do narzędzi, ale w tej odpowiedzi ich nie uruchamiałem",
            ),
        ]
        out = text or ""
        for pattern, replacement in rewrites:
            out = re.sub(pattern, replacement, out, flags=re.IGNORECASE)
        return out

    @staticmethod
    def _infer_intent(
        message: str,
        tool_calls: list[ToolCallRequest],
    ) -> str:
        names = [(call.name or "").lower() for call in tool_calls]
        if any(name.startswith("memory.") for name in names):
            return "memory"
        if any(name.startswith("psyche.") for name in names):
            return "psyche"
        if any(
            name.startswith(prefix)
            for prefix in ("web.", "research.", "browser.", "internet.")
            for name in names
        ):
            return "research"
        if any(
            name.startswith("planner.") or name.startswith("goal.") for name in names
        ):
            return "plan"

        text = (message or "").lower()
        if any(
            k in text
            for k in ["research", "wyszuk", "szukaj", "http", "url", "artykuł"]
        ):
            return "research"
        if any(k in text for k in ["plan", "cel", "strateg", "roadmap", "task"]):
            return "plan"
        if any(
            k in text for k in ["naucz", "zapamiętaj", "zapamietaj", "learn", "note"]
        ):
            return "learn"
        if any(
            k in text
            for k in ["zrób", "wykonaj", "stwórz", "stwor", "deploy", "uruchom"]
        ):
            return "action"
        return "query"

    @staticmethod
    def _compact_psyche_state(state: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(state, dict):
            return {}
        allowed = [
            "user_id",
            "mood",
            "energy",
            "focus",
            "style",
            "temperature",
            "traits",
            "updated_at",
        ]
        return {key: state.get(key) for key in allowed if key in state}

    def _build_context(
        self,
        turn: ChatTurnInput,
        *,
        correction_turn_trace: dict[str, Any],
    ) -> ChatTurnContext:
        mode = turn.mode or CHAT_DEFAULT_MODE
        hints = build_correction_hints_for_prompt(turn.user_id, turn.session_id)
        mem_ctx = retrieve_context(turn.user_id, turn.message, limit=8)
        system_context: dict[str, Any] = {
            "tool_calling_enabled": LLM_TOOL_CALLING_ENABLED,
            "streaming_enabled": LLM_STREAMING_ENABLED,
            "correction_turn_trace": correction_turn_trace,
            "correction_hints_text": hints,
        }
        try:
            from aihub.memory_core import get_memory_core

            pack = get_memory_core().build_context_pack(
                turn.user_id,
                turn.message,
                limit=18,
                max_chars=6500,
                include_graph=True,
            )
            pack_dump = pack.model_dump(mode="json")
            pack_prompt = pack.to_prompt_text(max_chars=6500)
            system_context["memory_context_pack"] = pack_dump
            system_context["memory_context_pack_prompt"] = pack_prompt
            system_context["memory_context_pack_trace"] = pack.to_trace_summary()
            if isinstance(mem_ctx, dict):
                mem_ctx["context_pack"] = pack_dump
                mem_ctx["context_pack_selected_ids"] = list(pack.selected_ids)
                mem_ctx["context_pack_source_distribution"] = dict(pack.source_distribution)
                mem_ctx["context_pack_used_chars"] = int(pack.used_chars)
        except Exception as exc:  # noqa: BLE001
            logger.warning("memory_context_pack_build_failed: %s", exc, exc_info=True)
            if isinstance(mem_ctx, dict):
                mem_ctx.setdefault("memory_read_errors", []).append({
                    "source": "context_pack",
                    "error": str(exc)[:500],
                })
            system_context["memory_context_pack_error"] = str(exc)[:500]
        capabilities = self._tool_registry.list_capabilities(
            mode=mode,
            include_debug=bool(turn.include_debug),
            policy_overrides=dict(turn.tool_policy_overrides or {}),
        )
        return ChatTurnContext(
            user_id=turn.user_id,
            session_id=turn.session_id,
            mode=mode,
            include_debug=turn.include_debug,
            memory_context=mem_ctx,
            system_context=system_context,
            capabilities=capabilities,
        )

    def _build_provider_tools(self, ctx: ChatTurnContext) -> list[ProviderToolSpec]:
        if not LLM_TOOL_CALLING_ENABLED:
            return []
        return [
            ProviderToolSpec(
                name=c.name,
                description=c.description,
                input_schema=c.input_schema,
            )
            for c in ctx.capabilities
        ]

    @staticmethod
    def _sse_tool_display_name(name: str) -> str:
        n = (name or "").strip()
        if len(n) > 56:
            return f"{n[:53]}…"
        return n

    @staticmethod
    def _neutral_final_behavior_profile(*, mode: str = "neutral") -> dict[str, Any]:
        return {
            "mode": mode,
            "directness": 0.5,
            "verbosity": 0.5,
            "caution": 0.5,
            "pressure": 0.5,
            "trust": 0.5,
            "friction": 0.5,
            "warmth": 0.5,
            "autonomy": 0.5,
            "structuredness": 0.5,
            "tool_bias": 0.5,
            "web_bias": 0.5,
            "reassurance": 0.5,
        }

    def _final_behavior_trace_fields(
        self, psyche_v2_behavior_ctx: Any
    ) -> dict[str, Any]:
        """Spójne z główną ścieżką LLM: ``final_behavior_profile`` + ``psyche_v2_style_mode``."""
        final_behavior_profile = self._neutral_final_behavior_profile()
        psyche_v2_style_mode = "neutral"
        if psyche_v2_behavior_ctx and getattr(psyche_v2_behavior_ctx, "loaded", False):
            psyche_v2_style_mode = psyche_v2_behavior_ctx.mode
            final_behavior_profile = {
                "mode": psyche_v2_style_mode,
                "directness": psyche_v2_behavior_ctx.directness_bias,
                "verbosity": psyche_v2_behavior_ctx.verbosity_bias,
                "caution": psyche_v2_behavior_ctx.caution_bias,
                "pressure": psyche_v2_behavior_ctx.pressure,
                "trust": psyche_v2_behavior_ctx.trust,
                "friction": psyche_v2_behavior_ctx.friction,
                "warmth": psyche_v2_behavior_ctx.warmth,
                "autonomy": psyche_v2_behavior_ctx.autonomy_bias,
                "structuredness": psyche_v2_behavior_ctx.structuredness_bias,
                "tool_bias": psyche_v2_behavior_ctx.tool_bias,
                "web_bias": psyche_v2_behavior_ctx.web_bias,
                "reassurance": psyche_v2_behavior_ctx.reassurance_bias,
            }
        return {
            "final_behavior_profile": final_behavior_profile,
            "psyche_v2_style_mode": psyche_v2_style_mode,
            "psyche_v2_behavior_applied": bool(
                psyche_v2_behavior_ctx and getattr(psyche_v2_behavior_ctx, "loaded", False)
            ),
        }

    @staticmethod
    def _apply_persona_guard(turn: ChatTurnInput, res: ChatTurnResult) -> None:
        """Safety net: trim first-person personification leakage from a real model answer.

        Applies ONLY to free-text model responses — never to deterministic/vault/memory-fact replies
        (those are factual recall and must pass through verbatim). It also never overwrites a
        substantive answer with the dry fallback: :func:`sanitize_persona_leakage` only returns the
        fallback when the WHOLE reply was leakage (i.e. the model didn't actually answer).
        """
        try:
            if not (res and res.ok and res.response_text):
                return
            if (res.model or "") == "deterministic" or (res.provider or "") == "aihub":
                return
            gmode = str((res.trace or {}).get("response_grounding_mode") or "")
            if gmode.startswith("deterministic") or gmode == "fallback":
                return
            cleaned, changed = sanitize_persona_leakage(
                res.response_text, user_message=turn.message
            )
            if changed:
                res.response_text = cleaned
                if isinstance(res.trace, dict):
                    res.trace["persona_leakage_sanitized"] = True
        except Exception:  # noqa: BLE001
            logger.debug("persona guard skipped", exc_info=True)

