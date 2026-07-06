#!/usr/bin/env python3
"""
Game News Fetcher — Python port of game_news_ingestion.gs
---------------------------------------------------------
Reads sources.json, pulls official game news from each source, categorizes each
item (Category + Likely_Sales_Impact), and writes data.json.

Run locally:      python3 fetcher.py
Run in CI:        GitHub Actions calls this on a schedule (see .github/workflows).

Env vars (optional):
  HENRIK_API_KEY     free HenrikDev key, required only for "valorant" sources
  ANTHROPIC_API_KEY  enables the AI classifier (Claude) instead of keyword rules
  USE_AI_CLASSIFIER  set to "1" to turn the AI classifier on (needs ANTHROPIC_API_KEY)

No third-party packages required (uses the Python standard library only).
"""

import json
import os
import re
import sys
import time
import html
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCES_FILE = os.path.join(HERE, "sources.json")
DATA_FILE = os.path.join(HERE, "data.json")

HENRIK_API_KEY = os.environ.get("HENRIK_API_KEY", "").strip()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
USE_AI_CLASSIFIER = os.environ.get("USE_AI_CLASSIFIER", "0").strip() == "1"

DEFAULT_UA = "g2g-market-intel/1.0 (internal research)"


# ------------------------------------------------------------------ HTTP helper
def http_get(url, headers=None, timeout=25):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": DEFAULT_UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        code = resp.getcode()
        body = resp.read().decode("utf-8", errors="replace")
    if code != 200:
        raise RuntimeError("HTTP %s" % code)
    return body


def iso(dt):
    if isinstance(dt, datetime):
        return dt.astimezone(timezone.utc).isoformat()
    return dt or ""


# ------------------------------------------------------------------ fetchers
def fetch_steam(appid):
    url = ("https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"
           "?appid=%s&count=20&maxlength=300&format=json" % urllib.parse.quote(str(appid)))
    data = json.loads(http_get(url))
    items = (data.get("appnews") or {}).get("newsitems") or []
    out = []
    for n in items:
        pub = datetime.fromtimestamp(n["date"], tz=timezone.utc) if n.get("date") else ""
        out.append({"title": n.get("title", ""), "url": n.get("url", ""),
                    "published": iso(pub), "native_category": ""})
    return out


def fetch_hoyolab(spec):
    parts = str(spec).split(":")
    gid = parts[0]
    typ = parts[1] if len(parts) > 1 else "1"
    url = ("https://bbs-api-os.hoyolab.com/community/post/wapi/getNewsList"
           "?gids=%s&page_size=15&type=%s" % (urllib.parse.quote(gid), urllib.parse.quote(typ)))
    body = http_get(url, headers={
        "x-rpc-language": "en-us",
        "Referer": "https://www.hoyolab.com/",
        "User-Agent": "Mozilla/5.0 g2g-market-intel/1.0",
    })
    lst = ((json.loads(body).get("data")) or {}).get("list") or []
    type_name = {"1": "Notice", "2": "Event", "3": "Info"}.get(typ, "News")
    out = []
    for row in lst:
        p = row.get("post") or {}
        pub = datetime.fromtimestamp(p["created_at"], tz=timezone.utc) if p.get("created_at") else ""
        out.append({"title": p.get("subject", ""),
                    "url": "https://www.hoyolab.com/article/" + str(p.get("post_id", "")),
                    "published": iso(pub), "native_category": type_name})
    return out


def fetch_valorant(country_code):
    if not HENRIK_API_KEY:
        raise RuntimeError("HENRIK_API_KEY not set (needed for valorant sources).")
    cc = country_code or "en-us"
    url = "https://api.henrikdev.xyz/valorant/v1/website/" + urllib.parse.quote(cc)
    body = http_get(url, headers={"Authorization": HENRIK_API_KEY, "User-Agent": DEFAULT_UA})
    data = (json.loads(body) or {}).get("data") or []
    out = []
    for a in data:
        out.append({"title": a.get("title", ""),
                    "url": a.get("external_link") or a.get("url") or "",
                    "published": a.get("date", "") or "",
                    "native_category": a.get("category", "") or ""})
    return out


def fetch_fandom_new(domain):
    base = "https://" + domain
    url = (base + "/api.php?action=query&list=recentchanges&rctype=new"
           "&rcnamespace=0&rclimit=20&rcprop=title%7Ctimestamp&format=json")
    body = http_get(url)
    changes = ((json.loads(body).get("query")) or {}).get("recentchanges") or []
    out = []
    for c in changes:
        title = c.get("title", "")
        out.append({"title": title,
                    "url": base + "/wiki/" + urllib.parse.quote(title.replace(" ", "_")),
                    "published": c.get("timestamp", "") or "", "native_category": ""})
    return out


