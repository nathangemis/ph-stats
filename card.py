"""Render a ph-stats result as a shareable card image.

The design is lifted verbatim from
image_gen/producthunt_yearly_dark_card_portrait.html: same type scale, spacing,
radii, numerals and series colours. Three variation axes only - chart line or
bar, shape square or portrait, theme dark or light.

Rendering shells out to headless Chrome, so there is nothing to pip install.
"""

import html
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
LOGO = os.path.join(HERE, "image_gen", "logo", "ph.svg")
OUTPUT_DIR = os.path.join(HERE, "image_gen", "output")

FONT = ("-apple-system,'SF Pro Display','SF Pro Text',Inter,system-ui,"
        "sans-serif")

PRIMARY = "#FF6B45"
SECONDARY = "#3AB8DE"

THEMES = {
    "dark": {
        "backdrop": "#000000",
        "card": "#1C1C1E",
        "pill_bg": "#2C2C2E",
        "pill_text": "#AEAEB2",
        "label": "#8E8E93",
        "muted": "#636366",
        "divider": "#38383A",
    },
    "light": {
        "backdrop": "#E5E5EA",
        "card": "#FFFFFF",
        "pill_bg": "#F2F2F7",
        "pill_text": "#6C6C70",
        "label": "#6C6C70",
        "muted": "#8E8E93",
        "divider": "#D1D1D6",
    },
}

# Square has far less height to spend, so it runs a smaller type scale and
# abbreviates its figures. Portrait keeps the original card exactly.
SHAPES = {
    "square": {"w": 600, "h": 600, "view": 122, "hero": 40, "second": 28,
               "label": 16, "abbrev": False, "short": True},
    "portrait": {"w": 600, "h": 750, "view": 250, "hero": 64, "second": 42,
                 "label": 19, "abbrev": False, "short": True},
}

# used on the stat rows wherever short is on; the subtitle keeps the full
# words, so the card still says what sub. and feat. stand for
SHORT_LABELS = {"submitted": "sub.", "featured": "feat.",
                "unfeatured": "unfeat."}

# even breathing room between the card and the edge of the exported image;
# the canvas keeps its size, so the card shrinks by twice this
MARGIN = 24

CHART_W = 504.0      # data width inside the -8 -10 520 N viewBox
TOP = 5.7            # y of the maximum value
BOTTOM_PAD = 10.0    # gap between the baseline and the viewBox floor
TENSION = 0.14       # reproduces the reference bezier control points exactly

MAX_AXIS_LABELS = 6
AXIS_CHAR_PX = 9.5   # rough width of a 17px SF digit
AXIS_GUTTER = 20     # clear space demanded between neighbouring labels


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------

def _baseline(view_h):
    return view_h - BOTTOM_PAD - 10.0  # viewBox starts at y = -10


def _scale(values, view_h):
    """Map a value onto the shared y axis. All series use one scale."""
    base = _baseline(view_h)
    top = max(values) if values else 0
    span = base - TOP
    if not top:
        return lambda v: base
    return lambda v: base - (v / top) * span


def _points(values, y):
    """Screen coordinates for one series, using the shared y scale."""
    n = len(values)
    step = CHART_W / (n - 1) if n > 1 else 0.0
    return [(i * step, y(v)) for i, v in enumerate(values)]


def _smooth(points):
    """Catmull-Rom through the points, emitted as cubic beziers."""
    if len(points) < 2:
        return ""
    out = [f"M{points[0][0]:.1f},{points[0][1]:.1f}"]
    for i in range(len(points) - 1):
        p0 = points[i - 1] if i > 0 else points[i]
        p1, p2 = points[i], points[i + 1]
        p3 = points[i + 2] if i + 2 < len(points) else points[i + 1]
        c1 = (p1[0] + TENSION * (p2[0] - p0[0]),
              p1[1] + TENSION * (p2[1] - p0[1]))
        c2 = (p2[0] - TENSION * (p3[0] - p1[0]),
              p2[1] - TENSION * (p3[1] - p1[1]))
        out.append(f"C{c1[0]:.1f},{c1[1]:.1f} {c2[0]:.1f},{c2[1]:.1f} "
                   f"{p2[0]:.1f},{p2[1]:.1f}")
    return " ".join(out)


