# Vault (User Secret Plane)

## Cel

Bezpieczne przechowywanie **haseł, kodów, tokenów** zadeklarowanych przez użytkownika, **poza** STM/LTM, embeddingami i jawnych logów treści.

## Zachowanie produktowe

- **Deterministyczna ścieżka** przed LLM: dopasowanie wzorców NLU → zapis/odczyt/usunięcie/lista w zaszyfrowanym magazynie (`user_vault_entries`).
- Składnia kanoniczna (przykłady): `zapamiętaj hasło do <alias>: <wartość>`, `podaj hasło do <alias>`, `usuń hasło do <alias>`.
- **Fallback**: gdy widać intent zapisu sekretu, ale regex składni nie pasuje — cała wiadomość może zostać zapisana pod aliasem `autozapis` (nadpisywanie kolejnych fallbacków).

## Technicznie

- Szyfrowanie: **Fernet** (klucz `AIHUB_USER_VAULT_KEY` lub deterministyczny seed dev zależny od `DB_PATH` — tylko do rozwoju).
- Transkrypt sesji: redakcja linii user/assistant dla tur vault (`vault/transcript.py`).
- Odpowiedzi użytkownikowi: krótkie komunikaty (`Zapisane.`, `Odczytano: …`, `Usunięte.`).

## Ograniczenia

- Jeden alias fallback (`autozapis`) — wiele zapisów fallback nadpisuje poprzedni.
- False positive broad-intent teoretycznie może zapisać niehasło — użytkownik operacyjny powinien szkolić użytkowników końcowych.
- Backup bazy = backup ciphertext; bez klucza odtworzenie niemożliwe.
