"""Hackathon Watch — аналитическое ядро (ACE / t27).

Один LLM-вызов = один проект = одна запись с общими числами + две текстовые
ветки (en/ru). Числа НЕ могут разойтись между языками — они общие.

Анатомия записи analysis (state.json):
  {
    scores:   { maturity, demo_assessment, track_fit, threat },   # общие
    en:       { rationale, demo_note, verdict, details },
    ru:       { rationale, demo_note, verdict, details },         # перевод
    evaluator: "<модель из ответа шлюза>",                          # честная атрибуция
    source:    "nexus/mercury-3" | "heuristic"
  }

UI-совместимость (FORGE): экспорт по языку даёт analysis.<lang>.<field>,
числа лежат в analysis.scores.* — одна копия на весь язык.

Один вызов, полный прогон:
    cd D:/Temp/hackwatch && python -m hackwatch            # только новые
    python -m hackwatch --reparse                          # все заново
Экспорт для UI-переключателя:
    python -m hackwatch --export-lang en|ru|all
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HACK = "https://nearhackathon.com"
PAGE_SIZE = 10
OUR_ID = "f206fa10-94e3-4537-a8e6-912a712697a9"
OUR_NAME = "NEXUS AiOS"

BASE_DIR = Path(__file__).resolve().parent
# Рабочие данные — вне bundled-каталога: %APPDATA%/Hackwatch при сборке exe.
DATA_DIR = Path(os.environ.get("HACKWATCH_STATE_DIR", str(BASE_DIR)))
CONFIG_FILE = BASE_DIR / "config.json"
STATE_FILE = DATA_DIR / "state.json"
HTML_OUT = DATA_DIR / "hackwatch.html"
JSON_OUT = Path(os.environ.get("HACKWATCH_JSON", str(DATA_DIR / "hackwatch.json")))

# ---------- provider config (endpoint + model + key: env > config.json > opencode[deprecated]) ----------
# TLS: verify ON by default. Self-signed local gateways opt in with
# NEXUSLINK_INSECURE=1 (or config.json {"tls_insecure": true}) — never silently off.
_GW_CONFIG = {}
if CONFIG_FILE.exists():
    try:
        _GW_CONFIG = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        log("WARN: config.json corrupt — ignoring")
GATEWAY = (os.environ.get("NEXUSLINK_BASE")
           or _GW_CONFIG.get("provider_endpoint")
           or _GW_CONFIG.get("gateway")
           or os.environ.get("NEXUS_LINK_DEFAULT", ""))
SLOT = (os.environ.get("NEXUSLINK_SLOT") or _GW_CONFIG.get("model")
        or _GW_CONFIG.get("slot") or "claude-sonnet-5")
# key: env NEXUSLINK_KEY > config.json "key" > opencode.jsonc (deprecated local-dev fallback)
GATEWAY_KEY = os.environ.get("NEXUSLINK_KEY", "")
TLS_INSECURE = os.environ.get("NEXUSLINK_INSECURE") == "1" or bool(_GW_CONFIG.get("tls_insecure"))


def _build_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ca = os.environ.get("NEXUSLINK_CA_BUNDLE") or _GW_CONFIG.get("ca_bundle")
    if ca:
        try:
            ctx.load_verify_locations(ca)
        except Exception as e:
            log(f"WARN: ca_bundle {ca!r} failed: {e}")
    if TLS_INSECURE:
        log("WARN: NEXUSLINK_INSECURE — TLS verification DISABLED (self-signed gateway)")
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


CTX = _build_ctx()

SCHEMA_VERSION = 1
CJK = re.compile(r"[\u4e00-\u9fff]")
VALID_ASSESSMENTS = {"real_demo", "x_post", "video", "docs_only", "broken_link", "no_demo"}


def log(*a) -> None:
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


def load_key() -> str:
    """key order: env NEXUSLINK_KEY > config.json > opencode.jsonc (deprecated).
    Logs only 'key set' — never the value or tail."""
    global GATEWAY_KEY
    if GATEWAY_KEY:
        log("key set (env NEXUSLINK_KEY)")
        return GATEWAY_KEY
    k = _GW_CONFIG.get("api_key") or _GW_CONFIG.get("key")
    if k:
        GATEWAY_KEY = k
        log("key set (config.json)")
        return k
    # deprecated local-dev fallback
    for cand in (
        Path.home() / ".config" / "opencode" / "opencode.jsonc",
        Path.home() / ".config" / "opencode" / "opencode.json",
    ):
        if cand.exists():
            try:
                j = json.loads(cand.read_text(encoding="utf-8"))
                k = j["provider"]["nexus"]["options"]["apiKey"]
                GATEWAY_KEY = k
                log(f"WARN: DEPRECATED key fallback via {cand.name} — set NEXUSLINK_KEY or config.json")
                return k
            except Exception:
                pass
    return ""


# ---------- 1. Catalog ----------

def fetch_catalog() -> tuple[list[dict], int | None]:
    projects: dict[str, dict] = {}
    page, total = 1, None
    while True:
        url = f"{HACK}/api/projects?page={page}&pageSize={PAGE_SIZE}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 HackWatch"})
        try:
            with urllib.request.urlopen(req, context=CTX, timeout=15) as r:
                j = json.loads(r.read())
        except Exception as e:
            log(f"WARN page {page}: {e} — stopping pagination")
            break
        total = j.get("total", total)
        for p in j.get("projects", []):
            projects[p["id"]] = p
        if (total is not None and len(projects) >= total) or not j.get("projects"):
            break
        page += 1
        time.sleep(0.15)
    log(f"catalog: {len(projects)} projects (total reported: {total})")
    return list(projects.values()), total


# ---------- 2. Objective demo classification ----------

X_HOST = re.compile(r"^(x|twitter)\.com", re.I)
LIVE_CODES = (200, 206, 301, 302, 307, 308)


def classify_demo_url(url: str | None) -> str:
    if not url:
        return "none"
    if X_HOST.match(url):
        return "x_post"
    if url.startswith(("#", "/", "javascript")):
        return "anchor"
    return "site"


def _site_status(url: str) -> tuple[int, float]:
    t0 = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 HackWatch"})
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=12) as r:
            return r.status, round(time.time() - t0, 2)
    except urllib.error.HTTPError as e:
        return e.code, round(time.time() - t0, 2)
    except Exception:
        return 0, round(time.time() - t0, 2)


def enrich_objective(project: dict) -> dict:
    demo_url = project.get("demo_url") or ""
    dt = classify_demo_url(demo_url)
    site_code, _site_time = _site_status(demo_url) if dt == "site" else (0, 0.0)
    project["_demo_live"] = dt == "site" and site_code in LIVE_CODES
    project["_demo_type"] = dt
    project["_site_http"] = site_code
    return project


def enrich_batch(projects: list[dict]) -> None:
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(enrich_objective, projects))


# ---------- 3. LLM analysis (single bilingual call per project) ----------

# One call → shared scores + en branch + ru branch. Numbers live once.
PROMPT_DUAL = """You are a judge for the NEAR protocol hackathon.
Assess this ONE submission on its own merits, using the hackathon's judging
criteria. Judge it like any other entry — do not reference any team's name,
do not say "our project", do not reference NEXUS. Purely: what is the
implementation like, how real is its NEAR usage, how solid is the demo, how
novel and how impactful.