def _figure(value, abbrev):
    """151,137 or, where space is tight, 151K."""
    if not abbrev or value < 1000:
        return f"{value:,}"
    if value >= 1_000_000:
        text = f"{value / 1_000_000:.1f}M"
    else:
        scaled = value / 1000
        text = f"{scaled:.0f}K" if scaled >= 100 else f"{scaled:.1f}K"
    return text.replace(".0K", "K").replace(".0M", "M")


def _bar(x, y, width, base, radius):
    """A bar with rounded top corners."""
    r = min(radius, width / 2, max(base - y, 0))
    if r <= 0:
        return f"M{x:.1f},{base:.1f} L{x + width:.1f},{base:.1f} Z"
    return (f"M{x:.1f},{base:.1f} L{x:.1f},{y + r:.1f} "
            f"Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} "
            f"L{x + width - r:.1f},{y:.1f} "
            f"Q{x + width:.1f},{y:.1f} {x + width:.1f},{y + r:.1f} "
            f"L{x + width:.1f},{base:.1f} Z")


# --------------------------------------------------------------------------
# chart svg
# --------------------------------------------------------------------------

def _line_chart(series, view_h, theme):
    base = _baseline(view_h)
    parts = [
        '<defs><linearGradient id="phGP" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{PRIMARY}" stop-opacity=".40"/>'
        f'<stop offset="100%" stop-color="{PRIMARY}" stop-opacity="0"/>'
        '</linearGradient></defs>'
    ]

    y_of = _scale([v for _, values, _ in series for v in values], view_h)
    drawn = []
    for index, (_, values, colour) in enumerate(series):
        points = _points(values, y_of)
        path = _smooth(points)
        if index == 0:
            parts.append(
                f'<path fill="url(#phGP)" d="{path} '
                f'L{CHART_W:.0f},{base:.0f} L0,{base:.0f} Z"/>')
        drawn.append((path, points[-1], colour))

    # the flatter series is stroked first so the hero line sits on top
    for path, _, colour in reversed(drawn):
        parts.append(
            f'<path fill="none" stroke="{colour}" stroke-width="6" '
            f'stroke-linecap="round" stroke-linejoin="round" d="{path}"/>')
    for _, (x, y), colour in reversed(drawn):
        parts.append(
            f'<circle cx="{x:.0f}" cy="{y:.1f}" r="8" fill="{colour}" '
            f'stroke="{theme["card"]}" stroke-width="4"/>')
    return "".join(parts)


def _bar_chart(series, view_h, theme):
    base = _baseline(view_h)
    count = len(series[0][1])
    slot = CHART_W / count
    pad = slot * 0.16
    gap = slot * 0.08 if len(series) > 1 else 0.0
    width = (slot - 2 * pad - gap) / len(series)

    y_of = _scale([v for _, values, _ in series for v in values], view_h)
    parts = []
    for index, (_, values, colour) in enumerate(series):
        bars = [
            _bar(i * slot + pad + index * (width + gap), y_of(v), width, base,
                 8.0)
            for i, v in enumerate(values)
        ]
        parts.append(f'<path fill="{colour}" d="{" ".join(bars)}"/>')
    return "".join(parts)


# --------------------------------------------------------------------------
# card html
# --------------------------------------------------------------------------

def _logo():
    """The Product Hunt mark, inlined so rendering needs no file access."""
    with open(LOGO) as handle:
        svg = handle.read()
    svg = svg[svg.index("<svg"):]
    return svg.replace('width="100%" height="100%"',
                       'width="100%" height="100%" style="display:block"', 1)


