# Game News Intelligence

A premium dashboard that collects **official game announcements** (banners, patch notes, events, maintenance, esports), categorizes each item, and shows **why marketplace sales might spike or drop**. Built for G2G market intelligence.

No Google Sheet. No server to run. Everything lives on GitHub: a static dashboard + a scheduled fetcher.

---

## How it works

```
GitHub Actions (the timer)  →  fetcher.py pulls news  →  data.json  →  index.html (dashboard)
```

- **`index.html`** — the dashboard. Static, hosted free on GitHub Pages. Reads `data.json`.
- **`fetcher.py`** — pulls news from each source, categorizes it, writes `data.json`. Pure Python standard library (no installs).
- **`sources.json`** — the list of games/feeds to pull. Editable in the dashboard (Sources tab) or by hand.
- **`.github/workflows/fetch-news.yml`** — runs the fetcher on a schedule (every 6 hours by default) and commits the fresh `data.json`.
- **`data.json`** — the collected news (auto-refreshed). Ships pre-seeded with sample items so the page looks alive on day one.

---

## What Lizhen needs (one-time setup)

1. **A free GitHub account.**
2. **Create a public repo** and upload every file in this folder (keep the `.github/workflows/` path intact).
3. **Turn on GitHub Pages:** repo **Settings ▸ Pages ▸ Build and deployment ▸ Deploy from a branch ▸ `main` / root ▸ Save.** After a minute you get the public dashboard URL.
4. **Actions are on by default.** The fetcher will run on schedule. To run it immediately: **Actions tab ▸ "Fetch game news" ▸ Run workflow.**
5. **Valorant only —** get a free key at `api.henrikdev.xyz/dashboard`, then add it in the repo: **Settings ▸ Secrets and variables ▸ Actions ▸ New repository secret**, name `HENRIK_API_KEY`. (Steam, HoYoLAB, and wikis need no key.)

That's it — no coding.

---

## Adding or changing sources

Open the dashboard's **Sources** tab:

- **Add a source** — fill in Game, Category ID, Source type, Source ID. It saves instantly, previews live news in your browser, and joins the list.
- **Toggle / delete** sources with the row controls.
- **Export sources.json** (or **Copy JSON**) and commit that file to the repo. The scheduled fetcher reads `sources.json`, so this is what keeps the "timer" pulling your new source going forward.

**Source types**

| Type | Source ID | Notes |
|---|---|---|
| `steam` | Steam appid (ZZZ = `4162040`) | Clean & stable |
| `hoyolab` | game id: `2` Genshin, `6` HSR, `8` ZZZ, `1` HI3 · add `:2` Events, `:3` Info | May be blocked from servers → use `fandom_new` as fallback |
| `valorant` | country code (`en-us`) | Needs `HENRIK_API_KEY` |
| `fandom_new` | wiki domain, e.g. `genshin-impact.fandom.com` | Backup / extra coverage, noisier |
| `reddit_rss` | subreddit name | Noisy + commercial-restricted — backup only |

---

## The "why sales moved" view (Sales Impact tab)

The news timeline is real; the sales line is **sample** until you connect data. To go live:

1. Export sales as **`sales.json`** next to `index.html`, shaped like:
   ```json
   [{"game":"Genshin Impact","date":"2026-06-01","value":1234}, ...]
   ```
2. The chart auto-overlays it against the high/medium-impact news markers.

⚠️ **Sales figures are private.** Never put real sales on the public Pages URL. Host that view behind a login (e.g. an internal deployment), not on public GitHub Pages.

---

## Roadmap (already wired, just flip on later)

- **AI categorization with Claude** — replaces the keyword rules for accurate type + impact in any language. Add `ANTHROPIC_API_KEY` as a repo secret and set the repo **variable** `USE_AI_CLASSIFIER` to `1`. The hook is already in `fetcher.py`; no other change needed.
- **Sales attribution** — anomaly detection (rolling mean ± std-dev) + event attribution accounting for announcement-vs-effective-date lag.

---

## Running the fetcher locally (optional)

```bash
python3 fetcher.py          # writes data.json
# for Valorant: HENRIK_API_KEY=xxxx python3 fetcher.py
```

Categories: Banner / Update / Event / Maintenance / Esports / Cosmetic / Other · Impact: High / Medium / Low / None.
