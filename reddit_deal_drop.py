#!/usr/bin/env python3
"""
reddit_deal_drop.py -- Daily Reddit deal-chatter harvester for the
Inference Deal Tracker.

Runs on YOUR OWN machine (trusted home IP) using your personal Reddit
script app. Polls the configured subreddits for deal / promo / pricing /
limit discussion in posts AND comments, and writes a structured JSON drop
that the tracker ingests.

Setup:
  1. pip install requests   (or: pip3 install --user requests)
  2. copy config.example.json -> config.json and fill in your Reddit app
     credentials (client_id, client_secret) plus your Reddit username and
     password. config.json is git-ignored; it never leaves this machine.
  3. python3 reddit_deal_drop.py   (writes drops/YYYY-MM-DD.json)

Designed to run once a day from cron, followed by
`git add drops && git commit && git push` (see run_drop.sh).
"""

import datetime
import json
import os
import re
import sys
import time

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
DROPS_DIR = os.path.join(BASE_DIR, "drops")

SUBREDDITS = [
    "hermesagent",
    "opencodeCLI",
    "ZaiGLM",
    "opencode",
    "Cline",
    "Deepseek",
    "MistralAI",
    "CommandCode",
]

WINDOW_HOURS = 24
REQUEST_PAUSE = 1.1  # seconds between API calls (script apps: 60 req/min)
BODY_MAX = 2000

# ---------------------------------------------------------------------------
# Keyword signal lists. A mention qualifies on any price OR limit hit; the
# matched terms are recorded so the tracker can separate price deals from
# limit/capacity intel.
# ---------------------------------------------------------------------------
PRICE_WORDS = [
    "price", "pricing", "promo", "promotion", "discount", "deal", "coupon",
    "cheap", "cheaper", "cheapest", "affordable", "expensive", "overpriced",
    "credits", "trial", "freemium", "subscription", "billing", "payg",
    "pay-as-you-go",
]
PRICE_PATTERNS = [
    r"\$\s?\d",                                        # $5, $ 0.50
    r"\b\d+(?:\.\d+)?\s?/\s?(?:m\b|mil\b|million\b)",  # 2/M, 0.50/million
    r"\bper\s+(?:1\s?m|million)\s+tokens?\b",          # per 1M tokens
    r"\btokens?\s+per\s+doll?ar\b",                    # tokens per dollar
]
LIMIT_WORDS = [
    "rate limit", "ratelimit", "quota", "context window",
    "token limit", "tpm", "rpm",
]

PRICE_RES = [re.compile(p, re.IGNORECASE) for p in PRICE_PATTERNS]


def match_keywords(text):
    """Return (hits, categories). hits = matched terms; categories is a
    sorted subset of {'price', 'limits'}."""
    hits, cats = [], set()
    if not text:
        return hits, sorted(cats)
    low = text.lower()
    for w in PRICE_WORDS:
        if re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", low):
            hits.append(w)
            cats.add("price")
    for pat in PRICE_RES:
        m = pat.search(text)
        if m:
            hits.append(m.group(0).strip())
            cats.add("price")
    for w in LIMIT_WORDS:
        if re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", low):
            hits.append(w)
            cats.add("limits")
    return hits, sorted(cats)


# ---------------------------------------------------------------------------
# Reddit API (script-app password grant)
# ---------------------------------------------------------------------------
class Reddit:
    def __init__(self, cfg):
        self.cfg = cfg
        self.token = None
        self.session = requests.Session()
        self.session.headers["User-Agent"] = cfg["user_agent"]

    def get_token(self):
        r = self.session.post(
            "https://www.reddit.com/api/v1/access_token",
            auth=(self.cfg["client_id"], self.cfg["client_secret"]),
            data={"grant_type": "password",
                  "username": self.cfg["username"],
                  "password": self.cfg["password"]},
            timeout=30,
        )
        r.raise_for_status()
        self.token = r.json()["access_token"]

    def api(self, method, url, **kw):
        if not self.token:
            self.get_token()
        for attempt in (1, 2):
            headers = dict(kw.pop("headers", {}))
            headers["Authorization"] = "bearer " + self.token
            r = self.session.request(method, url, headers=headers,
                                     timeout=30, **kw)
            if r.status_code == 401 and attempt == 1:
                self.get_token()  # token expired; refresh once and retry
                continue
            if r.status_code == 429:
                time.sleep(60)
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError("reddit api failed after retries: " + url)


def get_new_posts(rd, sub):
    data = rd.api("GET", "https://oauth.reddit.com/r/%s/new" % sub,
                  params={"limit": 100})
    return [c["data"] for c in data["data"]["children"]
            if c.get("kind") == "t3"]


def get_comments(rd, sub, post_id):
    data = rd.api(
        "GET",
        "https://oauth.reddit.com/r/%s/comments/%s" % (sub, post_id),
        params={"sort": "top", "limit": 200, "depth": 8},
    )
    out = []
    if len(data) > 1:
        walk(data[1]["data"]["children"], out)
    return out


