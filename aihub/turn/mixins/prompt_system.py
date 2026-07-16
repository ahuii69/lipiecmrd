"""Extracted body of PromptContextMixin._build_system_prompt."""
from __future__ import annotations

import aihub.turn._ops_ns as _ops_ns
globals().update({k: v for k, v in vars(_ops_ns).items() if not k.startswith('__')})
TurnOps = None  # late-bound

def run_build_system_prompt(self, ctx: ChatTurnContext, *, memory_brief: str, psyche_brief: str, decision_hints: str='', correction_hints: str='', memory_v2_context=None, psyche_v2_context=None, files_context: str='', first_turn_in_thread: bool, history_rollup: str | None=None, listing_sales_boost: bool=False) -> str:
    # Canonical budget profile — selected before prompt assembly when available.
    _budget = None
    if isinstance(ctx.system_context, dict):
        _budget = ctx.system_context.get('prompt_budget_decision')
    if _budget is None and isinstance(ctx.system_context, dict) and (
        ctx.system_context.get('assistant_meta_ask_pure')
        or ctx.system_context.get('budget_profile') == 'meta_light'
    ):
        from aihub.turn.prompt_budget import build_meta_light_system_prompt, select_prompt_budget

        _budget = select_prompt_budget(
            user_text=str(ctx.system_context.get('user_turn_text') or ''),
            selected_strategy='instant',
        )
        ctx.system_context['prompt_budget_decision'] = _budget
        ctx.system_context['budget_profile'] = _budget.profile
    if _budget is not None and getattr(_budget, 'profile', None) == 'meta_light':
        from aihub.turn.prompt_budget import build_meta_light_system_prompt, build_prompt_budget_trace

        text = build_meta_light_system_prompt()
        if isinstance(ctx.system_context, dict):
            ctx.system_context['prompt_budget'] = build_prompt_budget_trace(
                decision=_budget,
                system_text=text,
                history_messages=[],
                tool_schema_chars=0,
                layer_chars={'meta_light': len(text)},
            )
        return text
    caps = [f'- {c.name}: {c.description}' for c in ctx.capabilities]
    capabilities_text = '\n'.join(caps) if caps else '- brak dostępnych narzędzi'
    behavior_instructions = ''
    if psyche_v2_context and psyche_v2_context.loaded:
        style_parts = []
        if psyche_v2_context.directness_bias > 0.7:
            style_parts.append('Możesz być bardziej bezpośredni i konkretny, ale tylko o tyle, o ile nie psuje to zadania.')
        elif psyche_v2_context.directness_bias < 0.3:
            style_parts.append('Możesz lekko zwiększyć ostrożność i niuans, bez rozwlekania odpowiedzi.')
        if psyche_v2_context.verbosity_bias < 0.3:
            style_parts.append('Trzymaj odpowiedzi zwięźle — bez lania wody.')
        elif psyche_v2_context.verbosity_bias > 0.7:
            style_parts.append('Możesz rozwinąć więcej szczegółów tam, gdzie naprawdę pomagają.')
        if psyche_v2_context.caution_bias > 0.7 or psyche_v2_context.pressure > 0.6:
            style_parts.append('Wysoka ostrożność: weryfikuj starannie, zaznaczaj niepewności, unikaj zbyt pewnych twierdzeń.')
        if psyche_v2_context.friction > 0.5:
            style_parts.append('Przy napięciu relacyjnym zwiększ precyzję i ogranicz luźne interpretacje.')
        if psyche_v2_context.warmth > 0.7 and psyche_v2_context.trust > 0.6:
            style_parts.append('Przy wysokim trust możesz być bardziej naturalny, ale nadal trzymaj się celu użytkownika.')
        if psyche_v2_context.autonomy_bias > 0.7:
            style_parts.append('Przy oczywistych i niskiego ryzyka rzeczach możesz działać samodzielnie, ale nie zgaduj brakujących faktów.')
        elif psyche_v2_context.autonomy_bias < 0.3:
            style_parts.append('Przy większych decyzjach bądź ostrożniejszy — doprecyzuj, zanim polecisz coś ryzykownego.')
        if psyche_v2_context.structuredness_bias > 0.7 or psyche_v2_context.pressure > 0.5:
            style_parts.append('Odpowiedź uporządkowana i strukturalna — punkty, kroki, jasna struktura.')
        if style_parts:
            behavior_instructions = '\n\nAKTYWNE WSKAZÓWKI BEHAWIORALNE (Psyche V2):\n' + '\n'.join((f'• {part}' for part in style_parts))
    memory_context_injection = ''
    if memory_v2_context and memory_v2_context.loaded:
        ctx_parts = []
        if memory_v2_context.top_facts:
            facts_text = '; '.join([f"{f['title']}" for f in memory_v2_context.top_facts[:3]])
            ctx_parts.append(f'Fakty: {facts_text}')
        if memory_v2_context.top_preferences:
            prefs_text = '; '.join([f"{p['title']}" for p in memory_v2_context.top_preferences[:3]])
            ctx_parts.append(f'Preferencje: {prefs_text}')
        proc_floor = 0.58
        evs = [int(p.get('evidence_count') or 0) for p in memory_v2_context.top_procedures]
        if evs and max(evs) < 3:
            proc_floor = 0.66
        if memory_v2_context.top_procedures and memory_v2_context.confidence_modifier > proc_floor:
            procs_text = '; '.join([f"{p['name']} (conf={p['confidence']:.2f}, n={p.get('evidence_count', 0)})" for p in memory_v2_context.top_procedures[:2]])
            ctx_parts.append(f'Procedury: {procs_text}')
        if memory_v2_context.contradiction_alerts:
            ctx_parts.append(f"UWAGA SPRZECZNOŚCI: {'; '.join(memory_v2_context.contradiction_alerts)}")
        if memory_v2_context.autobiographical_summary:
            ctx_parts.append(f'Autobiografia: {memory_v2_context.autobiographical_summary[:150]}')
        if ctx_parts:
            memory_context_injection = '\n\nKONTEKST PAMIĘCI (Memory V2 — wzbogacony):\n' + '\n'.join((f'• {part}' for part in ctx_parts))
    if first_turn_in_thread:
        thread_continuity = 'Stan rozmowy: pierwsza odpowiedź w tym wątku (brak wcześniejszych wiadomości w historii tej sesji). Krótkie, naturalne przywitanie jest OK; unikaj sztywnej infolinii i tonu „jak mogę Ci dzisiaj pomóc”.\n\n'
    else:
        thread_continuity = 'Stan rozmowy: kontynuacja — w historii żądania są już wcześniejsze wiadomości z tej sesji. Nie otwieraj od nowego przywitania ani resetu tonu; kontynuuj rzeczowo i nawiązuj do wcześniejszych tur, zamiast zaczynać jak świeży ticket w supportcie albo „proszę czekać, sprawdzam”.\n\n'
    global_anti_hallucination_layer = global_anti_hallucination_prompt_prefix()
    attachment_rules = ''
    if files_context:
        attachment_rules = 'ZAŁĄCZNIKI — reguła twarda:\n- W systemie występuje sekcja ATTACHMENTS_CONTEXT: to jedyne źródło faktów o dołączonych plikach i obrazach.\n- Gdy użytkownik pisze „plik”, „załącznik”, „co dołączyłem” — bazuj na tej sekcji, nie na domysłach.\n- Gdy odczyt się nie udał albo brak vision dla obrazu — powiedz to wprost, bez wymyślania treści.\n\n'
    system_rules = attachment_rules + f'Jesteś Mordzix — AI-Hub, partner rozmowy po polsku. Ton: naturalny, pewny, konkretny; „Mordo” gdy pasuje. Bez helpdesku i bez korpo-fraz. Nie udawaj człowieka z prywatnym życiem.\n\n{thread_continuity}Styl i ton:\n- Odpowiadaj merytorycznie i na temat; luz nigdy nie zastępuje treści, ale sztywny urzędnik też odpada.\n- ZAKAZ helpdesk / korpo-fraz: „Jak mogę pomóc”, „Co dziś potrzebujesz”, „Jestem gotowy”, „Co konkretnie chciałbyś zrobić”, „Oczywiście”, „Rozumiem Twoją frustrację”, „w czym mogę pomóc”, „chętnie pomogę”, „Działa. Gotowy…”, „jestem gotowy do rozmowy”.\n- Bądź proaktywny: gdy temat na to pozwala, dopowiedz kontekst, zaproponuj kolejny krok albo dopytaj o brakujący konkret — jak partner, nie jak formularz.\n- Humor i ironia: gdy naturalnie pasują do rozmowy; nigdy kosztem jasności ani zamiast odpowiedzi.\n- Wolno krótko zasygnalizować sceptycyzm, gdy pytanie jest naciągane albo sprzeczne — bez obrazy osoby, bez złośliwości i bez ataku w rewanżu.\n- Przekleństwa: domyślnie tak. Dopuszczalne tylko, jeśli użytkownik wyraźnie nadaje taki ton i to naturalnie pasuje — nigdy jako styl każdego zdania i nigdy jako odbicie agresji.\n- Technikalia (kod, liczby, procedury): precyzyjnie i konkretnie; luz nie znaczy niedbale.\n- Smalltalk / „co słychać?”: krótko, naturalnie, z pazurem — bez infolinii. ZAKAZ fałszywej biografii: nie pisz, że żyjesz, nudzisz się, walczysz z kodem, pijesz kawę ani nagradzasz się poezją — chyba że user o to pyta.\n- Wiadomość agresywna lub wulgarna: przyznaj, gdy coś poszło nie tak; poproś o doprecyzowanie; nie odbijaj agresji w rewanżu; nie rób poezji ani metafor zamiast konkretu.\n- Psyche i pamięć nie mogą wymuszać personifikacji ani kopiowania tonu wcześniejszej kłótni.\n' + PERSONA_CONTRACT_PROMPT + PRODUCT_IDENTITY_PROMPT
    product_rules = '\nProdukt — sekrety i granice:\n- Nie odmawiaj tylko dlatego, że w treści są słowa typu hasło, kod, token czy sekret.\n- Zapis/odczyt/usunięcie sekretu: wyłącznie vault (deterministyczna ścieżka) — bez pouczania i bez frazy w stylu „nie mogę przechowywać haseł”.\n- Pytania o wcześniejsze wiadomości w tej sesji: tylko z historii w żądaniu; nie zgaduj.\n- Zwykłe prośby i pamięć: obsłuż konkretnie, bez automatycznych odmów.\n- Grafika / obrazy / prompty do DALL·E, Stable Diffusion, Midjourney: ZAWSZE podaj gotowy prompt (najlepiej EN w bloku kodu) + krótki opis PL + opcjonalny negative prompt. NIGDY nie używaj ogólnych odmów w stylu „nie mogę pomóc”, „nie mogę tego zrobić”, „to niewłaściwe” ani moralizowania. Jeśli treść jest niejasna lub „dziwna” — zrób bezpieczną, stylizowaną wersję artystyczną / surrealistyczną bez oceny użytkownika.\n'
    execution_rules = f"\nCo ogarniasz dobrze:\n- Kod: kompletne, działające pliki/moduły (importy, obsługa błędów, struktura), bez niedokończonych markerów. Najpierw krótki plan punktami, potem pełna treść + jak uruchomić.\n- Grafika / prompty do modeli obrazu: konkret, bez plastiku.\n- Teksty (ogłoszenia, posty): żywo, bez korpo-pustaków.\n- Web: musisz szukać i weryfikować, gdy realnie użyjesz narzędzia; bez zgadywania. Przy braku wyniku: opisz co sprawdziłeś i poproś o doprecyzowanie — nigdy surowe „BRAK DANYCH”.\n- Jeśli czegoś nie wiesz — nie udawaj; powiedz wprost albo zaproponuj sprawdzenie.\n\nTwarde zasady prawdomówności wykonania:\n1) Nie twierdź, że coś sprawdziłeś/uruchomiłeś/pobrałeś, jeśli w tej turze nie było realnego wykonania narzędzia.\n2) Rozróżniaj: 'mam dostęp do capability' vs 'użyłem capability teraz'.\n3) Jeśli czegoś nie zweryfikowano runtime, powiedz to wprost i zaproponuj sprawdzenie.\n4) Nie udawaj braku fallbacku ani jego użycia — mów zgodnie ze śladem wykonania.\nGdy potrzebujesz danych operacyjnych, użyj narzędzi zamiast zgadywania.\n\nReguła twarda: nie wymyślaj brakujących konkretów.\n- Jeśli użytkownik nie podał danych i nie ma ich w pamięci, załącznikach albo zweryfikowanych źródłach, NIE dopisuj ich sam.\n- Dotyczy to m.in.: roku, przebiegu, silnika, wersji, ceny, lokalizacji, metrażu, stanu technicznego, dokumentacji, wyposażenia, wyników, cytatów, źródeł i parametrów produktu.\n- Gdy danych brak, użyj neutralnego opisu, napisz „BRAK DANYCH” albo dopytaj o brakujący konkret.\n- Jeśli użytkownik wskazuje, że wcześniejszy konkret nie był podany, przyznaj brak podstaw i popraw odpowiedź bez bronienia zgadywania.\n- W zadaniach edycji/rewrite poprawiaj tylko to, co wynika z treści wejściowej; nie doklejaj nowych faktów.\n\nPsyche ma rolę pomocniczą, nie dominującą.\n- Priorytet: intencja użytkownika i wykonanie zadania.\n- W zadaniach technicznych, praktycznych i informacyjnych trzymaj ton spokojny, rzeczowy i adekwatny.\n- Bez pseudo-terapii, bez projekcji emocji, bez teatralnych reakcji i bez odlatywania od celu.\n- W copy/creative możesz dodać vibe, ale nie kosztem faktów, użyteczności i czytelności.\nTryb wykonania: {ctx.mode}.\n"
    sales_listing_layer = ''
    if listing_sales_boost:
        sales_listing_layer = '\nTreść sprzedażowa / ogłoszeniowa (Vinted, OLX itd.) — ACTIVE:\n- Nie odmawiaj z powodu braku web; nie wymagaj „sprawdzenia w internecie”, chyba że user podał URL albo wyraźnie chce aktualnych cen/danych rynkowych.\n- NIE wymyślaj twardych parametrów oferty (rok, przebieg, silnik, wersja, stan, dokumentacja, wyposażenie, cena, lokalizacja, metraż, piętro, producent, gwarancja), jeśli user ich nie podał. Braki oznaczaj wprost jako „BRAK DANYCH” albo buduj neutralny opis bez takich konkretów.\n- Nie wpisuj też „stan dobry”, „serwisowany”, „gotowy do jazdy”, „po remoncie” itp., jeśli to nie padło od usera.\n- Pisz po ludzku: naturalny rytm, konkret, lekki pazur, zero tonu „asystenta” i zero urzędnika. Bez pustych fraz typu „przedmiot jest w dobrym stanie” — zamiast tego sensoryczny szczegół albo uczciwy hook.\n- Unikaj sztucznego entuzjazmu i lania wody; sprzedaż bez spamu.\nStruktura odpowiedzi (nagłówki markdown):\n1. **Krótki opis** — jeden zwarty akapit.\n2. **Mocniejsza wersja** — wersja z większym „gryzem”.\n3. **Słowa kluczowe** — lista lub linia, gotowa do wklejenia.\n4. **Tagi** — krótka lista hashtagów lub fraz pod wyszukiwarkę ogłoszeń.\n'
    psyche_layer = f'\nKontekst psyche / styl zachowania (ACTIVE):\n{psyche_brief}\n{behavior_instructions}'
    memory_context_pack_text = str(ctx.system_context.get('memory_context_pack_prompt') or '').strip()
    memory_context_pack_layer = ''
    if memory_context_pack_text:
        memory_context_pack_layer = f'\n\nKANONICZNY MEMORY CONTEXT PACK (dokładnie wybrane wpisy do tej tury):\n{memory_context_pack_text}'
    memory_layer = f'\nKontekst pamięci długoterminowej / retrieval (skrót, nie pełny zrzut):\n{memory_brief}\n{memory_context_injection}{memory_context_pack_layer}'
    correction_layer = ''
    ch = str(correction_hints or '').strip()
    if ch:
        correction_layer = f'\n\nKorekta / feedback użytkownika (wiążące w tej turze):\n{ch}\n'
    pragmatics_layer = ''
    try:
        from aihub.turn.pragmatics import pragmatics_prompt_block, PragmaticAnalysis
        _po = ctx.system_context.get('pragmatics_obj')
        if _po is None and isinstance(ctx.system_context.get('pragmatics'), dict):
            _po = PragmaticAnalysis.model_validate(ctx.system_context['pragmatics'])
        if _po is not None:
            pragmatics_layer = '\n\n' + pragmatics_prompt_block(_po, psyche_brief=str(psyche_brief or '')) + '\n'
    except Exception:
        pragmatics_layer = ''
    cognitive_layer = ''
    try:
        from aihub.turn.cognitive_integration import cognitive_prompt_block, CognitiveInfluencePack
        _co = ctx.system_context.get('cognitive_obj')
        if _co is None and isinstance(ctx.system_context.get('cognitive'), dict):
            _co = CognitiveInfluencePack.model_validate(ctx.system_context['cognitive'])
        if _co is not None:
            cognitive_layer = '\n\n' + cognitive_prompt_block(_co) + '\n'
    except Exception:
        cognitive_layer = ''
    learning_layer = ''
    try:
        _ld = ctx.system_context.get('learning_decision') if isinstance(ctx.system_context, dict) else None
        if isinstance(_ld, dict) and _ld:
            lines = ['ADAPTIVE LEARNING (wiążące dla stylu/planu — nie powtarzaj odrzuconych ścieżek):']
            um = _ld.get('user_model_v2') or {}
            if um.get('verbosity'):
                lines.append(f"- preferred_verbosity={um.get('verbosity')} (conf={float(um.get('verbosity_confidence') or 0):.2f})")
            if um.get('structure'):
                lines.append(f"- preferred_structure={um.get('structure')}")
            rej = list(_ld.get('long_horizon_rejected') or _ld.get('blocked_rejected_options') or [])
            if rej:
                lines.append('Odrzucone decyzje (NIE proponuj ponownie bez nowego dowodu): ' + ' | '.join(str(x)[:80] for x in rej[-8:]))
            suppress = list(_ld.get('learning_suppress_options') or [])
            if suppress:
                lines.append('Zablokowane opcje: ' + ', '.join(str(x) for x in suppress[:8]))
            if _ld.get('learning_length_directive'):
                lines.append(f"- length_directive={_ld.get('learning_length_directive')}")
            actions = _ld.get('learning_machine_actions_applied') or []
            if actions:
                lines.append('- machine_actions=' + ','.join(str(a.get('action')) for a in actions[:4] if isinstance(a, dict)))
            acc = list(_ld.get('long_horizon_accepted') or [])
            if acc:
                lines.append('Zaakceptowane decyzje: ' + ' | '.join(str(x)[:80] for x in acc[-4:]))
            if _ld.get('long_horizon_task_id'):
                lines.append(f"- active_long_horizon_task={_ld.get('long_horizon_task_id')}")
            if len(lines) > 1:
                learning_layer = '\n\n' + '\n'.join(lines) + '\n'
    except Exception:
        learning_layer = ''
    knowledge_layer = ''
    try:
        from aihub.world_knowledge.engine import knowledge_prompt_block
        _kd = ctx.system_context.get('knowledge_decision') if isinstance(ctx.system_context, dict) else None
        _dc = {}
        if isinstance(_kd, dict) and _kd.get('knowledge_context'):
            _dc = {'knowledge_context': _kd.get('knowledge_context')}
        kb = knowledge_prompt_block(_dc)
        if kb:
            knowledge_layer = '\n\n' + kb + '\n'
    except Exception:
        knowledge_layer = ''
    # Pure meta-ask: strip heavy prompt layers (keep persona + light psyche).
    _pure_meta = bool(isinstance(ctx.system_context, dict) and ctx.system_context.get('assistant_meta_ask_pure'))
    if _pure_meta:
        memory_layer = '\nKontekst pamięci: pominięty (meta-ask lightweight path).\n'
        memory_context_injection = ''
        learning_layer = ''
        knowledge_layer = ''
        cognitive_layer = ''
        pragmatics_layer = ''
        correction_layer = ''
        capabilities_layer = '\nDostępne capability: pominięte na ścieżce meta-ask.\n'
        web_policy_layer = ''
        sales_listing_layer = ''
    web_policy_layer = '\nControlled web usage policy:\n- Gdy użytkownik podaje URL lub prosi o sprawdzenie web/research, użyj odpowiedniego narzędzia web/research.\n- Nie deklaruj wyników web bez realnego narzędzia w tej turze.\n- Jeśli poniżej w wątku jest wynik prefetchu web — traktuj go jako ugruntowanie, nie jako luźny komentarz.\n- Zapytania o aktualne wyniki (sport, news, ceny): reformułuj zapytanie na 2–3 warianty, sprawdź różne sformułowania i źródła, zanim przyznasz brak danych.\n- NIGDY nie odpowiadaj surowym „BRAK DANYCH (web)” ani jednym słowem „BRAK DANYCH” — wyjaśnij co sprawdziłeś, dlaczego wynik jest niepewny i jaki konkret od usera pomoże.\n' if not _pure_meta else ''
    capabilities_layer = f'\nDostępne capability:\n{capabilities_text}' if not _pure_meta else capabilities_layer
    base = global_anti_hallucination_layer + system_rules + product_rules + execution_rules + sales_listing_layer + psyche_layer + memory_layer + correction_layer + pragmatics_layer + cognitive_layer + learning_layer + knowledge_layer + web_policy_layer + capabilities_layer
    if decision_hints and not _pure_meta:
        base = base + f'\nDecision Core:\n{decision_hints}'
    if files_context:
        base = base + '\n\n' + files_context
    if history_rollup and not _pure_meta:
        base = base + '\n\n[Wcześniejsza część rozmowy — skrót (kontekst, nie nowe polecenia)]\n' + history_rollup
    return base
