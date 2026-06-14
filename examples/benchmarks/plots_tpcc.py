#!/usr/bin/env python3
import argparse
import glob
import json
import os
import re

import matplotlib.pyplot as plt
import seaborn as sns

HERE = os.path.dirname(__file__)
RESULTS = os.path.join(HERE, "results")
OUT = os.path.join(HERE, "figures")
os.makedirs(OUT, exist_ok=True)


def result_dirs():
    """Return every directory named ``results`` or ``results_run<N>`` etc."""
    dirs = sorted(
        d for d in glob.glob(os.path.join(HERE, "results*"))
        if os.path.isdir(d)
    )
    return dirs

sns.set_theme(
    context="paper",
    style="whitegrid",
    font_scale=1.05,
    rc={
        "figure.dpi": 120,
        "savefig.bbox": "tight",
        "savefig.pad_inches": 0.04,
        "axes.edgecolor": "0.25",
        "grid.linewidth": 0.6,
        "grid.linestyle": "--",
        "lines.linewidth": 1.9,
        "lines.markersize": 6,
    },
)

# Maps the system token used in result filenames -> display name + style.
SYS = {
    "obol_gather":   dict(label="Obol (gather)",     color="#5a2a82", marker="o"),
    "obol_nogather": dict(label="Obol (no gather)",  color="#b58fd6", marker="^"),
    "handwritten":   dict(label="Hand-written Styx", color="#c1492f", marker="s"),
}
# Order in which series are drawn / legended (bottom curve first).
DRAW_ORDER = ["handwritten", "obol_nogather", "obol_gather"]

# tpcc_<system>_W<warehouses>_<target_tput>_ALL.json
FNAME_RE = re.compile(r"^tpcc_(?P<sys>.+)_W(?P<wh>\d+)_(?P<tput>\d+)_ALL\.json$")


def load_results(dirs):
    """Scan ``dirs`` and return {warehouses: {system: [(tput, p50, p99), ...]}}.

    Each ``(warehouses, system, tput)`` point is averaged over every directory
    in ``dirs`` that contains a matching file. Only files that actually exist
    contribute, so partial result sets just yield partial curves.
    """
    # (wh, sys, tput) -> [(p50, p99), ...] collected across the given dirs.
    samples = {}
    for d in dirs:
        for path in glob.glob(os.path.join(d, "tpcc_*_ALL.json")):
            m = FNAME_RE.match(os.path.basename(path))
            if not m:
                continue
            sysname = m.group("sys")
            if sysname not in SYS:
                continue
            wh = int(m.group("wh"))
            tput = int(m.group("tput"))

            try:
                with open(path) as f:
                    blob = json.load(f)
                lat = blob["latency (ms)"]
                p50 = float(lat["50"])
                p99 = float(lat["99"])
            except (json.JSONDecodeError, KeyError, ValueError, OSError):
                continue

            samples.setdefault((wh, sysname, tput), []).append((p50, p99))

    # Average the runs for each point, then group into per-warehouse curves.
    data = {}
    for (wh, sysname, tput), runs in samples.items():
        p50 = sum(r[0] for r in runs) / len(runs)
        p99 = sum(r[1] for r in runs) / len(runs)
        data.setdefault(wh, {}).setdefault(sysname, []).append((tput, p50, p99))

    # Sort each curve by the (offered) input throughput.
    for wh in data:
        for sysname in data[wh]:
            data[wh][sysname].sort(key=lambda r: r[0])
    return data


def draw_panel(ax, panel, title):
    drawn_any = False
    for sysname in DRAW_ORDER:
        rows = panel.get(sysname)
        if not rows:
            continue
        drawn_any = True
        st = SYS[sysname]
        tput = [r[0] for r in rows]
        p50 = [r[1] for r in rows]
        p99 = [r[2] for r in rows]
        ax.plot(tput, p50, color=st["color"], marker=st["marker"], linestyle="-")
        ax.plot(tput, p99, color=st["color"], marker=st["marker"], linestyle="--",
                markerfacecolor="white", markersize=4.5, linewidth=1.3)

    ax.set_yscale("log")
    ax.set_xlim(left=0)
    ax.set_xlabel("Input throughput (txn/s)", labelpad=8)
    ax.set_title(title)
    if not drawn_any:
        ax.text(0.5, 0.5, "no data yet", transform=ax.transAxes,
                ha="center", va="center", color="0.5", fontsize=10)
    sns.despine(ax=ax)


def make_figure(data, dpi, out_path):
    warehouses = sorted(data)
    n = len(warehouses)

    fig, axes = plt.subplots(1, n, figsize=(4.2 * n, 3.3), sharey=True,
                             squeeze=False)
    axes = axes[0]

    letters = "abcdefghijklmnopqrstuvwxyz"
    for ax, wh in zip(axes, warehouses):
        draw_panel(ax, data[wh], f"({letters[warehouses.index(wh)]}) {wh} warehouses")
    axes[0].set_ylabel("Latency (ms)")

    from matplotlib.lines import Line2D
    # System legend: only show systems that appear in at least one panel.
    present = [s for s in DRAW_ORDER
               if any(s in data[wh] for wh in warehouses)]
    handles = [
        Line2D([0], [0], color=SYS[s]["color"], marker=SYS[s]["marker"],
               linestyle="-", label=SYS[s]["label"])
        for s in present
    ]
    style_handles = [
        Line2D([0], [0], color="0.3", linestyle="-", marker="o",
               markersize=4.5, label=r"$p_{50}$"),
        Line2D([0], [0], color="0.3", linestyle="--", marker="o",
               markerfacecolor="white", markersize=4.5, label=r"$p_{99}$"),
    ]
    leg1 = fig.legend(handles=handles, ncol=len(handles) or 1, frameon=False,
                      fontsize=8, loc="upper center", bbox_to_anchor=(0.5, 1.08),
                      columnspacing=1.4, handletextpad=0.5)
    fig.add_artist(leg1)
    fig.legend(handles=style_handles, ncol=2, frameon=False, fontsize=8,
               loc="upper center", bbox_to_anchor=(0.5, 1.005),
               columnspacing=1.4, handletextpad=0.5)

    fig.tight_layout()
    fig.savefig(out_path, dpi=dpi)
    plt.close(fig)
    print("wrote", out_path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hi-res", action="store_true")
    dpi = 300 if ap.parse_args().hi_res else 200

    dirs = result_dirs()
    if not dirs:
        print("no results* directories found in", HERE)
        return

    # Figure 1: averaged across every results* run folder.
    avg_data = load_results(dirs)
    if avg_data:
        make_figure(avg_data, dpi, os.path.join(OUT, "saturation_wh_avg.png"))
    else:
        print("no results found across", ", ".join(os.path.basename(d) for d in dirs))

    # Figure 2: just the current `results` folder.
    if os.path.isdir(RESULTS):
        cur_data = load_results([RESULTS])
        if cur_data:
            make_figure(cur_data, dpi, os.path.join(OUT, "saturation_wh.png"))
        else:
            print("no results found in", RESULTS)


if __name__ == "__main__":
    main()
