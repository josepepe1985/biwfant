from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Optional


class Team(BaseModel):
    id: int
    name: str
    slug: str


class Player(BaseModel):
    id: int
    name: str
    slug: str = ""
    position: int  # 1=GK 2=DEF 3=MID 4=FWD
    price: int = 0
    fantasyPrice: Optional[int] = None
    status: str = "ok"
    priceIncrement: int = 0
    team: Optional[Team] = None
    fitness: list[int | str | None] = Field(default_factory=list)
    points: int = 0
    playedHome: int = 0
    playedAway: int = 0
    pointsHome: int = 0
    pointsAway: int = 0
    pointsLastSeason: Optional[int] = 0
    prices: list[list[int]] = Field(default_factory=list)

    @property
    def position_name(self) -> str:
        return {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}.get(self.position, "???")

    @property
    def games_played(self) -> int:
        return self.playedHome + self.playedAway

    @property
    def points_per_game(self) -> float:
        return self.points / self.games_played if self.games_played else 0.0

    @property
    def price_millions(self) -> float:
        return self.price / 1_000_000

    @property
    def value_score(self) -> float:
        """Points per million — primary value metric."""
        return self.points_per_game / self.price_millions if self.price else 0.0

    @property
    def fitness_avg(self) -> float:
        """Average of last 5 jornada points; None/strings (benched/injured) treated as 0."""
        scores = [f if isinstance(f, int) else 0 for f in self.fitness[-5:]]
        return sum(scores) / len(scores) if scores else 0.0

    @property
    def is_available(self) -> bool:
        return self.status in ("ok", "doubt")

    @property
    def price_trend(self) -> str:
        if self.priceIncrement > 30_000:
            return "rising"
        if self.priceIncrement < -30_000:
            return "falling"
        return "stable"

    @property
    def trend_emoji(self) -> str:
        return {"rising": "📈", "falling": "📉", "stable": "➡️"}.get(self.price_trend, "")


class Lineup(BaseModel):
    type: str = "4-4-2"
    players: list[Player] = Field(default_factory=list)
    count: int = 0


class Squad(BaseModel):
    id: int
    name: str
    balance: int = 0
    points: int = 0
    lineup: Optional[Lineup] = None
    players: list[Player] = Field(default_factory=list)


class MarketSale(BaseModel):
    price: int
    player_id: int
    seller_user_id: Optional[int] = None  # None = free pool


class MarketData(BaseModel):
    balance: int = 0
    maximum_bid: int = 0
    sales: list[MarketSale] = Field(default_factory=list)


class LeagueStanding(BaseModel):
    id: int
    name: str
    points: int = 0
    teamValue: int = 0
    teamValueInc: int = 0
    position: int = 0
