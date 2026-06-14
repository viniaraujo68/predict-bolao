"""Extrator de odds direto do DOM da bet365.bet.br.

Fonte primária das odds: o que está renderizado na tela da partida está aqui.
Quando o DOM não renderiza, `ingestion.py` recorre aos frames WebSocket.

Seletores observados em maio/2026 na página de partida individual de futebol:

  Cabeçalho da partida:
    .sph-FixturePodHeader_TeamName  → nomes dos dois times

  Cada bloco de mercado:
    .gl-MarketGroupPod
      .cm-MarketGroupWithIconsButton_Text  → nome do mercado
      .gl-MarketGroupContainer             → outcomes dentro

  Resultado Final (1X2):
    .srb-ParticipantResponsiveText
      .srb-ParticipantResponsiveText_Name  → nome
      .srb-ParticipantResponsiveText_Odds  → odd decimal

  Gols Mais/Menos (totals):
    .srb-ParticipantLabelCentered_Name     → linha (ex "2.5")
    .gl-MarketColumnHeader                 → "Mais de" / "Menos de"
    .gl-ParticipantOddsOnly_Odds           → odd decimal

  Placar Exato:
    .mcs-MarketCorrectScore                → bloco do mercado
    Conteúdo é interativo (botões +/-), odds individuais NÃO estão no DOM
    como texto estático. Por enquanto retornamos vazio nesse mercado.
"""

from __future__ import annotations

import re
from datetime import date as _date
from datetime import datetime, timedelta

from bs4 import BeautifulSoup

from core.schemas import Market, Outcome, RawMatch


MONTH_ABBR = {
    # Inglês
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    # Português
    "fev": 2, "abr": 4, "mai": 5, "ago": 8, "set": 9, "out": 10, "dez": 12,
}

DATE_RE = re.compile(r"(\d{1,2})\s+([A-Za-zçÇ]{3,4})\s+(\d{1,2}):(\d{2})")


def _parse_match_date(text: str | None) -> datetime | None:
    """Parsea formatos tipo '11 Jun 16:00' ou '11 Jul 21:30'."""
    if not text:
        return None
    m = DATE_RE.search(text)
    if not m:
        return None
    day = int(m.group(1))
    mon_key = m.group(2).lower()[:3]
    month = MONTH_ABBR.get(mon_key)
    if not month:
        return None
    hour = int(m.group(3))
    minute = int(m.group(4))
    today = _date.today()
    year = today.year
    # Se a data extraida ja passou esse ano, assume proximo ano
    candidate = _date(year, month, day)
    if candidate < today:
        year += 1
    try:
        return datetime(year, month, day, hour, minute)
    except ValueError:
        return None


def _safe_float(s: str | None) -> float | None:
    if not s:
        return None
    try:
        v = float(s.strip().replace(",", "."))
        return v if v > 1.0 else None
    except ValueError:
        return None


def _market_name(group) -> str:
    text = group.select_one(".cm-MarketGroupWithIconsButton_Text")
    return text.get_text(strip=True) if text else ""


TITLE_TEAMS_RE = re.compile(
    r"^\s*(.+?)\s+(?:v|vs\.?|x)\s+(.+?)\s*(?:[-–—|].*)?$", re.IGNORECASE
)


