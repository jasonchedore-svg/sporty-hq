# Sporty HQ

Hybrid **research + alerts** desk for a FanDuel user in **Ontario, Canada**. You scan numbers here, then place **$25 flat** straights yourself on the FanDuel mobile app. Sporty HQ **does not place bets**, **does not fake fills**, and **does not log into FanDuel**. Full-auto is off until explicitly greenlit.

Demo odds ship as fixtures (JSON/CSV). No paid API key is required. **Payable path:** The Odds API (`THE_ODDS_API_KEY`) and/or SportsGameOdds (`SPORTSGAMEODDS_API_KEY`), FanDuel as the retail take, plus **Pinnacle only if that provider exposes it**. OpticOdds is **not** a hard requirement and is **not invented**. Keys are env vars only (never committed, never logged). HQ **never** places bets, **never** fake-fills, and **never** runs scrape loops. **Paper trade only.**

---

## Architecture (v0.3)

Hybrid Ontario desk: **research + alerts + CLV log**. You still tap FanDuel yourself. This protocol is **paper only** — no live tickets. Kill switch stays.

**Standing order:** Gate 1 backtest is positive CLV on the **cheaper feed** (Odds API / SportsGameOdds), not an enterprise OpticOdds archive you cannot buy. Honest desk > fake OpticOdds backtest. Expect **thinner edge / more missing history** — that cost is logged, not filled. The backtest **report** (`data/backtest.md`) must log **every assumption**, **every data source**, and **KNOWN LIABILITY** if Pinnacle closes are missing.

**Locked feed:** Odds API **or** SportsGameOdds. Pinnacle is optional (only if exposed). OpticOdds is leftover/explicit-only and cannot clear gate 1.

**Feed parity:** Backtest must match that payable path (`posted_feed=oddsapi|sportsgameodds`, `posted_book=fanduel`). Pinnacle-only posted history still cannot clear. OpticOdds history cannot clear. Missing Pinnacle close is a **liability log**, not a fake close.

**Archive audit (hard prerequisite):** Coverage gaps, stale/missing timestamps, true close vs last-seen. Thin/junk → `data/archive_audit.md` and **stop**. No vanity CLV.

```
 Odds API REST  OR  SportsGameOdds REST   (+ Pinnacle IF the provider returned it)
            │  explicit leftover: OpticOdds WS/SSE (not required, not invented)
            │  offline: fixtures
            ▼
     ingest ──► SQLite quotes
            │
            ▼
     CLV dashboard (scoreboard)  ←  paper log-bet / settle --close-odds
            │
            ├─ scan (optional candy) HOLD this phase
            └─ kill switch armed; live tickets refused
            ▼
     gate 0 archive audit (HARD STOP if thin/junk)
            ▼
     gate 1 cheap-feed historical backtest (caveat: ≠ will work again)
            → gate 2 paper ~2–3 weeks, avg CLV must stay > 0
            → live stays LOCKED (paper only)
```

| Layer | What it does | What it does not |
|---|---|---|
| **Archive audit** | Gate 0 hard stop: events/markets/seasons, timestamps, `true_close` vs last-seen | Vanity CLV on a thin or last-seen dump |
| **Feed parity** | Backtest and live share Odds API / SportsGameOdds + FanDuel take | OpticOdds-hard-lock. Invented Pinnacle. Pinnacle-only posted take |
| **Sharp** | Pinnacle close **if the cheaper feed exposes it**; else liability | Invent Pinnacle closes. Treat Pinnacle as an Ontario book |
| **Scan** | ≥3% juice-removed EV vs consensus | Place tickets. Replace CLV as the scoreboard. |
| **Bankroll** | Flat **$25** unit; optional fractional Kelly **capped at 1 unit**; daily **−$100** / seasonal **−$500** | Auto-stake. `--force` bypass of kill switch. Live tickets. |
| **Settle** | Require close line; CLV (flat=0) | Fake fills |
| **CLV gate** | Paper avg CLV is the **only** scoreboard. `FAILING` if n≥100 and avg CLV ≤ 0 | Unlock live. Judge on tiny samples. |

**Hypotheses (labeled, not proven):**

- Odds API historical snapshots are a paid add-on; scores `daysFrom` maxes at 3. Season-scale gate 1 needs an export `--path` unless the plan allows history.
- SportsGameOdds `includeOpenCloseOdds` / finalized history needs a Pro-class plan. Unmapped books/markets are skipped, never invented.
- Pinnacle may be absent on cheaper US-region snapshots. That is logged as liability, not filled.
- OpticOdds leftover WS/SSE still exists for explicit `--source opticodds` but is not the payable path.
- Default season window is **March 20 ET** (MLB-ish). Override with `SPORTY_HQ_SEASON_START`.

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
| Feed | **Odds API or SportsGameOdds + FanDuel take.** Pinnacle only if exposed. OpticOdds not required. Paper only. |

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

Build order does not change: **CLV dashboard first** → **Odds API / SportsGameOdds** (Pinnacle if exposed) → hold human-source/tipster lessons. Scan is not the scoreboard. Kill switch is unchanged (A: acted before gates / live attempt; B: paper avg CLV ≤ 0 at n≥100). **Paper trade only — live stays locked.**

