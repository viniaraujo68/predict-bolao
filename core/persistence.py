"""Persiste odds capturadas em dois niveis:

- capturas/<ts>.json : snapshots imutaveis, um por execucao de captura.
- odds_atuais.json    : store com as odds mais recentes POR PARTIDA.

Cada partida carrega seu proprio `captured_at`, entao odds de idades diferentes
no mesmo store ficam rastreaveis (e o relatorio pode avisar das mais velhas).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from config import OUTPUT_DIR
from core.schemas import RawMatch

CAPTURAS_DIR = OUTPUT_DIR / "capturas"
RELATORIOS_DIR = OUTPUT_DIR / "relatorios"
STORE_PATH = OUTPUT_DIR / "odds_atuais.json"
# Placares reais inputados manualmente, separados das odds: uma nova captura
# sobrescreve o store de odds, mas nunca apaga um resultado ja registrado.
RESULTS_PATH = OUTPUT_DIR / "resultados.json"

TS_FORMAT = "%Y-%m-%d_%Hh%M"


def format_ts(ts: datetime | None = None) -> str:
    """Timestamp pra nomes de arquivo: YYYY-MM-DD_HHhMM."""
    return (ts or datetime.now()).strftime(TS_FORMAT)


def _unique_path(path: Path) -> Path:
    """Se `path` ja existe, acrescenta sufixo _2, _3, ... pra nao sobrescrever."""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    n = 2
    while True:
        candidate = path.with_name(f"{stem}_{n}{suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def save_snapshot(matches: list[RawMatch], ts: datetime | None = None) -> Path:
    """Grava um snapshot imutavel em capturas/<ts>.json.

    Formato {saved_at, matches}. Nunca sobrescreve: se o arquivo ja existir,
    acrescenta sufixo numerico.
    """
    CAPTURAS_DIR.mkdir(parents=True, exist_ok=True)
    ts = ts or datetime.now()
    path = _unique_path(CAPTURAS_DIR / f"{format_ts(ts)}.json")
    payload = {
        "saved_at": ts.isoformat(),
        "matches": [m.model_dump(mode="json") for m in matches],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    return path


def update_store(matches: list[RawMatch]) -> tuple[Path, int, int]:
    """Carrega o store, insere/substitui por match_id (captura nova SEMPRE ganha)
    e regrava. Cada partida preserva seu proprio `captured_at`.

    Retorna (path, novas, atualizadas).
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    existing: dict[str, RawMatch] = {m.match_id: m for m in load_store()}

    novas = atualizadas = 0
    for m in matches:
        if m.match_id in existing:
            atualizadas += 1
        else:
            novas += 1
        existing[m.match_id] = m

    payload = {
        "saved_at": datetime.now().isoformat(),
        "matches": [m.model_dump(mode="json") for m in existing.values()],
    }
    STORE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    return STORE_PATH, novas, atualizadas


def load_store() -> list[RawMatch]:
    """Carrega as partidas do store (odds_atuais.json). Vazio se nao existir."""
    if not STORE_PATH.exists():
        return []
    try:
        payload = json.loads(STORE_PATH.read_text())
    except Exception:
        return []
    return [RawMatch.model_validate(m) for m in payload.get("matches", [])]


def load_results() -> dict[str, tuple[int, int]]:
    """Carrega os placares reais inputados (resultados.json). Vazio se nao existir.

    Retorna {match_id: (gols_casa, gols_fora)}.
    """
    if not RESULTS_PATH.exists():
        return {}
    try:
        payload = json.loads(RESULTS_PATH.read_text())
    except Exception:
        return {}
    out: dict[str, tuple[int, int]] = {}
    for mid, v in payload.get("results", {}).items():
        try:
            out[mid] = (int(v["home"]), int(v["away"]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def save_results(results: dict[str, tuple[int, int]]) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "saved_at": datetime.now().isoformat(),
        "results": {mid: {"home": h, "away": a} for mid, (h, a) in results.items()},
    }
    RESULTS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    return RESULTS_PATH


def set_result(match_id: str, home: int, away: int) -> Path:
    """Registra/atualiza o placar real de uma partida."""
    results = load_results()
    results[match_id] = (home, away)
    return save_results(results)


def load_snapshot(path: Path | str) -> tuple[list[RawMatch], datetime | None]:
    """Carrega um snapshot/raw antigo especifico.

    Compat com os raws antigos (raw_*.json): se os matches nao tiverem
    `captured_at`, usa o `saved_at` do arquivo como captured_at.

    Retorna (matches, saved_at).
    """
    path = Path(path)
    payload = json.loads(path.read_text())
    saved_at: datetime | None = None
    try:
        saved_at = datetime.fromisoformat(payload.get("saved_at", ""))
    except (ValueError, TypeError):
        pass
    matches = []
    for raw in payload.get("matches", []):
        m = RawMatch.model_validate(raw)
        if m.captured_at is None:
            m.captured_at = saved_at
        matches.append(m)
    return matches, saved_at
