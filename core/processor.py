"""Pipeline Polars: une RawMatches + calculos matematicos no DataFrame da tabela."""

from __future__ import annotations

import polars as pl

from core.math_engine import consolidate
from core.schemas import Outcome, RawMatch, RichMatch


SCHEMA = {
    "match_id": pl.String,
    "match_date": pl.Datetime,
    "home_team": pl.String,
    "away_team": pl.String,
    "odd_home_win": pl.Float64,
    "odd_draw": pl.Float64,
    "odd_away_win": pl.Float64,
    "odds_captured_at": pl.Datetime,
    "predicted_score": pl.String,
    "prob_score": pl.Float64,
    "p_home": pl.Float64,
    "p_draw": pl.Float64,
    "p_away": pl.Float64,
    "lambda_home": pl.Float64,
    "lambda_away": pl.Float64,
}


def _lookup_h2h(outcomes: list[Outcome]) -> tuple[float, float, float]:
    """Retorna (home, draw, away) odds buscando por palavras-chave nos nomes."""
    h = d = a = 0.0
    for o in outcomes:
        n = o.name.lower()
        # "x" sozinho e simbolo de empate, mas casa por substring com qualquer
        # nome contendo "x" (ex: Mexico) — por isso so vale por igualdade exata.
        is_draw = any(k in n for k in ("draw", "empate", "tie")) or n.strip() == "x"
        if any(k in n for k in ("home", "casa", "mandante", "1 ")) or n.endswith(" 1"):
            h = h or o.odd
        elif is_draw:
            d = d or o.odd
        elif any(k in n for k in ("away", "fora", "visitante", "2 ")) or n.endswith(" 2"):
            a = a or o.odd
    if (h and d and a):
        return h, d, a
    if len(outcomes) == 3 and not (h or d or a):
        return outcomes[0].odd, outcomes[1].odd, outcomes[2].odd
    return h, d, a


def _lookup_totals(outcomes: list[Outcome]) -> tuple[float, float]:
    """Retorna (over_2_5, under_2_5)."""
    over = under = 0.0
    for o in outcomes:
        n = o.name.lower()
        if "over" in n or "mais" in n:
            over = over or o.odd
        elif "under" in n or "menos" in n:
            under = under or o.odd
    return over, under


def enrich(matches: list[RawMatch]) -> list[RichMatch]:
    rich = []
    for m in matches:
        h2h = m.market("h2h")
        totals = m.market("totals")
        odd_h, odd_d, odd_a = _lookup_h2h(h2h.outcomes) if h2h else (0.0, 0.0, 0.0)
        odd_over, odd_under = _lookup_totals(totals.outcomes) if totals else (0.0, 0.0)

        pred = consolidate(odd_h, odd_d, odd_a, odd_over, odd_under)
        rich.append(RichMatch(raw=m, prediction=pred))
    return rich


def to_dataframe(rich: list[RichMatch]) -> pl.DataFrame:
    rows = []
    for r in rich:
        h2h = r.raw.market("h2h")
        odd_h, odd_d, odd_a = _lookup_h2h(h2h.outcomes) if h2h else (None, None, None)
        p = r.prediction
        rows.append({
            "match_id": r.raw.match_id,
            "match_date": r.raw.match_date,
            "home_team": r.raw.home_team,
            "away_team": r.raw.away_team,
            "odd_home_win": odd_h or None,
            "odd_draw": odd_d or None,
            "odd_away_win": odd_a or None,
            "odds_captured_at": r.raw.captured_at,
            "predicted_score": p.score,
            "prob_score": p.prob_score,
            "p_home": p.p_home,
            "p_draw": p.p_draw,
            "p_away": p.p_away,
            "lambda_home": p.lambda_home,
            "lambda_away": p.lambda_away,
        })
    if not rows:
        return pl.DataFrame(schema=SCHEMA)
    return pl.DataFrame(rows, schema=SCHEMA)
