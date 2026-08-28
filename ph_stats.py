#!/usr/bin/env python3
"""ph-stats: count Product Hunt submissions and featured launches over time.

Uses the totalCount field on the posts connection, so no pagination is
needed. One request covers a batch of periods via GraphQL aliases, which
keeps a five-year monthly pull well inside the rate limit.

Requires a Product Hunt API token:
    https://www.producthunt.com/v2/oauth/applications
    export PRODUCTHUNT_TOKEN="your_developer_token"

Examples:
    ph_stats.py --from 2022 --to 2026
    ph_stats.py --from 2025 --granularity month --format csv --out ph.csv
    ph_stats.py --from 2024 --to 2026 --granularity quarter
    ph_stats.py --from 2026-01 --to 2026-08 --topic artificial-intelligence
    ph_stats.py --from 2026-08-01 --to 2026-08-28 --granularity day
    ph_stats.py --from 2026-08-28 --to 2026-08-28 --granularity hour
    ph_stats.py --from 2022 --to 2026 --image --image-theme light
"""

import argparse
import csv
import io
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

__version__ = "1.3.0"

ENDPOINT = "https://api.producthunt.com/v2/api/graphql"
ISO = "%Y-%m-%dT%H:%M:%SZ"


# --------------------------------------------------------------------------
# periods
# --------------------------------------------------------------------------

def parse_bound(text, is_end):
    """Parse YYYY, YYYY-MM or YYYY-MM-DD into a UTC datetime.

    For an end bound the value is treated as inclusive, so it is pushed to
    the start of the following period to give a half-open window.
    """
    parts = text.split("-")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        raise argparse.ArgumentTypeError(f"bad date: {text}")

    if len(nums) == 1:
        year = nums[0]
        return datetime(year + 1 if is_end else year, 1, 1, tzinfo=timezone.utc)
    if len(nums) == 2:
        year, month = nums
        if not is_end:
            return datetime(year, month, 1, tzinfo=timezone.utc)
        return (datetime(year + 1, 1, 1, tzinfo=timezone.utc) if month == 12
                else datetime(year, month + 1, 1, tzinfo=timezone.utc))
    if len(nums) == 3:
        day = datetime(nums[0], nums[1], nums[2], tzinfo=timezone.utc)
        return day + timedelta(days=1) if is_end else day

    raise argparse.ArgumentTypeError(f"bad date: {text}")


def add_period(dt, granularity):
    if granularity == "hour":
        return dt + timedelta(hours=1)
    if granularity == "day":
        return dt + timedelta(days=1)
    if granularity == "month":
        return (datetime(dt.year + 1, 1, 1, tzinfo=timezone.utc) if dt.month == 12
                else datetime(dt.year, dt.month + 1, 1, tzinfo=timezone.utc))
    if granularity == "quarter":
        month = dt.month + 3
        return (datetime(dt.year + 1, month - 12, 1, tzinfo=timezone.utc) if month > 12
                else datetime(dt.year, month, 1, tzinfo=timezone.utc))
    return datetime(dt.year + 1, 1, 1, tzinfo=timezone.utc)


def label_for(dt, granularity):
    if granularity == "hour":
        return dt.strftime("%Y-%m-%dT%H")
    if granularity == "day":
        return dt.strftime("%Y-%m-%d")
    if granularity == "month":
        return dt.strftime("%Y-%m")
    if granularity == "quarter":
        return f"{dt.year}-Q{(dt.month - 1) // 3 + 1}"
    return dt.strftime("%Y")


def align(dt, granularity):
    """Snap a start bound down to the beginning of its period."""
    if granularity == "hour":
        return dt.replace(minute=0, second=0, microsecond=0)
    if granularity == "day":
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if granularity == "month":
        return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if granularity == "quarter":
        return dt.replace(month=(dt.month - 1) // 3 * 3 + 1, day=1,
                          hour=0, minute=0, second=0, microsecond=0)
    return dt.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)


