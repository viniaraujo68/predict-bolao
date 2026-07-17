"""Busca placares finais por DATA na API pública de scoreboard da ESPN e os casa
aos jogos do store por NOME dos times (não precisa de id).

A API (`site.api.espn.com/.../soccer/fifa.world/scoreboard?dates=YYYYMMDD`) com
`lang=pt&region=br` devolve nomes em português. Tanto eles quanto os nomes do
store (bet365) passam por `canonical_team` (core/teams.py), então o casamento é
igualdade exata do nome canônico — sem heurística de similaridade. A orientação
do placar (quem é casa) é resolvida pela IDENTIDADE dos times, não pela posição —
bet365 e ESPN podem discordar de mando em jogos de sede neutra.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta

from core.schemas import RawMatch
from core.teams import canonical_team

ESPN_SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/"
    "scoreboard?dates={date}&lang=pt&region=br"
)
# O scoreboard dá o placar final e o placar de pênaltis, mas NÃO decompõe por
# período — pra separar 90' (tempo regulamentar) da prorrogação precisamos do
# summary, que traz `linescores` (1ºT, 2ºT, 1ºTP, 2ºTP, [pênaltis]) por time.
ESPN_SUMMARY_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/"
    "summary?event={event}&lang=pt&region=br"
)


def _same_team(a: str, b: str) -> bool:
    """Mesmo time? Compara o nome canônico (core/teams.py) — bet365 e ESPN
    convergem pra mesma grafia, então basta igualdade exata."""
    return canonical_team(a) == canonical_team(b)


def _to_int(v) -> int | None:
    """Converte um campo de placar da ESPN (str/num) em int; None se vazio."""
    if v in (None, ""):
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


class EspnEvent:
    __slots__ = (
        "home", "away", "home_score", "away_score", "completed",
        "home_pen", "away_pen", "status_name", "event_id",
    )

    def __init__(self, home, away, home_score, away_score, completed,
                 home_pen=None, away_pen=None, status_name="", event_id=None):
        self.home = home
        self.away = away
        self.home_score = home_score      # placar ao fim do jogo (inclui prorrogação)
        self.away_score = away_score
        self.completed = completed
        self.home_pen = home_pen          # placar nos pênaltis (None se não houve)
        self.away_pen = away_pen
        self.status_name = status_name    # ex.: STATUS_FINAL, STATUS_FINAL_PEN
        self.event_id = event_id          # id pra buscar o summary (decomposição)

    @property
    def went_to_penalties(self) -> bool:
        return self.home_pen is not None and self.away_pen is not None

    @property
    def went_to_extra_time(self) -> bool:
        """Foi além dos 90' (prorrogação e/ou pênaltis)? Detecta pelo status."""
        return "AET" in (self.status_name or "") or self.went_to_penalties


def parse_espn_events(payload: dict) -> list[EspnEvent]:
    """Extrai (home, away, placar, pênaltis, status, id) do scoreboard da ESPN."""
    out: list[EspnEvent] = []
    for ev in payload.get("events", []) or []:
        comps = ev.get("competitions") or []
        if not comps:
            continue
        comp = comps[0]
        home = away = None
        hs = as_ = hp = ap = None
        for c in comp.get("competitors", []) or []:
            name = (c.get("team") or {}).get("displayName") or c.get("team", {}).get("name")
            score = _to_int(c.get("score"))
            pen = _to_int(c.get("shootoutScore"))
            if c.get("homeAway") == "home":
                home, hs, hp = name, score, pen
            elif c.get("homeAway") == "away":
                away, as_, ap = name, score, pen
        status = ((comp.get("status") or ev.get("status") or {}).get("type") or {})
        completed = bool(status.get("completed"))
        if home and away:
            out.append(EspnEvent(
                home, away, hs, as_, completed,
                home_pen=hp, away_pen=ap,
                status_name=status.get("name") or "",
                event_id=ev.get("id"),
            ))
    return out


def find_event(m: RawMatch, events: list[EspnEvent]) -> tuple[EspnEvent, bool] | None:
    """Acha o evento ESPN de `m` e diz se o mando está INVERTIDO vs o store
    (`True` => ESPN gravou casa/fora ao contrário). Só jogos encerrados com
    placar dos dois lados. Retorna None se não casar."""
    for ev in events:
        if not ev.completed or ev.home_score is None or ev.away_score is None:
            continue
        if _same_team(ev.home, m.home_team) and _same_team(ev.away, m.away_team):
            return ev, False
        if _same_team(ev.home, m.away_team) and _same_team(ev.away, m.home_team):
            return ev, True
    return None


def match_event(m: RawMatch, events: list[EspnEvent]) -> tuple[int, int] | None:
    """Placar final de `m` orientado pra (casa, fora) do store. Atalho mantido
    por compat; `resolve_event` é a via completa (separa 90' de prorrogação)."""
    found = find_event(m, events)
    if found is None:
        return None
    ev, inverted = found
    return (ev.away_score, ev.home_score) if inverted else (ev.home_score, ev.away_score)


def _periods(linescores) -> list[int]:
    """Gols por período de um `linescores` da ESPN (lista de {displayValue})."""
    out: list[int] = []
    for p in linescores or []:
        out.append(_to_int((p or {}).get("displayValue")) or 0)
    return out