def _team_names(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    names = [n.get_text(strip=True) for n in soup.select(".sph-FixturePodHeader_TeamName")]
    if len(names) >= 2:
        return names[0], names[1]
    # Variantes do header (bet365 troca sufixos de classe entre layouts)
    loose = [
        n.get_text(strip=True)
        for n in soup.select('[class*="FixturePodHeader"] [class*="TeamName"]')
    ]
    loose = [n for n in loose if n]
    if len(loose) >= 2:
        return loose[0], loose[1]
    return None, None


def _teams_from_title(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    """Fallback: <title> da pagina ("Canadá v Bósnia e Herzegovina - ...")."""
    if not soup.title:
        return None, None
    m = TITLE_TEAMS_RE.match(soup.title.get_text(strip=True))
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return None, None


DRAW_NAMES = {"empate", "draw", "tie", "x"}


def _h2h_outcome_names(group) -> list[str] | None:
    """Se o pod tem cara de 1X2 (3 outcomes, empate no meio), retorna os nomes."""
    names = [
        el.get_text(strip=True)
        for el in group.select(".srb-ParticipantResponsiveText_Name")
    ]
    names = [n for n in names if n]
    if len(names) == 3 and names[1].lower() in DRAW_NAMES:
        return names
    return None


def _find_h2h_group(soup: BeautifulSoup):
    """Localiza o pod do 1X2: por nome do grupo, ou — no layout novo, onde o
    mercado em destaque vem num pod sem título — pela assinatura dos outcomes."""
    for group in soup.select(".gl-MarketGroupPod"):
        name = _market_name(group)
        if name and _is_h2h_group(name):
            return group
        if not name and _h2h_outcome_names(group):
            return group
    return None


def _teams_from_h2h(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    """Fallback: outcomes do 1X2 (ordem fixa casa/empate/fora)."""
    group = _find_h2h_group(soup)
    if group is not None:
        names = _h2h_outcome_names(group)
        if names is None:
            names = [
                el.get_text(strip=True)
                for el in group.select(".srb-ParticipantResponsiveText_Name")
            ]
            names = [n for n in names if n]
        if len(names) == 3:
            return names[0], names[2]
    return None, None


DATE_TEXT_RE = re.compile(r"^\s*\d{1,2}\s+[A-Za-zçÇ]{3,4}\s+\d{1,2}:\d{2}\s*$")


def _match_date(soup: BeautifulSoup) -> datetime | None:
    el = soup.select_one(".sph-ExtraData_TimeStamp")
    if el:
        return _parse_match_date(el.get_text(strip=True))
    # Layout novo: a data ("12 Jun 22:00") existe mas em classe ofuscada que
    # muda a cada build. Procura o primeiro nó de texto que seja exatamente
    # uma data — aparece uma única vez na página (verificado nos dois layouts).
    node = soup.find(string=DATE_TEXT_RE)
    if node:
        return _parse_match_date(node.strip())
    return None


def _extract_h2h(group, home: str, away: str) -> Market | None:
    """Extrai 1X2 e normaliza nomes pra Home/Draw/Away (compatível com processor.py)."""
    raw: list[tuple[str, float]] = []
    for row in group.select(".srb-ParticipantResponsiveText"):
        name_el = row.select_one(".srb-ParticipantResponsiveText_Name")
        odd_el = row.select_one(".srb-ParticipantResponsiveText_Odds")
        if not name_el or not odd_el:
            continue
        name = name_el.get_text(strip=True)
        odd = _safe_float(odd_el.get_text())
        if odd is None:
            continue
        raw.append((name, odd))
    if len(raw) != 3:
        return None

    # Tenta casar por nome; se nao fechar exatamente Home/Draw/Away, usa a
    # ordem fixa do Resultado Final na bet365 (casa / empate / fora) — os
    # nomes do header podem divergir dos do mercado (abreviacoes, traducao).
    draw_keys = ("empate", "draw", "tie", "x")
    by_name: list[str | None] = []
    for name, _ in raw:
        n_low = name.lower()
        if n_low == home.lower():
            by_name.append("home")
        elif n_low == away.lower():
            by_name.append("away")
        elif any(k == n_low for k in draw_keys):
            by_name.append("draw")
        else:
            by_name.append(None)
    if sorted(x for x in by_name if x) != ["away", "draw", "home"]:
        by_name = ["home", "draw", "away"]

    label = {
        "home": f"Home ({home})",
        "draw": "Draw",
        "away": f"Away ({away})",
    }
    outcomes = [
        Outcome(name=label[slot], odd=odd) for slot, (_, odd) in zip(by_name, raw)
    ]
    return Market(kind="h2h", outcomes=outcomes)


def _extract_totals(group, target_line: float = 2.5) -> Market | None:
    """Extrai outcomes Over/Under na linha-alvo (default 2.5).

    No DOM da bet365, a linha aparece num bloco coluna-cabeçalho e as duas
    odds aparecem nas próximas colunas (Mais de / Menos de). Pode haver
    múltiplas linhas (1.5, 2.5, 3.5...) lado a lado.
    """
    # Procura todos blocos com a linha-alvo
    target_str = f"{target_line:g}".rstrip("0").rstrip(".")
    target_alt = f"{target_line}"
    over_odd = under_odd = None

    # Iteração linear: para cada label de linha igual ao alvo, pega as duas
    # odds que aparecem em sequência (Mais de antes, Menos de depois) no
    # mesmo container .gl-MarketGroupContainer.
    container = group.select_one(".gl-MarketGroupContainer")
    if container is None:
        return None

    rows = container.find_all("div", recursive=False)
    # Cada "row" no DOM bet365 é uma coluna vertical: a primeira tem o label
    # da linha, as próximas duas têm Mais de / Menos de
    # Estrutura: [coluna-labels] [coluna-mais-de] [coluna-menos-de]
    # Cada coluna pode ter várias linhas (uma por handicap)
    if len(rows) < 3:
        return None

    labels_col, over_col, under_col = rows[0], rows[1], rows[2]

    line_labels = [
        el.get_text(strip=True)
        for el in labels_col.select(".srb-ParticipantLabelCentered_Name")
    ]
    over_odds = [
        _safe_float(el.get_text())
        for el in over_col.select(".gl-ParticipantOddsOnly_Odds")
    ]
    under_odds = [
        _safe_float(el.get_text())
        for el in under_col.select(".gl-ParticipantOddsOnly_Odds")
    ]

    for label, oo, uo in zip(line_labels, over_odds, under_odds):
        if label in (target_str, target_alt) and oo and uo:
            over_odd, under_odd = oo, uo
            break

    if over_odd is None or under_odd is None:
        return None

    return Market(
        kind="totals",
        outcomes=[
            Outcome(name=f"Mais de {target_str}", odd=over_odd),
            Outcome(name=f"Menos de {target_str}", odd=under_odd),
        ],
    )


H2H_GROUP_NAMES = {
    "Resultado Final",          # pt-BR
    "Match Result",             # en
    "Match Odds",               # en alt
    "Full Time Result",         # en alt
    "Resultado da Partida",
    "1X2",
}

TOTALS_GROUP_NAMES = {
    "Gols Mais/Menos",          # pt-BR
    "Mais/Menos Gols",
    "Total de Gols",
    "Goals Over/Under",         # en
    "Match Goals",
    "Goals O/U",
    "Total Goals",
    "Over/Under",
}


def _is_h2h_group(name: str) -> bool:
    return name.strip() in H2H_GROUP_NAMES


def _is_totals_group(name: str) -> bool:
    return name.strip() in TOTALS_GROUP_NAMES


def extract_match_from_html(
    html: str,
    event_id: str | None = None,
    fallback_teams: tuple[str | None, str | None] | None = None,
) -> RawMatch | None:
    """Parsea uma página de partida da bet365.bet.br e retorna RawMatch.

    Identificação dos times em cadeia de fallbacks: header da partida →
    <title> da página → outcomes do Resultado Final → `fallback_teams`
    (ex: nomes lidos do card da overview antes do clique).

    Retorna None apenas se não conseguir identificar os times. Se identifica
    a partida mas não acha h2h/totals (ex: pagina de mata-mata com mercados
    diferentes), retorna o match com markets vazio — o caller pode ainda
    adicionar correct_score via cliques.
    """
    soup = BeautifulSoup(html, "html.parser")
    home, away = _team_names(soup)
    if not home or not away:
        home, away = _teams_from_title(soup)
    if not home or not away:
        home, away = _teams_from_h2h(soup)
    if (not home or not away) and fallback_teams:
        home, away = fallback_teams
    if not home or not away:
        return None

    markets: dict[str, Market] = {}
    for group in soup.select(".gl-MarketGroupPod"):
        name = _market_name(group)
        is_h2h = _is_h2h_group(name) if name else bool(_h2h_outcome_names(group))
        if is_h2h and "h2h" not in markets:
            m = _extract_h2h(group, home, away)
            if m:
                markets["h2h"] = m
        elif name and _is_totals_group(name) and "totals" not in markets:
            m = _extract_totals(group, target_line=2.5)
            if m:
                markets["totals"] = m

    return RawMatch(
        match_id=event_id or f"{home}_vs_{away}".replace(" ", "_"),
        match_date=_match_date(soup),
        home_team=home,
        away_team=away,
        markets=markets,
    )
