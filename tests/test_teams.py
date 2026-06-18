"""Testes da padronização de nomes (core/teams.py): bet365 (inglês ou português)
e ESPN convergem pro mesmo nome canônico, e o RawMatch já grava canônico."""

from __future__ import annotations

from core.teams import canonical_team
from core.schemas import RawMatch


def test_canonical_unifica_idiomas_e_grafias():
    # inglês (bet365) e português (ESPN) -> mesmo canônico
    for en, pt in [
        ("Brazil", "Brasil"),
        ("Algeria", "Argélia"),
        ("Germany", "Alemanha"),
        ("England", "Inglaterra"),
        ("Netherlands", "Holanda"),
        ("Türkiye", "Turquia"),
        ("South Korea", "Coreia do Sul"),
        ("Ivory Coast", "Costa do Marfim"),
        ("Czechia", "República Tcheca"),
    ]:
        assert canonical_team(en) == canonical_team(pt) == pt, en

    # variações do mesmo time -> um só canônico
    assert canonical_team("USA") == canonical_team("EUA") == "Estados Unidos"
    assert (
        canonical_team("DR Congo")
        == canonical_team("RD Congo")
        == canonical_team("Congo DR")
        == "República Democrática do Congo"
    )

    # desconhecido (placeholder de mata-mata) passa direto
    assert canonical_team("2º do Grupo A") == "2º do Grupo A"


def test_rawmatch_grava_canonico():
    # a bet365 às vezes grava em inglês; o RawMatch já normaliza pra PT canônico.
    m = RawMatch(match_id="x", home_team="Argentina", away_team="Algeria")
    assert m.home_team == "Argentina" and m.away_team == "Argélia"


if __name__ == "__main__":
    test_canonical_unifica_idiomas_e_grafias()
    test_rawmatch_grava_canonico()
    print("OK: testes de padronização de nomes (canonical + RawMatch) passaram")
