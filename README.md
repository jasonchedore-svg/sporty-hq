# Sporty HQ

Hybrid **research + alerts** desk for a FanDuel user in **Ontario, Canada**. You scan numbers here, then place **$25 flat** straights yourself on the FanDuel mobile app. Sporty HQ **does not place bets**, **does not fake fills**, and **does not log into FanDuel**. Full-auto is off until explicitly greenlit.

Demo odds ship as fixtures (JSON/CSV). No paid API key is required. Live ingest prefers **OpticOdds realtime** (`OPTICODDS_API_KEY`): **WebSocket first**, then the documented **SSE** stream if WS does not connect (OpticOdds FAQ: they do not offer WebSockets). **Pinnacle** is the sharp benchmark on that same stream. The Odds API is an **optional REST stub** only when no OpticOdds key is set — never preferred, never a silent substitute. Keys are env vars only (never committed, never logged). HQ **never** places bets, **never** fake-fills, and **never** runs scrape loops.

---

## Architecture (v0.2)

Hybrid Ontario desk: **research + alerts + CLV log**. You still tap FanDuel yourself.

**Locked feed:** OpticOdds realtime first (WebSocket attempt → SSE fallback) with Pinnacle as sharp overlay. Odds API is not on the live path.

```
 OpticOdds realtime (WS first → SSE)  +  Pinnacle sharp on the same stream
            │  optional: Odds API REST stub (only if no OpticOdds key)
            │  offline: fixtures
            ▼
     ingest ──► SQLite quotes
            │
            ▼
     CLV dashboard (scoreboard)  ←  paper log-bet / settle --close-odds
            │
            ├─ scan (optional candy) + Pinnacle overlay + fractional Kelly cap 1u
            ├─ post-result lessons (settle --postmortem/--lesson → next scan)
            └─ daily −$100 / seasonal −$500 stops (live); kill switch armed
            │  human-source / lessons HOLD until feed+dashboard logging
            ▼
     gate 1 historical backtest (caveat: ≠ will work again)
            → gate 2 paper ~2–3 weeks, avg CLV must stay > 0
            → live only then
```

| Layer | What it does | What it does not |
|---|---|---|
| **Ingest** | OpticOdds **WebSocket first**, **SSE** if WS fails; Pinnacle on that stream; Odds API **one-shot REST stub**; fixtures | Prefer Odds API. Poll loops. Commit/log keys. FanDuel login. |
| **Sharp** | Pinnacle as benchmark vs FanDuel retail when the book is on the board | Treat Pinnacle as a bettable Ontario book |
| **Scan** | ≥3% juice-removed EV vs consensus; Pinnacle overlay; lessons from prior settles | Place tickets. Replace CLV as the scoreboard. |
| **Bankroll** | Flat **$25** unit; optional fractional Kelly **capped at 1 unit**; daily **−$100** / seasonal **−$500** | Auto-stake on FanDuel. `--force` bypass of stops or kill switch. |
| **Settle** | Require close line; CLV (flat=0); optional postmortem + lesson (feedback loop) | Fake fills |
| **CLV gate** | Paper avg CLV is the **only** scoreboard. `FAILING` if n≥100 and avg CLV ≤ 0 | Judge the process on tiny samples. Unlock live from scan edge. |

**Hypotheses (labeled, not proven):**

- Owner lock is WebSocket-first. OpticOdds FAQ still says they do **not** offer WebSockets; SSE is the documented realtime product. HQ tries WS, then SSE, and will not invent a private WS protocol.
- The Odds API has **no public WebSocket** as of 2026; a hinted Quant-tier push is **not** wired and is not the live path.
- Pinnacle multiplicative de-vig is a better “true market” read than a multi-book median. Keepers still use **consensus median**; Pinnacle is an overlay until that is A/B’d.
- Default season window is **March 20 ET** (MLB-ish), rolling back a year if earlier. Override with `SPORTY_HQ_SEASON_START`.
- Quarter-Kelly (`--kelly` when `KELLY_FRACTION=0`) is a common fractional default; still hard-capped at 1 unit.
- “Similar” lessons match **same sport family + overlapping team tokens**; market match is a bonus, not a hard filter.

