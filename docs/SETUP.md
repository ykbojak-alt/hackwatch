# Setup

## Requirements

- Python 3.12+ (recommended 3.12; 3.14 works with `python -S` workarounds on Windows)
- NEAR CLI (`near`) only if you need to deploy/query the contract
- Rust toolchain (`wasm32-unknown-unknown`) only to rebuild the contract

## Install (desktop shell)

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

Or use the one-shot launcher (creates the venv and installs pywebview itself):

```
powershell -ExecutionPolicy Bypass -File desktop/run_desktop.ps1
```

## Provider configuration

The scorer needs an OpenAI-compatible chat-completions endpoint. Public repo
ships an empty template at `hackwatch/config.example.json`:

```json
{
  "provider_endpoint": "",
  "model": "",
  "api_key": "",
  "catalog_url": "https://nearhackathon.com/api/projects",
  "contract": "hackwatch.yksunder.testnet"
}
```

Copy it to `hackwatch/config.json` and fill the provider fields. Priority is:
**config.json > config.example.json > environment (NEXUSLINK_BASE / NEXUSLINK_SLOT / NEXUSLINK_KEY) > built-in default**.

Without a key the scorer degrades to a heuristic fallback (no network calls).

## Running the scorer

```
cd hackwatch
python -m hackwatch            # analyze only new projects
python -m hackwatch --reparse  # re-analyze everything
python -m hackwatch --export-lang all   # emit hackwatch.en.json / .ru.json
```

## Contract

On-chain registry: `hackwatch.yksunder.testnet` (NEAR Testnet). Methods:
`set_score` (owner only), `get_score`, `get_all`. Source in
`contracts/hackwatch_scores/`.
