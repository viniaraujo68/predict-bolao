"""Parsers para JSONs interceptados da bet365.

A estrutura exata dos JSONs internos da bet365 não é documentada e pode mudar.
Este módulo expõe funções heurísticas que tentam reconhecer os 3 mercados
(`h2h`, `totals`, `correct_score`) a partir de objetos arbitrários.

Fluxo recomendado: rodar `python main.py rodada --debug-network` na primeira vez,
inspecionar os JSONs salvos em `output/debug/`, e ajustar `MARKET_NAME_PATTERNS`
e os field-paths em `_iter_outcomes` se necessário.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from core.schemas import Market, Outcome


MARKET_NAME_PATTERNS = {
    "h2h": re.compile(r"(?i)\b(match\s*odds|full\s*time\s*result|1x2|resultado(\s+final)?|vencedor)\b"),
    "totals": re.compile(r"(?i)\b(over[/\s]+under|goals?\s*over[/\s]+under|total\s+de\s+gols|mais[/\s]+menos)\b"),
    "correct_score": re.compile(r"(?i)\b(correct\s*score|placar\s*exato|placar\s*correto)\b"),
}

ODD_FIELDS = ("odd", "odds", "price", "decimalOdds", "decimal_odds", "od")
NAME_FIELDS = ("name", "label", "outcome", "title", "selection", "nm")
OUTCOMES_FIELDS = ("outcomes", "selections", "runners", "options", "items")

CORRECT_SCORE_RE = re.compile(r"^\s*(\d+)\s*[-–x×:]\s*(\d+)\s*$")
TOTALS_LINE_RE = re.compile(r"(over|under|mais|menos)\s*(\d+(?:[.,]\d+)?)", re.IGNORECASE)


def _to_float(v: Any) -> float | None:
    try:
        f = float(str(v).replace(",", "."))
        return f if f > 1.0 else None
    except (TypeError, ValueError):
        return None


def _first_present(obj: dict, keys: Iterable[str]) -> Any:
    for k in keys:
        if k in obj and obj[k] is not None:
            return obj[k]
    return None


def _iter_outcomes(raw_market: dict) -> list[Outcome]:
    raw_outcomes = _first_present(raw_market, OUTCOMES_FIELDS)
    if not isinstance(raw_outcomes, list):
        return []
    outcomes: list[Outcome] = []
    for raw in raw_outcomes:
        if not isinstance(raw, dict):
            continue
        name = _first_present(raw, NAME_FIELDS)
        odd = _to_float(_first_present(raw, ODD_FIELDS))
        if name is None or odd is None:
            continue
        outcomes.append(Outcome(name=str(name).strip(), odd=odd))
    return outcomes


def classify_market(raw_market: dict) -> str | None:
    """Identifica o tipo de mercado (`h2h`, `totals`, `correct_score`) pelo nome."""
    name = _first_present(raw_market, NAME_FIELDS) or ""
    for kind, pattern in MARKET_NAME_PATTERNS.items():
        if pattern.search(str(name)):
            return kind
    return None


def parse_market(raw_market: dict) -> Market | None:
    kind = classify_market(raw_market)
    if kind is None:
        return None
    outcomes = _iter_outcomes(raw_market)
    if not outcomes:
        return None
    if kind == "totals":
        outcomes = [o for o in outcomes if _is_2_5_line(o.name)]
        if len(outcomes) < 2:
            return None
    if kind == "correct_score":
        outcomes = [_normalize_score_outcome(o) for o in outcomes]
        outcomes = [o for o in outcomes if o is not None]
        if not outcomes:
            return None
    return Market(kind=kind, outcomes=outcomes)


def _is_2_5_line(name: str) -> bool:
    m = TOTALS_LINE_RE.search(name)
    if not m:
        return False
    return float(m.group(2).replace(",", ".")) == 2.5


def _normalize_score_outcome(o: Outcome) -> Outcome | None:
    m = CORRECT_SCORE_RE.match(o.name)
    if not m:
        return None
    return Outcome(name=f"{m.group(1)}-{m.group(2)}", odd=o.odd)


def walk_markets(payload: Any) -> Iterable[dict]:
    """Caminha recursivamente pelo payload procurando objetos que pareçam mercados.

    Heurística: dict com algum campo de nome e algum campo de outcomes.
    """
    if isinstance(payload, dict):
        if any(k in payload for k in NAME_FIELDS) and any(k in payload for k in OUTCOMES_FIELDS):
            yield payload
        for v in payload.values():
            yield from walk_markets(v)
    elif isinstance(payload, list):
        for item in payload:
            yield from walk_markets(item)


def extract_markets_from_payload(payload: Any) -> dict[str, Market]:
    """Varre um JSON inteiro e retorna {kind: Market} para mercados reconhecidos."""
    found: dict[str, Market] = {}
    for raw_market in walk_markets(payload):
        market = parse_market(raw_market)
        if market and market.kind not in found:
            found[market.kind] = market
    return found
