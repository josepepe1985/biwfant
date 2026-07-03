from __future__ import annotations

from typing import Optional

import requests
import urllib3
from loguru import logger

from config import settings

urllib3.disable_warnings()

_BASE = "https://biwenger.as.com/api/v2"
_CF_BASE = "https://cf.biwenger.com/api/v2"


class BiwengerClient:
    def __init__(self) -> None:
        self._token: Optional[str] = None
        self._session = requests.Session()
        self._session.verify = settings.ssl_verify

    # ------------------------------------------------------------------ auth

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "X-League": str(settings.biwenger_league_id),
            "X-User": str(settings.biwenger_user_id),
            "X-Lang": "es",
            "Content-Type": "application/json",
        }

    def login(self) -> str:
        r = self._session.post(
            f"{_BASE}/auth/login",
            json={
                "email": settings.biwenger_email,
                "password": settings.biwenger_password,
            },
            verify=settings.ssl_verify,
        )
        r.raise_for_status()
        self._token = r.json()["token"]
        logger.info("Authenticated with Biwenger.")
        return self._token

    def ensure_authenticated(self) -> None:
        if not self._token:
            self.login()

    # --------------------------------------------------------------- helpers

    def _get(self, path: str, params: dict | None = None, base: str = _BASE) -> dict:
        self.ensure_authenticated()
        r = self._session.get(
            f"{base}{path}",
            headers=self._headers(),
            params=params,
            verify=settings.ssl_verify,
        )
        r.raise_for_status()
        return r.json().get("data", {})

    def _post(self, path: str, payload: dict) -> dict:
        self.ensure_authenticated()
        r = self._session.post(
            f"{_BASE}{path}",
            json=payload,
            headers=self._headers(),
            verify=settings.ssl_verify,
        )
        r.raise_for_status()
        return r.json().get("data", {})

    def _put(self, path: str, payload: dict) -> dict:
        self.ensure_authenticated()
        r = self._session.put(
            f"{_BASE}{path}",
            json=payload,
            headers=self._headers(),
            verify=settings.ssl_verify,
        )
        r.raise_for_status()
        return r.json().get("data", {})

    def _delete(self, path: str, params: dict | None = None) -> dict:
        self.ensure_authenticated()
        r = self._session.delete(
            f"{_BASE}{path}",
            headers=self._headers(),
            params=params,
            verify=settings.ssl_verify,
        )
        r.raise_for_status()
        return r.json().get("data", {})

    # --------------------------------------------------------------- account

    def get_account(self) -> dict:
        return self._get("/account")

    # --------------------------------------------------------------- squad

    def get_squad(self) -> dict:
        """
        Returns user data with ALL squad players (starters + bench).

        Strategy:
        - `lineup` (no sub-field restriction) → 11 starters with full data
        - `players(id)` → all 14 squad IDs
        - bench IDs = all IDs - lineup IDs → fetch individually
        """
        raw = self._get(
            "/user",
            params={
                "fields": "*,lineup,players(id),market(*,-userID),-trophies"
            },
        )

        lineup = raw.get("lineup") or {}
        starter_players = lineup.get("players") or []
        lineup_ids: set[int] = set(lineup.get("playersID") or [])
        all_ids = [p["id"] for p in (raw.get("players") or []) if p.get("id")]
        bench_ids = [i for i in all_ids if i not in lineup_ids]

        bench_players = []
        for pid in bench_ids:
            try:
                bench_players.append(self.get_player(pid))
            except Exception as exc:
                logger.warning(f"Could not fetch bench player {pid}: {exc}")

        raw["all_players"] = starter_players + bench_players
        raw["lineup_player_ids"] = list(lineup_ids)
        return raw

    def get_lineup(self) -> dict:
        return self._get(
            "/user", params={"fields": "lineup(date,type,playersID,count)"}
        )

    def set_lineup(
        self,
        formation: str,
        player_ids: list[int],
        reserves_ids: list[int] | None = None,
        captain: int = 0,
    ) -> dict:
        payload = {
            "lineup": {
                "type": formation,
                "playersID": player_ids,
                "reservesID": reserves_ids or [],
                "captain": captain,
            }
        }
        if settings.dry_run:
            logger.info(
                f"[DRY RUN] set_lineup {formation} | players={player_ids}"
            )
            return {}
        return self._put("/user?fields=lineup(date,type,playersID)", payload)

    # --------------------------------------------------------------- market

    def get_market(self) -> dict:
        return self._get("/market")

    def list_player_for_sale(self, player_id: int, price: int) -> dict:
        if settings.dry_run:
            logger.info(
                f"[DRY RUN] list_for_sale player={player_id} price=€{price:,}"
            )
            return {}
        return self._post("/market", {"playerID": player_id, "price": price})

    def remove_from_market(self, player_id: int) -> dict:
        if settings.dry_run:
            logger.info(f"[DRY RUN] remove_from_market player={player_id}")
            return {}
        return self._delete("/market", params={"player": player_id})

    def place_bid(
        self,
        player_id: int,
        amount: int,
        seller_user_id: int | None = None,
        offer_type: str = "purchase",
    ) -> dict:
        """
        Bid on a player.
        - seller_user_id=None  → free-pool (system) daily-market bid
        - seller_user_id=int   → clausulazo or offer on rival's player
        """
        payload = {
            "to": seller_user_id,
            "type": offer_type,
            "amount": amount,
            "requestedPlayers": [player_id],
        }
        if settings.dry_run:
            logger.info(
                f"[DRY RUN] place_bid player={player_id} €{amount:,} "
                f"seller={seller_user_id}"
            )
            return {}
        return self._post("/offers", payload)

    # --------------------------------------------------------------- players

    def get_player(self, player_id: int) -> dict:
        return self._get(
            f"/players/{settings.biwenger_competition}/{player_id}",
            params={
                "lang": "es",
                "fields": "*,team,fitness,reports,prices,seasons",
            },
        )

    def get_all_players(self) -> dict:
        """Full competition data (public endpoint — works from GH Actions)."""
        try:
            return self._get(
                f"/competitions/{settings.biwenger_competition}/data",
                params={"lang": "es", "score": settings.biwenger_score_id},
                base=_CF_BASE,
            )
        except Exception as exc:
            logger.warning(
                f"cf.biwenger.com unreachable ({exc}). "
                "Skipping full player map."
            )
            return {}

    # --------------------------------------------------------------- league

    def get_league(self) -> dict:
        return self._get(
            f"/league/{settings.biwenger_league_id}",
            params={"fields": "standings"},
        )

    def get_rounds(self) -> dict:
        return self._get("/rounds/league")

    def get_league_board(self, offset: int = 0, limit: int = 50) -> dict:
        return self._get(
            f"/league/{settings.biwenger_league_id}/board",
            params={"offset": offset, "limit": limit, "type": "transfer,market"},
        )
