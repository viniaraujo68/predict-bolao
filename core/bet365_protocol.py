"""Parser do protocolo proprietário bet365 (mensagens via WebSocket).

Formato observado (zap):

    TOPIC|REC1|REC2|...|RECn

Cada `REC` começa com um código de 2 caracteres (tipo do registro) seguido
de pares `;CAMPO=valor`. Dentro de valores, são usados como sub-separadores:
`~`, `^`, `$`, `#`, `¬`.

Tipos de registro relevantes:
  - `CL` — classification (esporte)
  - `CT` — competition (liga/torneio)
  - `EV` — event (partida)
  - `MA` — market (mercado de aposta)
  - `GD` — grid divider (delimitador entre mercados/colunas)
  - `PA` — participant (outcome/selection com odd)

Hierarquia: EV -> MA -> (GD;) -> PA, PA, PA

Odds vêm em formato fracionário: `OD=7/4` => 1 + 7/4 = 2.75 decimal.
`OD=SP` ou vazio = mercado suspenso/sem preço.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator


FRAC_RE = re.compile(r"^(\d+)/(\d+)$")
NUM_RE = re.compile(r"^\d+(\.\d+)?$")


def fractional_to_decimal(od: str) -> float | None:
    """`7/4` -> 2.75 ; `1/2` -> 1.5. Retorna None para vazio/SP/inválido."""
    if not od or od == "SP":
        return None
    m = FRAC_RE.match(od.strip())
    if m:
        num, den = int(m.group(1)), int(m.group(2))
        if den == 0:
            return None
        return round(1.0 + num / den, 4)
    if NUM_RE.match(od.strip()):
        v = float(od)
        return v if v > 1.0 else None
    return None


@dataclass
class ParsedOutcome:
    name: str
    odd: float | None
    handicap: str | None = None
    order: int | None = None
    raw_fields: dict = field(default_factory=dict)


@dataclass
class ParsedMarket:
    code: str
    name: str
    outcomes: list[ParsedOutcome] = field(default_factory=list)


@dataclass
class ParsedEvent:
    event_id: str
    name: str
    markets: list[ParsedMarket] = field(default_factory=list)
    raw_fields: dict = field(default_factory=dict)

    def home_away(self) -> tuple[str | None, str | None]:
        if " v " in self.name:
            h, a = self.name.split(" v ", 1)
            return h.strip(), a.strip()
        if " @ " in self.name:
            a, h = self.name.split(" @ ", 1)
            return h.strip(), a.strip()
        return None, None


def _parse_record_fields(rec: str) -> tuple[str, dict[str, str]]:
    """`MA;ID=1;NA=Resultado Final;` -> ('MA', {'ID':'1','NA':'Resultado Final'})"""
    parts = rec.split(";")
    rtype = parts[0][:2]
    fields = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            fields[k] = v
    return rtype, fields


def split_records(frame: str) -> list[str]:
    """Quebra um frame em records, ignorando o topic header inicial."""
    if "\n" in frame and frame.lstrip().startswith("#"):
        frame = frame.split("\n", 1)[1]
    return [r for r in frame.split("|") if r]


def parse_frame(frame: str) -> list[ParsedEvent]:
    """Extrai eventos com seus mercados e outcomes do frame.

    Estado: ao caminhar pelos records, mantém referência ao último EV e à última
    MA encontrada. PA records são anexados à MA corrente.
    """
    events: dict[str, ParsedEvent] = {}
    current_event: ParsedEvent | None = None
    current_market: ParsedMarket | None = None

    for rec in split_records(frame):
        if len(rec) < 2:
            continue
        rtype, fields = _parse_record_fields(rec)

        if rtype == "EV":
            ev_id = fields.get("ID") or fields.get("IT") or ""
            if not ev_id:
                continue
            if ev_id not in events:
                events[ev_id] = ParsedEvent(
                    event_id=ev_id,
                    name=fields.get("NA", "?"),
                )
            current_event = events[ev_id]
            # Guarda os campos crus do EV (carregam o placar quando o jogo ja
            # comecou/terminou); merge pra acumular updates parciais do mesmo EV.
            current_event.raw_fields.update(fields)
            current_market = None

        elif rtype == "MA":
            if current_event is None:
                continue
            current_market = ParsedMarket(
                code=fields.get("MA") or fields.get("ID") or "",
                name=fields.get("NA", "?"),
            )
            current_event.markets.append(current_market)

        elif rtype == "PA":
            if current_market is None:
                continue
            odd = fractional_to_decimal(fields.get("OD", ""))
            order_str = fields.get("OR", "")
            order = int(order_str) if order_str.isdigit() else None
            current_market.outcomes.append(
                ParsedOutcome(
                    name=fields.get("NA", "").strip() or _name_from_order(current_market, order),
                    odd=odd,
                    handicap=fields.get("HA") or None,
                    order=order,
                    raw_fields=fields,
                )
            )

    return list(events.values())


# Placar de um evento (jogo em andamento/encerrado) nos campos crus do EV.
# A bet365 publica o placar num destes campos, em formato "casa-fora" (ex "2-1");
# a lista e best-effort e deve ser confirmada/ajustada com um dump ao vivo de um
# jogo ja encerrado (rode `extract --dump-scores`). SS e o candidato mais provavel.
SCORE_RE = re.compile(r"^(\d{1,2})-(\d{1,2})$")
SCORE_FIELD_CANDIDATES = ("SS", "SC", "FS", "SL", "XP")


def parse_event_score(fields: dict) -> tuple[int, int] | None:
    """Extrai (gols_casa, gols_fora) dos campos crus de um EV, ou None.

    Procura em SCORE_FIELD_CANDIDATES um valor no formato "casa-fora". So casa
    por campo conhecido (nao varre todos) pra evitar falso positivo de algum
    outro campo que por acaso tenha a forma "n-n".
    """
    for key in SCORE_FIELD_CANDIDATES:
        v = (fields.get(key) or "").strip()
        m = SCORE_RE.match(v)
        if m:
            return int(m.group(1)), int(m.group(2))
    return None


def _name_from_order(market: ParsedMarket, order: int | None) -> str:
    """Para mercados como Resultado Final, PA records frequentemente vêm sem NA;
    o nome é inferido do order (0=home, 1=draw, 2=away) ou de tabelas conhecidas."""
    if order is None:
        return "?"
    fallback = {
        "Resultado Final": ("Home", "Draw", "Away"),
        "Partida - Vencedor": ("Home", "Away"),
    }.get(market.name)
    if fallback and 0 <= order < len(fallback):
        return fallback[order]
    return f"Outcome#{order}"


# ---------------------------------------------------------------------------
# Mapeamento dos mercados-alvo para os tipos canônicos do projeto
# ---------------------------------------------------------------------------

# Códigos MA= conhecidos da bet365 (mais confiável que match por nome)
H2H_CODES = {"1777"}
TOTALS_CODES = {"10124", "10552"}
CORRECT_SCORE_CODES = set()  # ainda desconhecido — capturar de exemplo real

# Match por substring no nome (fallback / complemento)
H2H_NAME_KEYS = ("Resultado Final", "Match Odds", "Full Time Result")
TOTALS_NAME_KEYS = (
    "Partida - Gols",
    "Total de Gols",
    "Total - 2 Opções",
    "Mais/Menos",
    "Gols Mais/Menos",
    "Gols +/-",
    "Over/Under",
)
CORRECT_SCORE_NAME_KEYS = (
    "Placar Exato",
    "Placar Correto",
    "Correct Score",
    "Resultado Exato",
)


def classify_market_kind(market_or_name, code: str | None = None) -> str | None:
    """Classifica um mercado como h2h / totals / correct_score.

    Aceita um `ParsedMarket` (lê `.name` e `.code`) ou uma string com o nome
    (e `code` opcional como kwarg). Usa o MA= code primeiro, fallback por nome.
    """
    if isinstance(market_or_name, ParsedMarket):
        name = market_or_name.name
        code = market_or_name.code or code
    else:
        name = str(market_or_name)
    if code:
        if code in H2H_CODES:
            return "h2h"
        if code in TOTALS_CODES:
            return "totals"
        if code in CORRECT_SCORE_CODES:
            return "correct_score"
    n = name.strip()
    if any(k in n for k in H2H_NAME_KEYS):
        return "h2h"
    if any(k in n for k in CORRECT_SCORE_NAME_KEYS):
        return "correct_score"
    if any(k in n for k in TOTALS_NAME_KEYS):
        return "totals"
    return None
