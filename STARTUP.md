# French Tutor — Startup Instructions

## How to run the server

Open Terminal and run the following commands:

```bash
cd /Users/josephgrgic/Documents/GitHub/Language_tool
source .venv/bin/activate
python server.py
```

Then open **http://127.0.0.1:8000** in Chrome.

## How to kill and restart the server

If the server is already running (e.g. after a code change), kill it first then restart:

```bash
kill $(pgrep -f "python server.py")
cd /Users/josephgrgic/Documents/GitHub/Language_tool
source .venv/bin/activate
python server.py
```

## Notes
- Use Chrome or Edge (Web Speech API for microphone input requires these browsers)
- The server runs locally — no internet connection needed except for Mistral AI API calls
- To stop the server, press `Ctrl+C` in the terminal