def fetch_reddit_rss(subreddit):
    url = "https://www.reddit.com/r/%s/.rss?limit=25" % urllib.parse.quote(subreddit)
    body = http_get(url)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(body)
    out = []
    for e in root.findall("atom:entry", ns):
        title_el = e.find("atom:title", ns)
        link_el = e.find("atom:link", ns)
        upd = e.find("atom:updated", ns)
        pub = e.find("atom:published", ns)
        out.append({
            "title": title_el.text if title_el is not None else "",
            "url": link_el.get("href") if link_el is not None else "",
            "published": (upd.text if upd is not None else (pub.text if pub is not None else "")),
            "native_category": "",
        })
    return out


def _tag(el):
    return el.tag.split("}")[-1]


def fetch_rss(url):
    """Generic feed reader — handles both RSS 2.0 (<item>) and Atom (<entry>).
    Use for gaming-media outlets (IGN, GameSpot, etc.) or any feed URL."""
    body = http_get(url, headers={"User-Agent": DEFAULT_UA})
    root = ET.fromstring(body)
    out = []
    for it in [e for e in root.iter() if _tag(e) in ("item", "entry")]:
        title, link, pub = "", "", ""
        for ch in it:
            t = _tag(ch)
            if t == "title" and not title:
                title = (ch.text or "").strip()
            elif t == "link" and not link:
                link = ch.get("href") or (ch.text or "").strip()
            elif t in ("pubDate", "published", "updated") and not pub:
                pub = (ch.text or "").strip()
        if title:
            out.append({"title": title, "url": link, "published": pub, "native_category": ""})
    return out


def fetch_newsapi(query):
    """Keyword news across many outlets via NewsAPI.org. Needs NEWS_API_KEY.
    Note: NewsAPI's free tier is for development only — use a paid plan or GNews
    for commercial/production use."""
    key = os.environ.get("NEWS_API_KEY", "").strip()
    if not key:
        raise RuntimeError("NEWS_API_KEY not set (needed for newsapi sources).")
    url = ("https://newsapi.org/v2/everything?q=" + urllib.parse.quote(query)
           + "&language=en&sortBy=publishedAt&pageSize=25&apiKey=" + urllib.parse.quote(key))
    data = json.loads(http_get(url))
    out = []
    for a in data.get("articles", []):
        out.append({"title": a.get("title", ""), "url": a.get("url", ""),
                    "published": a.get("publishedAt", "") or "", "native_category": ""})
    return out


FETCHERS = {
    "steam": fetch_steam,
    "hoyolab": fetch_hoyolab,
    "valorant": fetch_valorant,
    "fandom_new": fetch_fandom_new,
    "reddit_rss": fetch_reddit_rss,
    "rss": fetch_rss,
    "newsapi": fetch_newsapi,
}

# First-party publisher feeds vs. third-party press coverage.
OFFICIAL_TYPES = {"steam", "hoyolab", "valorant"}


def tier_for(source_type):
    return "Official" if source_type in OFFICIAL_TYPES else "Media"


# ------------------------------------------------------------------ classifier
CLASSIFY_RULES = [
    {"words": ["maintenance", "compensation", "server", "downtime"],         "category": "Maintenance", "impact": "None"},
    {"words": ["banner", "wish", "warp", "signal search", "rerun", "gacha"], "category": "Banner",      "impact": "High"},
    {"words": ["version ", "patch note", "patch notes", "update"],           "category": "Update",      "impact": "High"},
    {"words": ["anniversary", "celebration", "festival", "login", "event"],  "category": "Event",       "impact": "Medium"},
    {"words": ["skin", "bundle", "outfit", "cosmetic"],                      "category": "Cosmetic",    "impact": "Medium"},
    {"words": ["vct", "champions", "masters", "tournament", "esports"],      "category": "Esports",     "impact": "Low"},
]


def keyword_match(title):
    t = str(title).lower()
    for r in CLASSIFY_RULES:
        for w in r["words"]:
            if w in t:
                return {"category": r["category"], "impact": r["impact"]}
    return None


def impact_for_native(nc):
    n = str(nc).lower()
    if "patch" in n or "update" in n:
        return "High"
    if "esport" in n or "dev" in n or "info" in n:
        return "Low"
    return "Medium"


