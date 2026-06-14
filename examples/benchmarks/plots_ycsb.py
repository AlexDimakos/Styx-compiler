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
    "obol":        dict(label="Obol",              color="#5a2a82", marker="o"),
    "handwritten": dict(label="Hand-written Styx", color="#c1492f", marker="s"),
}
# Order in which series are drawn / legended (bottom curve first).
DRAW_ORDER = ["handwritten", "obol"]

# ycsbt_<system>_K<keys>_<offered_tput>.json  (uniform sweep, no zipf token)
FNAME_RE = re.compile(r"^ycsbt_(?P<sys>.+)_K(?P<keys>\d+)_(?P<tput>\d+)\.json$")


def load_results(dirs):
    """Scan ``dirs`` and return {keys: {system: [(tput, p50, p99), ...]}}.

    Each ``(keys, system, tput)`` point is averaged over every directory in
    ``dirs`` that contains a matching file, so partial result sets just yield
    partial curves.
    """
    # (keys, sys, tput) -> [(p50, p99), ...] collected across the given dirs.
    samples = {}
    for d in dirs:
        for path in glob.glob(os.path.join(d, "ycsbt_*_K*_*.json")):
            m = FNAME_RE.match(os.path.basename(path))
            if not m:
                continue
            sysname = m.group("sys")
            if sysname not in SYS:
                continue
            keys = int(m.group("keys"))
            tput = int(m.group("tput"))

            try:
                with open(path) as f:
                    blob = json.load(f)
                lat = blob["latency (ms)"]
                p50 = float(lat["50"])
                p99 = float(lat["99"])
            except (json.JSONDecodeError, KeyError, ValueError, OSError):
                continue

            samples.setdefault((keys, sysname, tput), []).append((p50, p99))

    # Average the runs for each point, then group into per-key-space curves.
    data = {}
    for (keys, sysname, tput), runs in samples.items():
        p50 = sum(r[0] for r in runs) / len(runs)
        p99 = sum(r[1] for r in runs) / len(runs)
        data.setdefault(keys, {}).setdefault(sysname, []).append((tput, p50, p99))

    # Sort each curve by the (offered) input throughput.
    for keys in data:
        for sysname in data[keys]:
            data[keys][sysname].sort(key=lambda r: r[0])
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
    # YCSB spans ~100 .. ~50k txn/s (2-3 decades), so a log throughput axis
    # keeps the low-rate flat region readable instead of crushing it at x=0.
    ax.set_xscale("log")
    ax.set_xlabel("Input throughput (txn/s)", labelpad=8)
    ax.set_title(title)
    if not drawn_any:
        ax.text(0.5, 0.5, "no data yet", transform=ax.transAxes,
                ha="center", va="center", color="0.5", fontsize=10)
    sns.despine(ax=ax)


def _panel_title(letter, keys, n_panels):
    """Label panels; for the standard 2-keyspace sweep, name the regime."""
    if n_panels == 2:
        return f"({letter}) {keys:,} keys"
    return f"({letter}) {keys:,} keys"


def make_figure(data, dpi, out_path):
    global _panel_keys
    _panel_keys = sorted(data)
    keyspaces = _panel_keys
    n = len(keyspaces)

    fig, axes = plt.subplots(1, n, figsize=(4.2 * n, 3.3), sharey=True,
                             squeeze=False)
    axes = axes[0]

    letters = "abcdefghijklmnopqrstuvwxyz"
    for ax, keys in zip(axes, keyspaces):
        draw_panel(ax, data[keys], _panel_title(letters[keyspaces.index(keys)], keys, n))
    axes[0].set_ylabel("Latency (ms)")

    from matplotlib.lines import Line2D
    # System legend: only show systems that appear in at least one panel.
    present = [s for s in DRAW_ORDER
               if any(s in data[keys] for keys in keyspaces)]
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
        make_figure(avg_data, dpi, os.path.join(OUT, "ycsb_results_avg.png"))
    else:
        print("no YCSB results found across",
              ", ".join(os.path.basename(d) for d in dirs))

    # Figure 2: just the current `results` folder.
    if os.path.isdir(RESULTS):
        cur_data = load_results([RESULTS])
        if cur_data:
            make_figure(cur_data, dpi, os.path.join(OUT, "ycsb_results.png"))
        else:
            print("no YCSB results found in", RESULTS)


if __name__ == "__main__":
    main()