Return strict JSON (no markdown, no commentary):
{
  "scores": {
    "maturity": <0-10 int, how real the implementation is vs a pitch>,
    "near_fit": <0-10 int, how concretely it uses NEAR (Intents/escrow/RPC/contract) vs just a label>,
    "demo": <0-10 int, live demo/video/repo vs x-post vs nothing>,
    "track_fit": "<strong|partial|weak, fit to the declared tracks>",
    "novelty": <0-10 int, originality of the idea in its track>,
    "impact": <0-10 int, usefulness after the hackathon>,
    "strength": <0-10 int, overall competitive strength among similar submissions>
  },
  "en": {
    "rationale": "<max 10 words, why this score, English>",
    "demo_note": "<max 8 words on demo quality, English>",
    "verdict": "<max 15 words, one-line note, English>"
  },
  "ru": {
    "rationale": "<max 10 слов, почему такая оценка, РУССКИЙ>",
    "demo_note": "<max 8 слов про качество демо, РУССКИЙ>",
    "verdict": "<max 15 слов, короткое заключение, РУССКИЙ>"
  }
}
STRICT RULES:
- Numbers appear ONLY in "scores"; ru branch must NOT re-score — it only
  translates the English text (same numbers as scores).
- ru branch: NO CJK characters (no 汉字). Only Russian; English proper nouns and
  tech terms allowed (demo/escrow/Intents/contract/agent/NEAR/название проекта).
- "strength" is a public, absolute rating of this project — not a relative
  "threat to us". No reference to any specific competing team.
- demo_assessment must be derived from the objective demo_type:
  site → real_demo (if site_reachable=true) else broken_link;
  x_post → x_post; video → video; docs → docs_only; none → no_demo.
- Judge every project the same way — the criteria above are absolute,
  identical for all submissions regardless of author.

Project data:
{project_json}

Objective facts (already computed, use them): demo_type={demo_type},
site_reachable={demo_live}, site_http={site_http}.