def decompose_linescores(
    home_ls, away_ls, has_penalties: bool
) -> tuple[int, int, int, int, int | None, int | None]:
    """Decompõe os `linescores` (por período) em:

    (gols90_casa, gols90_fora, gols120_casa, gols120_fora, pen_casa, pen_fora)

    - 90' = soma dos dois primeiros períodos (1º e 2º tempo);
    - 120' = soma de todos os tempos de jogo (exclui o período de pênaltis);
    - pênaltis = último período, só quando `has_penalties` (senão None).
    """
    h = _periods(home_ls)
    a = _periods(away_ls)
    pen_h = pen_a = None
    if has_penalties and h and a:
        pen_h, pen_a = h[-1], a[-1]
        h, a = h[:-1], a[:-1]
    reg_h, reg_a = sum(h[:2]), sum(a[:2])
    et_h, et_a = sum(h), sum(a)
    return reg_h, reg_a, et_h, et_a, pen_h, pen_a


def fetch_summary(event_id: str, timeout: float = 15.0) -> tuple[list, list]:
    """Busca o summary de um evento e devolve (linescores_casa, linescores_fora)
    na MESMA orientação do scoreboard (casa = `homeAway == 'home'`)."""
    url = ESPN_SUMMARY_URL.format(event=event_id)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    comps = (payload.get("header") or {}).get("competitions") or []
    home_ls = away_ls = []
    if comps:
        for c in comps[0].get("competitors", []) or []:
            if c.get("homeAway") == "home":
                home_ls = c.get("linescores") or []
            elif c.get("homeAway") == "away":
                away_ls = c.get("linescores") or []
    return home_ls, away_ls


@dataclass
class ResolvedResult:
    """Resultado resolvido de um jogo, orientado pra (casa, fora) do store.

    `home`/`away` são SEMPRE o placar de 90' (o que o bolão pontua). `ko` só
    existe em mata-mata decidido além dos 90' e descreve prorrogação/pênaltis.
    `degraded` sinaliza que o summary falhou e o placar pode incluir a
    prorrogação (não foi possível isolar os 90')."""

    home: int
    away: int
    ko: dict | None = None
    degraded: bool = False


def resolve_event(m: RawMatch, events: list[EspnEvent], summary_fetcher=fetch_summary):
    """Resolve `m` contra o pool ESPN. Em jogo normal devolve o placar de 90'.
    Em mata-mata além dos 90', busca o summary, isola os 90' e monta o bloco
    `ko` (prorrogação/pênaltis) orientado ao mando do store."""
    found = find_event(m, events)
    if found is None:
        return None
    ev, inverted = found

    def orient(h, a):
        return (a, h) if inverted else (h, a)

    if not ev.went_to_extra_time:
        h, a = orient(ev.home_score, ev.away_score)
        return ResolvedResult(h, a)

    try:
        home_ls, away_ls = summary_fetcher(ev.event_id)
    except Exception:
        h, a = orient(ev.home_score, ev.away_score)
        return ResolvedResult(h, a, degraded=True)

    has_pen = ev.went_to_penalties
    reg_h, reg_a, et_h, et_a, pen_h, pen_a = decompose_linescores(home_ls, away_ls, has_pen)
    placar = orient(reg_h, reg_a)
    et = orient(et_h, et_a)
    pen = orient(pen_h, pen_a) if has_pen else (None, None)

    home_wins_espn = (pen_h or 0) > (pen_a or 0) if has_pen else et_h > et_a
    if inverted:
        winner = "away" if home_wins_espn else "home"
    else:
        winner = "home" if home_wins_espn else "away"

    ko = {
        "stage": "penaltis" if has_pen else "prorrogacao",
        "et_home": et[0], "et_away": et[1],
        "pen_home": pen[0], "pen_away": pen[1],
        "winner": winner,
    }
    return ResolvedResult(placar[0], placar[1], ko=ko)


def fetch_scoreboard(date_yyyymmdd: str, timeout: float = 15.0) -> list[EspnEvent]:
    """GET no scoreboard da ESPN de uma data (YYYYMMDD) e devolve os eventos."""
    url = ESPN_SCOREBOARD_URL.format(date=date_yyyymmdd)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return parse_espn_events(payload)


def unresolved_past_matches(
    matches: list[RawMatch],
    results: dict[str, tuple[int, int]],
    now: datetime,
) -> list[RawMatch]:
    """Jogos ja comecados/encerrados (match_date < now) que ainda nao tem placar
    coletado, ordenados por data. Sao os alvos de `results`."""
    targets = [
        m for m in matches
        if m.match_date and m.match_date < now and m.match_id not in results
    ]
    targets.sort(key=lambda m: m.match_date)
    return targets


def dates_window(matches: list[RawMatch]) -> list[str]:
    """Conjunto de datas YYYYMMDD a consultar: a data de cada jogo ±1 dia (cobre
    a diferença de fuso entre o horário gravado e a data usada pela ESPN)."""
    days: set[str] = set()
    for m in matches:
        if not m.match_date:
            continue
        for delta in (-1, 0, 1):
            days.add((m.match_date + timedelta(days=delta)).strftime("%Y%m%d"))
    return sorted(days)
