# Pulse

A daily briefing you can keep on your phone's home screen. Every morning it runs
[last30days](https://github.com/mvanhorn/last30days-skill) on the topics you follow,
has Claude write up what people actually said (with the best quotes and links to every
source), and publishes it as a small installable web app.

- **Today**: one headline and three bullets for the day, then each topic with a 30-day
  activity line, a short summary and its top stories.
- **Topic pages**: every story with a plain-language write-up, the strongest community
  quote and its vote count, Polymarket odds when relevant, and source links.
- **Rising**: topics picking up speed this week, from last30days' discovery sweep.
- **Archive**: every past brief, one per day. Works offline once opened.

The coloured bar beside each story is its heat: how hard people engaged with it compared
with everything else that platform turned up that day.

## Set it up (about 10 minutes)

1. **Create a repository** on GitHub and push this folder to it.
   With a free GitHub account the repository must be **public** for GitHub Pages to
   host it. Private repositories need GitHub Pro or a paid plan.

   ```bash
   cd pulse
   git init -b main && git add . && git commit -m "Pulse"
   git remote add origin https://github.com/<you>/pulse.git
   git push -u origin main
   ```

2. **Turn on Pages.** In the repository: Settings > Pages > Build and deployment >
   Source: **GitHub Actions**.

3. **Add your Anthropic API key** (recommended). Settings > Secrets and variables >
   Actions > New repository secret. Name: `ANTHROPIC_API_KEY`. Without it you still get a
   brief, built straight from the engine's ranking, but it reads like a list of headlines.

4. **Run it once.** Actions tab > **Daily brief** > Run workflow. The first run takes
   10 to 20 minutes. After that it runs by itself every morning and is usually ready by
   6:00 Berlin time.

5. **Put it on your phone.** Open `https://<you>.github.io/pulse/`.
   - iPhone: Safari > Share > **Add to Home Screen**.
   - Android: Chrome > menu > **Install app** (or Add to Home screen).

   It opens full screen like an app, and tells you when a newer brief is ready.

## Use your own domain

To serve it at something like `news.example.com`:

1. Tell GitHub the domain first (GitHub recommends doing this before the DNS change):
   ```bash
   gh api -X PUT repos/<you>/<repo>/pages -f cname=news.example.com
   ```
   or Settings > Pages > Custom domain.
2. Where your domain's DNS is managed, add a **CNAME** record: name `news`, value `<you>.github.io`.
3. Once GitHub has issued the certificate (usually within an hour), turn on HTTPS:
   ```bash
   gh api -X PUT repos/<you>/<repo>/pages -F https_enforced=true
   ```

Then change the `https://news.chuka.io/` addresses in `site/index.html` (canonical link and
link-preview tags) to your domain.

No CNAME file is needed, because the site is published by a workflow. To protect the
domain from takeover, also verify it under your GitHub account's Settings > Pages.

## Link previews

Each morning the run also draws `site/data/og.png`: today's headline over the month's
activity line. Sites like LinkedIn, X, WhatsApp and Slack show it when the link is
shared. LinkedIn keeps a preview for about a week, so before posting, paste the link into
LinkedIn's Post Inspector (linkedin.com/post-inspector) to fetch the fresh card.

## Choose your topics

Edit `pulse.toml`. Each topic is a block:

```toml
[[topic]]
name = "Coding agents"             # what the app shows
query = "AI coding agents"         # what the engine searches for
subreddits = ["ClaudeAI", "ChatGPTCoding"]   # optional, sharpens Reddit
# github_repos = ["anthropics/claude-code"]   # optional, issues and releases
# x_handle = "AnthropicAI"                    # optional, needs an X source (below)
# search = ["reddit", "hackernews", "youtube", "polymarket", "github"]  # optional
```

The `[rising]` block sets the domain for the Rising tab (`domain = ""` means global
trending). Commit the change and the next run picks it up. You can edit the file from
GitHub's website on your phone.

## Optional keys: more sources, better ranking

Add any of these as repository secrets, with exactly these names. Each one is optional.

| Secret | What it adds | Cost |
|---|---|---|
| `ANTHROPIC_API_KEY` | Claude writes the brief | See below |
| `SCRAPECREATORS_API_KEY` | TikTok, Instagram, and a backstop when Reddit blocks GitHub's servers. The most useful extra. | 10,000 free calls, then pay as you go |
| `XAI_API_KEY` | X (Twitter) search | Paid, xAI pricing |
| `AUTH_TOKEN` + `CT0` | X search with your own X session cookies | Free, but automated use can put that account at risk |
| `BRAVE_API_KEY` | Better web search and handle resolution | Free tier of 2,000 queries a month |
| `GOOGLE_API_KEY` | Gemini ranks and filters results inside the engine | Free tier available |
| `BSKY_HANDLE` + `BSKY_APP_PASSWORD` | Bluesky | Free |

With no keys at all you get Reddit, Hacker News, Polymarket, GitHub, YouTube, arXiv,
Techmeme and Digg.

## What it costs

- **GitHub**: free for a public repository. A private one uses about 300 to 600 of the
  2,000 free Actions minutes a month that GitHub Free includes.
- **Claude**: with Claude Sonnet 5.5 ($2 per million input tokens, $10 per million output),
  three topics come to roughly $5 to $8 a month. Set `model = "claude-haiku-5-5"` in
  `pulse.toml` to bring that well under $1, with plainer writing.
- **Privacy**: a GitHub Pages site is public to anyone who has the address, even when the
  repository is private. The briefs only contain public posts, but keep it in mind before
  adding personal topics.

## How it works

```
pulse.toml  >  GitHub Actions (daily)  >  last30days engine, one run per topic
                                            |  ranked evidence + structured results
                                            v
                                        pipeline/  >  Claude writes the brief; every link
                                            |         and quote is checked against the evidence
                                            v
                         site/data/days/YYYY-MM-DD.json  >  GitHub Pages  >  your phone
```

- `pipeline/run_daily.py` runs the engine headlessly (`--emit=compact`), reads its
  structured results, asks Claude for a brief as strict JSON, drops any link or quote that
  is not in the evidence, computes heat and the 30-day activity line, and writes the day.
- The engine runs in its own job with a read-only token. A second job commits the new
  brief and a third deploys the site.
- `site/` is plain HTML, CSS and JavaScript with no build step. A service worker keeps the
  app and the briefs you have opened available offline.
- If a run fails completely, nothing is overwritten: yesterday's brief stays up.

## Run it on your own machine

```bash
git clone --depth 1 --branch v4.6.0 https://github.com/mvanhorn/last30days-skill.git ../last30days
export ANTHROPIC_API_KEY=...          # optional
python3 pipeline/run_daily.py --engine ../last30days/skills/last30days/scripts/last30days.py
python3 -m http.server -d site 8000   # then open http://localhost:8000
```

Add `--mock` to use the engine's offline test data, `--only coding-agents` to run one
topic, and `--no-rising` to skip the sweep. Tests: `python3 -m unittest discover -s pipeline/tests`.

## When something looks off

- **A topic says it didn't come through**: open the run in the Actions tab. The summary
  lists each topic, and the `engine-logs` artifact has the engine's full output.
- **Reddit is thin**: Reddit often blocks requests from GitHub's servers. Adding
  `SCRAPECREATORS_API_KEY` gives the engine a fallback.
- **The app shows an old brief**: pull down won't refresh it; close and reopen the app,
  or wait for the "A new brief is ready" bar.

## Credits

Research engine: [last30days](https://github.com/mvanhorn/last30days-skill) by Matt Van
Horn, MIT licence. Fonts: Literata and Schibsted Grotesk, SIL Open Font License (see
`site/fonts`).
