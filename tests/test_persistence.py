"""Testes da deduplicação do store de odds: mesma partida (times+data) não pode
virar duas entradas só porque a bet365 caiu no match_id sintético 'Home_vs_Away'
(ex.: capturando um jogo isolado com ENTER, sem a lista da Copa)."""

from __future__ import annotations

from datetime import datetime

from core.persistence import _fold, _is_synthetic_id, _match_key
from core.schemas import RawMatch


def _m(mid: str, home="Inglaterra", away="Argentina", when="2026-07-15T16:00:00") -> RawMatch:
    return RawMatch(
        match_id=mid,
        home_team=home,
        away_team=away,
        match_date=datetime.fromisoformat(when) if when else None,
    )


def _fold_all(*matches: RawMatch) -> list[RawMatch]:
    by_id: dict = {}
    by_key: dict = {}
    for m in matches:
        _fold(by_id, by_key, m)
    return list(by_id.values())


def test_id_sintetico_nao_duplica_partida_ja_no_store():
    # Ordem do bug real: id numérico primeiro, depois a recaptura sintética.
    result = _fold_all(_m("197780870"), _m("Inglaterra_vs_Argentina"))
    assert len(result) == 1
    # Preserva o id real/numérico (mantém o vínculo com resultados.json).
    assert result[0].match_id == "197780870"


def test_id_sintetico_upgrada_para_numerico_quando_o_real_chega_depois():
    result = _fold_all(_m("Inglaterra_vs_Argentina"), _m("197780870"))
    assert len(result) == 1
    assert result[0].match_id == "197780870"


def test_dado_novo_sempre_ganha():
    antigo = _m("197780870")
    antigo.captured_at = datetime(2026, 7, 15, 12, 38)
    novo = _m("Inglaterra_vs_Argentina")
    novo.captured_at = datetime(2026, 7, 15, 15, 52)
    result = _fold_all(antigo, novo)
    assert len(result) == 1
    assert result[0].captured_at == datetime(2026, 7, 15, 15, 52)


def test_mesmos_times_em_datas_diferentes_sao_partidas_distintas():
    result = _fold_all(
        _m("1", when="2026-06-16T22:00:00"),
        _m("2", when="2026-07-15T16:00:00"),
    )
    assert len(result) == 2


def test_partidas_diferentes_convivem():
    result = _fold_all(_m("1"), _m("2", home="França", away="Brasil"))
    assert len(result) == 2


def test_deteccao_de_id_sintetico():
    assert _is_synthetic_id("Inglaterra_vs_Argentina")
    assert not _is_synthetic_id("197780870")


def test_match_key_usa_data_apenas():
    a = _m("x", when="2026-07-15T16:00:00")
    b = _m("y", when="2026-07-15T20:00:00")  # mesmo dia, hora diferente
    assert _match_key(a) == _match_key(b)
