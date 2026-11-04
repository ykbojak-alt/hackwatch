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

**Integration boundary:** a normal scorer run writes its assessment locally. The optional `--publish-scores` path submits up to 20 owner-authorized `set_score` transactions; `--publish-dry` reads `get_all()` and previews a candidate payload without writing. Dry-run output is not a row-by-row comparison. Only a successful live transaction is on-chain evidence. The publisher converts `win_prob` from 0–10 to the contract's 0–100 score by multiplying by 10.

## Verify our scores yourself (15 min)

### English (EN)

Follow this checklist from the repository root. Fifteen minutes is a target, not a guarantee: a fresh run fetches the whole live catalog and asks the configured model to assess each uncached project.

1. **Install.** Run `pip install -r requirements.txt`. The scorer itself uses the Python standard library; this file installs the desktop UI dependency.
2. **Configure the model.** The template in this repository is `hackwatch/config.example.json` (not `config.json.example`). Copy it to `hackwatch/config.json`, then run this from the `hackwatch/` directory after making `INCEPTION_KEY` available in the process environment:

   ```powershell
   Copy-Item .\config.example.json .\config.json
   python -c "import json,os; from pathlib import Path; p=Path('config.json'); c=json.loads(Path('config.example.json').read_text(encoding='utf-8')); c.update(gateway='https://api.inceptionlabs.ai/v1', slot='mercury-3', key=os.environ['INCEPTION_KEY']); p.write_text(json.dumps(c, indent=2), encoding='utf-8')"
   ```

   The script reads the key from `INCEPTION_KEY` and writes it only to the local config file; it does not print the value. `hackwatch/config.json` is not ignored by the repository's `.gitignore`, so keep it local and never stage it. Leave `NEXUSLINK_BASE`, `NEXUSLINK_SLOT`, and `NEXUSLINK_KEY` unset if the local config should take effect.
3. **Run the evaluation.** From `hackwatch/`, run `python -m hackwatch`. It writes `hackwatch.json` beside the scorer. The checked-in demo snapshot has 95 rows; a fresh run uses the live catalog, which returned 98 projects when checked on 2026-11-03. The live count can change.
4. **Inspect the chain read-only.** From `hackwatch/`, run `python -m hackwatch --publish-scores --publish-dry`. This calls `get_all()`, prints the on-chain entry count and up to five current records, then prints one example from the proposed top-20 payload. It does not call `set_score`, alter the contract, or create transaction links, and it does not compare every local score with every on-chain entry. The dry-run publisher currently imports helper modules from a separate NEXUS checkout at a hardcoded Windows path; those modules are not included in this public repository or `requirements.txt`, so this step is not standalone on a fresh public clone. If you run it in an environment with those helpers, compare the displayed records with `hackwatch.json` manually.
5. **Check transaction proof.** After an owner-authorized live publish, each successful top-20 write adds `ranking[*].analysis.tx` and `ranking[*].analysis.tx_explorer` to `hackwatch.json`. Open each `analysis.tx_explorer` URL; it points to the NEAR Testnet transaction page on NearBlocks. Dry-run does not create these fields. The current desktop table reads top-level transaction fields, not nested `analysis.tx`, so inspect the JSON link directly if the UI shows a dash.

### Русский (RU)

1. **Установите зависимости:** из корня репозитория выполните `pip install -r requirements.txt`. Сам scorer использует стандартную библиотеку Python; requirements устанавливает зависимость desktop UI.
2. **Настройте модель:** фактический шаблон — `hackwatch/config.example.json`. Скопируйте его в `hackwatch/config.json` и заполните из переменной окружения `INCEPTION_KEY` в Python-команде выше: шлюз `https://api.inceptionlabs.ai/v1`, слот `mercury-3`, ключ читается из переменной и не печатается. Локальный `hackwatch/config.json` не исключён из `.gitignore` — не добавляйте его в Git. Чтобы читалась конфигурация из файла, оставьте `NEXUSLINK_BASE`, `NEXUSLINK_SLOT` и `NEXUSLINK_KEY` незаданными.
3. **Запустите оценку:** из `hackwatch/` выполните `python -m hackwatch`. Файл `hackwatch.json` появится рядом со scorer. В сохранённом demo-снимке 95 строк; при проверке 2026-11-03 live-каталог вернул 98 проектов, поэтому число в новом запуске меняется вместе с каталогом.
4. **Проверьте on-chain данные без записи:** команда `python -m hackwatch --publish-scores --publish-dry` вызывает `get_all()`, показывает число записей и до пяти примеров, затем выводит один пример из планируемого top-20 payload. Она не вызывает `set_score`, не меняет контракт и не сравнивает все строки автоматически. Если у вас есть требуемые внешние helper-модули, сравните показанные записи с `hackwatch.json` вручную. Publisher сейчас импортирует эти модули из отдельного NEXUS checkout по жёстко заданному Windows-пути; их нет в публичном HackWatch и `requirements.txt`, поэтому в чистом standalone-клоне команда пока не работает.
5. **Проверьте доказательство транзакции:** только после разрешённой владельцем реальной публикации успешные записи top-20 получают поля `ranking[*].analysis.tx` и `analysis.tx_explorer`. Откройте URL из `analysis.tx_explorer` на NearBlocks Testnet. Dry-run эти поля не создаёт. Текущая desktop-таблица не читает вложенный `analysis.tx`; если там тире, проверьте ссылку прямо в JSON.

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

