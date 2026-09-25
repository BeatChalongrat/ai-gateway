"""Charts as PNG for the report and the PDF (the deck uses native charts).

Every chart is drawn from a spec built by content.charts(): the values are
the computed table's own strings. The returned binding records which
computed path each series came from, and the quality gate compares the
plotted values back against the computed table.
"""

from __future__ import annotations

import os
from decimal import Decimal

from .theme import FONT_FILES, mpl


def _setup(theme: dict):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    face = theme["fonts"]["thai"]
    for path in FONT_FILES.get(face, {}).values():
        if os.path.exists(path):
            font_manager.fontManager.addfont(path)
    plt.rcParams.update({
        "font.family": "sans-serif", "font.sans-serif": [face, "DejaVu Sans"], "axes.unicode_minus": False,
        "font.size": 10, "axes.titlesize": 12, "axes.edgecolor": mpl(theme["palette"]["rule"]),
        "axes.labelcolor": mpl(theme["palette"]["muted"]), "xtick.color": mpl(theme["palette"]["muted"]),
        "ytick.color": mpl(theme["palette"]["muted"]), "svg.fonttype": "none",
    })
    return plt


def series_palette(theme: dict) -> list[str]:
    p = theme["palette"]
    return [p["accent"], p["negative"], p["positive"], p["gold"], p["primary"], p["muted"]]


def render_png(spec: dict, theme: dict, path: str, width_in: float = 6.3, height_in: float = 2.9) -> dict:
    plt = _setup(theme)
    p = theme["palette"]
    colors = series_palette(theme)
    scale = Decimal(str(spec.get("scale", 1)))
    fig, ax = plt.subplots(figsize=(width_in, height_in), dpi=200)
    fig.patch.set_facecolor("white")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.grid(axis="y" if spec["kind"] != "barh" else "x", color=mpl(p["rule"]), linewidth=0.6)
    ax.set_axisbelow(True)
    plotted = []
    cats = spec["categories"]
    series_colors = [mpl(p["accent"]), mpl(p["gold"]), mpl(p["positive"])]
    if spec["kind"] == "line" and len(cats) <= 3:
        # Two or three months read better as grouped columns than as a line.
        import numpy as np

        n = len(spec["series"])
        width = 0.8 / n
        x = np.arange(len(cats))
        for i, s in enumerate(spec["series"]):
            vals = [float(Decimal(v) / scale) for v in s["values"]]
            bars = ax.bar(x + (i - (n - 1) / 2) * width, vals, width * 0.92, color=series_colors[i % 3], label=s["name"])
            for b, v in zip(bars, vals):
                ax.annotate(f"{v:,.2f}", (b.get_x() + b.get_width() / 2, v), textcoords="offset points", xytext=(0, 3),
                            ha="center", va="bottom", fontsize=8, color=mpl(p["ink"]))
            plotted.append({"name": s["name"], "values": [str(v) for v in s["values"]], "path": s.get("path")})
        ax.set_xticks(x, cats)
        ax.legend(frameon=False, fontsize=8.5, loc="upper center", bbox_to_anchor=(0.5, -0.1), ncol=3)
        ax.margins(y=0.15)
    elif spec["kind"] == "line":
        for i, s in enumerate(spec["series"]):
            vals = [float(Decimal(v) / scale) for v in s["values"]]
            color = series_colors[i % 3]
            ax.plot(cats, vals, marker="o", linewidth=2.2, markersize=4.5, color=color, label=s["name"])
            ax.annotate(f"{vals[-1]:,.2f}", (len(cats) - 1, vals[-1]), textcoords="offset points", xytext=(6, 0),
                        va="center", fontsize=8.5, color=color)
            plotted.append({"name": s["name"], "values": [str(v) for v in s["values"]], "path": s.get("path")})
        ax.legend(frameon=False, fontsize=8.5, loc="upper center", bbox_to_anchor=(0.5, -0.1), ncol=3)
        ax.set_xlim(-0.2, len(cats) - 0.6)
        ax.set_ylim(bottom=0)
    else:
        s = spec["series"][0]
        vals = [float(Decimal(v) / scale) for v in s["values"]]
        labels = list(reversed(cats))
        vals_r = list(reversed(vals))
        if spec.get("signed"):
            cols = [mpl(p["negative"]) if v > 0 else mpl(p["positive"]) for v in vals_r]
        else:
            cols = [mpl(p["accent"])] * len(vals_r)
        bars = ax.barh(labels, vals_r, color=cols, height=0.62)
        span = max((abs(v) for v in vals_r), default=1) or 1
        for b, v in zip(bars, vals_r):
            txt = f"{v:,.2f}" if scale != 1 else f"{v:+,.0f}" if spec.get("signed") else f"{v:,.0f}"
            # Labels sit right of max(0, v): a negative bar's label never collides with the axis labels.
            ax.annotate(txt, (max(0.0, v), b.get_y() + b.get_height() / 2), textcoords="offset points",
                        xytext=(4, 0), ha="left", va="center", fontsize=8.5, color=mpl(p["ink"]))
        ax.set_xlim(min(0, min(vals_r) * 1.15), max(0, max(vals_r)) + span * 0.24)
        if spec.get("signed"):
            ax.axvline(0, color=mpl(p["muted"]), linewidth=0.8)
        ax.tick_params(axis="y", labelsize=9)
        from matplotlib.ticker import FuncFormatter

        ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x:,.0f}" if scale == 1 else f"{x:,.1f}"))
        plotted.append({"name": s["name"], "values": [str(v) for v in s["values"]], "path": s.get("path")})
    ax.tick_params(axis="both", length=0)
    fig.tight_layout()
    fig.savefig(path, facecolor="white")
    plt.close(fig)
    return {"chart": spec["id"], "file": os.path.basename(path), "kind": spec["kind"], "categories": cats,
            "series": plotted, "cite": spec.get("cite"), "scale": str(scale)}