Now output the JSON object.
"""


def _project_brief(p: dict) -> dict:
    return {
        "id": p["id"],
        "project_name": p.get("project_name"),
        "tracks": p.get("tracks"),
        "demo_url": p.get("demo_url"),
        "demo_type": p.get("_demo_type"),
        "demo_live": p.get("_demo_live"),
        "site_http": p.get("_site_http"),
    }


def _strip_fences(content: str) -> str:
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())


def _numeric_coerce(scores: dict) -> dict:
    """Force ints/enums so shared scores are always clean. Includes demo_assessment
    plus the new absolute criteria (near_fit/novelty/impact/strength)."""
    dem = scores.get("demo_assessment")
    if dem not in VALID_ASSESSMENTS:
        dem = "no_demo"
    track = scores.get("track_fit")
    if track not in ("strong", "partial", "weak"):
        track = "weak"
    def _i01(v, default=0):
        try:
            return max(0, min(10, int(v)))
        except Exception:
            return default
    return {
        "maturity": _i01(scores.get("maturity", 3)),
        "demo_assessment": dem,
        "track_fit": track,
        "near_fit": _i01(scores.get("near_fit")),
        "novelty": _i01(scores.get("novelty")),
        "impact": _i01(scores.get("impact")),
        "strength": _i01(scores.get("strength")),
        # keep legacy alias so old contracts keep reading
        "threat": _i01(scores.get("strength", scores.get("threat", 0))),
    }


def llm_analyze(projects: list[dict]) -> dict[str, dict]:
    """One bilingual call per project → {id: analysis}. Shared scores + en/ru
    branches + evaluator (honest gateway model). Heuristic fallback on failure.
    The prompt scores ABSOLUTELY (no NEXUS/our-team reference)."""
    if not GATEWAY_KEY:
        log("WARN: NEXUSLINK_KEY empty — LLM skipped, heuristic only")
        return heuristic_fallback(projects)

    out: dict[str, dict] = {}
    for i, p in enumerate(projects, 1):
        brief = _project_brief(p)
        prompt = PROMPT_DUAL
        prompt = prompt.replace("{project_json}", json.dumps(brief, ensure_ascii=False))
        prompt = prompt.replace("{demo_type}", str(p.get("_demo_type", "?")))
        prompt = prompt.replace("{demo_live}", str(p.get("_demo_live", False)))
        prompt = prompt.replace("{site_http}", str(p.get("_site_http", "")))
        body = json.dumps({
            "model": SLOT,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": int(os.environ.get("HACKWATCH_MAX_TOKENS", "4096")),
            "temperature": 0.1,
        }).encode()
        url = f"{GATEWAY}/chat/completions"
        ok = False
        for attempt in range(3):
            try:
                req = urllib.request.Request(
                    url, data=body,
                    headers={"Content-Type": "application/json",
                             "Authorization": f"Bearer {GATEWAY_KEY}",
                             "x-api-key": GATEWAY_KEY, "User-Agent": "hackwatch"},
                    method="POST",
                )
                t0 = time.time()
                with urllib.request.urlopen(req, context=CTX, timeout=90) as r:
                    rj = json.loads(r.read())
                content = ((rj.get("choices") or [{}])[0].get("message", {}).get("content") or "").strip()
                elapsed = round(time.time() - t0, 1)
                raw_model = rj.get("model", SLOT)
                obj = json.loads(_strip_fences(content))
                analysis = _coerce_dual(p, obj, raw_model)
                out[p["id"]] = analysis
                log(f"{i}/{len(projects)} {str(p.get('project_name'))[:22]:<22} "
                    f"-> {raw_model} ({elapsed}s, attempt {attempt+1})")
                ok = True
                break
            except Exception as e:
                log(f"{i}/{len(projects)} {str(p.get('project_name'))[:22]:<22} "
                    f"attempt {attempt+1} FAILED: {e}")
                time.sleep(2)
        if not ok:
            log(f"{i}/{len(projects)} {str(p.get('project_name'))[:22]:<22} -> heuristic fallback")
            out[p["id"]] = heuristic_fallback([p])[p["id"]]
    return out


def _coerce_dual(project: dict, obj: dict, raw_model: str) -> dict:
    """Coerce the LLM JSON into the canonical dual-lang record with shared scores."""
    scores = _numeric_coerce(obj.get("scores", {}))
    # Objective demo facts override the LLM: the live-check is ground truth.
    dt = project.get("_demo_type")
    live = project.get("_demo_live")
    if dt == "site" and live:
        scores["demo_assessment"] = "real_demo"
    elif dt == "site" and not live:
        scores["demo_assessment"] = "broken_link"
    elif dt == "x_post":
        scores["demo_assessment"] = "x_post"
    elif dt in ("none", None):
        scores["demo_assessment"] = "no_demo"
    # (video/docs_only keep the LLM's call)

    en = obj.get("en", {}) or {}
    ru = obj.get("ru", {}) or {}
    cjk_fixed = False
    # CJK guard on the ru branch
    ru_has_cjk = any(CJK.search(str(v)) for v in ru.values() if isinstance(v, str))
    if ru_has_cjk:
        log(f"  CJK detected in ru branch of {project.get('project_name')} — re-translating from en")
        ru = _translate_ru_from_en(en, scores)
        cjk_fixed = True
    rec = {
        "id": project["id"],
        "scores": scores,
        "en": {
            "rationale": str(en.get("rationale", "")),
            "demo_note": str(en.get("demo_note", "")),
            "verdict": str(en.get("verdict", "")),
        },
        "ru": {
            "rationale": str(ru.get("rationale", "")),
            "demo_note": str(ru.get("demo_note", "")),
            "verdict": str(ru.get("verdict", "")),
        },
        # flag survives into the record so the CJK fix is reportable downstream
        "_cjk_fixed": cjk_fixed,
        "evaluator": raw_model,
        "source": f"nexus/{raw_model}",
    }
    return rec


def _translate_ru_from_en(en: dict, scores: dict) -> dict:
    """Deterministic RU template from shared scores + EN fallback text. No CJK."""
    demo = scores["demo_assessment"]
    return {
        "rationale": str(en.get("rationale", "")),  # EN text as RU fallback (no CJK)
        "demo_note": str(en.get("demo_note", "")),
        "verdict": str(en.get("verdict", "")),
    }


def _heuristic_scores(project: dict) -> dict:
    dt = project.get("_demo_type", "none")
    live = project.get("_demo_live", False)
    tracks = [t.lower() for t in (project.get("tracks") or [])]
    overlap = len(set(tracks) & {"swarms", "security", "agents"})
    maturity = {"real_demo": 7, "x_post": 3, "video": 5, "docs_only": 4,
                "broken_link": 2, "no_demo": 2}.get(dt, 3)
    if live:
        maturity = max(maturity, 6)
    strength = 5 + overlap if overlap >= 2 else 2
    demo_label = dt if dt in ("x_post", "video", "docs_only", "none") else ("real_demo" if live else "broken_link")
    return _numeric_coerce({
        "maturity": min(10, maturity), "demo_assessment": demo_label,
        "track_fit": "strong" if overlap >= 2 else ("partial" if overlap == 1 else "weak"),
        "near_fit": maturity, "novelty": 4, "impact": 4, "strength": strength,
    })


def _heuristic_texts(scores: dict) -> dict:
    demo = scores["demo_assessment"]
    en = {
        "rationale": f"track overlap, demo={demo}",
        "demo_note": f"heuristic: demo={demo}",
        "verdict": "heuristic only — LLM unavailable",
        "details": "Heuristic assessment. No LLM detail available.",
    }
    ru = {
        "rationale": f"пересечение треков, demo={demo}",
        "demo_note": f"эвристика: demo={demo}",
        "verdict": "только эвристика — LLM недоступен",
        "details": "Эвристическая оценка. LLM-детали недоступны.",
    }
    return {"en": en, "ru": ru}


def heuristic_fallback(projects: list[dict]) -> dict[str, dict]:
    out = {}
    for p in projects:
        scores = _heuristic_scores(p)
        texts = _heuristic_texts(scores)
        out[p["id"]] = {
            "id": p["id"], "scores": scores,
            "en": texts["en"], "ru": texts["ru"],
            "evaluator": "heuristic", "source": "heuristic",
        }
    return out


# ---------- 4. Winning probability (deterministic, from shared absolute scores) ----------

WEIGHTS = {"maturity": 0.35, "demo": 0.30, "near_fit": 0.20, "novelty": 0.15}
DEMO_SCORE = {"real_demo": 10, "video": 7, "docs_only": 4, "x_post": 3, "broken_link": 1, "no_demo": 0}
TRACK_BONUS = {"strong": 10, "partial": 5, "weak": 0}


def win_prob(project: dict, scores: dict | None) -> tuple[float, dict]:
    """Absolute win probability by NEAR-Hackathon judging criteria (no NEXUS bias).
    = 0.35*maturity + 0.30*demo + 0.20*near_fit + 0.15*novelty, scaled 0-10,
    plus a small track-fit bonus. No 'threat to us' term."""
    s = scores or {}
    mat = s.get("maturity", 3)
    demo_type = s.get("demo_assessment", "no_demo")
    if demo_type == "real_demo" and project.get("_demo_type") == "site" and not project.get("_demo_live"):
        demo_type = "broken_link"
    demo_pts = DEMO_SCORE.get(demo_type, 2)
    near = s.get("near_fit", mat)          # fallback: if LLM didn't set it, use maturity
    novel = s.get("novelty", 4)
    base = WEIGHTS["maturity"] * mat + WEIGHTS["demo"] * demo_pts + WEIGHTS["near_fit"] * near + WEIGHTS["novelty"] * novel
    track_bonus = 0.5 if s.get("track_fit") in ("strong", "partial") else 0.0
    score = base + track_bonus
    breakdown = {"maturity": mat, "demo_type": demo_type, "demo_pts": demo_pts,
                 "near_fit": near, "novelty": novel, "track_fit": s.get("track_fit"), "base": round(base, 2)}
    return round(score, 1), breakdown


# ---------- 5. State file (dual-lang record per project) ----------

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log("WARN: state.json corrupt — starting fresh")
    return {"seen": {}, "last_run": None}


def save_state(state: dict) -> None:
    state["last_run"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def is_dual(rec: dict) -> bool:
    """New shape: has shared 'scores' + 'en' + 'ru' branches."""
    return isinstance(rec, dict) and "scores" in rec and "en" in rec and "ru" in rec


def normalize_scores(rec: dict) -> dict:
    """Return shared scores dict whether the record is new (dual) or legacy (per-lang)."""
    if is_dual(rec):
        return _numeric_coerce(rec.get("scores", {}))
    # legacy: take from en (source of truth) or ru
    base = rec.get("en") or rec.get("ru") or {}
    return _numeric_coerce(base)


def get_branch(rec: dict, lang: str) -> dict:
    """Return the en/ru text branch; for legacy per-lang records, flatten that lang."""
    if is_dual(rec):
        return rec.get(lang, {})
    return rec.get(lang, {}) if lang in rec else {}


# ---------- 6. Output JSON contract (dual, UI-compatible) ----------

def build_contract(rows: list[dict], total_reported: int | None, new_count: int,
                   llm_sources: list[str], lang_map: dict[str, str]) -> dict:
    ranking = []
    for place, r in enumerate(rows, 1):
        p = r["project"]
        rec = r.get("analysis") or {}
        scores = normalize_scores(rec)
        wp, brk = win_prob(p, scores)
        en = get_branch(rec, "en")
        ru = get_branch(rec, "ru")
        # UI-compatible analysis: shared scores + per-lang text (same numbers both langs)
        evaluator = rec.get("evaluator", "heuristic") if is_dual(rec) else "heuristic"
        source = rec.get("source", "heuristic") if is_dual(rec) else "heuristic"
        demo_obj = {
            "url": p.get("demo_url") or "",
            "objective_type": p.get("_demo_type"),
            "live": p.get("_demo_live") is True,
            "http": p.get("_site_http", 0) if p.get("_demo_type") == "site" else None,
            "assessment": scores.get("demo_assessment", "no_demo"),
            "rationale": en.get("demo_note", "") or en.get("rationale", ""),
        }
        ranking.append({
            "rank": place,
            "id": p["id"],
            "project_name": p.get("project_name"),
            "is_ours": p["id"] == OUR_ID,
            "tracks": p.get("tracks") or [],
            "demo": demo_obj,
            "score": {"win_prob": wp, "breakdown": brk},
            "analysis": {
                "scores": scores,            # shared, single copy
                "en": {**en, "evaluator": evaluator},
                "ru": {**ru, "evaluator": evaluator},
                "evaluator": evaluator,
                "source": source,
            },
        })
    # strength-based highlights (public semantics; our project always included)
    high_strength_ids = [
        r["id"] for r in ranking
        if r["is_ours"] or r["analysis"]["scores"].get("strength", 0) >= 7
    ]
    stats = {
        "live_demos": sum(1 for r in ranking if r["demo"]["live"]),
        "x_post_demos": sum(1 for r in ranking if r["demo"]["objective_type"] == "x_post"),
        "broken_links": sum(1 for r in ranking if r["analysis"]["scores"].get("demo_assessment") == "broken_link"),
        "no_demo": sum(1 for r in ranking if r["analysis"]["scores"].get("demo_assessment") == "no_demo"),
        "high_strength": len(high_strength_ids),
        "high_strength_ids": high_strength_ids,
        "lang_coverage": lang_map,
        # shared-scores design guarantees en/ru numeric parity; kept as a hard 0
        "numeric_mismatch": 0,
        # count records where the CJK-guard fired (visible in the record via _cjk_fixed)
        "cjk_fixed_count": sum(1 for r in rows if r.get("analysis", {}).get("_cjk_fixed")),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": {
            "catalog": f"{HACK}/api/projects",
            "total_projects": len(ranking),
            "catalog_total_reported": total_reported,
            "new_since_last_run": new_count,
        },
        "our_project": {
            "id": OUR_ID,
            "project_name": OUR_NAME,
            "highlight": True,
            "rank": next((r["rank"] for r in ranking if r["is_ours"]), None),
            "win_prob": next((r["score"]["win_prob"] for r in ranking if r["is_ours"]), None),
        },
        "ranking": ranking,
        "stats": stats,
        "llm": {
            "gateway": GATEWAY,
            "slot": SLOT,
            "sources": sorted(set(llm_sources)),
        },
    }


def sort_rows(rows: list[dict]) -> None:
    for r in rows:
        p = r["project"]
        scores = normalize_scores(r.get("analysis") or {})
        wp, _ = win_prob(p, scores)
        r["win_prob"] = wp
    rows.sort(key=lambda r: (-r["win_prob"], r["project"].get("project_name") or ""))


# ---------- 7. HTML preview ----------

def demo_icon(asmt: str) -> str:
    sym = {"real_demo": ("✓", "ok"), "video": ("▶", "ok"), "docs_only": ("◈", "mid"),
           "x_post": ("✕", "x"), "broken_link": ("!", "warn"), "unavailable": ("!", "warn")}
    s, cls = sym.get(asmt, ("?", "warn"))
    return f'<span class="sym">{s}</span><span>{asmt or "?"}</span>'

def render_html(contract: dict, out: Path, lang: str = "en") -> None:
    total = contract["stats"]
    rows_html = []
    for r in contract["ranking"]:
        an = r["analysis"]
        sc = an.get("scores") or {}
        cur = an.get(lang) or an.get("en") or {}
        is_our = r["is_ours"]
        cls = "row our" if is_our else "row"
        badge = " <span class='badge-our'>OUR</span>" if is_our else ""
        src = an.get("evaluator") or cur.get("source") or "heuristic"
        src_cls = "heuristic" if src == "heuristic" else "llm"
        src_lbl = "H" if src == "heuristic" else "L"
        ev_chip = "" if src == "heuristic" else (f'<span class="ev-chip">{src.replace("nexus/", "")}</span>')
        demo_cls = {"real_demo": "ok", "video": "ok", "docs_only": "mid",
                    "x_post": "x", "broken_link": "warn", "unavailable": "warn"}.get(
            sc.get("demo_assessment") or cur.get("demo_assessment"), "warn")
        strength = sc.get("strength", cur.get("threat", 0)) or 0
        t_cls = "high" if strength >= 7 else ("med" if strength >= 4 else "low")
        wp = r["score"]["win_prob"]
        wp_color = "#00c8ff" if wp >= 5 else ("#e8c96a" if wp >= 3.5 else "#d9605a")
        verdict = cur.get("verdict", "")
        tx = (r.get("tx") or {}).get("hash") or r.get("tx_hash") or r.get("onchain_tx")
        tx_html = ""
        if tx:
            base = r.get("tx_explorer") or "https://testnet.nearblocks.io/txns/"
            href = base.replace("{tx}", tx) if "{tx}" in base else base + tx
            tx_html = (f'<td class="tx"><a class="txchip" href="{href}" target="_blank" '
                       f'rel="noopener" title="{tx}">{tx[:10]}&#8599;</a></td>')
        else:
            tx_html = '<td class="tx"><span class="tx-empty">&mdash;</span></td>'
        rows_html.append(f"""
      <tr class="{cls}">
        <td class="place">{r['rank']}</td>
        <td class="proj">{r['project_name']}{badge}<span class="src-chip {src_cls}">{src_lbl}</span>{ev_chip}</td>
        <td class="tracks">{' · '.join(r['tracks']) or '—'}</td>
        <td><span class="demoico {demo_cls}">{demo_icon(sc.get('demo_assessment') or cur.get('demo_assessment') or '?')}</span></td>
        <td><span class="cellbar"><span class="bar"><span class="fill" style="width:{wp*10:.0f}%;background:{wp_color}"></span></span><span class="v" style="color:{wp_color}">{wp}</span></span></td>
        <td><span class="strength {t_cls}">{strength}<span class="scale">/10</span></span></td>
        {tx_html}
        <td class="verdict">{verdict[:160]}</td>
      </tr>""")
    html = f"""<!doctype html>
