"""Backtest do motor consolidado contra resultados reais.

Le um CSV no formato football-data.co.uk (PL, La Liga etc) e, para cada
partida, compara duas variantes do motor a partir das odds Bet365 pre-jogo:

  - antigo: Poisson puro com split heuristico (share = p_H + p_D/2),
    palpite = placar mais provavel (argmax);
  - novo:   Dixon-Coles com (lambda_H, lambda_A) ajustados pro 1X2 + under 2.5,
    palpite = placar que maximiza os pontos esperados do bolao.

Metricas:
  - pontos_bolao: media de pontos do bolao por partida (metrica que importa)
  - placar_exato_pct / vencedor_pct / ou25_pct
  - market_*_pct: linha de base usando a opiniao do mercado (menor odd)
  - log_loss_1x2 / brier_1x2: qualidade da distribuicao H/D/A (menor melhor)

Format esperado do CSV:
  Date, HomeTeam, AwayTeam, FTHG, FTAG, FTR (H/D/A),
  B365H, B365D, B365A, B365>2.5, B365<2.5
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import polars as pl

from core.math_engine import (
    bolao_points,
    dixon_coles_matrix,
    expected_points_matrix,
    fit_lambdas,
    matrix_1x2,
    matrix_under_2_5,
    score_matrix,
)


@dataclass
class ApproachMetrics:
    pontos_bolao: float
    placar_exato_pct: float
    vencedor_pct: float
    ou25_pct: float
    log_loss_1x2: float
    brier_1x2: float
    avg_pick_prob: float


@dataclass
class BacktestResult:
    n_matches: int
    antigo: ApproachMetrics
    novo: ApproachMetrics
    market_vencedor_pct: float
    market_ou25_pct: float
    sample_predictions: list[dict]


def _market_winner(odd_h: float, odd_d: float, odd_a: float) -> str:
    odds = {"H": odd_h, "D": odd_d, "A": odd_a}
    return min(odds, key=lambda k: odds[k])


def _evaluate(
    matrix: np.ndarray, pick: tuple[int, int], fthg: int, ftag: int, ftr: str,
) -> dict:
    """Avalia (matriz, palpite) contra o resultado real."""
    i, j = pick
    p_h, p_d, p_a = matrix_1x2(matrix)
    pred_winner = "H" if i > j else ("A" if i < j else "D")
    p_over = 1 - matrix_under_2_5(matrix)
    pred_ou = "O" if p_over > 0.5 else "U"
    actual_ou = "O" if (fthg + ftag) > 2 else "U"
    p_real = max(
        p_h if ftr == "H" else (p_d if ftr == "D" else p_a), 1e-10
    )
    target = (
        1 if ftr == "H" else 0,
        1 if ftr == "D" else 0,
        1 if ftr == "A" else 0,
    )
    return {
        "i": i, "j": j,
        "pontos": bolao_points(i, j, fthg, ftag),
        "is_placar": i == fthg and j == ftag,
        "is_winner": pred_winner == ftr,
        "is_ou": pred_ou == actual_ou,
        "actual_ou": actual_ou,
        "log_loss": -math.log(p_real),
        "brier": (p_h - target[0])**2 + (p_d - target[1])**2 + (p_a - target[2])**2,
        "pick_prob": float(matrix[i, j]),
    }


def run_backtest(csv_path: str | Path, sample_size: int = 8) -> BacktestResult:
    df = pl.read_csv(
        str(csv_path),
        ignore_errors=True,
        infer_schema_length=2000,
    )

    required = ["HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR",
                "B365H", "B365D", "B365A", "B365>2.5", "B365<2.5"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Colunas faltando no CSV: {missing}")

    df = df.filter(
        pl.col("B365H").is_not_null() &
        pl.col("B365D").is_not_null() &
        pl.col("B365A").is_not_null() &
        pl.col("B365>2.5").is_not_null() &
        pl.col("B365<2.5").is_not_null() &
        pl.col("FTHG").is_not_null() &
        pl.col("FTAG").is_not_null()
    )

    n = 0
    n_market_vencedor = 0
    n_market_ou25 = 0
    acc = {
        "antigo": [0.0, 0, 0, 0, 0.0, 0.0, 0.0],
        "novo": [0.0, 0, 0, 0, 0.0, 0.0, 0.0],
    }
    # estrutura: [pontos, placar, vencedor, ou, log_loss, brier, pick_prob]
    sample_predictions: list[dict] = []

    for row in df.iter_rows(named=True):
        try:
            odd_h = float(row["B365H"]); odd_d = float(row["B365D"]); odd_a = float(row["B365A"])
            odd_over = float(row["B365>2.5"]); odd_under = float(row["B365<2.5"])
            fthg = int(row["FTHG"]); ftag = int(row["FTAG"])
            ftr = row["FTR"]
        except (TypeError, ValueError):
            continue

        m_antigo = score_matrix(odd_h, odd_d, odd_a, odd_over, odd_under)
        lams_novo = fit_lambdas(odd_h, odd_d, odd_a, odd_over, odd_under)
        if m_antigo is None or lams_novo is None:
            continue
        m_novo = dixon_coles_matrix(*lams_novo)

        pick_antigo = np.unravel_index(np.argmax(m_antigo), m_antigo.shape)
        ep = expected_points_matrix(m_novo)
        pick_novo = np.unravel_index(np.argmax(ep), ep.shape)

        res_antigo = _evaluate(m_antigo, tuple(map(int, pick_antigo)), fthg, ftag, ftr)
        res_novo = _evaluate(m_novo, tuple(map(int, pick_novo)), fthg, ftag, ftr)

        n += 1
        for key, res in (("antigo", res_antigo), ("novo", res_novo)):
            acc[key][0] += res["pontos"]
            acc[key][1] += int(res["is_placar"])
            acc[key][2] += int(res["is_winner"])
            acc[key][3] += int(res["is_ou"])
            acc[key][4] += res["log_loss"]
            acc[key][5] += res["brier"]
            acc[key][6] += res["pick_prob"]

        mkt_winner = _market_winner(odd_h, odd_d, odd_a)
        mkt_ou = "O" if odd_over < odd_under else "U"
        if mkt_winner == ftr:
            n_market_vencedor += 1
        if mkt_ou == res_novo["actual_ou"]:
            n_market_ou25 += 1

        if len(sample_predictions) < sample_size:
            sample_predictions.append({
                "home": row["HomeTeam"],
                "away": row["AwayTeam"],
                "pred_antigo": f"{res_antigo['i']}-{res_antigo['j']}",
                "pred_novo": f"{res_novo['i']}-{res_novo['j']}",
                "actual_score": f"{fthg}-{ftag}",
                "actual_winner": ftr,
            })

    if n == 0:
        raise ValueError("Nenhuma partida valida no CSV.")

    def metrics(key):
        a = acc[key]
        return ApproachMetrics(
            pontos_bolao=a[0] / n,
            placar_exato_pct=a[1] / n,
            vencedor_pct=a[2] / n,
            ou25_pct=a[3] / n,
            log_loss_1x2=a[4] / n,
            brier_1x2=a[5] / n,
            avg_pick_prob=a[6] / n,
        )

    return BacktestResult(
        n_matches=n,
        antigo=metrics("antigo"),
        novo=metrics("novo"),
        market_vencedor_pct=n_market_vencedor / n,
        market_ou25_pct=n_market_ou25 / n,
        sample_predictions=sample_predictions,
    )
