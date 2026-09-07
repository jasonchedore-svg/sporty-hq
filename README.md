# Sporty HQ

Hybrid **research + alerts** desk for a FanDuel user in **Ontario, Canada**. You scan numbers here, then place **$25 flat** straights yourself on the FanDuel mobile app. Sporty HQ **does not place bets**, **does not log into FanDuel**, and will not grow a full-auto bettor until that is explicitly greenlit.

Demo odds ship as fixtures (JSON/CSV). No paid API key is required.

---

## Gambling disclaimer

**This is not gambling advice, not a tip service, and not an offer to bet.** Sporty HQ is a personal research, logging, and alerting tool. You are solely responsible for every wager. Use it only if you are of **legal gambling age** in a **legal jurisdiction**. The playbook below assumes Ontario (iGaming Ontario / AGCO). If betting is illegal where you are, do not use this software to gamble. Gambling can be addictive — [ConnexOntario](https://www.connexontario.ca/) (1-866-531-2600) if you need help.

Past CLV or P&L in the demo (or your own log) is **not** a prediction of future results.

---

## Playbook (hard-coded defaults)

| Rule | Default |
|---|---|
| Mode | Hybrid: research + alerts; **you** tap the bet on mobile |
| Unit | **$25** flat |
| Session stop | **−$100** (worst-case: realized P&L minus open stake) |
| Markets | **Straights only**: moneyline / spread / total |
| Open tickets | **Max 1 per event** |
| Volume | **~4 bets / session** (local calendar day, `America/Toronto`) |
| Edge | **≥ ~3%** expected value **after juice** vs consensus (de-vigged median of other books) |
| CLV | Log **odds at bet vs close** on every settlement |
| Auto-betting | **Off.** Greenlight required for full-auto. |

Session caps can be overridden with `sporty log-bet --force` (still no auto-wager).

---

## Quick start

Python **3.11+**.

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env        # optional; never commit .env
```

Fixture demo (candidates + sample CLV, no API key):

```bash
sporty demo --data-dir data
```

That ingests `fixtures/demo_odds.json`, scans for ≥3% edges, logs two **sample** tickets (not real wagers), settles them against `fixtures/demo_closes.json`, and writes `data/clv-report.md` + `data/clv-report.html`.

Day-to-day CLI:

```bash
sporty ingest --source fixture --path fixtures/demo_odds.json
sporty scan --alerts          # ranked table + optional new_candidate alerts
sporty log-bet --candidate-id 1
sporty settle <bet-id> --result win --close-odds +148
sporty clv-report --format html --out data/clv-report.html
sporty remind --minutes 45    # pre-game + settle reminders
sporty alert-test
```

```bash
pytest
```

---

## Edge and CLV

**Consensus fair probability** is the median of **multiplicative de-vig** probabilities from books other than the target (default `fanduel`). FanDuel juice is reported but the filter is vs **fair** (after juice), not vs another book’s raw number.

\[
\text{edge} = p_{\text{fair}} \times d_{\text{FanDuel}} - 1
\]

Default keepers: `edge ≥ 0.03`. Override with `--min-edge` or `SPORTY_HQ_MIN_EDGE`.

A **model stub** (`home_prob_bump` in `ScanConfig`) can tilt home-team fair probability. It is a placeholder until a real model exists; the default bump is `0`.

**CLV** (price form):

\[
\text{CLV\%} = (d_{\text{bet}} / d_{\text{close}} - 1) \times 100
\]

Positive CLV means you beat the closing number, independent of win/loss. Settlement also stores P&L (`win` = stake × (decimal − 1), `loss` = −stake, `push`/`void` = 0).

---

## Odds ingest

Providers implement `fetch_quotes()` → normalized `Quote` rows (American odds, straights only).

| Provider | How | Keys |
|---|---|---|
| **Fixture JSON** | The Odds API v4 shape (`id`, `home_team`, `bookmakers`, `markets` `h2h`/`spreads`/`totals`) | None |
| **Fixture CSV** | Columns: `event_id,sport,commence_time,home_team,away_team,book,market,selection,price,point` | None |
| **The Odds API** | `sporty ingest --source oddsapi` | `THE_ODDS_API_KEY` (env only) |

Add a new book/feed by implementing `OddsProvider` in `src/sporty_hq/providers.py` and registering it in `load_provider`.

Target book defaults to FanDuel (`SPORTY_HQ_TARGET_BOOK=fanduel`). Scan writes a markdown snapshot to `data/last_scan.md`.

---

## Bet log and dashboard

SQLite at `$SPORTY_HQ_DATA_DIR/sporty.db` (default `./data`, gitignored). Columns match the playbook log:

`date, event, market, selection, odds_at_bet, stake, result, close_odds, CLV, pnl, notes`

`sporty clv-report` prints **cumulative CLV, win rate, P&L** as markdown (default), HTML, or JSON.

---

## Alert bus

Notifiers (all optional except console + file):

1. **Console** — Rich stdout  
2. **File** — JSONL at `data/alerts.jsonl`  
3. **Generic webhook** — `SPORTY_HQ_WEBHOOK_URL` JSON POST `{type,title,body,dedup_key,payload}`  
4. **Slack incoming webhook** — `SLACK_WEBHOOK_URL`

**Dedup** is SQLite-unique on `dedup_key` (candidate keys include odds, so a number change re-alerts).

| Type | When | Dedup key |
|---|---|---|
| `new_candidate` | `sporty scan --alerts` | event + market + selection + line + odds |
| `pre_game_reminder` | `sporty remind` (default **45 minutes** before `commence_at`) | event + window |
| `settle_reminder` | Open bets whose event has already started | bet id |
| `test` | `sporty alert-test` | unique each run |

### Slack incoming webhook

1. In Slack: **Incoming Webhooks** app → add to a channel → copy the URL.  
2. `export SLACK_WEBHOOK_URL='https://hooks.slack.com/services/…'` (or put it in `.env`).  
3. `sporty alert-test`

No Slack bot token is required. HQ never stores the URL in git.

### Optional SMS (Twilio / IFTTT) via webhook

Sporty HQ does not speak Twilio’s API directly. Point `SPORTY_HQ_WEBHOOK_URL` at:

- **IFTTT Webhooks** (“If webhook, then SMS”), or  
- a small **Twilio Function** / Zapier / n8n flow that maps `{title, body}` to `Messages.create`.

Same JSON schema as the generic webhook. Keep Twilio auth **out of this repo**.

---

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `SPORTY_HQ_DATA_DIR` | `data` | SQLite + reports |
| `SPORTY_HQ_MIN_EDGE` | `0.03` | Keep threshold |
| `SPORTY_HQ_UNIT_STAKE` | `25` | Flat unit |
| `SPORTY_HQ_SESSION_STOP` | `-100` | Session stop (CAD-style units) |
| `SPORTY_HQ_MAX_BETS_PER_SESSION` | `4` | Soft cap (hard unless `--force`) |
| `SPORTY_HQ_TARGET_BOOK` | `fanduel` | Book to score |
| `SPORTY_HQ_REMIND_MINUTES` | `45` | Pre-game window |
| `SPORTY_HQ_WEBHOOK_URL` | unset | Generic POST |
| `SLACK_WEBHOOK_URL` | unset | Slack incoming webhook |
| `THE_ODDS_API_KEY` | unset | Live odds (optional) |

**Never commit secrets.** `.env` is gitignored; `.env.example` has empty placeholders only.

---

## Layout

```
src/sporty_hq/
  cli.py          # typer: ingest, scan, log-bet, settle, clv-report, alert-test, remind, demo
  engine.py       # consensus de-vig + edge rank
  odds_math.py    # American/decimal, juice, CLV, P&L
  providers.py    # fixture JSON/CSV + Odds API
  playbook.py     # session stop, 1/event, straights, ~4/session
  storage.py      # SQLite
  reports.py      # markdown + HTML dashboard
  alerts/         # bus + console/file/webhook/Slack
fixtures/         # demo odds + closes (no keys)
tests/
```

---

## What this will not do

- Place or route bets to FanDuel (or any book)
- Automate FanDuel login, cookies, or the mobile app
- Run unattended full-auto staking without a separate, explicit greenlight

If you want that later, treat it as a new product decision — not a flag to flip casually.
