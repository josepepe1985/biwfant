"""
LLM Advisor — uses a free OpenAI-compatible LLM (Groq by default) to
reason about transfers and narrate lineup decisions in Spanish.

Pattern mirrors telegram-affiliate-bot/phase1_niche/niche_research.py:
  client = OpenAI(api_key=..., base_url=...)

If LLM_API_KEY is not configured the module degrades gracefully —
all functions return None and the bot continues with heuristic output.
"""

from __future__ import annotations

import json
import re
from typing import Any

from loguru import logger

from config import settings

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SYSTEM_TRANSFER = """\
Eres un experto en fantasy fútbol español (Biwenger, La Liga).
Analizas datos estadísticos y recomiendas fichajes y ventas para maximizar puntos.
Responde SIEMPRE en JSON válido, sin texto adicional fuera del JSON.
"""

_TRANSFER_PROMPT = """\
Analiza la situación de la plantilla y recomienda la mejor operación de mercado.

DATOS:
{context_json}

Devuelve exactamente este JSON:
{{
  "recommendation": "SELL_AND_BUY" | "BUY_ONLY" | "SELL_ONLY" | "HOLD",
  "sell": {{
    "player": "<nombre o null>",
    "reason": "<razón concisa en español>"
  }},
  "buy": {{
    "player": "<nombre o null>",
    "reason": "<razón concisa en español>"
  }},
  "confidence": <0.0 a 1.0>,
  "summary_es": "<párrafo de 2-3 frases explicando la recomendación en español>"
}}
"""

_SYSTEM_LINEUP = """\
Eres un experto en fantasy fútbol español (Biwenger, La Liga).
Explicas decisiones de alineación de forma concisa y atractiva en español.
Responde con un párrafo corto (2-4 frases), sin JSON, sin listas.
"""

_LINEUP_PROMPT = """\
Explica brevemente en español por qué esta alineación es óptima para la próxima jornada.
Destaca 2-3 jugadores clave y menciona su forma reciente o fixture favorable.

ALINEACIÓN:
{lineup_json}

Escribe como si fuera un comentarista de fantasy, directo y entusiasta. Máximo 4 frases.
"""


# ---------------------------------------------------------------------------
# Client factory
# ---------------------------------------------------------------------------

def _get_client():
    """Return an OpenAI-compatible client or None if not configured."""
    if not settings.llm_enabled:
        return None
    try:
        import httpx
        from openai import OpenAI
        http_client = httpx.Client(verify=settings.ssl_verify)
        return OpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url or None,
            http_client=http_client,
        )
    except ImportError:
        logger.warning("openai package not installed — LLM features disabled")
        return None


def _call(client, system: str, user: str, max_tokens: int = 512) -> str | None:
    """Make a chat completion call. Returns raw string or None on failure."""
    try:
        resp = client.chat.completions.create(
            model=settings.llm_model,
            max_tokens=max_tokens,
            temperature=0.3,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return resp.choices[0].message.content.strip()
    except Exception as exc:
        logger.warning(f"LLM call failed: {exc}")
        return None


def _extract_json(raw: str) -> dict | None:
    """Extract JSON from a string that may contain markdown code fences."""
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()
    # Try to find JSON object even if there's surrounding text
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        raw = match.group(0)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(f"LLM returned invalid JSON: {raw[:200]}")
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def advise_transfers(
    balance: int,
    jornada: int | None,
    sell_candidates: list[dict],
    buy_opportunities: list[dict],
    squad_summary: str = "",
) -> dict[str, Any] | None:
    """
    Ask the LLM to reason about the best transfer move.

    Returns structured dict:
      {recommendation, sell, buy, confidence, summary_es}
    or None if LLM is unavailable.
    """
    client = _get_client()
    if not client:
        return None

    def _player_ctx(p) -> dict:
        return {
            "name": p.name,
            "position": p.position_name,
            "price_M": round(p.price / 1_000_000, 2),
            "fitness_avg": round(p.fitness_avg, 1),
            "season_avg": round(p.points_per_game, 1),
            "trend": p.price_trend,
            "team": p.team.name if p.team else "?",
        }

    context = {
        "balance_M": round(balance / 1_000_000, 2),
        "jornada": jornada,
        "squad_summary": squad_summary,
        "sell_candidates": [
            {**_player_ctx(c["player"]), "triggers": c["triggers"]}
            for c in sell_candidates[:3]
        ],
        "buy_opportunities": [
            {
                **_player_ctx(opp["player"]),
                "market_price_M": round(opp["market_price"] / 1_000_000, 2),
                "predicted_pts": round(opp["predicted_points"], 1),
                "value_efficiency": round(opp["value_efficiency"], 2),
                "fixture_difficulty": opp.get("fixture_difficulty", 1.0),
                "source": "libre" if opp["is_free_pool"] else "rival",
            }
            for opp in buy_opportunities[:5]
        ],
    }

    prompt = _TRANSFER_PROMPT.format(context_json=json.dumps(context, ensure_ascii=False, indent=2))
    raw = _call(client, _SYSTEM_TRANSFER, prompt, max_tokens=600)
    if not raw:
        return None

    result = _extract_json(raw)
    if result:
        logger.info(f"LLM transfer advice: {result.get('recommendation')} (confidence {result.get('confidence')})")
    return result


def narrate_lineup(
    starting_xi: list,
    formation: str,
    predicted_pts: float,
    fixture_map: dict[str, float] | None = None,
) -> str | None:
    """
    Ask the LLM to write a short Spanish narrative about the lineup.
    Returns a plain string paragraph or None if LLM unavailable.
    """
    client = _get_client()
    if not client:
        return None

    lineup_data = {
        "formation": formation,
        "predicted_pts": round(predicted_pts, 1),
        "players": [
            {
                "name": p.name,
                "position": p.position_name,
                "fitness_avg": round(p.fitness_avg, 1),
                "season_avg": round(p.points_per_game, 1),
                "trend": p.price_trend,
                "fixture_diff": round(
                    fixture_map.get(p.team.slug if p.team else "", 1.0), 2
                ) if fixture_map else 1.0,
                "team": p.team.name if p.team else "?",
            }
            for p in starting_xi
        ],
    }

    prompt = _LINEUP_PROMPT.format(lineup_json=json.dumps(lineup_data, ensure_ascii=False, indent=2))
    narrative = _call(client, _SYSTEM_LINEUP, prompt, max_tokens=300)
    if narrative:
        logger.info("LLM lineup narrative generated")
    return narrative


def summarise_market_scan(
    opportunities: list[dict],
    balance: int,
) -> str | None:
    """
    One-sentence LLM summary of market state for the status message.
    Returns plain Spanish string or None.
    """
    client = _get_client()
    if not client:
        return None

    if not opportunities:
        return None

    top3 = [
        {
            "name": opp["player"].name,
            "position": opp["player"].position_name,
            "predicted_pts": round(opp["predicted_points"], 1),
            "price_M": round(opp["market_price"] / 1_000_000, 2),
            "efficiency": round(opp["value_efficiency"], 2),
        }
        for opp in opportunities[:3]
    ]

    prompt = (
        f"Hay €{balance/1_000_000:.1f}M disponibles. "
        f"Top 3 oportunidades de mercado: {json.dumps(top3, ensure_ascii=False)}. "
        "En una frase directa, ¿merece la pena actuar hoy en el mercado? Sin JSON."
    )
    return _call(client, _SYSTEM_TRANSFER, prompt, max_tokens=120)
