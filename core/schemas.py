from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from core.teams import canonical_team


MarketKind = Literal["h2h", "totals", "correct_score", "btts"]


class Outcome(BaseModel):
    name: str
    odd: float = Field(gt=1.0)


class Market(BaseModel):
    kind: MarketKind
    outcomes: list[Outcome]


class RawMatch(BaseModel):
    match_id: str
    match_date: datetime | None = None
    home_team: str
    away_team: str
    captured_at: datetime | None = None  # momento em que estas odds foram capturadas
    markets: dict[MarketKind, Market] = Field(default_factory=dict)

    @field_validator("home_team", "away_team", mode="before")
    @classmethod
    def _canonicalize_team(cls, v: str) -> str:
        # Padroniza a grafia (bet365 grava ora em inglês, ora em português) pra
        # casar 1:1 com a ESPN. Ver core/teams.py.
        return canonical_team(v) if isinstance(v, str) else v

    def market(self, kind: MarketKind) -> Market | None:
        return self.markets.get(kind)


class ConsolidatedPrediction(BaseModel):
    """Palpite único consolidado: Dixon-Coles calibrado no 1X2 + totals,
    placar escolhido por máximo de pontos esperados do bolão."""

    score: str | None = None
    prob_score: float = 0.0          # P(placar exato do palpite)
    expected_points: float = 0.0     # E[pontos do bolao] do palpite
    p_home: float | None = None      # probs justas do modelo (1X2)
    p_draw: float | None = None
    p_away: float | None = None
    lambda_home: float | None = None
    lambda_away: float | None = None
    rho: float | None = None         # correlacao Dixon-Coles ajustada por partida
    matrix: list[list[float]] | None = None  # P(home=i, away=j) completa


class KnockoutDecision(BaseModel):
    """Desfecho de um mata-mata decidido além dos 90' (prorrogação/pênaltis).

    Independente do placar de 90' (que o bolão pontua): só descreve quem avançou."""

    stage: str               # 'prorrogacao' | 'penaltis'
    winner: str              # 'home' | 'away' (mando do store)
    et_home: int | None = None   # placar após prorrogação (120')
    et_away: int | None = None
    pen_home: int | None = None  # placar nos pênaltis (None se decidido na prorrogação)
    pen_away: int | None = None


class RichMatch(BaseModel):
    raw: RawMatch
    prediction: ConsolidatedPrediction
    # Placar real de 90' (inputado/ESPN). Preenchido quando o jogo ja foi resolvido.
    actual_home: int | None = None
    actual_away: int | None = None
    # Desfecho de mata-mata, quando passou dos 90'. None em jogo normal.
    decision: KnockoutDecision | None = None

    @property
    def is_resolved(self) -> bool:
        return self.actual_home is not None and self.actual_away is not None
