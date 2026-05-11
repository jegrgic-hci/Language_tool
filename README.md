# French Tutor

An AI-powered French language learning tool built for immersive listening and speaking practice. Designed for learners in France who want to go beyond textbook French and develop real conversational fluency.

## What it does

- **Conversation mode** — Free-form chat with an AI tutor that responds naturally in French, correcting errors in context rather than interrupting flow
- **Scenario mode** — Roleplay real-world situations (markets, cafés, transit, administration) with the AI acting as a native speaker
- **Drill mode** — Focused exercises on numbers, connector words, and Marseille geography
- **Document mode (RAG)** — Upload a PDF (article, textbook chapter, transcript) and have a guided conversation grounded in that material
- **Shadow reading** — Read a French passage aloud and get word-by-word pronunciation scoring
- **Paragraph practice** — Sentence-by-sentence guided reading with per-sentence scores
- **Practice list** — Save words or phrases you want to drill, with active listening mode (30-second microphone sessions with auto-restart)
- **Listening mode** — Blur all tutor responses until you choose to reveal them, forcing active listening before reading
- **Text-to-speech** — Every tutor reply is spoken aloud in a natural French voice

## Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI + Python |
| LLM | Mistral AI (native French capability) |
| TTS | edge-tts, `fr-FR-DeniseNeural` voice |
| Speech input | Web Speech API (browser-native, fr-FR) |
| RAG | PDF ingestion injected into tutor context |
| Frontend | Single-page app, no framework dependencies |

## Requirements

- Python 3.9+
- Chrome or Edge (Web Speech API is required for microphone input)
- A Mistral AI API key

## Setup

```bash
git clone https://github.com/jegrgic-hci/Language_tool.git
cd Language_tool

python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

pip install -r requirements.txt

# Create a .env file with your Mistral API key
echo "MISTRAL_API_KEY=your_key_here" > .env

python server.py
```

Then open [http://127.0.0.1:8000](http://127.0.0.1:8000) in Chrome.

## Status

Currently in private testing. Not open for public use or contribution.

## License

Copyright 2026 Joseph Grgic. All rights reserved. See [LICENSE](LICENSE).
