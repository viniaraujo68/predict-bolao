"""Testes do mercado 'Ambos Marcam' (BTTS): extracao do DOM, classificacao WS
e a garantia de que, sem a odd, o calculo nao muda."""

import numpy as np

from core.bet365_protocol import classify_market_kind
from core.dom_parser import extract_match_from_html
from core.math_engine import _fair_btts, consolidate, matrix_btts

_BASE = dict(
    odd_home=1.95, odd_draw=3.40, odd_away=4.00,
    odd_over_2_5=1.90, odd_under_2_5=1.90,
)

_HTML_HEAD = """
<html><head><title>Brasil v Argentina - Copa</title></head><body>
<div class="sph-FixturePodHeader_TeamName">Brasil</div>
<div class="sph-FixturePodHeader_TeamName">Argentina</div>
<div class="gl-MarketGroupPod">
  <div class="cm-MarketGroupWithIconsButton_Text">Resultado Final</div>
  <div class="srb-ParticipantResponsiveText"><span class="srb-ParticipantResponsiveText_Name">Brasil</span><span class="srb-ParticipantResponsiveText_Odds">2.10</span></div>
  <div class="srb-ParticipantResponsiveText"><span class="srb-ParticipantResponsiveText_Name">Empate</span><span class="srb-ParticipantResponsiveText_Odds">3.30</span></div>
  <div class="srb-ParticipantResponsiveText"><span class="srb-ParticipantResponsiveText_Name">Argentina</span><span class="srb-ParticipantResponsiveText_Odds">3.40</span></div>
</div>
"""
_HTML_TAIL = "</body></html>"


def _btts_pod_rows() -> str:
    # Layout real do pod "Both Teams to Score" da Copa (jun/2026):
    # pares .gl-ParticipantBorderless_Name / _Odds. Aqui em pt (Sim/Não).
    return (
        '<div class="gl-MarketGroupPod">'
        '<div class="cm-MarketGroupWithIconsButton_Text">Ambos Marcam</div>'
        '<div class="gl-ParticipantBorderless"><span class="gl-ParticipantBorderless_Name">Sim</span><span class="gl-ParticipantBorderless_Odds">1.66</span></div>'
        '<div class="gl-ParticipantBorderless"><span class="gl-ParticipantBorderless_Name">Não</span><span class="gl-ParticipantBorderless_Odds">2.10</span></div>'
        '</div>'
    )


def _btts_pod_en() -> str:
    # Mesmo layout, em inglês (Yes/No) — como a bet365 serviu Escócia v Brasil.
    return (
        '<div class="gl-MarketGroupPod">'
        '<div class="cm-MarketGroupWithIconsButton_Text">Both Teams to Score</div>'
        '<div class="gl-ParticipantBorderless"><span class="gl-ParticipantBorderless_Name">Yes</span><span class="gl-ParticipantBorderless_Odds">1.66</span></div>'
        '<div class="gl-ParticipantBorderless"><span class="gl-ParticipantBorderless_Name">No</span><span class="gl-ParticipantBorderless_Odds">2.10</span></div>'
        '</div>'
    )


def _btts_pod_cols() -> str:
    return (
        '<div class="gl-MarketGroupPod">'
        '<div class="cm-MarketGroupWithIconsButton_Text">Ambos Marcam</div>'
        '<div class="gl-MarketColumnHeader">Sim</div><div class="gl-MarketColumnHeader">Não</div>'
        '<div class="gl-ParticipantOddsOnly_Odds">1.66</div><div class="gl-ParticipantOddsOnly_Odds">2.10</div>'
        '</div>'
    )


def test_btts_extraido_layout_borderless():
    html = _HTML_HEAD + _btts_pod_rows() + _HTML_TAIL
    m = extract_match_from_html(html, event_id="1")
    btts = m.market("btts")
    assert btts is not None
    assert [(o.name, o.odd) for o in btts.outcomes] == [("Sim", 1.66), ("Não", 2.10)]


def test_btts_extraido_em_ingles_yes_no():
    html = _HTML_HEAD + _btts_pod_en() + _HTML_TAIL
    m = extract_match_from_html(html, event_id="3")
    btts = m.market("btts")
    assert btts is not None
    # normalizado pra Sim/Não independentemente do idioma do site
    assert [(o.name, o.odd) for o in btts.outcomes] == [("Sim", 1.66), ("Não", 2.10)]


def test_btts_extraido_layout_colunas():
    html = _HTML_HEAD + _btts_pod_cols() + _HTML_TAIL
    m = extract_match_from_html(html, event_id="2")
    btts = m.market("btts")
    assert btts is not None
    assert [(o.name, o.odd) for o in btts.outcomes] == [("Sim", 1.66), ("Não", 2.10)]


def test_classify_ws_btts_por_nome_e_codigo():
    assert classify_market_kind("Ambos Marcam") == "btts"
    assert classify_market_kind("Both Teams to Score") == "btts"
    assert classify_market_kind("qualquer", code="10565") == "btts"


def test_sem_btts_nao_muda_o_calculo():
    """Jogos sem a odd de 'ambos marcam' produzem exatamente o mesmo palpite."""
    base = consolidate(**_BASE)
    same = consolidate(**_BASE, odd_btts_yes=None, odd_btts_no=None)
    assert base.score == same.score
    assert base.expected_points == same.expected_points
    assert base.matrix == same.matrix


def test_com_btts_aproxima_da_odd_de_mercado():
    """Com a odd, a P(ambos marcam) do modelo se move em direcao ao mercado."""
    base = consolidate(**_BASE)
    target = _fair_btts(1.66, 2.10)
    withb = consolidate(**_BASE, odd_btts_yes=1.66, odd_btts_no=2.10)
    p_base = matrix_btts(np.array(base.matrix))
    p_with = matrix_btts(np.array(withb.matrix))
    # o alvo esta acima do base; o ajuste deve aproximar (sem necessariamente cravar,
    # pois sao 5 alvos pra 3 graus de liberdade)
    assert p_base < p_with <= target + 1e-6
