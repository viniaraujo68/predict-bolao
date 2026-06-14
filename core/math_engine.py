"""Motor consolidado de previsão de placar exato a partir de odds.

Pipeline:
  1. Probabilidades justas de 1X2 e under 2.5 (overround removido).
  2. Ajuste numérico de (λ_H, λ_A) para que a matriz Dixon-Coles reproduza
     simultaneamente o 1X2 e o under 2.5 do mercado (em vez de partir
     λ_total por uma heurística de share).
  3. Palpite final = placar que maximiza os pontos esperados do bolão
     (placar exato / saldo / vencedor), não apenas o placar mais provável.
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import brentq, minimize
from scipy.stats import poisson

from config import (
    BOLAO_BASE_OUTCOME,
    BOLAO_BLOWOUT_GOALS,
    BOLAO_BONUS_BLOWOUT,
    BOLAO_BONUS_DIFF,
    BOLAO_BONUS_EXACT,
    BOLAO_BONUS_LOSER_SCORE,
    BOLAO_BONUS_WINNER_SCORE,
    DIXON_COLES_RHO,
    MAX_GOALS,
    POISSON_LAMBDA_BOUNDS,
)
from core.schemas import ConsolidatedPrediction


def _safe_inv(odd: float) -> float:
    """Probabilidade implícita bruta. Retorna 0.0 se odd inválida."""
    if odd is None or odd <= 1.0:
        return 0.0
    return 1.0 / odd


def _fair_h2h(odd_h: float, odd_d: float, odd_a: float) -> tuple[float, float, float]:
    p_h, p_d, p_a = _safe_inv(odd_h), _safe_inv(odd_d), _safe_inv(odd_a)
    total = p_h + p_d + p_a
    if total <= 0 or not (p_h and p_d and p_a):
        raise ValueError("odds h2h inválidas")
    return p_h / total, p_d / total, p_a / total


def _fair_under(odd_over: float, odd_under: float) -> float:
    p_o, p_u = _safe_inv(odd_over), _safe_inv(odd_under)
    total = p_o + p_u
    if total <= 0 or not (p_o and p_u):
        raise ValueError("odds totals inválidas")
    return p_u / total


def _solve_lambda_total(p_under_2_5: float) -> float:
    """Encontra λ tal que P(Gols ≤ 2 | Poisson(λ)) == p_under_2_5."""
    p_clamped = min(max(p_under_2_5, 1e-6), 1 - 1e-6)
    f = lambda lam: poisson.cdf(2, lam) - p_clamped
    lo, hi = POISSON_LAMBDA_BOUNDS
    if f(lo) * f(hi) > 0:
        return lo if p_clamped > 0.9 else hi
    return brentq(f, lo, hi, xtol=1e-4)


def _poisson_matrix(lam_h: float, lam_a: float) -> np.ndarray:
    """Matriz (MAX_GOALS+1) × (MAX_GOALS+1) com P(home=i, away=j)."""
    grid = np.arange(MAX_GOALS + 1)
    return np.outer(poisson.pmf(grid, lam_h), poisson.pmf(grid, lam_a))


def _dixon_coles_tau(lam_h: float, lam_a: float, rho: float) -> np.ndarray:
    """Matriz de ajuste DC: 1 em todas as celulas exceto o canto 2x2."""
    tau = np.ones((MAX_GOALS + 1, MAX_GOALS + 1))
    if MAX_GOALS >= 1:
        tau[0, 0] = 1 - lam_h * lam_a * rho
        tau[0, 1] = 1 + lam_h * rho
        tau[1, 0] = 1 + lam_a * rho
        tau[1, 1] = 1 - rho
    # Garante que tau nao gere prob negativa em casos extremos
    tau = np.clip(tau, 0.0, None)
    return tau


def dixon_coles_matrix(
    lam_h: float, lam_a: float, rho: float = DIXON_COLES_RHO,
) -> np.ndarray:
    """Matriz Poisson com ajuste Dixon-Coles, normalizada pra somar 1."""
    pois = _poisson_matrix(lam_h, lam_a)
    tau = _dixon_coles_tau(lam_h, lam_a, rho)
    adj = pois * tau
    total = adj.sum()
    if total <= 0:
        return pois
    return adj / total


def matrix_1x2(matrix: np.ndarray) -> tuple[float, float, float]:
    """Da matriz P(i, j), retorna (p_home_win, p_draw, p_away_win)."""
    n = matrix.shape[0]
    diff = np.subtract.outer(np.arange(n), np.arange(n))
    p_h = float(matrix[diff > 0].sum())
    p_d = float(matrix[diff == 0].sum())
    p_a = float(matrix[diff < 0].sum())
    total = p_h + p_d + p_a
    return p_h / total, p_d / total, p_a / total


def matrix_under_2_5(matrix: np.ndarray) -> float:
    """P(total de gols ≤ 2)."""
    n = matrix.shape[0]
    total_goals = np.add.outer(np.arange(n), np.arange(n))
    return float(matrix[total_goals <= 2].sum() / matrix.sum())


def estimate_lambdas(
    odd_home: float,
    odd_draw: float,
    odd_away: float,
    odd_over_2_5: float,
    odd_under_2_5: float,
) -> tuple[float, float] | None:
    """Heurística de partida: λ_total via under 2.5, share = p_H + p_D/2.

    Usada como chute inicial do ajuste fino (e como baseline no backtest).
    """
    try:
        p_h, p_d, p_a = _fair_h2h(odd_home, odd_draw, odd_away)
        p_under = _fair_under(odd_over_2_5, odd_under_2_5)
    except ValueError:
        return None
    lam_total = _solve_lambda_total(p_under)
    share_h = p_h + p_d / 2
    return lam_total * share_h, lam_total * (1 - share_h)


def fit_lambdas(
    odd_home: float,
    odd_draw: float,
    odd_away: float,
    odd_over_2_5: float,
    odd_under_2_5: float,
    rho: float = DIXON_COLES_RHO,
) -> tuple[float, float, float] | None:
    """Ajusta (λ_H, λ_A, ρ) para a matriz DC reproduzir o 1X2 e o under 2.5.

    Quatro alvos (p_H, p_D, p_A, p_under) e tres graus de liberdade
    (log λ_H, log λ_A, ρ) — minimos quadrados via Nelder-Mead. Com o ρ livre
    o ajuste zera o erro nos quatro alvos. `rho` aqui e so o chute inicial.
    Retorna (λ_H, λ_A, ρ) ou None se odds invalidas.
    """
    try:
        p_h, p_d, p_a = _fair_h2h(odd_home, odd_draw, odd_away)
        p_under = _fair_under(odd_over_2_5, odd_under_2_5)
    except ValueError:
        return None

    heur = estimate_lambdas(odd_home, odd_draw, odd_away, odd_over_2_5, odd_under_2_5)
    lo, hi = POISSON_LAMBDA_BOUNDS
    x0 = np.array([*np.log(np.clip(heur, max(lo, 0.05), hi)), rho])

    def loss(x: np.ndarray) -> float:
        lam_h, lam_a = np.exp(x[0]), np.exp(x[1])
        m = dixon_coles_matrix(lam_h, lam_a, x[2])
        m_h, m_d, m_a = matrix_1x2(m)
        m_under = matrix_under_2_5(m)
        return (
            (m_h - p_h) ** 2
            + (m_d - p_d) ** 2
            + (m_a - p_a) ** 2
            + (m_under - p_under) ** 2
        )

    res = minimize(
        loss, x0, method="Nelder-Mead",
        options={"xatol": 1e-5, "fatol": 1e-12, "maxiter": 1000},
    )
    lam_h, lam_a = np.exp(res.x[0]), np.exp(res.x[1])
    rho_fit = float(res.x[2])
    if not (np.isfinite(lam_h) and np.isfinite(lam_a) and np.isfinite(rho_fit)):
        return (*heur, rho)
    lam_h = float(np.clip(lam_h, lo, hi))
    lam_a = float(np.clip(lam_a, lo, hi))
    rho_fit = float(np.clip(rho_fit, -0.9, 0.9))
    return lam_h, lam_a, rho_fit


def score_matrix(
    odd_home: float,
    odd_draw: float,
    odd_away: float,
    odd_over_2_5: float,
    odd_under_2_5: float,
) -> np.ndarray | None:
    """Matriz Poisson pura com a heurística antiga (baseline do backtest)."""
    lams = estimate_lambdas(odd_home, odd_draw, odd_away, odd_over_2_5, odd_under_2_5)
    if lams is None:
        return None
    return _poisson_matrix(*lams)


def bolao_points(pred_h: int, pred_a: int, real_h: int, real_a: int) -> float:
    """Pontos do bolão pra um palpite contra um resultado real.

    Errou o resultado (vencedor/empate) → 0, sem bônus. Acertou → base +
    no máximo um bônus de placar (exato / placar do vencedor / saldo /
    placar do perdedor — acertar dois deles implicaria exato) + bônus de
    goleada se o jogo real teve um time com BOLAO_BLOWOUT_GOALS+ gols.
    """
    pred_d, real_d = pred_h - pred_a, real_h - real_a
    if np.sign(pred_d) != np.sign(real_d):
        return 0.0
    pts = BOLAO_BASE_OUTCOME
    if (pred_h, pred_a) == (real_h, real_a):
        pts += BOLAO_BONUS_EXACT
    elif real_d == 0:
        # empate certo de placar errado: o saldo (0) bate sempre
        pts += BOLAO_BONUS_DIFF
    else:
        winner_pred, loser_pred = (pred_h, pred_a) if real_d > 0 else (pred_a, pred_h)
        winner_real, loser_real = (real_h, real_a) if real_d > 0 else (real_a, real_h)
        if winner_pred == winner_real:
            pts += BOLAO_BONUS_WINNER_SCORE
        elif pred_d == real_d:
            pts += BOLAO_BONUS_DIFF
        elif loser_pred == loser_real:
            pts += BOLAO_BONUS_LOSER_SCORE
    if max(real_h, real_a) >= BOLAO_BLOWOUT_GOALS:
        pts += BOLAO_BONUS_BLOWOUT
    return float(pts)


def expected_points_matrix(prob: np.ndarray) -> np.ndarray:
    """E[pontos do bolão] pra cada palpite (i, j) sob a distribuição `prob`."""
    n = prob.shape[0]
    A, B = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
    real_d = A - B
    winner_real = np.where(real_d > 0, A, B)
    loser_real = np.where(real_d > 0, B, A)
    blowout = np.maximum(A, B) >= BOLAO_BLOWOUT_GOALS

    ep = np.empty_like(prob)
    for i in range(n):
        for j in range(n):
            pred_d = i - j
            ok = np.sign(real_d) == np.sign(pred_d)
            exact = (A == i) & (B == j)
            winner_pred, loser_pred = (i, j) if pred_d > 0 else (j, i)
            bonus = np.where(
                exact, BOLAO_BONUS_EXACT,
                np.where(real_d == 0, BOLAO_BONUS_DIFF,
                np.where(winner_real == winner_pred, BOLAO_BONUS_WINNER_SCORE,
                np.where(real_d == pred_d, BOLAO_BONUS_DIFF,
                np.where(loser_real == loser_pred, BOLAO_BONUS_LOSER_SCORE, 0)))))
            pts = ok * (BOLAO_BASE_OUTCOME + bonus + BOLAO_BONUS_BLOWOUT * blowout)
            ep[i, j] = float((prob * pts).sum())
    return ep


def consolidate(
    odd_home: float | None,
    odd_draw: float | None,
    odd_away: float | None,
    odd_over_2_5: float | None,
    odd_under_2_5: float | None,
) -> ConsolidatedPrediction:
    """Pipeline completo: calibra Dixon-Coles e maximiza E[pontos do bolão]."""
    lams = fit_lambdas(odd_home, odd_draw, odd_away, odd_over_2_5, odd_under_2_5)
    if lams is None:
        return ConsolidatedPrediction()
    lam_h, lam_a, rho = lams
    matrix = dixon_coles_matrix(lam_h, lam_a, rho)

    ep = expected_points_matrix(matrix)
    i, j = np.unravel_index(np.argmax(ep), ep.shape)
    p_h, p_d, p_a = matrix_1x2(matrix)
    return ConsolidatedPrediction(
        score=f"{i}-{j}",
        prob_score=float(matrix[i, j]),
        expected_points=float(ep[i, j]),
        p_home=p_h,
        p_draw=p_d,
        p_away=p_a,
        lambda_home=lam_h,
        lambda_away=lam_a,
        rho=rho,
        matrix=matrix.tolist(),
    )


if __name__ == "__main__":
    # Smoke test: favorito claro (México x África do Sul, odds reais 09/06)
    pred = consolidate(odd_home=1.42, odd_draw=4.50, odd_away=7.50,
                       odd_over_2_5=2.20, odd_under_2_5=1.66)
    print(f"Palpite: {pred.score}  P(placar)={pred.prob_score:.4f}  "
          f"E[pontos]={pred.expected_points:.2f}")
    print(f"1X2: H={pred.p_home:.1%} D={pred.p_draw:.1%} A={pred.p_away:.1%}  "
          f"λ=({pred.lambda_home:.2f}, {pred.lambda_away:.2f}) ρ={pred.rho:+.3f}")