def walk(children, out):
    for child in children or []:
        if not isinstance(child, dict) or child.get("kind") != "t1":
            continue  # skip "more" placeholders in v1
        d = child["data"]
        out.append(d)
        replies = d.get("replies") or {}
        walk((replies.get("data") or {}).get("children"), out)


# ---------------------------------------------------------------------------
# Drop building
# ---------------------------------------------------------------------------
def trunc(s, n=BODY_MAX):
    s = s or ""
    return s if len(s) <= n else s[:n] + "\u2026"


def make_mention(kind, sub, post, comment, hits, cats):
    base = {
        "type": kind,
        "subreddit": sub,
        "categories": cats,
        "matched": sorted(set(hits)),
        "post_id": post["id"],
        "post_title": post.get("title", ""),
        "post_url": "https://www.reddit.com" + post.get("permalink", ""),
        "post_score": post.get("score", 0),
        "post_created_utc": int(post.get("created_utc", 0)),
    }
    if kind == "comment":
        base.update({
            "comment_id": comment["id"],
            "comment_url": ("https://www.reddit.com"
                            + post.get("permalink", "")
                            + comment["id"] + "/"),
            "author": comment.get("author", ""),
            "score": comment.get("score", 0),
            "created_utc": int(comment.get("created_utc", 0)),
            "body": trunc(comment.get("body", "")),
        })
    else:
        base.update({
            "author": post.get("author", ""),
            "score": post.get("score", 0),
            "created_utc": int(post.get("created_utc", 0)),
            "body": trunc(post.get("selftext", "")),
        })
    return base


def load_config():
    if not os.path.exists(CONFIG_PATH):
        sys.exit("config.json not found next to the script -- copy "
                 "config.example.json to config.json and fill it in.")
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    for k in ("client_id", "client_secret", "username", "password",
              "user_agent"):
        if not cfg.get(k) or str(cfg[k]).startswith("YOUR_"):
            sys.exit("config.json: fill in %r first." % k)
    return cfg


def main():
    cfg = load_config()
    rd = Reddit(cfg)
    cutoff = time.time() - WINDOW_HOURS * 3600
    mentions = []
    stats = {"posts_scanned": 0, "comments_scanned": 0}

    for sub in SUBREDDITS:
        try:
            posts = get_new_posts(rd, sub)
        except Exception as e:  # noqa: BLE001 -- keep the daily job going
            print("[%s] listing failed: %s" % (sub, e), file=sys.stderr)
            continue
        for p in posts:
            if p.get("created_utc", 0) < cutoff:
                continue
            stats["posts_scanned"] += 1
            hits, cats = match_keywords(
                (p.get("title") or "") + "\n" + (p.get("selftext") or ""))
            if hits:
                mentions.append(make_mention("post", sub, p, None, hits, cats))
            try:
                comments = get_comments(rd, sub, p["id"])
            except Exception as e:  # noqa: BLE001
                print("[%s] comments failed for %s: %s" % (sub, p["id"], e),
                      file=sys.stderr)
                continue
            for c in comments:
                stats["comments_scanned"] += 1
                chits, ccats = match_keywords(c.get("body") or "")
                if chits:
                    mentions.append(
                        make_mention("comment", sub, p, c, chits, ccats))
            time.sleep(REQUEST_PAUSE)
        time.sleep(REQUEST_PAUSE)

    day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    os.makedirs(DROPS_DIR, exist_ok=True)
    payload = {
        "date": day,
        "generated_at_utc": datetime.datetime.now(
            datetime.timezone.utc).isoformat(),
        "window_hours": WINDOW_HOURS,
        "subreddits": SUBREDDITS,
        "stats": dict(stats, deal_mentions=len(mentions)),
        "mentions": mentions,
    }
    path = os.path.join(DROPS_DIR, day + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print("wrote %s: %d posts, %d comments, %d mentions" % (
        path, stats["posts_scanned"], stats["comments_scanned"],
        len(mentions)))


def self_test():
    h, c = match_keywords(
        "Where is the cheapest place? $2/M tokens, great deal")
    assert "cheapest" in h and "deal" in h, h
    assert c == ["price"], (h, c)
    h, c = match_keywords("hit the rate limit after 200k context window")
    assert c == ["limits"], (h, c)
    assert "rate limit" in h and "context window" in h, h
    h, c = match_keywords("I love sunny days")
    assert not h and not c, (h, c)
    m = make_mention(
        "comment", "ZaiGLM",
        {"id": "abc", "title": "t", "permalink": "/r/ZaiGLM/comments/abc/t/",
         "score": 5, "created_utc": 1, "author": "op"},
        {"id": "def", "body": "cheap!", "score": 3,
         "created_utc": 2, "author": "u1"},
        ["cheap"], ["price"])
    assert m["comment_url"].endswith("/r/ZaiGLM/comments/abc/t/def/"), \
        m["comment_url"]
    assert m["type"] == "comment" and m["subreddit"] == "ZaiGLM"
    assert m["categories"] == ["price"]
    print("self-test OK")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        self_test()
    else:
        main()