def build_periods(start, end, granularity):
    """Half-open windows covering [start, end)."""
    periods = []
    cursor = align(start, granularity)
    while cursor < end:
        nxt = add_period(cursor, granularity)
        periods.append((label_for(cursor, granularity), cursor, nxt))
        cursor = nxt
    return periods


# --------------------------------------------------------------------------
# api
# --------------------------------------------------------------------------

def build_query(batch, mode, topic):
    """Aliased query covering one batch of periods.

    mode 'both' asks for the unfiltered total plus the featured subset.
    'true' and 'false' ask only for that side of the featured filter.
    """
    topic_arg = f', topic: "{topic}"' if topic else ""
    lines = []
    for index, (_, start, end) in enumerate(batch):
        window = (f'postedAfter: "{start.strftime(ISO)}", '
                  f'postedBefore: "{end.strftime(ISO)}"{topic_arg}')
        if mode in ("both", "total"):
            lines.append(f"  b{index}_total: posts({window}) {{ totalCount }}")
        if mode in ("both", "true"):
            lines.append(
                f"  b{index}_feat: posts({window}, featured: true) {{ totalCount }}")
        if mode == "false":
            lines.append(
                f"  b{index}_unfeat: posts({window}, featured: false) {{ totalCount }}")
    return "{\n" + "\n".join(lines) + "\n}"


