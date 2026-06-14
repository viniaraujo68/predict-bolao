"""Testes do buscador de resultados da ESPN: parse do JSON, casamento por nome
(com apelidos PT/store) e orientação do placar pela identidade dos times."""

from __future__ import annotations

from core.results_source import (
    EspnEvent,
    _same_team,
    match_event,
    parse_espn_events,
)
from core.schemas import RawMatch


def _m(home: str, away: str) -> RawMatch:
    return RawMatch(match_id="x", home_team=home, away_team=away)


def _ev(home, hs, away, as_, completed=True) -> EspnEvent:
    return EspnEvent(home, away, hs, as_, completed)


def test_same_team_apelidos_e_normalizacao():
    assert _same_team("Qatar", "Catar")
    assert _same_team("USA", "Estados Unidos")
    assert _same_team("Paraguay", "Paraguai")
    assert _same_team("Tchéquia", "República Tcheca")
    assert _same_team("Bosnia-Herzegovina", "Bósnia e Herzegovina")
    assert _same_team("Canada", "Canadá")
    assert _same_team("Países Baixos", "Holanda")
    assert not _same_team("Coreia do Sul", "Coreia do Norte")
    assert not _same_team("Brasil", "Argentina")


def test_match_event_orienta_pela_identidade():
    # ESPN com mando ora igual, ora invertido vs o store.
    events = [
        _ev("México", 2, "África do Sul", 0),
        _ev("Coreia do Sul", 2, "República Tcheca", 1),
        _ev("Canadá", 1, "Bósnia e Herzegovina", 1),
        _ev("Paraguai", 1, "Estados Unidos", 4),     # INVERTIDO (store: USA em casa)
        _ev("Catar", 1, "Suíça", 1),
        _ev("Brasil", 1, "Marrocos", 1),
        _ev("Escócia", 1, "Haiti", 0),               # INVERTIDO (store: Haiti em casa)
        _ev("Austrália", 2, "Turquia", 0),
        _ev("Alemanha", None, "Curaçao", None, completed=False),  # futuro
    ]
    casos = {
        ("México", "África do Sul"): (2, 0),
        ("Coreia do Sul", "Tchéquia"): (2, 1),
        ("Canada", "Bosnia-Herzegovina"): (1, 1),
        ("USA", "Paraguay"): (4, 1),                 # reorientado pro mando do store
        ("Qatar", "Suíça"): (1, 1),
        ("Brasil", "Marrocos"): (1, 1),
        ("Haiti", "Escócia"): (0, 1),                # reorientado
        ("Austrália", "Turquia"): (2, 0),
    }
    for (h, a), expected in casos.items():
        assert match_event(_m(h, a), events) == expected, f"{h} x {a}"

    # jogo não encerrado -> sem placar
    assert match_event(_m("Alemanha", "Curaçao"), events) is None


def test_parse_espn_events_estrutura():
    payload = {
        "events": [
            {"competitions": [{
                "competitors": [
                    {"homeAway": "home", "team": {"displayName": "Brasil"}, "score": "1"},
                    {"homeAway": "away", "team": {"displayName": "Marrocos"}, "score": "1"},
                ],
                "status": {"type": {"completed": True}},
            }]},
        ]
    }
    evs = parse_espn_events(payload)
    assert len(evs) == 1
    assert evs[0].home == "Brasil" and evs[0].away == "Marrocos"
    assert evs[0].home_score == 1 and evs[0].away_score == 1 and evs[0].completed


if __name__ == "__main__":
    test_same_team_apelidos_e_normalizacao()
    test_match_event_orienta_pela_identidade()
    test_parse_espn_events_estrutura()
    print("OK: testes do buscador ESPN (parse + casamento + orientação) passaram")
