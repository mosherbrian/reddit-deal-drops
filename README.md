# Reddit Deal Drop

Daily harvester that polls Reddit for inference-deal chatter and pushes a
structured JSON drop to this repo, where the Inference Deal Tracker ingests it.

It runs on your own Linux box (not in the cloud) because Reddit blocks
datacenter IPs — your home IP is trusted, so your personal script app works
with the normal password grant.

## Setup (one time)

1. **Prereqs:** Python 3.10+, `pip`, `git`:
   `sudo apt install python3 python3-pip git` (Debian/Ubuntu)
   `pip3 install --user requests`
2. **Repo:** create this repo on GitHub (public is simplest — the drop is
   just aggregated public Reddit chatter), then:
   `git clone <your-repo-url>` and copy these files into it.
3. **Credentials:** `cp config.example.json config.json` and fill in your
   Reddit script app's `client_id` / `client_secret` plus your Reddit
   `username` / `password`. `config.json` is git-ignored — it never leaves
   this machine and is never shared with anyone.
4. **Test it:** `python3 reddit_deal_drop.py` — you should see
   `wrote drops/YYYY-MM-DD.json: N posts, M comments, K mentions`.
   Inspect the JSON; tune the keyword lists at the top of the script if
   you want broader/narrower coverage.

## Schedule it

Make the wrapper executable and add a cron job (runs 9:00 AM daily, ahead of
the tracker's 9:50 AM refresh — adjust the hour to your timezone):

```
chmod +x run_drop.sh
crontab -e
```

Add:

```
PATH=/usr/local/bin:/usr/bin:/bin
0 9 * * * /home/YOU/reddit-deal-drop/run_drop.sh
```

The box must be powered on at 9 AM. Check `drop.log` if a day's drop
doesn't appear.

## What it collects

For each of r/hermesagent, r/opencodeCLI, r/ZaiGLM, r/opencode, r/Cline,
r/Deepseek, r/MistralAI, r/CommandCode: posts from the last 24h and their
comment trees, filtered for price/promo chatter (`price` category) and
limit/capacity chatter (`limits` category). Each mention records the
subreddit, post, comment permalink, author, score, matched keywords, and a
body excerpt (2000 chars).

## Notes / limits (v1)

- Comment `"more"` children aren't expanded — very deep threads may be
  partially covered.
- Script apps get 60 requests/minute; the script paces itself (~1.1s
  between calls). A full run takes a few minutes.
- `python3 reddit_deal_drop.py --self-test` runs the built-in logic checks.