---

## Gambling disclaimer

**This is not gambling advice, not a tip service, and not an offer to bet.** Sporty HQ is a personal research, logging, and alerting tool. You are solely responsible for every wager. Use it only if you are of **legal gambling age** in a **legal jurisdiction**. The playbook assumes Ontario (iGaming Ontario / AGCO). If betting is illegal where you are, do not use this software to gamble. Gambling can be addictive — [ConnexOntario](https://www.connexontario.ca/) (1-866-531-2600) if you need help.

Past CLV or P&L (including the sample session fixture) is **not** a prediction of future results. **Judge CLV on ~100+ bets**; smaller samples are directional only.

---

## Locked v1 prefs

| Rule | v1 |
|---|---|
| Mode | Hybrid: research + alerts. **You** tap FanDuel. Never fake fills. |
| Unit | **$25** (`stake_unit_usd`). Optional fractional Kelly, **capped at 1 unit** unless `SPORTY_HQ_KELLY_CAP_UNITS` is raised. |
| Daily stop | **−$100** (`stop_loss_usd` / `SPORTY_HQ_SESSION_STOP`). Hard — `--force` cannot bypass. |
| Seasonal stop | **−$500** (20 units; `SPORTY_HQ_SEASONAL_STOP`). Hard — `--force` cannot bypass. |
| Edge floor | **3%** after juice (`edge_floor_pct`) |
| Volume | **max_bets = 4** / ET session |
| Markets | Straights only: ML / spread / total. **No props, no parlays.** |
| Open tickets | Max 1 per event |
| Sports | **MLB + NFL first** (NFL from **Week 1 Wednesday** onward). Then **NCAAF**. **NBA/NHL** when in season. **Soccer** only if liquid mains (≥3 books). |
| Alerts | **Chat (console) + file**; generic webhook if configured. Slack incoming webhook optional later. SMS later. |
| Pre-game | **30–60 min** before tip, **flagged plays only** (≥3% edge). **Quiet** if nothing clears. |
| CLV | American odds at bet vs close, **same market**. **Flat = 0**. Positive = beat the close. |

---

## Bet log columns (exact)

`Time (ET)` | `Event` | `Market` | `Pick` | `Odds at bet` | `Stake` | `Close odds` | `CLV` | `Result` | `P&L` | `Edge note`

Time is **America/New_York**, labeled ET.

---

## session.json (exact fields)

See `schemas/session.schema.json`. Runtime file: `data/session.json` (gitignored).

```json
{
  "session_id": "2026-09-07",
  "status": "open",
  "stake_unit_usd": 25,
  "stop_loss_usd": -100,
  "edge_floor_pct": 3,
  "max_bets": 4,
  "pnl_usd": 0,
  "clv_sum": 0,
  "clv_n": 0,
  "bets": []
}
```

`status`: `open` | `stopped` | `closed` | `sample` (fixture illustration only). `bets[]` rows use the log columns (`time_et`, `event`, `market`, `pick`, …). HQ writes this from **user-logged** tickets after `log-bet` / `settle` — it never invents a FanDuel fill.

---

## Validation path (owner lock)

Build order does not change: **CLV dashboard first** → **OpticOdds realtime + Pinnacle sharp** → hold human-source/tipster lessons until feed+dashboard are logging. Scan is not the scoreboard. Kill switch is unchanged (A: acted before gates; B: paper avg CLV ≤ 0 at n≥100).

1. **Gate 1 — historical backtest** vs prior season(s) closing lines (1–3 seasons). Same CLV math as the dashboard. Must beat the close (`avg CLV > 0`) on the OpticOdds+Pinnacle path. **Caveat: backtest ≠ will work again.**
2. **Gate 2 — paper the current season ~2–3 weeks** (default 21 days; not a full 17-week NFL slate). Avg CLV must **stay positive** or this gate (and live) un-clears.
3. **Live only after both gates**, and only while paper avg CLV stays positive. `log-bet --live` before that trips kill switch A.

---

## Historical backtest (gate 1)

CLV dashboard + paper `log-bet` are the scoreboard. **No live tickets** until this backtest (non-sample) and a ~2–3 week paper confirm both clear. Kill switch stays armed.

OpticOdds archive audit was **owner-CLEARED 2026-09-08**. File checks still run on every backtest (coverage, timestamps, true close vs last-seen). HQ **does not invent closes**. OpticOdds SSE is realtime only — there is no historical pull in this CLI.

End-to-end:

```bash
# 1) Standing owner flag (not a CLV number)
sporty archive-audit --owner-cleared

# 2) Audit a real OpticOdds archive export (CSV/JSON). Required columns:
#    season,event_id,sport,event,market,selection,posted_odds,close_odds,
#    posted_feed,posted_book,close_book,close_kind,posted_at,close_at,commence_at
#    posted_feed=opticodds  posted_book=fanduel  close_book=pinnacle  close_kind=true_close
sporty archive-audit --path /path/to/opticodds-archive.csv --seasons 1

# 3) Grade vs closes (same CLV math as the dashboard). Logs assumptions + data sources.
sporty backtest --path /path/to/opticodds-archive.csv --seasons 1
# optional: --gaps fixtures/opticodds_archive_gaps.example.json
# writes data/backtest.json and data/archive_audit.json
# exit 0 only if avg CLV > 0, n ≥ min_n, feed parity, archive audit, and source is not a repo fixture

# Will NOT invent numbers:
sporty backtest --source opticodds          # fails: need OPTICODDS_API_KEY note + --path export
sporty backtest                             # fails: --path required

# Schema sample (cannot clear gate 1):
sporty backtest --path fixtures/historical_closes.csv --seasons 1

sporty clv-report            # paper CLV dashboard
sporty log-bet ...           # paper default
sporty log-bet --live        # trips kill switch A until both gates clear
sporty desk-status
```

Feed parity: **OpticOdds posted / FanDuel take + Pinnacle close**. Pinnacle-only history cannot clear. Odds API is an optional REST stub, not this path.

---

## Quick start

Python **3.11+**.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env        # optional; never commit .env
```

Fixture demo (candidates + **sample** CLV file — not fills):

```bash
sporty demo --data-dir data
```

That ingests `fixtures/demo_odds.json` (MLB/NFL/NCAAF; NBA off-season and illiquid soccer are skipped), scans for ≥3% edges, and writes a **SAMPLE** CLV report from `fixtures/sample_session.json`. It does **not** log fake bets.

Day-to-day:

```bash
sporty ingest --source fixture --path fixtures/demo_odds.json
sporty ingest --source stream --replay fixtures/demo_odds.json   # push-path stub, no keys
sporty scan --alerts          # quiet if nothing ≥3%; refuses if a stop is hit
sporty brief                  # Odds Arcade education brief (JSON)
sporty log-bet --candidate-id 1 --pick "Yankees"   # after YOU bet on mobile
sporty log-bet --candidate-id 1 --kelly            # optional size suggestion, cap 1u
sporty settle <bet-id> --result win --close-odds +148
sporty settle <bet-id> --result loss --close-odds +140 --postmortem injury_missed --lesson "…"
sporty session                # data/session.json
sporty clv-report --format html --out data/clv-report.html
sporty clv-report --gate      # exit 1 if FAILING (n≥100 and avg CLV ≤ 0)
sporty remind                 # 30–60m, flagged only
sporty alert-test
```

```bash
pytest
```

---

## Odds Arcade brief

Education-only copy for a daily drop. **Sporty HQ does not place bets**, does not fake fills, and does not log into FanDuel. Briefs explain line moves and juice; they are not tickets.

Cadence (America/New_York):

| When | Command | Contents |
|---|---|---|
| **Daily 9am** | `sporty brief` | `line_move_of_the_day` + `juice_explainer` ($100 risk compare) |
| **Thursday** | `sporty brief --pack` | Close-challenge pack (2–3 games); saved under `data/close_challenge_pack.json` |
| **Friday** | `sporty brief --closes` | Pack vs close: `close_line`, `close_price`, `beat_close` + note |

Schema: `schemas/brief.schema.json`. JSON is the default; `--format md` prints markdown.

Open lines come from **earlier ingest snapshots** in the HQ database. A single fixture snapshot has no open, so `line_move_of_the_day.example` is `true` and `why_hint` says the open is illustrated. Juice compare uses live/fixture books when two prices exist (`example=false`).

```bash
sporty brief --source fixture --path fixtures/demo_odds.json
sporty brief --pack --format md
sporty brief --closes --out data/brief-closes.json
```

`$100` juice math (American): plus money risks `100 × 100 / odds` to win $100; minus money risks `|odds|` to win $100. `diff_usd` is the extra stake at the worse number.

---

## Edge and CLV

**Consensus fair** = median multiplicative de-vig across books other than FanDuel.

\[
\text{edge} = p_{\text{fair}} \times d_{\text{FanDuel}} - 1
\]

Keepers: `edge ≥ 0.03` (3%).

**CLV** (same market, American vs American):

- Same number as close → **0** (flat)
- Better number than close → **positive**
- Worse → negative

\[
\text{CLV\%} = (d_{\text{bet}} / d_{\text{close}} - 1) \times 100 \quad (\text{or } 0 \text{ if American equal})
\]

Do not over-read average CLV until **n ≥ ~100**. `sporty clv-report --gate` treats **average CLV as the primary health metric**:

| health | When |
|---|---|
| `INSUFFICIENT_SAMPLE` | n < 100 |
| `PASSING` | n ≥ 100 and avg CLV > 0 |
| `FAILING` | n ≥ 100 and avg CLV ≤ 0 (flat close is 0) |

Every logged bet stores `odds_at_bet`. `settle` **requires** `--close-odds` so CLV is always computed. HQ never invents a close.

---

## Odds ingest

Live path prefers **streaming/push**, not scrape loops. Fixtures stay for offline/dev. **Keys are never committed.**

| Provider | Transport | Keys | Behavior |
|---|---|---|---|
| Fixture JSON/CSV | File | None | Default offline path |
| OpticOdds | **WebSocket first** `wss://api.opticodds.com/api/v3/stream/odds/{sport}`, then **SSE** `GET /api/v3/stream/odds/{sport}` | `OPTICODDS_API_KEY` | Live path. FAQ: no WebSockets — WS attempt then SSE. Pinnacle is a sportsbook on this stream. |
| The Odds API | REST snapshot | `THE_ODDS_API_KEY` | **Optional stub only.** No public WebSocket. Not used when OpticOdds is keyed. One snapshot, then stop. |
| Stream stub | `--source stream --replay` | None | Replays a fixture as a push batch so the live path can be tested offline. |

```bash
sporty ingest --source auto              # OpticOdds WS→SSE if keyed; else Odds API stub; else fixture
sporty ingest --source opticodds         # live path only (never Odds API)
sporty ingest --source stream --replay fixtures/demo_odds.json
sporty ingest --source oddsapi           # optional stub; degrades to fixture if no key
```

**Pinnacle** is the sharp benchmark book (`SPORTY_HQ_SHARP_BOOK=pinnacle`). Scan overlays Pin de-vig vs FanDuel retail in the rationale when both sides are present. Keys: `OPTICODDS_API_KEY` / `THE_ODDS_API_KEY` env only — never commit `.env`.

Props/period/alternates are dropped. Add a feed in `providers.py` / `streaming.py`.

---

## Alert bus (v1)

**Required:** console (chat) + JSONL file (`data/alerts.jsonl`).  
**Optional now:** generic webhook (`SPORTY_HQ_WEBHOOK_URL`) — JSON POST, including IFTTT/Twilio later for SMS.  
**Not required to merge or run:** Slack. Incoming webhook can be wired later with `SPORTY_HQ_ENABLE_SLACK=true` and `SLACK_WEBHOOK_URL`. There is **no Slack OAuth** and CI does not use Slack.

| Type | When |
|---|---|
| `new_candidate` | `scan --alerts` and edge ≥ 3%. Quiet otherwise. |
| `pre_game_reminder` | `remind`, **30–60 min** before tip, **flagged candidates only** |
| `settle_reminder` | Open **user-logged** tickets whose event has started |
| `test` | `alert-test` (console + file; webhook if set) |

Dedup is SQLite-unique on `dedup_key`.

---

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `SPORTY_HQ_DATA_DIR` | `data` | SQLite, `session.json`, reports |
| `SPORTY_HQ_MIN_EDGE` | `0.03` | 3% floor |
| `SPORTY_HQ_UNIT_STAKE` | `25` | Flat unit |
| `SPORTY_HQ_SESSION_STOP` | `-100` | Hard **daily** stop |
| `SPORTY_HQ_SEASONAL_STOP` | `-500` | Hard seasonal stop (20 units) |
| `SPORTY_HQ_SEASON_START` | Mar 20 ET (hypothesis) | Season window `YYYY-MM-DD` |
| `SPORTY_HQ_BANKROLL_USD` | `500` | Kelly bankroll (not a FanDuel balance) |
| `SPORTY_HQ_KELLY_FRACTION` | `0` | 0 = flat unit; e.g. `0.25` = quarter Kelly |
| `SPORTY_HQ_KELLY_CAP_UNITS` | `1` | Kelly hard cap in units |
| `SPORTY_HQ_MAX_BETS_PER_SESSION` | `4` | Cap |
| `SPORTY_HQ_TARGET_BOOK` | `fanduel` | Book to score |
| `SPORTY_HQ_SHARP_BOOK` | `pinnacle` | Sharp benchmark |
| `SPORTY_HQ_REMIND_MIN_MINUTES` | `30` | Pre-game window start |
| `SPORTY_HQ_REMIND_MAX_MINUTES` | `60` | Pre-game window end |
| `SPORTY_HQ_WEBHOOK_URL` | unset | Generic JSON POST (v1 optional) |
| `THE_ODDS_API_KEY` | unset | Optional REST stub (never preferred) |
| `OPTICODDS_API_KEY` | unset | OpticOdds realtime (WS first, SSE fallback) |

Slack (`SPORTY_HQ_ENABLE_SLACK`, `SLACK_WEBHOOK_URL`) is documented for a later pass and is off by default.

**Never commit secrets.** `.env` is gitignored.

---

## Layout

```
src/sporty_hq/
  cli.py          # ingest (stream stub), scan (stops), log-bet, settle --lesson, clv-report --gate, brief, …
  streaming.py    # OpticOdds WS first → SSE; Odds API stub; fixture replay
  bankroll.py     # fractional Kelly (capped), season window
  lessons.py      # postmortem match onto next-slate scans
  brief.py        # Odds Arcade education brief (no bet placement)
  engine.py       # consensus de-vig + Pinnacle sharp overlay; v1 sport calendar
  sports.py       # MLB/NFL first, Week 1 Wed+, NCAAF, in-season NBA/NHL, liquid soccer
  session.py      # session.json + bet-log columns
  odds_math.py    # American, juice, CLV (flat=0), P&L, Kelly
  playbook.py     # hard daily/seasonal stops, 1/event, straights, ~4/session
  storage.py      # SQLite (quotes, bets, lessons)
  reports.py      # markdown + HTML + CLV health gate
  alerts/         # console + file + webhook; Slack optional
schemas/          # session.json, bet log, brief, lesson, clv_health
fixtures/         # demo odds + sample_session.json + opticodds_sse.txt (not fills)
```

---

## What this will not do

- Place or route bets to FanDuel
- Fake fills or auto-log tickets you did not place
- Automate FanDuel login, cookies, or the mobile app
- Unattended full-auto staking without a separate greenlight
- Poll OpticOdds or The Odds API in a scrape loop (SSE / one snapshot / fixture only)
