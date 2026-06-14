from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


MarketKind = Literal["h2h", "totals", "correct_score"]


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


class RichMatch(BaseModel):
    raw: RawMatch
    prediction: ConsolidatedPrediction
    # Placar real (inputado manualmente). Preenchido quando o jogo ja foi resolvido.
    actual_home: int | None = None
    actual_away: int | None = None

    @property
    def is_resolved(self) -> bool:
        return self.actual_home is not None and self.actual_away is not None
