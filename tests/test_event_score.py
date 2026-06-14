"""Testes da coleta automatica de placar: parsing do campo de placar no frame
WS e casamento do placar coletado com as partidas do store."""

from __future__ import annotations

from core.bet365_protocol import parse_event_score, parse_frame
from core.schemas import RawMatch
from main import _apply_scores, _match_for_score


def _match(match_id: str, home: str, away: str) -> RawMatch:
    return RawMatch(match_id=match_id, home_team=home, away_team=away)


def test_parse_event_score_campo_conhecido():
    assert parse_event_score({"SS": "2-1"}) == (2, 1)
    assert parse_event_score({"SC": "0-3"}) == (0, 3)


def test_parse_event_score_sem_placar():
    assert parse_event_score({"NA": "Brasil v Marrocos", "TT": "1"}) is None
    # campo desconhecido com forma n-n NAO casa (evita falso positivo)
    assert parse_event_score({"ZZ": "2-1"}) is None


def test_parse_frame_guarda_raw_fields_e_placar():
    frame = "|EV;ID=123;NA=Brasil v Marrocos;SS=1-1;|"
    evs = parse_frame(frame)
    assert len(evs) == 1
    assert evs[0].raw_fields.get("SS") == "1-1"
    assert parse_event_score(evs[0].raw_fields) == (1, 1)


def test_match_for_score_por_id():
    ms = [_match("100", "México", "África do Sul"), _match("200", "Brasil", "Marrocos")]
    m = _match_for_score(ms, "200", "qualquer nome")
    assert m is not None and m.match_id == "200"


def test_match_for_score_por_nome_quando_id_diverge():
    # bet365 usa ids diferentes na URL e no WS — casa por nome dos times.
    ms = [_match("100", "México", "África do Sul"), _match("200", "Brasil", "Marrocos")]
    m = _match_for_score(ms, "999", "Brasil v Marrocos")
    assert m is not None and m.match_id == "200"


def test_apply_scores_nao_sobrescreve_existente(tmp_path, monkeypatch):
    import core.persistence as persistence

    monkeypatch.setattr(persistence, "RESULTS_PATH", tmp_path / "resultados.json")
    monkeypatch.setattr(persistence, "OUTPUT_DIR", tmp_path)
    # tambem o alias importado em main
    import main
    monkeypatch.setattr(main, "load_results", persistence.load_results)
    monkeypatch.setattr(main, "set_result", persistence.set_result)

    ms = [_match("100", "México", "África do Sul"), _match("200", "Brasil", "Marrocos")]
    persistence.set_result("100", 5, 5)  # ja existe (manual)

    applied = _apply_scores(ms, [("100", "México v África do Sul", (2, 0)),
                                 ("200", "Brasil v Marrocos", (1, 1))])
    ids = {m.match_id for m, _, _ in applied}
    assert ids == {"200"}                       # 100 preservado, so 200 aplicado
    assert persistence.load_results()["100"] == (5, 5)
    assert persistence.load_results()["200"] == (1, 1)


if __name__ == "__main__":
    test_parse_event_score_campo_conhecido()
    test_parse_event_score_sem_placar()
    test_parse_frame_guarda_raw_fields_e_placar()
    test_match_for_score_por_id()
    test_match_for_score_por_nome_quando_id_diverge()
    print("OK: testes de placar (parsing + casamento) passaram"
          " — o teste de nao-sobrescrita roda via pytest (usa fixtures).")
