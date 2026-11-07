"""Charts as PNG, drawn in the document's own look (AM21).

Every chart is drawn from a spec built by content.charts(): the values are the computed table's own
strings. The returned binding records which computed path each series came from, and the quality gate
draws the chart again from the computed numbers and compares the picture byte for byte.

The look is the theme's, never the drawing library's: the series take the theme's chart palette in its
fixed order — colour carries identity only, so a negative value is a sign (a bar to the left of the
zero line, a figure in parentheses), not another hue; there is no boxed plot area and no heavy grid, a
baseline and the faintest horizontal rules only, with recessive axes; up to four series are labelled
directly on the marks instead of a legend; values are put on marks selectively, never on every point; a
negative bar is drawn with an outline so a hairline of a value is still visible; every text is in the
document's text colours and typeface; one axis, never two; and the picture is drawn at 300 dpi, because
these are printed.
"""

from __future__ import annotations

import os
from decimal import Decimal

from .look.tokens import LOOK
from .theme import mpl

DPI = 300
SERIES_LIMIT_FOR_DIRECT_LABELS = 4


def _setup(theme: dict):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    for path in LOOK.font_files.values():
        if os.path.exists(path):
            font_manager.fontManager.addfont(path)
    p = theme["palette"]
    plt.rcParams.update({
        "font.family": "sans-serif", "font.sans-serif": [LOOK.family, "DejaVu Sans"], "axes.unicode_minus": False,
        "font.size": LOOK.pt("docx", "small"), "axes.titlesize": LOOK.pt("docx", "body"),
        "axes.edgecolor": mpl(p["rule"]), "axes.labelcolor": mpl(p["muted"]),
        "xtick.color": mpl(p["muted"]), "ytick.color": mpl(p["muted"]), "text.color": mpl(p["ink"]),
        "svg.fonttype": "none", "figure.facecolor": mpl(p["paper"]), "axes.facecolor": mpl(p["paper"]),
    })
    return plt


def series_palette(theme: dict) -> list[str]:
    """The theme's chart colours in their fixed order (look.json: themes.<id>.chart); never cycled."""
    p = theme["palette"]
    return [p[tok] for tok in theme["chart"]]


def _money(v: float, scale: Decimal) -> str:
    s = f"{abs(v):,.2f}" if scale != 1 else f"{abs(v):,.0f}"
    return f"({s})" if v < 0 else s