The scorer's `win_prob` is on a 0–10 scale. The optional publisher converts it to the contract's 0–100 `score` by multiplying by 10 and rounding.

To write a record manually, the contract owner must submit the transaction. The optional `--publish-scores` path can also submit owner-signed writes after an explicit run; neither path runs during the default local scoring command:

```powershell
$ownerAccount = Read-Host 'Contract owner account ID'
near contract call-function as-transaction hackwatch.yksunder.testnet set_score `
  json-args '{"project_id":"<catalog-project-id>","score":87,"note":"owner-submitted Testnet record"}' `
  prepaid-gas '30 Tgas' attached-deposit '0 NEAR' `
  sign-as $ownerAccount network-config testnet sign-with-keychain send
```

Use only the account that initialized the contract. A successful call returns a transaction ID; open the Testnet Explorer link printed by the CLI to inspect the transaction, then repeat `get_score` or `get_all` to confirm the stored record. Never paste a seed phrase or private key into this repository.

CLI command reference: [NEAR CLI RS guide](https://github.com/near/near-cli-rs/blob/main/docs/GUIDE.en.md).

## Download & Install (Windows, REL-1 v0.2.0)

Two artifacts are produced from `D:/hackwatch` (the desktop workspace). This repository documents them; it does not host the binaries. Both are Windows x64, per-user (no admin required), and loopback-only — the app opens a local HTTP server on a random free `127.0.0.1` port; nothing is published to the network.

- **Installer** — `HackathonWatch-Setup-0.2.0.exe` (Inno Setup; `installer/installer.iss`): installs to `%LOCALAPPDATA%\Programs`, creates a Start-menu group and an optional desktop shortcut, and adds an uninstaller.
- **Portable ZIP** — `HackathonWatch-0.2.0-portable.zip`: the `HackathonWatch.exe`, `ui.html`, and a short `HackathonWatch-README.txt`. Unzip to any folder, run the EXE, delete the folder to remove it.

Integrity (SHA-256, from `SHA256SUMS.txt`):

```
4af728c126f76a6ac06393d0e3e8c214433a307a2f0310e77b118fb9c5c8cfe1  HackathonWatch-Setup-0.2.0.exe
ac0eb23fbb954ceb7a79f756f05efa3d38b071b057bf0001b2f631e02bfd51c1  HackathonWatch-0.2.0-portable.zip
```

Where the app reads its data: user data lives in `%APPDATA%\Hackwatch\` (`hackwatch.json`). The installer/portable EXE ship only the UI — they never bundle or write any credentials. **Testnet-only:** scoring and the optional on-chain publish target `hackwatch.yksunder.testnet` (NEAR Testnet). No NEAR private key, seed phrase, or provider API key is shipped in either artifact. Without a provider key the scorer degrades to a local heuristic; the UI still runs.

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
- The default scorer run does not write to the chain. The separate `--publish-scores` option does; local JSON alone is not proof of an on-chain score.
- The public provider fields are blank by design. Keep credentials in a local file or environment, use a trusted provider, and check `git status` before sharing. The current `.gitignore` does not exclude `hackwatch/config.json`.
- TLS certificate validation is enabled by default. It can be disabled only by explicitly setting `NEXUSLINK_INSECURE=1` or `tls_insecure: true` in local config; avoid that for public providers.
- Testnet only. No mainnet write path is documented here.

## Second project by the same team

HackWatch is the team's second independent hackathon project. The first project is [NEXUS AiOS](https://github.com/ykbojak-alt/nexus-aios). It is a separate project and repository; this link is included for the hackathon's multiple-project disclosure requirement.

## License

MIT. See [`LICENSE`](LICENSE).

## Disclosure

HackWatch is an independent second submission by the same participant as [NEXUS AiOS](https://github.com/ykbojak-alt/nexus-aios). Both projects disclose each other under hackathon rule Q-015.

HackWatch — независимая вторая заявка того же участника, что и [NEXUS AiOS](https://github.com/ykbojak-alt/nexus-aios). Оба проекта раскрывают свою связь друг с другом согласно правилу хакатона Q-015.