def _axis(labels, chart, avail):
    """Absolutely positioned so every label sits over its own mark.

    Line points sit on the edges of the plot, bars in the middle of their
    slot, so the two chart types need different anchors.
    """
    n = len(labels)
    # Long labels (2022-Q1, or a full 2026-08-26T07) collide well before six of
    # them fit, so cap on width as well as on count. The end labels sit flush
    # with the edges rather than centred, which pushes them a half-width into
    # their neighbours - hence the 1.5x rather than 1x.
    text_w = max(len(text) for text in labels) * AXIS_CHAR_PX
    limit = max(2, min(MAX_AXIS_LABELS,
                       int(avail / (1.5 * text_w + AXIS_GUTTER)) + 1))
    if n <= limit:
        keep = list(range(n))
    else:
        step = (n - 1) / (limit - 1)
        keep = sorted({round(i * step) for i in range(limit)})

    spans = []
    for i in keep:
        if chart == "bar":
            pct = (i + 0.5) / n * 100
        else:
            pct = 0.0 if n == 1 else i / (n - 1) * 100
        # a centred label close to either end would hang off the card
        if pct <= 6:
            place = "left:0"
        elif pct >= 94:
            place = "right:0"
        else:
            place = f"left:{pct:.4f}%;transform:translateX(-50%)"
        spans.append(f'<span style="position:absolute;{place};white-space:nowrap">'
                     f'{html.escape(labels[i])}</span>')
    return "".join(spans)


def build_html(rows, granularity, topic=None, shape="square", chart="line",
               theme="dark"):
    """Return the full page markup for one card."""
    if shape not in SHAPES:
        raise ValueError(f"unknown shape: {shape}")
    if theme not in THEMES:
        raise ValueError(f"unknown theme: {theme}")
    if chart not in ("line", "bar"):
        raise ValueError(f"unknown chart: {chart}")
    if not rows:
        raise ValueError("no rows to render")

    palette = THEMES[theme]
    spec = SHAPES[shape]
    canvas_w, canvas_h, view_h = spec["w"], spec["h"], spec["view"]
    width, height = canvas_w - 2 * MARGIN, canvas_h - 2 * MARGIN

    keys = [k for k in ("submitted", "featured", "unfeatured") if k in rows[0]]
    colours = [PRIMARY, SECONDARY]
    series = [(k, [row[k] for row in rows], colours[i])
              for i, k in enumerate(keys)]

    labels = [row["period"] for row in rows]
    last = rows[-1]
    partial = bool(last.get("partial"))
    pill = f"{labels[-1]} SO FAR" if partial else labels[-1]

    span = labels[0] if len(labels) == 1 else f"{labels[0]} to {labels[-1]}"
    subtitle = " vs ".join(keys) + ", " + span
    if topic:
        subtitle += f" · {topic}"

    summary = "; ".join(
        f"{key} went from {values[0]:,} in {labels[0]} to {values[-1]:,} "
        f"in {labels[-1]}" if len(labels) > 1
        else f"{key} was {values[-1]:,} in {labels[-1]}"
        for key, values, _ in series
    )

    now = datetime.now(timezone.utc)
    footer = []
    if partial:
        footer.append(f"{labels[-1]} to {now.day} {now:%B}")
    footer.append("source: Product Hunt API")

    stats = [
        f'<div style="margin-top:{24 if i == 0 else 16}px;display:flex;'
        f'align-items:baseline;gap:{8 if i == 0 else 7}px">'
        f'<span style="font-size:{spec["hero"] if i == 0 else spec["second"]}px;'
        f'font-weight:700;'
        f'letter-spacing:{"-.025em" if i == 0 else "-.02em"};color:{colour};'
        f'line-height:1;font-variant-numeric:tabular-nums">'
        f'{_figure(values[-1], spec["abbrev"])}</span>'
        f'<span style="font-size:{spec["label"]}px;color:{palette["label"]}">'
        f'{SHORT_LABELS[key] if spec["short"] else key}</span>'
        '</div>'
        for i, (key, values, colour) in enumerate(series)
    ]

    if len(rows) > 1:
        body = (_bar_chart if chart == "bar" else _line_chart)(
            series, view_h, palette)
        chart_block = (
            '<div style="margin-top:auto;padding-top:40px">'
            f'<svg viewBox="-8 -10 520 {view_h}" '
            'style="width:100%;display:block;overflow:visible" '
            f'aria-hidden="true">{body}</svg>'
            f'<div style="height:1px;background:{palette["divider"]};'
            'margin-top:12px"></div>'
            '<div style="position:relative;height:22px;margin-top:8px;'
            f'font-size:17px;color:{palette["label"]};'
            f'font-variant-numeric:tabular-nums">{_axis(labels, chart, width - 96)}</div>'
            '</div>'
        )
    else:
        chart_block = '<div style="margin-top:auto"></div>'

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{html.escape(pill)}</title>
<style>
  html,body{{margin:0;padding:0;background:{palette["backdrop"]}}}
  body{{width:{canvas_w}px;height:{canvas_h}px;display:flex;
       align-items:center;justify-content:center}}
  *{{box-sizing:border-box}}