def request(query, token, retries=3):
    """POST a query. Returns (data, remaining, reset)."""
    payload = json.dumps({"query": query}).encode()
    req = urllib.request.Request(
        ENDPOINT, data=payload, method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": f"ph-stats/{__version__}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            body = json.loads(resp.read().decode())
            remaining = int(resp.headers.get("X-Rate-Limit-Remaining", -1))
            reset = int(resp.headers.get("X-Rate-Limit-Reset", 0))
    except urllib.error.HTTPError as exc:
        if exc.code == 429 and retries > 0:
            wait = int(exc.headers.get("X-Rate-Limit-Reset", 900)) + 5
            warn(f"rate limited, waiting {wait}s")
            time.sleep(wait)
            return request(query, token, retries - 1)
        if exc.code == 401:
            die("token rejected (401). Check PRODUCTHUNT_TOKEN.")
        detail = exc.read().decode()[:400]
        die(f"HTTP {exc.code}: {detail}")
    except urllib.error.URLError as exc:
        if retries > 0:
            warn(f"network error ({exc.reason}), retrying")
            time.sleep(5)
            return request(query, token, retries - 1)
        die(f"network error: {exc.reason}")

    if "errors" in body:
        die("API error:\n" + json.dumps(body["errors"], indent=2))
    return body["data"], remaining, reset


def collect(periods, token, mode, topic, batch_size, floor, verbose):
    rows = []
    now = datetime.now(timezone.utc)
    total_batches = (len(periods) + batch_size - 1) // batch_size

    for n, offset in enumerate(range(0, len(periods), batch_size), start=1):
        batch = periods[offset:offset + batch_size]
        if verbose:
            warn(f"[{n}/{total_batches}] {batch[0][0]} to {batch[-1][0]}")

        data, remaining, reset = request(build_query(batch, mode, topic), token)

        for index, (label, _, end) in enumerate(batch):
            row = {"period": label}
            if mode in ("both", "total"):
                row["submitted"] = data[f"b{index}_total"]["totalCount"]
            if mode in ("both", "true"):
                row["featured"] = data[f"b{index}_feat"]["totalCount"]
            if mode == "false":
                row["unfeatured"] = data[f"b{index}_unfeat"]["totalCount"]
            if mode == "both":
                row["one_in"] = (round(row["submitted"] / row["featured"], 1)
                                 if row["featured"] else None)
            row["partial"] = end > now
            rows.append(row)

        if 0 <= remaining < floor and offset + batch_size < len(periods):
            wait = reset + 5
            warn(f"quota low ({remaining} left), waiting {wait}s")
            time.sleep(wait)

    return rows


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------

def fields_for(rows):
    order = ["period", "submitted", "featured", "unfeatured", "one_in", "partial"]
    present = {key for row in rows for key in row}
    return [key for key in order if key in present]


def render_table(rows, fields):
    display = [
        {f: ("" if row.get(f) is None else
             ("yes" if row.get(f) is True else "" if row.get(f) is False else
              f"{row[f]:,}" if isinstance(row[f], int) else str(row[f])))
         for f in fields}
        for row in rows
    ]
    widths = {f: max(len(f), *(len(d[f]) for d in display)) for f in fields}
    out = ["  ".join(f.rjust(widths[f]) if f != "period" else f.ljust(widths[f])
                     for f in fields)]
    out.append("  ".join("-" * widths[f] for f in fields))
    for d in display:
        out.append("  ".join(d[f].rjust(widths[f]) if f != "period"
                             else d[f].ljust(widths[f]) for f in fields))
    return "\n".join(out)


def render_markdown(rows, fields):
    out = ["| " + " | ".join(fields) + " |",
           "|" + "|".join("---" for _ in fields) + "|"]
    for row in rows:
        cells = []
        for f in fields:
            value = row.get(f)
            if value is None or value is False:
                cells.append("")
            elif value is True:
                cells.append("yes")
            elif isinstance(value, int):
                cells.append(f"{value:,}")
            else:
                cells.append(str(value))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def render_csv(rows, fields):
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({f: ("" if row.get(f) is None else
                             "yes" if row.get(f) is True else
                             "" if row.get(f) is False else row[f])
                         for f in fields})
    return buffer.getvalue().rstrip("\n")


def render(rows, fmt):
    fields = fields_for(rows)
    if fmt == "json":
        return json.dumps(rows, indent=2)
    if fmt == "csv":
        return render_csv(rows, fields)
    if fmt == "markdown":
        return render_markdown(rows, fields)
    return render_table(rows, fields)


# --------------------------------------------------------------------------

def warn(message):
    print(message, file=sys.stderr)


def die(message):
    warn(f"error: {message}")
    sys.exit(1)


def write_image(rows, args):
    """Render the card PNG. Imported lazily so text output never needs it."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import card

    browser = card.find_browser()
    if not browser:
        die("--image needs Chrome, Chromium, Edge or Brave to render the PNG. "
            "Install one, or point PH_STATS_CHROME at the binary.")

    markup = card.build_html(rows, args.granularity, topic=args.topic,
                             shape=args.image_size, chart=args.image_chart,
                             theme=args.image_theme)
    out = args.image if isinstance(args.image, str) else card.default_path(
        rows, args.granularity, args.image_size, args.image_chart,
        args.image_theme)
    try:
        return card.render(markup, out, args.image_size, browser,
                           keep_html=args.image_html)
    except subprocess.CalledProcessError as exc:
        die("the browser failed to render the card:\n"
            + exc.stderr.decode()[:400])
    except (subprocess.TimeoutExpired, RuntimeError, OSError) as exc:
        die(f"could not render the card: {exc}")


def main():
    parser = argparse.ArgumentParser(
        prog="ph-stats",
        description="Count Product Hunt submissions and featured launches over time.",
        epilog=(
            "examples:\n"
            "  ph_stats.py --from 2022 --to 2026\n"
            "  ph_stats.py --from 2025 --granularity month --format csv --out ph.csv\n"
            "  ph_stats.py --from 2024 --to 2026 --granularity quarter\n"
            "  ph_stats.py --from 2026-01 --topic artificial-intelligence\n"
            "  ph_stats.py --from 2026-08-01 --to 2026-08-28 --granularity day\n"
            "  ph_stats.py --from 2026-08-28 --to 2026-08-28 --granularity hour\n"
            "  ph_stats.py --from 2022 --to 2026 --image --image-theme light\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--from", dest="start", required=True, metavar="DATE",
                        help="start of range: YYYY, YYYY-MM or YYYY-MM-DD")
    parser.add_argument("--to", dest="end", metavar="DATE",
                        help="end of range, inclusive (default: today)")
    parser.add_argument("--granularity",
                        choices=["year", "quarter", "month", "day", "hour"],
                        default="year", help="bucket size: year (default), "
                             "quarter, month, day or hour")
    parser.add_argument("--featured", choices=["both", "true", "false"],
                        default="both",
                        help="both: totals plus featured and the ratio; "
                             "true: featured only; false: unfeatured only")
    parser.add_argument("--topic", metavar="SLUG",
                        help="restrict to a topic slug, e.g. artificial-intelligence")
    parser.add_argument("--format", choices=["table", "csv", "json", "markdown"],
                        default="table", help="output format (default: table)")
    parser.add_argument("--out", metavar="FILE", help="write to a file instead of stdout")
    parser.add_argument("--image", nargs="?", const=True, metavar="FILE",
                        help="also render a PNG card for LinkedIn or X; "
                             "names it under image_gen/output/ if no path given")
    parser.add_argument("--image-size", choices=["square", "portrait"],
                        default="square",
                        help="square: 1200x1200, uncropped on both platforms "
                             "(default); portrait: 1200x1500")
    parser.add_argument("--image-chart", choices=["line", "bar"], default="line",
                        help="chart style on the card (default: line)")
    parser.add_argument("--image-theme", choices=["dark", "light"],
                        default="dark", help="card theme (default: dark)")
    parser.add_argument("--image-html", metavar="FILE",
                        help=argparse.SUPPRESS)  # keep the markup for design work
    parser.add_argument("--token", metavar="TOKEN",
                        help="API token (default: $PRODUCTHUNT_TOKEN)")
    parser.add_argument("--batch", type=int, default=12, metavar="N",
                        help="periods per request; lower it if you hit "
                             "complexity errors (default: 12)")
    parser.add_argument("--min-quota", type=int, default=500, metavar="N",
                        help="pause when remaining quota falls below this (default: 500)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the first query and exit, no network call")
    parser.add_argument("--quiet", action="store_true", help="suppress progress output")
    parser.add_argument("--version", action="version", version=f"ph-stats {__version__}")
    args = parser.parse_args()

    if args.batch < 1:
        die("--batch must be at least 1")

    now = datetime.now(timezone.utc)
    try:
        start = parse_bound(args.start, is_end=False)
        end = parse_bound(args.end, is_end=True) if args.end else now
    except (argparse.ArgumentTypeError, ValueError) as exc:
        die(str(exc))

    # never query windows that have not started yet
    end = min(end, now)

    if end <= start:
        die("--to must be after --from")

    periods = build_periods(start, end, args.granularity)
    if not periods:
        die("range covers no complete periods")

    if args.dry_run:
        print(build_query(periods[:args.batch], args.featured, args.topic))
        return

    token = args.token or os.environ.get("PRODUCTHUNT_TOKEN")
    if not token:
        die("no token. Set PRODUCTHUNT_TOKEN or pass --token. "
            "Get one at https://www.producthunt.com/v2/oauth/applications")

    if len(periods) > 400 and not args.quiet:
        batches = (len(periods) + args.batch - 1) // args.batch
        warn(f"note: {len(periods)} buckets in {batches} requests, "
             "this may pause for rate limits")

    rows = collect(periods, token, args.featured, args.topic,
                   args.batch, args.min_quota, not args.quiet)

    output = render(rows, args.format)
    if args.out:
        with open(args.out, "w") as handle:
            handle.write(output + "\n")
        if not args.quiet:
            warn(f"wrote {len(rows)} rows to {args.out}")
    else:
        print(output)

    if args.image:
        path = write_image(rows, args)
        if not args.quiet:
            warn(f"wrote {path}")

    if any(row["partial"] for row in rows) and not args.quiet:
        warn("note: the final period is still in progress")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except BrokenPipeError:
        # downstream closed the pipe, e.g. `ph_stats.py ... | head`
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)