<html lang="{lang}"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hackathon Watch — NEXUS AiOS</title>
<style>
:root {{ --bg:#0b0d11; --panel:#10131a; --raised:#161a22; --line:#232833; --line2:#2d3340;
  --fg:#e9e4d6; --cyan:#00c8ff; --gold:#d4af37; --red:#d9605a; --amber:#e8c96a; --green:#8fb573;
  --red-dim:rgba(217,96,90,.14); --cyan-dim:rgba(0,200,255,.12); --gold-dim:rgba(212,175,55,.10);
  --mono:ui-monospace,SFMono-Regular,Menlo,monospace; }}
* {{ box-sizing:border-box; }}
body {{ background:var(--bg); color:var(--fg); font:13.5px/1.5 ui-sans-serif,system-ui,"Segoe UI",Roboto,sans-serif; margin:0; padding:24px 30px 40px; }}
h1 {{ font-size:27.5px; margin:0 0 2px; }} h1 .our {{ color:var(--gold); }}
.sub {{ color:var(--muted, #7d8494); font-size:15.5px; margin-bottom:20px; }}
.cards {{ display:grid; grid-template-columns:repeat(5,1fr); gap:10px; margin-bottom:16px; }}
.card {{ background:var(--panel); border:1px solid var(--line); border-radius:9px; padding:12px 14px; }}
.card .n {{ font-size:31px; font-weight:700; font-family:var(--mono); }} .card .l {{ color:var(--muted, #7d8494); font-size:13.5px; }}
table {{ width:100%; border-collapse:collapse; background:var(--panel); border:1px solid var(--line); border-radius:10px; overflow:hidden; }}
th,td {{ text-align:left; padding:9px 12px; border-bottom:1px solid var(--line); }}
th {{ color:var(--muted, #7d8494); font-size:13.5px; text-transform:uppercase; letter-spacing:.07em; background:#0e131c; }}
tr.our td {{ background:linear-gradient(90deg, var(--gold-dim), transparent 38%); }}
tr.our {{ box-shadow:inset 2px 0 0 var(--gold); }}
tr.our .proj {{ color:var(--gold); }}
.badge-our {{ display:inline-block; border:1px solid var(--gold); color:var(--gold); font-size:12.5px; font-weight:700; letter-spacing:.08em; padding:0 5px; border-radius:4px; margin-left:6px; }}
.src-chip {{ display:inline-block; font-size:12.5px; font-weight:700; letter-spacing:.05em; padding:1px 6px; border-radius:4px; margin-left:6px; }}
.src-chip.llm {{ color:var(--cyan); border:1px solid rgba(0,200,255,.45); background:var(--cyan-dim); }}
.src-chip.heuristic {{ color:var(--muted, #7d8494); border:1px dashed var(--line2); opacity:.75; }}
.ev-chip {{ display:inline-block; font-size:12.5px; color:var(--muted, #7d8494); border:1px solid var(--line); border-radius:4px; padding:1px 5px; margin-left:4px; }}.
td.place {{ color:var(--muted, #7d8494); width:42px; font-family:var(--mono); text-align:right; }}
td.proj {{ min-width:180px; font-weight:600; }}
td.tracks {{ color:var(--cyan); font-size:15.5px; }}
.demoico {{ display:inline-flex; align-items:center; gap:5px; font-size:15px; color:var(--muted, #7d8494); }}
.demoico .sym {{ width:16px; height:16px; border-radius:50%; display:inline-flex; align-items:center; justify-content:center; font-size:11.5px; font-weight:800; }}
.demoico.ok .sym {{ background:rgba(143,181,115,.16); color:var(--green); }}
.demoico.x .sym {{ background:var(--red-dim); color:var(--red); }}
.demoico.warn .sym {{ background:rgba(232,201,106,.14); color:var(--amber); }}
.bar {{ display:inline-block; position:relative; width:64px; height:5px; border-radius:3px; background:var(--raised); overflow:hidden; vertical-align:middle; }}
.bar .fill {{ position:absolute; left:0; top:0; bottom:0; border-radius:3px; }}
.cellbar {{ display:flex; align-items:center; gap:7px; }}
.cellbar .v {{ font-family:var(--mono); font-size:16px; font-weight:700; min-width:26px; text-align:right; }}
.strength {{ display:inline-flex; align-items:center; gap:6px; font-family:var(--mono); font-size:15.5px; font-weight:700; padding:1px 7px; border-radius:5px; background:var(--raised); border:1px solid var(--line); }}
.strength .scale {{ color:var(--muted, #7d8494); font-weight:400; font-size:13px; }}
.strength.high {{ color:var(--cyan); border-color:rgba(0,200,255,.4); }}
.strength.med {{ color:var(--amber); border-color:rgba(232,201,106,.35); }}
.strength.low {{ color:var(--muted, #7d8494); }}
td.tx {{ width:110px; }}
.txchip {{ display:inline-flex; align-items:center; gap:5px; font-family:var(--mono); font-size:14.5px; color:var(--cyan); background:var(--cyan-dim); border:1px solid rgba(0,200,255,.3); border-radius:5px; padding:1px 7px; text-decoration:none; }}
.tx-empty {{ color:var(--muted, #7d8494); }}
td.verdict {{ color:var(--muted, #7d8494); font-size:16px; }}
</style></head><body>
<h1>⚔ Hackathon Watch <span class="our">· NEXUS AiOS</span> <span style="color:var(--muted, #7d8494);font-size:13px">[preview · lang={lang}]</span></h1>
<div class="sub">schema_v{SCHEMA_VERSION} · generated {contract['generated_at']} · {contract['source']['total_projects']} projects · shared scores (en=ru) · numeric_mismatch={total.get('numeric_mismatch',0)}</div>
<div class="cards">
  <div class="card"><div class="n">{contract['source']['total_projects']}</div><div class="l">Tracked</div></div>
  <div class="card"><div class="n">{total['live_demos']}</div><div class="l">Live demos</div></div>
  <div class="card"><div class="n">{total['high_strength']}</div><div class="l">High strength</div></div>
  <div class="card"><div class="n">{total.get('numeric_mismatch',0)}</div><div class="l">Numeric mismatch (en vs ru)</div></div>
  <div class="card"><div class="n">{(contract['our_project'].get('rank') or 0)}</div><div class="l">NEXUS rank</div></div>
</div>
<table>
<thead><tr><th>Place</th><th>Project</th><th>Tracks</th><th>Demo</th><th>WinProb</th><th>Strength</th><th>TxHash</th><th>Note ({lang})</th></tr></thead>
<tbody>{''.join(rows_html)}
</tbody>
</table>
<p style="margin-top:16px;color:var(--muted, #7d8494);font-size:11px">
  WinProb = 0.40·maturity + 0.35·demo + 0.25·track breadth. Strength 0–10. H — heuristic estimate. ★OUR = NEXUS AiOS.
  Re-run: cd D:/Temp/hackwatch &amp;&amp; python -m hackwatch
</p>
</body></html>
"""
    out.write_text(html, encoding="utf-8")
    log(f"HTML preview ({lang}) written to {out} ({out.stat().st_size} bytes)")


# ---------- 8. Export for UI language switcher ----------

def export_lang(contract: dict, lang: str) -> Path:
    """hackwatch.<lang>.json: ranking[].analysis is flat single-lang with shared scores.
    UI switcher: analysis.scores.* (shared) + analysis.<lang>.* (text).
    """
    out = DATA_DIR / f"hackwatch.{lang}.json"
    out_contract = json.loads(json.dumps(contract))
    for r in out_contract["ranking"]:
        an = r["analysis"]
        flat = {"scores": an.get("scores", {})}
        flat.update(an.get(lang) or an.get("en") or {})
        flat["lang"] = lang
        flat["evaluator"] = an.get("evaluator", "heuristic")
        flat["source"] = an.get("source", "heuristic")
        r["analysis"] = flat
    out_contract["exported_lang"] = lang
    out.write_text(json.dumps(out_contract, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"export ({lang}) -> {out}")
    return out


# ---------- main ----------

def main() -> int:
    t0 = time.time()
    lang = "en"
    reparse = False
    export_langs: list[str] = []
    publish = False
    publish_dry = False
    top_n = 20
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--lang" and i + 1 < len(args):
            v = args[i + 1].lower()
            lang = v if v in ("en", "ru") else "en"
            i += 2
        elif a == "--reparse":
            reparse = True
            i += 1
        elif a == "--export-lang" and i + 1 < len(args):
            v = args[i + 1].lower()
            export_langs = ["en", "ru"] if v == "all" else [v]
            i += 2
        elif a == "--publish-scores":
            publish = True
            i += 1
        elif a == "--publish-dry":
            publish = True
            publish_dry = True
            i += 1
        elif a == "--top-n" and i + 1 < len(args):
            try:
                top_n = max(1, int(args[i + 1]))
            except ValueError:
                log(f"WARN: bad --top-n {args[i+1]!r}, default 20")
            i += 2
        else:
            i += 1

    log(f"Hackathon Watch core starting (reparse={reparse}, export={export_langs or 'none'}, "
        f"publish={'dry' if publish_dry else ('on' if publish else 'off')}, top_n={top_n})")
    load_key()

    # 1. catalog
    projects, total_reported = fetch_catalog()
    if not projects:
        log("FATAL: empty catalog — aborting (state untouched)")
        return 1

    # 2. objective enrichment
    log(f"enriching {len(projects)} demo URLs (parallel, 8 workers)…")
    enrich_batch(projects)

    # 3. incremental: LLM (dual) only for NEW ids, or full reparse
    state = load_state()
    seen = state["seen"]
    # consider a project "done" only if it has a DUAL record (shared scores)
    new_ids = [p["id"] for p in projects if not is_dual(seen.get(p["id"], {}))]
    if reparse:
        log(f"--reparse: re-analyzing ALL {len(projects)} projects (wipe state)")
        seen.clear()
        new_ids = [p["id"] for p in projects]
    log(f"state: {sum(1 for v in seen.values() if is_dual(v))} dual-records, {len(new_ids)} to analyze")

    llm_sources: list[str] = []
    if new_ids:
        new_projects = [p for p in projects if p["id"] in set(new_ids)]
        log(f"analyzing {len(new_projects)} via NexusLink slot={SLOT} (single bilingual call each)")
        new_scores = llm_analyze(new_projects)
        for pid, rec in new_scores.items():
            seen[pid] = rec
            llm_sources.append(rec.get("source", "heuristic"))
        save_state(state)
    else:
        log("no new projects — using cached dual-lang analysis")

    # 4. rows: every project gets a dual record (heuristic if missing)
    rows = []
    for p in projects:
        rec = seen.get(p["id"])
        if not is_dual(rec):
            rec = heuristic_fallback([p])[p["id"]]
        rows.append({"project": p, "analysis": rec})
    sort_rows(rows)

    # 5. contract + html + exports
    lang_map = {}
    dual_count = sum(1 for r in rows if str(normalize_scores(r["analysis"]).get("source", "x")).startswith("nexus/"))
    llm_filled = sum(1 for r in rows if str(r["analysis"].get("source", "x")).startswith("nexus/"))
    lang_map = {
        "en": f"{llm_filled}/{len(rows)} LLM-filled (shared scores)",
        "ru": f"{llm_filled}/{len(rows)} LLM-filled (shared scores)",
    }
    contract = build_contract(rows, total_reported, len(new_ids), llm_sources, lang_map)
    JSON_OUT.write_text(json.dumps(contract, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"JSON contract written to {JSON_OUT}")
    render_html(contract, HTML_OUT, lang=lang)
    for el in export_langs:
        export_lang(contract, el)

    # 6. summary
    up = contract["our_project"]
    log(f"done in {round(time.time()-t0,1)}s · {len(rows)} rows · numeric_mismatch={contract['stats'].get('numeric_mismatch',0)} · top-5:")
    for r in contract["ranking"][:5]:
        sc = r["analysis"]["scores"]
        mark = " ★OUR" if r["is_ours"] else ""
        log(f"  {r['rank']:>2}. {r['project_name']:<30} wp={r['score']['win_prob']:>5} "
            f"mat={sc.get('maturity'):>2} threat={sc.get('threat'):>2} demo={sc.get('demo_assessment')}{mark}")
    log(f"OUR NEXUS AiOS: rank #{up['rank']}/{len(rows)}, win_prob={up['win_prob']}")

    # 7. on-chain publish (--publish-scores): топ-N → set_score; TxHash → analysis.tx
    if publish:
        import publish_scores
        rc = publish_scores.run_publish(contract, dry=publish_dry, top_n=top_n)
        if rc != 0:
            return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
