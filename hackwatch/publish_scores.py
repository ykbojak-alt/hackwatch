"""Hackathon Watch — on-chain score publisher (--publish-scores).

ТЗ A.L.I.C.E. (on-chain scores → hackwatch_scores.yksunder.testnet):
  1. --publish-scores: после rebuild hackwatch.json пишет топ-N (N=20) в
     контракт через set_score(project_id, score, note).
     score = round(win_prob*10) (0..100), note = короткий вердикт (≤200 символов).
  2. Клиент = near_rpc.NearTestnetClient (pure-Python, stdlib — то, что
     уже стоит в near-hackathon-nexus/tools).
  3. Account+ключ: owner = подписывающий аккаунт (env NEAR_HACKWATCH_ACCOUNT
     / NEAR_ACCOUNT_ID, дефолт yksunder.testnet), ключ — NEAR_PRIVATE_KEY
     (через resolve_private_key: env → ~/.near-credentials/testnet/<acc>.json).
  4. TxHash каждого set_score пишется обратно в JSON: analysis.tx → UI-колонка.
  5. Dry-run: get_all() через RPC (view, без ключа) → демо-записи, печатает
     'dry ok'. Реальных вызовов set_score НЕ делает.

Запуск:
    python -m hackwatch --publish-scores              # полный publish (top-20)
    python -m hackwatch --publish-scores --publish-dry   # dry: get_all + 'dry ok'
    python -m hackwatch --publish-scores --top-n 10   # свой топ-N
"""
from __future__ import annotations
import json
import re
import ssl
import sys
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, r"D:\near-hackathon-nexus")
sys.path.insert(0, str(BASE))

# near_rpc/research_agent_clients из репо (stdlib-only, без установки)
try:
    from near_rpc import NearTestnetClient, parse_near_private_key, EXPLORER_TX
    from research_agent_clients import env
except ImportError:
    from tools.near_rpc import NearTestnetClient, parse_near_private_key, EXPLORER_TX
    from tools.research_agent_clients import env

# локальные модули
import hackwatch as hw

CONTRACT_ID = env("NEAR_HACKWATCH_CONTRACT") or "hackwatch.yksunder.testnet"  # FORGE-деплой 8d2bad4: реальный id = hackwatch.* (не _scores)
OWNER_ACCOUNT = env("NEAR_HACKWATCH_ACCOUNT") or env("NEAR_ACCOUNT_ID") or "yksunder.testnet"
DEFAULT_TOP_N = 20
NOTE_MAX = 200


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


def _row_scores(r: dict) -> dict:
    """Shared scores: new dual format has analysis.scores; legacy v1 keeps the
    numeric fields inside the per-lang blocks. Read from whichever exists."""
    an = r.get("analysis", {})
    if "scores" in an:
        return an["scores"]
    # legacy v1: numbers live in analysis.en / analysis.ru
    base = an.get("en") or an.get("ru") or {}
    out = {}
    for k in ("maturity", "near_fit", "novelty", "impact", "strength",
              "track_fit", "demo_assessment"):
        if k in base:
            out[k] = base[k]
    return out


def _row_verdict(r: dict) -> str:
    an = r.get("analysis", {})
    if "en" in an and isinstance(an["en"], dict):
        return an["en"].get("verdict", "") or ""
    return ""


def _note_for(r: dict) -> str:
    """Короткий вердикт ≤200 символов (ascii/utf8), из EN-ветки + shared scores."""
    verdict = _row_verdict(r)
    scores = _row_scores(r)
    demo = scores.get("demo_assessment", "?")
    mat = scores.get("maturity", 0)
    near = scores.get("near_fit", 0)
    strength = scores.get("strength", scores.get("threat", 0))
    note = f"[{demo} m{mat} nf{near} str{strength}] {verdict}"
    note = note.replace("\u2028", " ").strip()
    return note[:NOTE_MAX]


def build_payloads(contract: dict, top_n: int) -> list[dict]:
    """Топ-N по win_prob → [{project_id, project_name, score, note}]."""
    ranking = sorted(contract["ranking"], key=lambda r: r["score"]["win_prob"], reverse=True)[:top_n]
    out = []
    for r in ranking:
        score = int(round(r["score"]["win_prob"] * 10))  # 0..100
        score = max(0, min(100, score))
        note = _note_for(r)
        out.append({
            "project_id": r["id"],
            "project_name": r["project_name"],
            "score": score,
            "note": note,
            "note_bytes": len(note.encode("utf-8")),
        })
    return out


