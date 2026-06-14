"""Testes do _lookup_h2h: a keyword de empate 'x' nao pode casar por substring
com nomes de time que contenham a letra x (ex: Mexico)."""

from __future__ import annotations

from core.processor import _lookup_h2h
from core.schemas import Outcome


def _outcomes(names_odds: list[tuple[str, float]]) -> list[Outcome]:
    return [Outcome(name=n, odd=o) for n, o in names_odds]


def test_czechia_v_mexico_nao_vira_empate():
    # Bug: "Away (Mexico)" contem "x" e era classificado como empate, deixando
    # a unica partida das 72 sem palpite. Agora "x" so casa por igualdade exata.
    outs = _outcomes([
        ("Home (Czechia)", 4.33),
        ("Draw", 3.5),
        ("Away (Mexico)", 1.85),
    ])
    assert _lookup_h2h(outs) == (4.33, 3.5, 1.85)


def test_empate_por_x_exato_ainda_funciona():
    # "x" sozinho (com ou sem espacos) ainda e reconhecido como empate.
    outs = _outcomes([("Home 1", 1.80), ("X", 3.60), ("Away 2", 4.50)])
    assert _lookup_h2h(outs) == (1.80, 3.60, 4.50)


def test_draw_substring_continua_valendo():
    # draw/empate/tie continuam casando por substring.
    outs = _outcomes([
        ("Home (Brazil)", 1.40),
        ("Empate", 4.50),
        ("Away (Croatia)", 7.50),
    ])
    assert _lookup_h2h(outs) == (1.40, 4.50, 7.50)


if __name__ == "__main__":
    test_czechia_v_mexico_nao_vira_empate()
    test_empate_por_x_exato_ainda_funciona()
    test_draw_substring_continua_valendo()
    print("OK: todos os testes de _lookup_h2h passaram")
