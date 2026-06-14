"""Testes da pontuação do bolão, com foco na regra de goleada: o bônus +1 só
vale se o PALPITE e o jogo real tiverem um time com 4+ gols (você previu a
goleada e ela aconteceu). Também checa a consistência entre o cálculo escalar
e a matriz vetorizada de pontos esperados (que escolhe o palpite)."""

from __future__ import annotations

import numpy as np

from config import (
    BOLAO_BASE_OUTCOME,
    BOLAO_BONUS_BLOWOUT,
    BOLAO_BONUS_EXACT,
    BOLAO_BONUS_WINNER_SCORE,
)
from core.math_engine import bolao_points, expected_points_matrix


def test_goleada_exige_prever_goleada():
    # Caso reportado: palpite 2-1 nao e goleada -> sem +1, mesmo o real sendo 4-1.
    assert bolao_points(2, 1, 4, 1) == BOLAO_BASE_OUTCOME + 1  # base + placar perdedor
    # Palpite goleada (4-0) e real goleada (4-1): ganha o +1.
    assert bolao_points(4, 0, 4, 1) == BOLAO_BASE_OUTCOME + BOLAO_BONUS_WINNER_SCORE + BOLAO_BONUS_BLOWOUT
    # Previu goleada (4-1) mas o real (2-1) nao foi goleada: sem +1.
    assert bolao_points(4, 1, 2, 1) == BOLAO_BASE_OUTCOME + 1  # base + placar perdedor


def test_placar_exato_de_goleada_inclui_bonus():
    # 4-1 cravado: exato + goleada (ambos sao goleada).
    assert bolao_points(4, 1, 4, 1) == BOLAO_BASE_OUTCOME + BOLAO_BONUS_EXACT + BOLAO_BONUS_BLOWOUT


def test_resultado_errado_zera():
    assert bolao_points(2, 1, 0, 1) == 0.0
    assert bolao_points(1, 1, 2, 0) == 0.0


def test_matriz_vetorizada_bate_com_escalar():
    rng = np.random.default_rng(7)
    P = rng.random((6, 6))
    P /= P.sum()
    EP = expected_points_matrix(P)
    for i in range(6):
        for j in range(6):
            brute = sum(
                P[a, b] * bolao_points(i, j, a, b)
                for a in range(6) for b in range(6)
            )
            assert abs(brute - EP[i, j]) < 1e-9


if __name__ == "__main__":
    test_goleada_exige_prever_goleada()
    test_placar_exato_de_goleada_inclui_bonus()
    test_resultado_errado_zera()
    test_matriz_vetorizada_bate_com_escalar()
    print("OK: testes de pontuação do bolão (regra de goleada + consistência) passaram")
