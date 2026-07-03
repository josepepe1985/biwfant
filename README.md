# 🤖 Biwfant — Biwenger Fantasy Automation Bot

Automated management bot for **Vampiros United** in the *Pez loco* league.

Runs on **GitHub Actions** (free, no server needed).  
Sends lineup and transfer recommendations via **Telegram** and waits for your ✅/❌ confirmation before acting.

---

## Features

| Feature | Status |
|---|---|
| 🔐 Auto-login (JWT refresh) | ✅ |
| 👥 Squad fetch (starters + bench) | ✅ |
| 🧠 Heuristic point scorer (fitness + form + trend) | ✅ |
| ⚽ PuLP MILP lineup optimizer (all 7 formations) | ✅ |
| 📊 Market scanner (value efficiency ranking) | ✅ |
| 💸 Sell candidate detection | ✅ |
| 📱 Telegram notifications with ✅/❌ confirmation | ✅ |
| ⏰ GitHub Actions cron (every 6h + pre-jornada) | ✅ |
| 🛡 Dry-run mode (default on) | ✅ |

---

## Quick Start

### 1. Fork / push this repo to GitHub

### 2. Add GitHub Actions secrets

Go to **Settings → Secrets and variables → Actions** and add:

| Secret | Value |
|---|---|
| `BIWENGER_EMAIL` | `jl.sanchezros@gmail.com` |
| `BIWENGER_PASSWORD` | your Biwenger password |
| `TELEGRAM_BOT_TOKEN` | the bot token |
| `TELEGRAM_CHAT_ID` | your numeric Telegram chat ID |

### 3. Get your Telegram chat ID

Send any message to the bot from your Telegram account, then run:
```bash
python3 scripts/setup_chat_id.py
```
Or do it from GitHub Actions → Run workflow manually.

### 4. First run (dry-run)

Trigger manually: **Actions → Biwfant Bot → Run workflow → dry_run: true**

You'll receive a Telegram message with the lineup proposal. No real actions are taken.

### 5. Go live

Change `DRY_RUN` to `false` in the workflow or trigger with `dry_run: false`.

---

## Schedule

| Cron | When | What |
|---|---|---|
| `0 */6 * * *` | Every 6 hours | Full cycle: squad + market scan |
| `0 20 * * 4` | Thursday 20:00 UTC | Pre-jornada lineup lock |
| `0 6 * * 1` | Monday 06:00 UTC | Post-weekend cleanup |

---

## Local Development

```bash
# Install deps
python3 -m pip install -r requirements.txt

# Run dry-run locally
cd biwfant/
python3 scripts/run_bot.py

# Get your Telegram chat ID
python3 scripts/setup_chat_id.py
```

The `.env` file is used locally. GitHub Actions uses repository secrets.

---

## Architecture

```
api/
  client.py       — Biwenger REST API wrapper (all endpoints)
  models.py       — Pydantic Player, Squad, Market models

engine/
  scorer.py       — Heuristic point predictor (fitness + form + trend)
  optimizer.py    — PuLP MILP lineup selector (all 7 formations)
  market_scanner.py — Value efficiency ranker + sell candidate finder

actions/
  lineup.py       — Lineup computation + Telegram message builder
  transfers.py    — Transfer recommendation message builder

bot/
  telegram_bot.py — Send messages, inline keyboards, long-poll confirmation

scripts/
  run_bot.py      — Main entry point (one full cycle)
  setup_chat_id.py — Helper to get your numeric Telegram chat ID
```

---

## Scoring Model

```
predicted_pts = (0.6 × fitness_avg + 0.4 × season_avg) × trend_mult

fitness_avg  = avg points in last 5 jornadas (None/injured = 0)
season_avg   = total season points / games played
trend_mult   = 1.10 (rising) | 1.00 (stable) | 0.90 (falling)
```

The **PuLP optimizer** then selects 11 players that maximise `Σ predicted_pts` subject to formation constraints, across all 7 valid formations.

---

## League Info

| | |
|---|---|
| **Team** | Vampiros United |
| **League** | Pez loco (ID: 1809775) |
| **Competition** | La Liga |
| **Current position** | 6th |
