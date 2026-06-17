"""Busca placares finais por DATA na API pública de scoreboard da ESPN e os casa
aos jogos do store por NOME dos times (não precisa de id).

A API (`site.api.espn.com/.../soccer/fifa.world/scoreboard?dates=YYYYMMDD`) com
`lang=pt&region=br` devolve nomes em português, então o casamento é quase direto;
uns poucos times têm grafia diferente da que o bet365 gravou no store e entram
no mapa de apelidos abaixo. A orientação do placar (quem é casa) é resolvida pela
IDENTIDADE dos times, não pela posição — bet365 e ESPN podem discordar de mando
em jogos de sede neutra.
"""

from __future__ import annotations

import json
import unicodedata
import urllib.request
from datetime import datetime, timedelta
from difflib import SequenceMatcher

from core.schemas import RawMatch

ESPN_SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/"
    "scoreboard?dates={date}&lang=pt&region=br"
)

# Grafias do store (bet365) que diferem da ESPN-PT -> forma canônica (a da ESPN).
# Chaves e valores são comparados já normalizados (sem acento, minúsculo).
_ALIASES = {
    "qatar": "catar",
    "paraguay": "paraguai",
    "tchequia": "republica tcheca",
    "paises baixos": "holanda",
    "republica da coreia": "coreia do sul",
    # bet365 às vezes grava o nome em inglês; ESPN-PT devolve em português.
    "algeria": "argelia",
    "usa": "estados unidos",
    "eua": "estados unidos",
    "belgium": "belgica",
    "brazil": "brasil",
    "cape verde": "cabo verde",
    "dr congo": "republica democratica do congo",
    "rd congo": "republica democratica do congo",
    "egypt": "egito",
    "england": "inglaterra",
    "france": "franca",
    "germany": "alemanha",
    "ivory coast": "costa do marfim",
    "morocco": "marrocos",
    "netherlands": "holanda",
    "new zealand": "nova zelandia",
    "norway": "noruega",
    "saudi arabia": "arabia saudita",
    "scotland": "escocia",
    "south africa": "africa do sul",
    "south korea": "coreia do sul",
    "spain": "espanha",
    "sweden": "suecia",
    "switzerland": "suica",
    "turkey": "turquia",
    "turkiye": "turquia",
}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("-", " ").replace(".", " ")
    return " ".join(s.split())


def _canon(name: str) -> str:
    n = _norm(name)
    return _ALIASES.get(n, n)


def _same_team(a: str, b: str) -> bool:
    """Mesmo time? canônico igual, OU tokens de um contidos no outro (Bósnia e
    Herzegovina vs Bosnia-Herzegovina), OU alta similaridade de string."""
    ca, cb = _canon(a), _canon(b)
    if ca == cb:
        return True
    ta, tb = set(ca.split()), set(cb.split())
    if ta and tb and (ta <= tb or tb <= ta):
        return True
    return SequenceMatcher(None, ca, cb).ratio() >= 0.84


class EspnEvent:
    __slots__ = ("home", "away", "home_score", "away_score", "completed")

    def __init__(self, home, away, home_score, away_score, completed):
        self.home = home
        self.away = away
        self.home_score = home_score
        self.away_score = away_score
        self.completed = completed


def parse_espn_events(payload: dict) -> list[EspnEvent]:
    """Extrai (home, away, placar, completed) do JSON de scoreboard da ESPN."""
    out: list[EspnEvent] = []
    for ev in payload.get("events", []) or []:
        comps = ev.get("competitions") or []
        if not comps:
            continue
        comp = comps[0]
        home = away = None
        hs = as_ = None
        for c in comp.get("competitors", []) or []:
            name = (c.get("team") or {}).get("displayName") or c.get("team", {}).get("name")
            try:
                score = int(c.get("score")) if c.get("score") not in (None, "") else None
            except (TypeError, ValueError):
                score = None
            if c.get("homeAway") == "home":
                home, hs = name, score
            elif c.get("homeAway") == "away":
                away, as_ = name, score
        status = ((comp.get("status") or ev.get("status") or {}).get("type") or {})
        completed = bool(status.get("completed"))
        if home and away:
            out.append(EspnEvent(home, away, hs, as_, completed))
    return out


def match_event(m: RawMatch, events: list[EspnEvent]) -> tuple[int, int] | None:
    """Acha o placar de `m` no pool de eventos ESPN, orientado pra (casa, fora)
    do store. Só considera jogos encerrados com placar de ambos os lados."""
    for ev in events:
        if not ev.completed or ev.home_score is None or ev.away_score is None:
            continue
        if _same_team(ev.home, m.home_team) and _same_team(ev.away, m.away_team):
            return ev.home_score, ev.away_score
        if _same_team(ev.home, m.away_team) and _same_team(ev.away, m.home_team):
            return ev.away_score, ev.home_score  # ESPN inverteu o mando
    return None


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
