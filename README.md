# ph-stats

Count Product Hunt submissions and featured launches over any time range.

> 👋 If you post one of these charts, a mention would genuinely make my day !
> I'm [Nathan Gemis](https://www.linkedin.com/in/nathan-gemis/) on LinkedIn.
> No obligation, and I'd love to see what you find.

Product Hunt's GraphQL API exposes `totalCount` on the `posts` connection, so
counting submissions needs no pagination at all: one request covers a batch of
periods using GraphQL aliases. Five years of monthly data takes about five
requests and a few seconds, well inside the rate limit.

No dependencies. Python 3.8+. (`--image` additionally needs a
Chrome-family browser installed, but nothing to pip install.)

## Setup

1. Go to https://www.producthunt.com/v2/oauth/applications (you must be signed
   in to Product Hunt for the page to render).
2. Create an application. Client type: **Confidential**. The redirect URI is
   required by the form but never used by this script, so `https://localhost:3000/`
   is fine.
3. On the application's page, generate a **Developer Token**.

```sh
export PRODUCTHUNT_TOKEN="your_developer_token"
```

## Usage

```sh
# yearly totals, 2022 to now
./ph_stats.py --from 2022

# monthly breakdown to a CSV
./ph_stats.py --from 2022 --to 2026 --granularity month --format csv --out ph.csv

# quarterly trend
./ph_stats.py --from 2024 --to 2026 --granularity quarter

# one topic only
./ph_stats.py --from 2026-01 --topic artificial-intelligence

# daily counts for a single month
./ph_stats.py --from 2026-08-01 --to 2026-08-28 --granularity day

# hour by hour across one day
./ph_stats.py --from 2026-08-28 --to 2026-08-28 --granularity hour

# a card to post on LinkedIn or X
./ph_stats.py --from 2022 --to 2026 --image
```

Example output:

```
period  submitted  featured  one_in  partial
------  ---------  --------  ------  -------
2022       29,197    10,473     2.8
2023       40,977    15,035     2.7
2024       43,474     7,270     6.0
2025       83,436     5,213    16.0
2026      151,135     5,004    30.2      yes
```

## Options

| Flag | Description |
|---|---|
| `--from DATE` | Start of range. `YYYY`, `YYYY-MM` or `YYYY-MM-DD`. Required. |
| `--to DATE` | End of range, inclusive. Defaults to today. |
| `--granularity` | `year` (default), `quarter`, `month`, `day` or `hour`. |
| `--featured` | `both` (default) for totals plus featured and the ratio, `true` for featured only, `false` for unfeatured only. |
| `--topic SLUG` | Restrict to a topic, e.g. `artificial-intelligence`. |
| `--format` | `table` (default), `csv`, `json` or `markdown`. |
| `--out FILE` | Write to a file instead of stdout. |
| `--token TOKEN` | Overrides `$PRODUCTHUNT_TOKEN`. |
| `--batch N` | Periods per request, default 12. Lower it if you hit complexity errors. |
| `--min-quota N` | Pause when remaining quota drops below this, default 500. |
| `--dry-run` | Print the generated query and exit. No network call. |
| `--quiet` | Suppress progress and notes on stderr. |
| `--version` | Print the version and exit. |
| `--image [FILE]` | Also render a PNG card. Without a path it names one under `image_gen/output/`. |
| `--image-size` | `square` (default, 1200×1200) or `portrait` (1200×1500). |
| `--image-chart` | `line` (default) or `bar`. |
| `--image-theme` | `dark` (default) or `light`. |

## Images

`--image` renders the result as a card sized for social feeds. It reflects
whatever query you ran — any range, granularity, topic or `--featured` mode —
and varies along three axes: **square or portrait**, **line or bar**, **dark or
light**. Everything else is fixed, so all eight combinations read as the same
card.

```sh
./ph_stats.py --from 2022 --to 2026 --image
./ph_stats.py --from 2025 --granularity quarter --image \
    --image-size portrait --image-chart bar --image-theme light
```

The headline number is the **last** period in the range, with the pill marking
it `SO FAR` while it is still open — so pick a range whose final bucket is the
one you want to lead with.

- **Sizes.** Square (1200×1200) is the default because it is the one ratio both
  LinkedIn and X show uncropped in the feed. Portrait (1200×1500, 4:5) is native
  to LinkedIn but X may crop it to a preview in the timeline. Square leaves far
  less room for the chart — roughly 105px against portrait's 220px — so reach for
  portrait when the trend line is the point rather than the headline number.
- **Stat labels are short** — `sub.` and `feat.` — on both shapes, while the
  figures stay exact. The subtitle still spells out "submitted vs featured", so
  the short forms are never left unexplained. Square additionally runs a smaller
  type scale to claw back chart height. Rounded figures (`151K`, `5K`, `1.2M`)
  are available per shape via the `abbrev` flag in `SHAPES`, currently off.
- **Rendering.** Shells out to headless Chrome, Chromium, Edge or Brave,
  whichever it finds first; `PH_STATS_CHROME` overrides the search. If none is
  installed the command fails rather than writing a half-finished file.
- **Fonts.** SF Pro resolves on macOS. Elsewhere the card falls back to Inter or
  the system UI font, so metrics shift slightly.

## Notes

- **Submitted vs featured.** Anyone can submit a product and it gets a page.
  Only a curated subset is *featured*: shown on the homepage, entered into the
  daily leaderboard, eligible for the newsletter. The `featured` filter keys off
  whether `featuredAt` is set.
- **Partial periods.** The final row is flagged when its window has not closed
  yet. Do not compare a partial year against complete ones without saying so.
- **Hourly granularity is coarser than it looks.** `postedAt` is the launch-day
  boundary, not the moment of submission, so a whole day's posts land in the
  single hour matching midnight Pacific: `T07` under PDT, `T08` under PST. The
  buckets are correct and sum to the daily total, but they will not tell you what
  time of day people actually submit.
- **Timezone.** Every window and label is UTC. With `--granularity hour`, `T00`
  is midnight UTC, not the start of a Product Hunt launch day, which runs on
  Pacific time. Shift the labels yourself when comparing against PH's own daily
  leaderboard.
- **Rate limits.** The GraphQL endpoint allows 6250 complexity points per 15
  minutes. The script reads `X-Rate-Limit-Remaining` from each response and
  pauses when it runs low. Hourly granularity is the quickest way to hundreds of
  buckets (a month is ~744, a year 8,760); daily over several years does it too.
  Past 400 buckets the script prints the bucket and request count before
  starting.
- **Terms.** Product Hunt's API documentation states the API must not be used
  for commercial purposes, and asks you to contact them for business use.

## Licence

MIT