</style></head><body>
<h2 style="position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)">{html.escape(summary)}.</h2>

<div style="width:{width}px;height:{height}px;background:{palette["card"]};border-radius:44px;padding:48px;display:flex;flex-direction:column;font-family:{FONT};-webkit-font-smoothing:antialiased">

  <div style="display:flex;align-items:center;gap:16px">
    <div id="ph-avatar" style="width:56px;height:56px;border-radius:50%;overflow:hidden;flex:none">{_logo()}</div>
    <div style="display:flex;flex-direction:column;gap:2px">
      <div style="font-size:15px;font-weight:590;letter-spacing:.04em;color:{palette["label"]}">Product Hunt</div>
      <div style="font-size:15px;color:{palette["muted"]}">{html.escape(subtitle)}</div>
    </div>
  </div>

  <div style="margin-top:32px">
    <span style="display:inline-block;padding:8px 14px 9px;border-radius:999px;background:{palette["pill_bg"]};font-size:13px;font-weight:590;letter-spacing:.05em;line-height:1;color:{palette["pill_text"]}">{html.escape(pill.upper())}</span>
  </div>

  {"".join(stats)}

  {chart_block}

  <div style="margin-top:24px;font-size:13px;color:{palette["muted"]}">{html.escape(" · ".join(footer))}</div>
</div>
</body></html>
"""


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

BROWSERS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
]
BROWSER_NAMES = ["google-chrome", "google-chrome-stable", "chromium",
                 "chromium-browser", "microsoft-edge", "brave-browser",
                 "chrome"]


def find_browser():
    override = os.environ.get("PH_STATS_CHROME")
    if override:
        return override if os.path.exists(override) else None
    for path in BROWSERS:
        if os.path.exists(path):
            return path
    for name in BROWSER_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return None


def default_path(rows, granularity, shape, chart, theme):
    keys = "-".join(k[:3] for k in ("submitted", "featured", "unfeatured")
                    if k in rows[0])
    name = (f"ph-{granularity}-{rows[0]['period']}-{rows[-1]['period']}"
            f"-{keys}-{shape}-{chart}-{theme}.png")
    return os.path.join(OUTPUT_DIR, name.replace(":", "").replace(" ", ""))


def render(markup, out_path, shape, browser, keep_html=None, attempts=3):
    """Screenshot the markup to a PNG at 2x. Returns the output path.

    Chrome launched in quick succession sometimes exits cleanly without
    writing anything, so a failed shot is retried rather than reported.
    """
    width, height = SHAPES[shape]["w"], SHAPES[shape]["h"]
    directory = os.path.dirname(os.path.abspath(out_path))
    if directory:
        os.makedirs(directory, exist_ok=True)

    with tempfile.TemporaryDirectory() as work:
        page = keep_html or os.path.join(work, "card.html")
        with open(page, "w") as handle:
            handle.write(markup)

        for attempt in range(attempts):
            subprocess.run(
                # no --user-data-dir: passing one makes Chrome hang after the
                # screenshot is written instead of exiting
                [browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                 "--force-device-scale-factor=2",
                 f"--window-size={width},{height}",
                 "--virtual-time-budget=2000",
                 f"--screenshot={os.path.abspath(out_path)}",
                 "file://" + os.path.abspath(page)],
                check=True, capture_output=True, timeout=120,
            )
            if os.path.exists(out_path):
                return out_path
            time.sleep(1 + attempt)

    raise RuntimeError(
        f"the browser exited without writing a screenshot after {attempts} tries")
