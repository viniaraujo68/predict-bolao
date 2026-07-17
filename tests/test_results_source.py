"""Testes do buscador de resultados da ESPN: parse do JSON, casamento por nome
(com apelidos PT/store) e orientação do placar pela identidade dos times."""

from __future__ import annotations

from datetime import datetime

from core.results_source import (
    EspnEvent,
    _same_team,
    decompose_linescores,
    match_event,
    parse_espn_events,
    resolve_event,
    unresolved_past_matches,
)
from core.schemas import RawMatch


def _ls(*vals):
    """Monta um `linescores` da ESPN (lista de {displayValue}) por período."""
    return [{"displayValue": str(v)} for v in vals]


def _m(home: str, away: str, mid: str = "x", when: datetime | None = None) -> RawMatch:
    return RawMatch(match_id=mid, home_team=home, away_team=away, match_date=when)


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


def test_unresolved_past_matches_filtra_e_ordena():
    now = datetime(2026, 6, 14, 12, 0)
    ms = [
        _m("X", "Y", "a", datetime(2026, 6, 13, 16, 0)),  # passado, sem placar -> alvo
        _m("Z", "W", "b", datetime(2026, 6, 12, 16, 0)),  # passado, sem placar -> alvo (mais antigo)
        _m("P", "Q", "c", datetime(2026, 6, 20, 16, 0)),  # futuro -> fora
        _m("R", "S", "d", datetime(2026, 6, 11, 16, 0)),  # passado mas JA tem placar -> fora
        _m("T", "U", "e", None),                          # sem data -> fora
    ]
    out = unresolved_past_matches(ms, {"d": (1, 0)}, now)
    assert [m.match_id for m in out] == ["b", "a"]


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


def test_parse_captura_penaltis_e_status():
    # Alemanha 1-1 Paraguai, decidido nos pênaltis (3-4).
    payload = {
        "events": [{
            "id": "760489",
            "competitions": [{
                "competitors": [
                    {"homeAway": "home", "team": {"displayName": "Alemanha"},
                     "score": "1", "shootoutScore": 3},
                    {"homeAway": "away", "team": {"displayName": "Paraguai"},
                     "score": "1", "shootoutScore": 4},
                ],
                "status": {"type": {"completed": True, "name": "STATUS_FINAL_PEN"}},
            }],
        }]
    }
    ev = parse_espn_events(payload)[0]
    assert ev.event_id == "760489"
    assert ev.home_pen == 3 and ev.away_pen == 4
    assert ev.went_to_penalties and ev.went_to_extra_time


def test_decompose_isola_90_da_prorrogacao_e_penaltis():
    # 1ºT/2ºT/1ºTP/2ºTP/pênaltis. 90' = 0+1 vs 1+0 -> 1-1; sem gol na prorrogação.
    casa = _ls(0, 1, 0, 0, 3)
    fora = _ls(1, 0, 0, 0, 4)
    reg_h, reg_a, et_h, et_a, pen_h, pen_a = decompose_linescores(casa, fora, True)
    assert (reg_h, reg_a) == (1, 1)
    assert (et_h, et_a) == (1, 1)
    assert (pen_h, pen_a) == (3, 4)

    # Gol na prorrogação, sem pênaltis: 1-1 em 90', 2-1 em 120'.
    reg = decompose_linescores(_ls(1, 0, 1, 0), _ls(0, 1, 0, 0), False)
    assert reg[:4] == (1, 1, 2, 1)
    assert reg[4] is None and reg[5] is None


def test_resolve_event_penaltis_orienta_e_separa_90():
    m = _m("Alemanha", "Paraguai")
    ev = EspnEvent("Alemanha", "Paraguai", 1, 1, True,
                   home_pen=3, away_pen=4, status_name="STATUS_FINAL_PEN", event_id="1")
    res = resolve_event(m, [ev], summary_fetcher=lambda _id: (_ls(0, 1, 0, 0, 3), _ls(1, 0, 0, 0, 4)))
    assert (res.home, res.away) == (1, 1)            # placar de 90' pontuado
    assert res.ko["stage"] == "penaltis"
    assert res.ko["winner"] == "away"                # Paraguai avançou
    assert (res.ko["pen_home"], res.ko["pen_away"]) == (3, 4)


def test_resolve_event_mando_invertido_reorienta_ko():
    # Store tem Paraguai em casa; ESPN gravou Alemanha em casa (invertido).
    m = _m("Paraguai", "Alemanha")
    ev = EspnEvent("Alemanha", "Paraguai", 1, 1, True,
                   home_pen=3, away_pen=4, status_name="STATUS_FINAL_PEN", event_id="1")
    res = resolve_event(m, [ev], summary_fetcher=lambda _id: (_ls(0, 1, 0, 0, 3), _ls(1, 0, 0, 0, 4)))
    assert (res.ko["pen_home"], res.ko["pen_away"]) == (4, 3)  # reorientado pro store
    assert res.ko["winner"] == "home"                          # Paraguai (casa no store)


def test_resolve_event_jogo_normal_sem_ko():
    m = _m("Brasil", "Marrocos")
    ev = EspnEvent("Brasil", "Marrocos", 2, 1, True, status_name="STATUS_FINAL")
    res = resolve_event(m, [ev])  # summary nunca é chamado
    assert (res.home, res.away) == (2, 1) and res.ko is None


def test_resolve_event_summary_falha_degrada_sem_abortar():
    m = _m("Alemanha", "Paraguai")
    ev = EspnEvent("Alemanha", "Paraguai", 1, 1, True,
                   home_pen=3, away_pen=4, status_name="STATUS_FINAL_PEN", event_id="1")

    def boom(_id):
        raise RuntimeError("sem rede")

    res = resolve_event(m, [ev], summary_fetcher=boom)
    assert (res.home, res.away) == (1, 1) and res.ko is None and res.degraded


if __name__ == "__main__":
    test_same_team_apelidos_e_normalizacao()
    test_match_event_orienta_pela_identidade()
    test_unresolved_past_matches_filtra_e_ordena()
    test_parse_espn_events_estrutura()
    test_parse_captura_penaltis_e_status()
    test_decompose_isola_90_da_prorrogacao_e_penaltis()
    test_resolve_event_penaltis_orienta_e_separa_90()
    test_resolve_event_mando_invertido_reorienta_ko()
    test_resolve_event_jogo_normal_sem_ko()
    test_resolve_event_summary_falha_degrada_sem_abortar()
    print("OK: testes do buscador ESPN (parse + casamento + orientação + alvos) passaram")
