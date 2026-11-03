# HackWatch

HackWatch is a local-first screening tool for NEAR hackathon submissions. It combines an LLM-assisted review, evidence links, and a NEAR Testnet score registry. A score is an aid for human review, not a final judging decision.

## Problem

Judges must review many submissions with uneven demos and documentation. A shortlist is hard to compare when scores and explanations are not visible or repeatable.

## Product

`hackwatch/hackwatch.py` reads the public project catalog, checks demo links, and creates a bilingual English/Russian assessment for each project. It writes a local JSON result and an HTML report. The desktop UI shows the ranking; select a row to open its accordion with score details, evaluator, rationale, verdict, and the full report.

The UI labels heuristic fallback rows. A heuristic row is not an LLM assessment.

## NEAR role

The Rust contract at `contracts/hackwatch_scores/` is the public Testnet registry at **`hackwatch.yksunder.testnet`**.

- `set_score(project_id, score, note)` writes one record. It is restricted to the account that initialized the contract and accepts scores from `0` to `100`.
- `get_score(project_id)` reads one record.
- `get_all()` reads the registry. Both read methods are public and do not require an account key.

**Integration boundary:** the current scorer writes its assessment to local files; it does not automatically call `set_score` or create an on-chain transaction. A registry entry is on-chain evidence only after an owner-signed `set_score` transaction succeeds. The UI does not infer an on-chain write from a local score.

## Reproduce the demo on Windows

The following path starts from the repository root. Allow about 10 minutes when Python and provider credentials are ready; the first run scans the full catalog, so its duration depends on network and provider response times.

### 1. Install the desktop runtime

Python 3.12 is recommended. Create the environment used by the desktop shell and install the single declared dependency:

```powershell
py -3.12 -m venv .\desktop\.venv
.\desktop\.venv\Scripts\python.exe -m pip install -r .\requirements.txt
```

### 2. Configure a provider locally

Copy the blank example and edit only your local copy:

```powershell
Copy-Item .\hackwatch\config.example.json .\hackwatch\config.json
notepad .\hackwatch\config.json
```

Set `provider_endpoint`, `model`, and `api_key` in that local file for an OpenAI-compatible provider. The committed `config.example.json` intentionally leaves those three values empty. Do not put a real key or provider URL in this README or commit your local `config.json`.

If no credential is available, the scorer may label results as heuristic; do not present those as LLM-reviewed. The current runtime can also discover credentials from a local OpenCode profile when the configured key is blank. For a controlled demo, set the intended provider and key explicitly and use a trusted endpoint.

Environment variables `NEXUSLINK_BASE`, `NEXUSLINK_SLOT`, and `NEXUSLINK_KEY` take precedence over the matching config fields. Leave them unset if you want the local file to control provider selection.

### 3. Run the assessment

From the repository root, this command saves the JSON where the desktop UI serves it:

```powershell
$repo = (Get-Location).Path
$env:HACKWATCH_JSON = Join-Path $repo 'desktop\hackwatch.json'
Push-Location (Join-Path $repo 'hackwatch')
try {
    & (Join-Path $repo 'desktop\.venv\Scripts\python.exe') -m hackwatch
} finally {
    Pop-Location
}
```

Expected result: the console reports the catalog and analysis progress, then writes `desktop/hackwatch.json`. The scorer also keeps its incremental state under `hackwatch/state.json`; later runs process new projects unless `--reparse` is supplied.

### 4. Open the UI and inspect the report

Launch the desktop shell from its directory so its local server can find `ui.html` and `hackwatch.json`:

```powershell
Push-Location .\desktop
try {
    & .\.venv\Scripts\python.exe .\desktop.py
} finally {
    Pop-Location
}
```

The HackWatch window opens with the sortable ranking table. Select a project row, then inspect the expanded accordion: score fields, evaluator, demo and threat rationale, verdict, and detailed report. Use the `EN` / `RU` controls to switch report language.

A one-step launcher is also available after the assessment has been written:

```powershell
Push-Location .\desktop
try {
    powershell -ExecutionPolicy Bypass -File .\run_desktop.ps1
} finally {
    Pop-Location
}
```

### 5. Verify the Testnet registry

Install the NEAR CLI (`near-cli-rs`) and configure a Testnet account only if you need to submit a transaction. Read the current registry without signing or exposing a key:

```powershell
near contract call-function as-read-only hackwatch.yksunder.testnet get_all json-args '{}' network-config testnet now
```

Read one project record (replace the placeholder with an ID returned by `get_all()`):

```powershell
near contract call-function as-read-only hackwatch.yksunder.testnet get_score json-args '{"project_id":"<catalog-project-id>"}' network-config testnet now
```

The scorer's ranking metric is on a 0–10 scale; the contract's `score` is 0–100, and this repository does not define an automatic conversion between them.

To write a record, the contract owner must submit the transaction. This is a separate, manual step; it is not performed by the scorer:

```powershell
$ownerAccount = Read-Host 'Contract owner account ID'
near contract call-function as-transaction hackwatch.yksunder.testnet set_score `
  json-args '{"project_id":"<catalog-project-id>","score":87,"note":"owner-submitted Testnet record"}' `
  prepaid-gas '30 Tgas' attached-deposit '0 NEAR' `
  sign-as $ownerAccount network-config testnet sign-with-keychain send
```

Use only the account that initialized the contract. A successful call returns a transaction ID; open the Testnet Explorer link printed by the CLI to inspect the transaction, then repeat `get_score` or `get_all` to confirm the stored record. Never paste a seed phrase or private key into this repository.

CLI command reference: [NEAR CLI RS guide](https://github.com/near/near-cli-rs/blob/main/docs/GUIDE.en.md).

## Implementation

| Part | Source | Responsibility |
|---|---|---|
| Scorer | `hackwatch/hackwatch.py` | Catalog ingestion, demo checks, bilingual LLM assessment, local JSON/HTML output |
| Desktop UI | `desktop/desktop.py`, `desktop/ui.html` | Local pywebview window and ranking/report interface |
| NEAR registry | `contracts/hackwatch_scores/` | Owner-only score writes and public view methods on Testnet |

The desktop shell is Python + pywebview; this repository provides a launcher, not a prebuilt Windows `.exe`. The scorer uses the Python standard library. `requirements.txt` declares pywebview for the desktop shell.

## Limitations and safety

- The scorer reviews the full catalog on its first run; elapsed time depends on the catalog, demo hosts, and provider.
- LLM output and heuristic fallback are assessments, not verified facts or a substitute for judges.
- The scorer and the on-chain registry are not automatically connected yet; local JSON is not proof of an on-chain score.
- The public provider fields are blank by design. Keep credentials in a local file or environment, use a trusted provider, and check `git status` before sharing. The current `.gitignore` does not exclude `hackwatch/config.json`.
- The current scorer disables TLS certificate validation for outbound requests. Do not send credentials over an untrusted network or to an untrusted provider.
- Testnet only. No mainnet write path is documented here.

## Second project by the same team

HackWatch is the team's second independent hackathon project. The first project is [NEXUS AiOS](https://github.com/ykbojak-alt/nexus-aios). It is a separate project and repository; this link is included for the hackathon's multiple-project disclosure requirement.

## License

MIT. See [`LICENSE`](LICENSE).