def resolve_owner_key() -> str:
    """Ключ подписывающего аккаунта (owner контракта). НЕ печатается в лог."""
    try:
        from near_contract_client import resolve_private_key
    except ImportError:
        from tools.near_contract_client import resolve_private_key
    return resolve_private_key(OWNER_ACCOUNT, "manager")


def dry_run(client: "NearTestnetClient", contract: dict) -> int:
    """get_all (view, без ключа) — должен вернуть демо-запись; печатает 'dry ok'."""
    log(f"DRY: view get_all() on {CONTRACT_ID} (no key, read-only)")
    t0 = time.time()
    raw = client.view_function(CONTRACT_ID, "get_all", {})
    # near_sdk возвращает Vec<(String, ScoreEntry)> как [[id, {score,note,ts_ms}]]
    entries = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                entries.append({"project_id": item[0], "entry": item[1]})
    log(f"DRY: get_all -> {len(entries)} on-chain entries in {round(time.time()-t0,1)}s")
    for e in entries[:5]:
        ent = e["entry"]
        log(f"   on-chain: {str(e['project_id'])[:16]}… score={ent.get('score')} ts_ms={ent.get('ts_ms')} note={str(ent.get('note'))[:40]!r}")
    # dry-ожидаемый payload (без отправки)
    payloads = build_payloads(contract, DEFAULT_TOP_N)
    log(f"DRY: would publish {len(payloads)} set_score calls (top-{DEFAULT_TOP_N}); example payload:")
    print(json.dumps(payloads[0], ensure_ascii=False, indent=2))
    log("dry ok")
    return 0


def publish(client: "NearTestnetClient", contract: dict, top_n: int) -> int:
    """Реальный publish: set_score топ-N, TxHash → analysis.tx в JSON."""
    key = resolve_owner_key()
    log(f"PUBLISH: owner={OWNER_ACCOUNT} contract={CONTRACT_ID} top-{top_n}")
    if not client.contract_deployed(CONTRACT_ID):
        log("FATAL: contract not deployed (code_hash empty) — abort")
        return 1
    payloads = build_payloads(contract, top_n)
    # сопоставить payload → строку контракта по project_id
    pid_to_row = {r["id"]: r for r in contract["ranking"]}
    written = 0
    for pl in payloads:
        args = {"project_id": pl["project_id"], "score": pl["score"], "note": pl["note"]}
        log(f"set_score({pl['project_id'][:12]}…, {pl['score']}, {pl['note'][:36]!r})")
        try:
            res = client.send_function_call(
                OWNER_ACCOUNT, CONTRACT_ID, "set_score", args, key,
                gas=50 * 10**12, deposit_yocto=0)
            tx_hash = res["tx_hash"]
            written += 1
            # пишу TxHash обратно в строку контракта
            row = pid_to_row.get(pl["project_id"])
            if row:
                row["analysis"]["tx"] = tx_hash
                row["analysis"]["tx_explorer"] = EXPLORER_TX + tx_hash
            log(f"  ok -> tx {tx_hash[:16]}…")
        except Exception as e:
            log(f"  FAILED set_score {pl['project_id'][:12]}…: {e}")
    # сохранить обновлённый контракт + state
    hw.JSON_OUT.write_text(json.dumps(contract, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"PUBLISH done: {written}/{len(payloads)} written; hackwatch.json updated with analysis.tx")
    return 0


def ensure_contract_id() -> str:
    """Подтвердить contract id (env override или дефолт)."""
    return CONTRACT_ID


def run_publish(contract: dict, dry: bool, top_n: int) -> int:
    client = NearTestnetClient()
    # preflight: contract deployed?
    deployed = client.contract_deployed(CONTRACT_ID)
    log(f"preflight: {CONTRACT_ID} deployed={deployed}")
    if dry:
        return dry_run(client, contract)
    if not deployed:
        log("FATAL: not deployed — cannot publish")
        return 1
    return publish(client, contract, top_n)