0. **Gate 0 — archive audit (hard stop).** Gaps, stale/missing timestamps, true close vs last-seen. Thin/junk → stop for human review. No vanity CLV.
1. **Gate 1 — historical backtest** on the cheaper feed (Odds API or SportsGameOdds, FanDuel posted). Must beat the close (`avg CLV > 0`). **Not** an enterprise OpticOdds archive. If Pinnacle closes are missing: **KNOWN LIABILITY**, never invent. **Caveat: backtest ≠ will work again.** Expect thinner edge / missing history.
2. **Gate 2 — paper the current season ~2–3 weeks.** Avg CLV must **stay positive**.
3. **Live stays locked.** `log-bet --live` trips kill switch A.

---

## Archive audit (gate 0 — hard stop)

Required **before** any historical backtest. `sporty archive-audit --path FILE` and `sporty backtest --path FILE` both run the same checks:

| Check | Fail means |
|---|---|
| Coverage | Gaps in events / markets (`ml`, `spread`, `total`) / requested seasons |
| Timestamps | Missing or stale `posted_at` / `close_at` / `commence_at` |
| Close quality | `close_kind` is last-seen / unlabeled, not `true_close` |

Failed audit → markdown report (`data/archive_audit.md`), exit 1, **no CLV scored**. Document known holes with `--gaps` (reason ≥20 chars) or row `excluded` + `exclude_reason`. `--owner-cleared` prints the 2026-09-08 standing flag only; it is not a skip and not a CLV number.

## Historical backtest (gate 1)

Feed parity is the **payable cheap path**: `posted_feed=oddsapi|sportsgameodds`, `posted_book=fanduel`. Pinnacle close is used **only if the provider actually returned it**. Missing Pinnacle = KNOWN LIABILITY in `data/backtest.md`. HQ **never invents** Pinnacle or OpticOdds prices. OpticOdds archives cannot clear. Sample fixtures cannot clear. Archive audit must pass first.

CLV dashboard + paper `log-bet` are the scoreboard. **No live tickets.** Kill switch stays armed.

File checks (coverage, timestamps, true close vs last-seen) still run every backtest. Odds API historical is a paid snapshot API (scores window ~3 days unless you pass `--path`). SportsGameOdds historical needs a plan that includes finalized + open/close odds.

End-to-end:

```bash
# Odds API historical pull (THE_ODDS_API_KEY). Thin window unless the plan allows more.
sporty backtest --source oddsapi --seasons 1

# Or an Odds API / SportsGameOdds export:
#    posted_feed=oddsapi|sportsgameodds  posted_book=fanduel
#    close_book=pinnacle if present, else the provider close + liability
sporty archive-audit --path /path/to/oddsapi-archive.csv --seasons 1
sporty backtest --path /path/to/oddsapi-archive.csv --seasons 1
sporty backtest --source sportsgameodds   # needs SPORTSGAMEODDS_API_KEY

# SCHEMA SAMPLE (runnable, cannot clear gate 1):
sporty backtest --path fixtures/historical_closes.csv --seasons 1

# Will NOT invent:
sporty backtest --source opticodds        # refused: not the payable path
sporty backtest                           # needs --path or THE_ODDS_API_KEY / SGO key
```

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

Payable path is **REST** (Odds API or SportsGameOdds), not scrape loops. Fixtures stay for offline/dev. **Keys are never committed.**

| Provider | Transport | Keys | Behavior |
|---|---|---|---|
| Fixture JSON/CSV | File | None | Default offline path |
| The Odds API | REST snapshot + historical snapshots (paid) | `THE_ODDS_API_KEY` | **Payable path.** FanDuel take; Pinnacle if the API returns it. No public WebSocket. |
| SportsGameOdds | REST `/v2/events` | `SPORTSGAMEODDS_API_KEY` | **Payable path.** Pinnacle if exposed. Historical needs a higher plan. |
| OpticOdds | WS first then SSE | `OPTICODDS_API_KEY` | Leftover / explicit only. **Not required. Not invented. Cannot clear gate 1.** |
| Stream stub | `--source stream --replay` | None | Replays a fixture as a push batch. |

```bash
sporty ingest --source auto              # Odds API if keyed, else SportsGameOdds, else fixture
sporty ingest --source oddsapi
sporty ingest --source sportsgameodds
sporty ingest --source opticodds         # leftover; not the payable path
```

**Pinnacle** is the preferred close **when the cheaper feed actually has it**. If not, the backtest logs KNOWN LIABILITY and uses the provider close. Keys: `THE_ODDS_API_KEY` / `SPORTSGAMEODDS_API_KEY` env only — never commit `.env`. Do not invent OpticOdds data.

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
| `THE_ODDS_API_KEY` | unset | Payable Odds API path (live snapshot + historical if the plan allows) |
| `SPORTSGAMEODDS_API_KEY` | unset | Payable SportsGameOdds path |
| `OPTICODDS_API_KEY` | unset | Leftover / explicit only — not required, not invented |

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
