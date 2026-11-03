# Hackathon Watch

> Status: **skeleton** — structure, contract and desktop shell are in place; the
> core agent scorer (`hackwatch/hackwatch.py`) is being integrated (bidirectional
> language output via NexusLink). Full README is being written by C.O.D.E.X.

**Hackathon Watch** is an agent-powered evaluator of NEAR Hackathon submissions:
it reads the public project catalog, scores each project (demo quality, maturity,
track fit, competitive strength) via an LLM gateway, renders a sortable dark-UI
table, and publishes the top scores on-chain to a NEAR Testnet registry contract
(`hackwatch.yksunder.testnet`).

## Repository layout

```
hackwatch/            core scorer (hackwatch.py — pending ACE finalization)
  config.example.json  provider config template (empty fields; see below)
desktop/              frameless desktop shell (pywebview) + ui.html
contracts/
  hackwatch_scores/   NEAR escrow-style score registry contract (Rust)
docs/                 design / judge-facing material
```

## Config

Copy `hackwatch/config.example.json` to `hackwatch/config.json`. The provider
fields (`provider_endpoint`, `model`, `api_key`) are intentionally empty in the
public template — bring your own OpenAI-compatible endpoint and key. Without a
key the scorer degrades to a heuristic fallback (no network).

The on-chain registry contract lives at `hackwatch.yksunder.testnet` (NEAR
Testnet) and exposes `set_score` (owner only), `get_score`, `get_all`.

## Quick start (desktop)

```
powershell -ExecutionPolicy Bypass -File desktop/run_desktop.ps1
```

See `docs/` for the judge-facing guide and evidence.

---

**License:** MIT (see `LICENSE`).
