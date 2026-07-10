# Self-hosted STT (faster-whisper) + Vision (Ollama) na VPS

Backend `/root/morda` używa **lokalnego** STT i **lokalnej** Ollamy — bez płatnych API jako wymogu.

## 1. Zależności systemowe (Debian/Ubuntu)

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg curl ca-certificates
```

## 2. Ollama (serwis)

```bash
curl -fsSL https://ollama.com/install.sh | sh
sudo systemctl enable ollama
sudo systemctl start ollama
```

Sprawdzenie:

```bash
curl -sS http://127.0.0.1:11434/api/tags
```

## 3. Modele vision (najpierw główny, potem fallback)

```bash
ollama pull qwen2.5vl:3b
ollama pull llava:7b
```

Jeśli `qwen2.5vl:3b` jest zbyt ciężki lub niestabilny na VPS, w `.env` ustaw:

```bash
CHAT_VISION_MODEL=llava:7b
```

## 4. Repozytorium i Python

```bash
cd /root/morda
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip setuptools wheel
pip install -r requirements.txt
```

Pierwsze uruchomienie **faster-whisper** pobierze model Whisper `small` z Hugging Face (cache w `~/.cache/huggingface` lub `XDG_CACHE_HOME`).

## 5. Zmienne środowiskowe (`morda/.env`)

Dopisz (lub zmień) **dokładnie**:

```bash
CHAT_STT_ENABLED=1
CHAT_STT_BACKEND=self_hosted_whisper
CHAT_STT_MODEL=small
CHAT_STT_DEVICE=cpu
CHAT_STT_COMPUTE_TYPE=int8

CHAT_VISION_ENABLED=1
CHAT_VISION_BACKEND=ollama
CHAT_VISION_MODEL=qwen2.5vl:3b
CHAT_VISION_OLLAMA_URL=http://127.0.0.1:11434
CHAT_VISION_FALLBACK_MODEL=llava:7b
```

Opcjonalnie (timeouty):

```bash
CHAT_STT_TIMEOUT_S=300
CHAT_VISION_TIMEOUT_S=120
```

## 6. Start aplikacji

```bash
cd /root/morda
./start.sh
```

Opcjonalnie smoke po starcie:

```bash
START_RUN_SELFHOSTED_SMOKE=1 ./start.sh
```

Tylko Ollama (bez wywołania `/chat/stt`):

```bash
SELFHOSTED_SMOKE_SKIP_STT=1 START_RUN_SELFHOSTED_SMOKE=1 ./start.sh
```

## 7. Smoke ręczny

```bash
cd /root/morda
source .venv/bin/activate
export CHAT_VISION_OLLAMA_URL=http://127.0.0.1:11434
export AIHUB_SMOKE_BASE_URL=http://127.0.0.1:8080
# Wartość API_KEY jak w morda/.env (hub)
export API_KEY="$(grep '^API_KEY=' .env | cut -d= -f2-)"
bash scripts/smoke_selfhosted_stt_vision.sh
```

Albo:

```bash
python -m aihub.scripts.selfhosted_stt_vision_smoke --hub-url http://127.0.0.1:8080 --api-key "$API_KEY"
```

## 8. Testy w repozytorium

```bash
cd /root/morda
source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q
```

## 9. Wymagania sprzętowe (orientacyjnie)

| Komponent | CPU | RAM (orientacyjnie) | Uwagi |
|-----------|-----|---------------------|--------|
| faster-whisper `small` + `int8` | tak | ~2–4 GiB + model | Pierwszy run = pobranie wag |
| Ollama `qwen2.5vl:3b` | CPU; GPU opcjonalnie | zależnie od Ollamy | Na słabym VPS ustaw `llava:7b` lub mniejszy tag |
| Ollama `llava:7b` | CPU/GPU | wyższe niż 3B VL | Często stabilniejszy na CPU-only |

**GPU:** `CHAT_STT_DEVICE=cuda` jest możliwe przy odpowiednim stacku CUDA + buildach — domyślnie VPS = `cpu`.

## 10. Typowe awarie

- **Brak ffmpeg:** STT dla WebM z przeglądarki — zainstaluj `ffmpeg`; smoke używa surowego WAV, ale produkcja dostaje często `webm`.
- **Ollama nie działa:** `systemctl status ollama`, port `11434`.
- **401 na `/chat/stt`:** ustaw nagłówek `x-api-key` zgodny z `API_KEY` w `.env` (ten sam co cockpit/BFF).
- **Pusty tekst STT:** cisza lub szum — komunikat `stt_no_speech_detected` / `stt_no_text`.