def _quiet_axes(ax, p: dict, horizontal: bool) -> None:
    for side in ("top", "right", "left" if not horizontal else "top"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(mpl(p["rule"]))
    ax.spines["bottom"].set_linewidth(0.8)
    if horizontal:
        ax.spines["left"].set_color(mpl(p["rule"]))
        ax.spines["left"].set_linewidth(0.8)
        ax.spines["right"].set_visible(False)
        ax.grid(axis="x", color=mpl(p["rule"]), linewidth=0.5, alpha=0.6)
    else:
        ax.grid(axis="y", color=mpl(p["rule"]), linewidth=0.5, alpha=0.6)
    ax.set_axisbelow(True)
    ax.tick_params(axis="both", length=0, labelsize=LOOK.pt("docx", "small"))


def render_png(spec: dict, theme: dict, path: str, width_in: float = 6.3, height_in: float = 2.9) -> dict:
    plt = _setup(theme)
    p = theme["palette"]
    colors = [mpl(c) for c in series_palette(theme)]
    ink, muted = mpl(p["ink"]), mpl(p["muted"])
    small = LOOK.pt("docx", "small")
    scale = Decimal(str(spec.get("scale", 1)))
    fig, ax = plt.subplots(figsize=(width_in, height_in), dpi=DPI)
    plotted = []
    cats = spec["categories"]
    n_series = len(spec["series"])
    direct = n_series <= SERIES_LIMIT_FOR_DIRECT_LABELS
    if spec["kind"] == "line" and len(cats) <= 3:
        # two or three months read better as grouped columns than as a line
        import numpy as np

        _quiet_axes(ax, p, horizontal=False)
        width = 0.8 / n_series
        x = np.arange(len(cats))
        for i, s in enumerate(spec["series"]):
            vals = [float(Decimal(v) / scale) for v in s["values"]]
            bars = ax.bar(x + (i - (n_series - 1) / 2) * width, vals, width * 0.9, color=colors[i % len(colors)], label=s["name"])
            # values on the latest period only; the series named once, on its first bar
            last = bars[-1]
            ax.annotate(_money(vals[-1], scale), (last.get_x() + last.get_width() / 2, vals[-1]), textcoords="offset points",
                        xytext=(0, 3), ha="center", va="bottom", fontsize=small, color=ink)
            if direct:
                # the series' name over its first bar, in the text colour: a direct label instead of a legend
                first = bars[0]
                ax.annotate(s["name"], (first.get_x() + first.get_width() / 2, vals[0]), textcoords="offset points", xytext=(0, 3),
                            ha="center", va="bottom", fontsize=small, color=muted)
            plotted.append({"name": s["name"], "values": [str(v) for v in s["values"]], "path": s.get("path")})
        ax.set_xticks(x, cats)
        if not direct:
            ax.legend(frameon=False, fontsize=small, loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=min(n_series, 4))
        ax.margins(y=0.18)
    elif spec["kind"] == "line":
        _quiet_axes(ax, p, horizontal=False)
        for i, s in enumerate(spec["series"]):
            vals = [float(Decimal(v) / scale) for v in s["values"]]
            color = colors[i % len(colors)]
            ax.plot(cats, vals, marker="o", linewidth=2.0, markersize=3.5, color=color, label=s["name"])
            # the last value, and the series' name at the line's end: a direct label instead of a legend
            ax.annotate(_money(vals[-1], scale) + (f"  {s['name']}" if direct else ""), (len(cats) - 1, vals[-1]),
                        textcoords="offset points", xytext=(6, 0), va="center", fontsize=small, color=ink)
            plotted.append({"name": s["name"], "values": [str(v) for v in s["values"]], "path": s.get("path")})
        if not direct:
            ax.legend(frameon=False, fontsize=small, loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=4)
        ax.set_xlim(-0.2, len(cats) - 0.6)
        ax.set_ylim(bottom=0)
        ax.margins(x=0.25)
    else:
        s = spec["series"][0]
        vals = [float(Decimal(v) / scale) for v in s["values"]]
        labels = list(reversed(cats))
        vals_r = list(reversed(vals))
        _quiet_axes(ax, p, horizontal=True)
        # one colour for every bar: the sign is the bar's direction and its figure's parentheses, never a hue
        bars = ax.barh(labels, vals_r, color=colors[0], height=0.62, edgecolor=colors[0], linewidth=1.2)
        span = max((abs(v) for v in vals_r), default=1) or 1
        # values on the largest three, the last, and every negative — not on every point
        order = sorted(range(len(vals_r)), key=lambda i: -abs(vals_r[i]))
        marked = set(order[:3]) | {0} | {i for i, v in enumerate(vals_r) if v < 0}
        for i, (b, v) in enumerate(zip(bars, vals_r)):
            if i not in marked:
                continue
            ax.annotate(_money(v, scale), (max(0.0, v), b.get_y() + b.get_height() / 2), textcoords="offset points",
                        xytext=(4, 0), ha="left", va="center", fontsize=small, color=ink)
        ax.set_xlim(min(0, min(vals_r) * 1.15), max(0, max(vals_r)) + span * 0.26)
        if spec.get("signed") or min(vals_r) < 0:
            ax.axvline(0, color=mpl(p["muted"]), linewidth=0.8)
        from matplotlib.ticker import FuncFormatter

        ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: _money(x, scale) if scale == 1 else f"{x:,.1f}"))
        plotted.append({"name": s["name"], "values": [str(v) for v in s["values"]], "path": s.get("path")})
    for lab in ax.get_xticklabels() + ax.get_yticklabels():
        lab.set_color(muted)
    fig.tight_layout()
    fig.savefig(path, facecolor=mpl(p["paper"]), dpi=DPI)
    plt.close(fig)
    return {"chart": spec["id"], "file": os.path.basename(path), "kind": spec["kind"], "categories": cats,
            "series": plotted, "cite": spec.get("cite"), "scale": str(scale), "dpi": DPI}