def title_case(s):
    return " ".join(w[:1].upper() + w[1:].lower() if w else w for w in str(s).split(" "))


def classify_keyword(title, native_category):
    kw = keyword_match(title)
    if native_category:
        return {"category": title_case(native_category),
                "impact": kw["impact"] if kw else impact_for_native(native_category)}
    return kw if kw else {"category": "Other", "impact": "Unknown"}


# --- AI classifier hook (Claude). Off unless USE_AI_CLASSIFIER=1 + key present. ---
def classify_ai(title, native_category):
    """Placeholder for the roadmap's Claude classifier. Falls back to keywords
    if the API key/flag is not set or the call fails. Wire the real Anthropic
    call in here when you're ready — the rest of the pipeline needs no changes."""
    if not (USE_AI_CLASSIFIER and ANTHROPIC_API_KEY):
        return classify_keyword(title, native_category)
    try:
        payload = json.dumps({
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 100,
            "messages": [{
                "role": "user",
                "content": (
                    "Classify this game news headline for a games marketplace. "
                    "Return ONLY JSON like {\"category\":\"Banner\",\"impact\":\"High\"}. "
                    "category one of: Banner, Update, Event, Maintenance, Esports, Cosmetic, Other. "
                    "impact one of: High, Medium, Low, None. "
                    "Headline: " + str(title)
                ),
            }],
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", data=payload,
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            out = json.loads(resp.read().decode("utf-8"))
        text = out["content"][0]["text"]
        obj = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
        return {"category": obj.get("category", "Other"), "impact": obj.get("impact", "Unknown")}
    except Exception as e:
        sys.stderr.write("AI classify failed (%s); using keywords.\n" % e)
        return classify_keyword(title, native_category)


def classify(title, native_category):
    return classify_ai(title, native_category)


# ------------------------------------------------------------------ main
def load_sources():
    with open(SOURCES_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    return cfg.get("sources", [])


# The generic URL used by the day-one sample seed. Real items never use it,
# so we drop any leftover seed rows on load — otherwise their links all point
# to the same (wrong) page.
SEED_PLACEHOLDER_URL = "https://www.hoyolab.com/"


def load_existing_events():
    if not os.path.exists(DATA_FILE):
        return []
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            evs = json.load(f).get("events", [])
    except Exception:
        return []
    return [e for e in evs if e.get("URL") and e.get("URL") != SEED_PLACEHOLDER_URL]


def main():
    sources = load_sources()
    existing = load_existing_events()
    seen = {e.get("URL") for e in existing if e.get("URL")}
    all_events = list(existing)
    new_count = 0
    errors = []

    for src in sources:
        if not src.get("active"):
            continue
        st = str(src.get("source_type", "")).strip().lower()
        sid = str(src.get("source_id", "")).strip()
        fetcher = FETCHERS.get(st)
        if not fetcher:
            errors.append("%s: unknown source_type '%s'" % (src.get("game"), st))
            continue
        try:
            items = fetcher(sid)
        except Exception as e:
            errors.append("%s [%s]: %s" % (src.get("game"), st, e))
            continue
        match = str(src.get("match", "")).strip().lower()
        for it in items:
            url = it.get("url")
            if not url or url in seen:
                continue
            # Optional keyword filter — keep only headlines mentioning it (e.g.
            # filter a site-wide outlet feed down to one game).
            if match and match not in str(it.get("title", "")).lower():
                continue
            seen.add(url)
            cls = classify(it.get("title", ""), it.get("native_category", ""))
            all_events.append({
                "Pulled_At": iso(datetime.now(timezone.utc)),
                "Game": src.get("game", ""),
                "Category_ID": src.get("category_id", ""),
                "Source": st,
                "Tier": tier_for(st),
                "Title": it.get("title", ""),
                "URL": url,
                "Published_Date": it.get("published", ""),
                "Category": cls["category"],
                "Likely_Sales_Impact": cls["impact"],
                "Reviewed": "N",
            })
            new_count += 1
        time.sleep(1.5)

    payload = {
        "generated_at": iso(datetime.now(timezone.utc)),
        "total": len(all_events),
        "new_this_run": new_count,
        "errors": errors,
        "events": all_events,
    }
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print("Wrote %s — %d total events (%d new). Errors: %d"
          % (DATA_FILE, len(all_events), new_count, len(errors)))
    for e in errors:
        print("  ! " + e)


if __name__ == "__main__":
    main()
