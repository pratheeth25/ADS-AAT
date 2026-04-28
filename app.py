# app.py — Streamlit dashboard: ESL vs Traditional Skiplist
#
# ============================================================
# ACADEMIC REFERENCE
# ============================================================
# This dashboard visualises the ESL (Express Skiplist) data structure
# proposed in:
#
#   Na, Y., Koo, B., Park, T., Park, J., & Kim, W.-H. (2023).
#   "ESL: A High-Performance Skiplist with Express Lane."
#   Applied Sciences, 13(17), 9925.
#   DOI: https://doi.org/10.3390/app13179925
#   URL: https://www.mdpi.com/2076-3417/13/17/9925
#
# The traditional skiplist baseline follows:
#   Pugh, W. (1990). "Skip Lists: A Probabilistic Alternative
#   to Balanced Trees." Commun. ACM, 33, 668-676.
#   DOI: https://doi.org/10.1145/78973.78977
#
# MODIFICATIONS from the paper:
#   - COIL uses sorted Python lists with bisect (binary search)
#     instead of contiguous arrays with exponential+linear search.
#   - PDL stores {key, data_pos} position hints into the Data list
#     for range-narrowing; rebuilt after every insert/delete.
#   - Real-time Streamlit visualization is our addition (not in paper).
#   - JSON state persistence (structure.json, op_log.json) for
#     CLI <-> dashboard synchronization is our addition.
# ============================================================

import streamlit as st
import json
import os
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import plotly.io as pio
import bisect
import math
import time as _time
import datetime as _dt
from random import Random
pio.templates.default = "plotly_dark"

_rng = Random()
NUM_COIL_LEVELS = 4

# ── File I/O helpers (module-level — available in all tabs) ──────────────
def _save_struct(s):
    with open("structure.json", "w") as f:
        json.dump(s, f, indent=2)

def _load_struct_disk():
    try:
        with open("structure.json", "r") as f:
            return json.load(f)
    except Exception:
        return None

def _save_oplog(log):
    with open("op_log.json", "w") as f:
        json.dump(log, f)

def _load_oplog():
    try:
        with open("op_log.json", "r") as f:
            return json.load(f)
    except Exception:
        return []

def _save_traverse_log(tlog):
    with open("traverse_logs.json", "w") as f:
        json.dump(tlog, f, indent=2)

def _load_traverse_log():
    try:
        with open("traverse_logs.json", "r") as f:
            return json.load(f)
    except Exception:
        return {"forward": [], "backward": []}

# ── Data-structure functions (module-level — available in all tabs) ───────
def normalize_esl(esl_data):
    """Fix old PDL format (identical to data) and ensure multi-coil layers."""
    pdl = esl_data.get("pdl", [])
    data = esl_data.get("data", [])
    coil = esl_data.get("coil", [])
    while len(coil) < NUM_COIL_LEVELS:
        coil.insert(0, [])
    if pdl and isinstance(pdl[0], (int, float)):
        if sorted(pdl) == sorted(data):
            step = max(1, len(data) // max(1, len(data) // 2))
            sampled = list(dict.fromkeys(data[::step] + ([data[-1]] if data else [])))
            pdl = [{"key": k, "data_pos": bisect.bisect_left(data, k)} for k in sorted(sampled)]
        else:
            pdl = [{"key": k, "data_pos": bisect.bisect_left(data, k)} for k in sorted(pdl)]
    esl_data["pdl"] = pdl
    esl_data["coil"] = coil
    return esl_data

def insert_traditional(levels, key):
    """Insert key into traditional skiplist (array-of-levels representation)."""
    if not levels:
        levels.append([])
    max_level = 0
    while _rng.random() < 0.25 and max_level < 15:
        max_level += 1
    while len(levels) - 1 < max_level:
        levels.insert(0, [])
    for i in range(max_level + 1):
        bisect.insort(levels[len(levels) - 1 - i], key)
    return levels

def insert_esl(esl_data, key):
    """Insert key into ESL with proper PDL/Data separation and multi-coil."""
    data = esl_data["data"]
    pdl = esl_data["pdl"]
    coil = esl_data["coil"]
    bisect.insort(data, key)
    if _rng.random() < 0.4:
        pos = bisect.bisect_left(data, key)
        pdl.append({"key": key, "data_pos": pos})
        pdl.sort(key=lambda x: x["key"])
    n = len(coil)
    # Hierarchical promotion: start from L0 (densest/bottom), roll p=0.25 each level.
    # Stop at the first failure. A key promoted to Lk appears in L0..Lk (all lower levels too).
    # coil[0] = L0 (densest, ~25% of keys), coil[n-1] = Ln-1 (sparsest, ~(0.25^n)% of keys)
    max_lvl = -1
    for i in range(n):
        if _rng.random() < 0.25:
            max_lvl = i
        else:
            break
    if max_lvl >= 0:
        for i in range(max_lvl + 1):
            bisect.insort(coil[i], key)
    for entry in pdl:
        entry["data_pos"] = bisect.bisect_left(data, entry["key"])
    return esl_data

def delete_traditional(levels, key):
    """Remove key from every level of the traditional skiplist."""
    for lvl in levels:
        try:
            lvl.remove(key)
        except ValueError:
            pass
    while len(levels) > 1 and not levels[0]:
        levels.pop(0)
    return levels

def delete_esl(esl_data, key):
    """Remove key from ESL data layer, PDL, and all COIL levels."""
    data = esl_data["data"]
    pdl  = esl_data["pdl"]
    coil = esl_data["coil"]
    try:
        data.remove(key)
    except ValueError:
        pass
    esl_data["pdl"] = [e for e in pdl if e["key"] != key]
    for lvl in coil:
        try:
            lvl.remove(key)
        except ValueError:
            pass
    for entry in esl_data["pdl"]:
        entry["data_pos"] = bisect.bisect_left(data, entry["key"])
    return esl_data

def search_traditional(levels, key):
    """Traditional skiplist search: linear scan at each level (top-down)."""
    comparisons = 0
    steps = 0
    path = []
    for level_idx, lvl in enumerate(levels):
        level_num = len(levels) - 1 - level_idx
        steps += 1
        for i, val in enumerate(lvl):
            comparisons += 1
            if val == key:
                path.append({"level": f"Level {level_num}", "action": f"Found {key} after {i+1} scans", "hit": True})
                return True, comparisons, steps, path
            elif val > key:
                path.append({"level": f"Level {level_num}", "action": f"Scanned {i+1} nodes, drop down"})
                break
        else:
            path.append({"level": f"Level {level_num}", "action": f"Scanned {len(lvl)} nodes, drop down"})
    return False, comparisons, steps, path

def search_esl(esl_data, key):
    """ESL search: binary search with range narrowing through COIL -> PDL -> Data."""
    data = esl_data["data"]
    pdl = esl_data["pdl"]
    coil = esl_data["coil"]
    comparisons = 0
    steps = 0
    path = []
    if not data:
        return False, 0, 0, [{"level": "Data", "action": "Empty", "hit": False}]
    lo, hi = 0, len(data) - 1
    # Traverse COIL from sparsest (highest index) to densest (lowest index = L0)
    for level_idx in range(len(coil) - 1, -1, -1):
        lvl = coil[level_idx]
        level_num = level_idx
        if not lvl:
            continue
        steps += 1
        bs_comps = max(1, math.ceil(math.log2(max(len(lvl), 2))))
        comparisons += bs_comps
        idx = bisect.bisect_left(lvl, key)
        if idx < len(lvl) and lvl[idx] == key:
            path.append({"level": f"COIL L{level_num}", "action": f"HIT {key} ({bs_comps} comps)", "hit": True})
            return True, comparisons, steps, path
        if idx > 0:
            lo = max(lo, bisect.bisect_left(data, lvl[idx - 1]))
        if idx < len(lvl):
            hi = min(hi, bisect.bisect_left(data, lvl[idx]))
        path.append({"level": f"COIL L{level_num}", "action": f"Narrow [{lo},{hi}] ({bs_comps} comps)"})
    if pdl:
        pdl_keys = [e["key"] for e in pdl]
        steps += 1
        bs_comps = max(1, math.ceil(math.log2(max(len(pdl_keys), 2))))
        comparisons += bs_comps
        idx = bisect.bisect_left(pdl_keys, key)
        if idx < len(pdl_keys) and pdl_keys[idx] == key:
            path.append({"level": "PDL", "action": f"HIT {key} ({bs_comps} comps)", "hit": True})
            return True, comparisons, steps, path
        if idx > 0:
            lo = max(lo, bisect.bisect_left(data, pdl_keys[idx - 1]))
        if idx < len(pdl_keys):
            hi = min(hi, bisect.bisect_left(data, pdl_keys[idx]))
        path.append({"level": "PDL", "action": f"Narrow [{lo},{hi}] ({bs_comps} comps)"})
    sub = data[lo:hi + 1]
    n = max(len(sub), 1)
    bs_comps = max(1, math.ceil(math.log2(max(n, 2))))
    comparisons += bs_comps
    steps += 1
    idx = bisect.bisect_left(sub, key)
    if idx < len(sub) and sub[idx] == key:
        path.append({"level": "Data", "action": f"Found at pos {lo + idx} ({bs_comps} comps)", "hit": True})
        return True, comparisons, steps, path
    path.append({"level": "Data", "action": f"Not found ({bs_comps} comps)"})
    return False, comparisons, steps, path

st.set_page_config(page_title="ESL vs Traditional Skiplist", layout="wide")

# ── Sidebar ──────────────────────────────────────────────
st.sidebar.title("Navigation")
page = st.sidebar.radio("Select Tab", ["Performance (3 Scales)", "Structure Visualization", "Thread & BG Logs", "Practical API Demo"])

st.sidebar.markdown("---")
st.sidebar.header("Upload JSON")
bench_upload = st.sidebar.file_uploader("benchmark_results.json", type="json", key="bench")
struct_upload = st.sidebar.file_uploader("structure.json", type="json", key="struct")

# ── Load data ────────────────────────────────────────────
def load_json(path, upload=None):
    if upload is not None:
        return json.load(upload)
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return None

bench = load_json("benchmark_results.json", bench_upload)
struct = load_json("structure.json", struct_upload)

# Color helpers
def better_color(trad_val, esl_val, lower_is_better=True):
    """Return (trad_color, esl_color) - green for winner, red for loser."""
    if lower_is_better:
        return (
            ("background-color: #d4edda" if trad_val <= esl_val else "background-color: #f8d7da"),
            ("background-color: #d4edda" if esl_val <= trad_val else "background-color: #f8d7da"),
        )
    else:
        return (
            ("background-color: #d4edda" if trad_val >= esl_val else "background-color: #f8d7da"),
            ("background-color: #d4edda" if esl_val >= trad_val else "background-color: #f8d7da"),
        )

# ════════════════════════════════════════════════════════════
# TAB 1 - Performance Comparison (3 Scales)
# ============================================================
if page == "Performance (3 Scales)":
    st.title("ESL vs Traditional - Performance at 3 Scales")

    if not bench:
        st.warning("No benchmark_results.json found. Run `benchmark.exe` first.")
        st.stop()

    experiments = bench.get("experiments", None)

    if not experiments:
        st.error("Old benchmark format detected. Re-run `benchmark.exe` to generate 3-scale results.")
        st.stop()

    # ── Summary table across scales ──────────────────────
    st.subheader("Scale Comparison Summary")
    summary_rows = []
    for exp in experiments:
        t = exp["traditional"]
        e = exp["esl"]
        esl_total = e["total_time"]
        trad_total = t["total_time"]
        speedup = trad_total / esl_total if esl_total > 0 else 0
        winner = "ESL" if speedup >= 1 else "Traditional"
        esl_insert = e.get("insert_plus_build_time", e["insert_time"])
        summary_rows.append({
            "Scale": f"{exp['scale']:,}",
            "Description": exp["label"],
            "Trad Insert (s)": f"{t['insert_time']:.5f}",
            "ESL Insert+Build (s)": f"{esl_insert:.5f}",
            "Trad Search (s)": f"{t['search_time']:.5f}",
            "ESL Search (s)": f"{e['search_time']:.5f}",
            "Overall Winner": winner,
            "Speedup": f"{max(speedup, 1/max(speedup, 0.0001)):.2f}x",
        })
    st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)

    # ── Per-scale tabs ────────────────────────────────────
    tab_labels = [f"{exp['scale']:,} ops" for exp in experiments]
    scale_tabs = st.tabs(tab_labels)

    for stab, exp in zip(scale_tabs, experiments):
        with stab:
            trad = exp["traditional"]
            esl_r = exp["esl"]
            esl_insert_total = esl_r.get("insert_plus_build_time", esl_r["insert_time"])

            metrics = [
                ("Insert+Build Time (s)", trad["insert_time"], esl_insert_total, True),
                ("Search Time (s)",       trad["search_time"], esl_r["search_time"], True),
                ("Avg Latency (us)",      trad["avg_search_latency_us"], esl_r["avg_search_latency_us"], True),
                ("Max Latency (us)",      trad["max_search_latency_us"], esl_r["max_search_latency_us"], True),
                ("Search Comparisons",    trad["search_comparisons"],    esl_r["search_comparisons"],    True),
                ("Traversal Steps",       trad["search_traversal_steps"], esl_r["search_traversal_steps"], True),
                ("Throughput (ops/s)",    trad["throughput"],            esl_r["throughput"],            False),
            ]
            rows = []
            for name, tv, ev, lower in metrics:
                winner = "ESL" if (ev < tv if lower else ev > tv) else "Traditional"
                if tv == ev: winner = "Tie"
                rows.append({"Metric": name,
                             "Traditional": f"{tv:,.4f}" if isinstance(tv, float) else f"{tv:,}",
                             "ESL": f"{ev:,.4f}" if isinstance(ev, float) else f"{ev:,}",
                             "Winner": winner})

            def _hl(row):
                s = [""] * len(row)
                if row["Winner"] == "ESL": s[2] = "font-weight: bold"
                elif row["Winner"] == "Traditional": s[1] = "font-weight: bold"
                return s

            st.dataframe(pd.DataFrame(rows).style.apply(_hl, axis=1),
                         use_container_width=True, hide_index=True)

            c1, c2 = st.columns(2)
            with c1:
                fig = go.Figure(data=[
                    go.Bar(name="Traditional", x=["Insert+Build"], y=[trad["insert_time"]], marker_color="#888888"),
                    go.Bar(name="ESL",         x=["Insert+Build"], y=[esl_insert_total],    marker_color="#dddddd"),
                ])
                fig.update_layout(title="Insert Time", barmode="group", height=280)
                st.plotly_chart(fig, use_container_width=True)
            with c2:
                fig = go.Figure(data=[
                    go.Bar(name="Traditional", x=["Search"], y=[trad["search_time"]], marker_color="#888888"),
                    go.Bar(name="ESL",         x=["Search"], y=[esl_r["search_time"]], marker_color="#dddddd"),
                ])
                fig.update_layout(title="Search Time", barmode="group", height=280)
                st.plotly_chart(fig, use_container_width=True)

            if "index_build_time" in esl_r:
                st.caption(
                    f"ESL index build: {esl_r['index_build_time']:.4f}s  |  "
                    f"COIL hit rate: {esl_r['coil_hit_rate']:.1f}%  |  "
                    f"PDL: {esl_r['pdl_size']:,} entries  |  "
                    f"BG ops: {esl_r['bg_ops_processed']:,}"
                )

    # ── Cross-scale charts ────────────────────────────────
    st.markdown("---")
    st.subheader("Cross-Scale: Throughput")
    scales = [f"{e['scale']:,}" for e in experiments]
    fig = go.Figure(data=[
        go.Bar(name="Traditional", x=scales,
               y=[e["traditional"]["throughput"] for e in experiments], marker_color="#888888"),
        go.Bar(name="ESL",         x=scales,
               y=[e["esl"]["throughput"] for e in experiments], marker_color="#dddddd"),
    ])
    fig.update_layout(barmode="group", height=320, xaxis_title="Scale", yaxis_title="ops/sec")
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("Cross-Scale: ESL Efficiency Gain over Traditional")
    comp_reds, step_reds = [], []
    for exp in experiments:
        tc, ec = exp["traditional"]["search_comparisons"], exp["esl"]["search_comparisons"]
        ts2, es = exp["traditional"]["search_traversal_steps"], exp["esl"]["search_traversal_steps"]
        comp_reds.append((1 - ec / tc) * 100 if tc > 0 else 0)
        step_reds.append((1 - es / ts2) * 100 if ts2 > 0 else 0)
    fig = go.Figure(data=[
        go.Bar(name="Comparison Reduction %",    x=scales, y=comp_reds, marker_color="#aaaaaa"),
        go.Bar(name="Traversal Step Reduction %",x=scales, y=step_reds, marker_color="#cccccc"),
    ])
    fig.update_layout(barmode="group", height=300, yaxis_title="% reduction vs Traditional")
    st.plotly_chart(fig, use_container_width=True)


# ════════════════════════════════════════════════════════════
# TAB 2 - Real-Time Structure Visualization
# ════════════════════════════════════════════════════════════
elif page == "Structure Visualization":
    st.title("ESL vs Traditional Skiplist - Real-Time Visualization")

    # ── Session State Initialization ──────────────────────

    if "struct_data" not in st.session_state:
        raw = _load_struct_disk() or struct or {
            "traditional": {"levels": [[]]},
            "esl": {"coil": [[] for _ in range(NUM_COIL_LEVELS)], "pdl": [], "data": []}
        }
        raw["esl"] = normalize_esl(raw.get("esl", {}))
        st.session_state.struct_data = raw

    if "last_inserted" not in st.session_state:
        st.session_state.last_inserted = None
    if "op_log" not in st.session_state:
        st.session_state.op_log = _load_oplog()
    if "struct_mtime" not in st.session_state:
        try:
            st.session_state.struct_mtime = os.path.getmtime("structure.json") if os.path.exists("structure.json") else 0
        except Exception:
            st.session_state.struct_mtime = 0
    if "traverse_log" not in st.session_state:
        st.session_state.traverse_log = _load_traverse_log()

    sd = st.session_state.struct_data

    # ── Auto-sync: pick up CLI changes via file mtime ─────
    try:
        curr_mtime = os.path.getmtime("structure.json") if os.path.exists("structure.json") else 0
    except Exception:
        curr_mtime = 0
    if curr_mtime > st.session_state.struct_mtime + 0.3:
        raw = _load_struct_disk()
        if raw:
            raw["esl"] = normalize_esl(raw.get("esl", {}))
            st.session_state.struct_data = raw
            st.session_state.struct_mtime = curr_mtime
            sd = st.session_state.struct_data
            st.info("Auto-synced: picked up changes from CLI")

    # ── Controls ──────────────────────────────────────────

    ctrl1, ctrl2, ctrl3, ctrl4, ctrl5, ctrl6 = st.columns([2, 1, 2, 1, 2, 1])
    with ctrl1:
        insert_val = st.number_input("Insert Value", min_value=1, max_value=999999, step=1, value=10)
    with ctrl2:
        st.markdown("<br>", unsafe_allow_html=True)
        insert_btn = st.button("Insert", type="primary", use_container_width=True)
    with ctrl3:
        search_val = st.number_input("Search Value", min_value=1, max_value=999999, step=1, value=1)
    with ctrl4:
        st.markdown("<br>", unsafe_allow_html=True)
        search_btn = st.button("Search", use_container_width=True)
    with ctrl5:
        delete_val = st.number_input("Delete Value", min_value=1, max_value=999999, step=1, value=1)
    with ctrl6:
        st.markdown("<br>", unsafe_allow_html=True)
        delete_btn = st.button("Delete", use_container_width=True)

    rb_col, rand_col1, rand_col2, clear_col = st.columns([2, 2, 1, 1])
    with rb_col:
        refresh_btn = st.button("Refresh from Disk", use_container_width=True)
    with rand_col1:
        rand_count = st.number_input("Bulk random count", min_value=10, max_value=1000, value=100, step=10, label_visibility="collapsed")
    with rand_col2:
        rand_btn = st.button(f"Insert {int(rand_count)} Random", use_container_width=True)
    with clear_col:
        clear_btn = st.button("Clear All", use_container_width=True, type="primary")

    # ── Handle Clear All ──────────────────────────────────

    if clear_btn:
        empty = {
            "traditional": {"levels": [[]]},
            "esl": {"coil": [[] for _ in range(NUM_COIL_LEVELS)], "pdl": [], "data": []}
        }
        st.session_state.struct_data = empty
        st.session_state.op_log = []
        st.session_state.last_inserted = None
        _save_struct(empty)
        _save_oplog([])
        sd = empty
        st.success("All structures cleared and JSON files reset.")

    # ── Handle Refresh ────────────────────────────────────

    if refresh_btn:
        raw = _load_struct_disk()
        if raw:
            raw["esl"] = normalize_esl(raw.get("esl", {}))
            st.session_state.struct_data = raw
            st.session_state.struct_mtime = curr_mtime
            sd = raw
            st.success("Reloaded structure.json from disk.")
        else:
            st.error("Could not read structure.json")

    # ── Handle Bulk Random Insert ─────────────────────────

    if rand_btn:
        import random as _rand_mod
        existing = set(sd["esl"].get("data", []))
        available = [k for k in range(1, 100000) if k not in existing]
        new_keys = sorted(_rand_mod.sample(available, min(int(rand_count), len(available))))
        t0 = _time.perf_counter()
        for k in new_keys:
            sd["traditional"]["levels"] = insert_traditional(
                sd["traditional"].get("levels", [[]]), k)
            sd["esl"] = insert_esl(sd["esl"], k)
        _save_struct(sd)
        elapsed = _time.perf_counter() - t0
        st.session_state.last_inserted = new_keys[-1] if new_keys else None
        st.session_state.op_log.append({
            "op": "BULK_INSERT", "key": f"{new_keys[0]}-{new_keys[-1]}",
            "trad_us": "-", "esl_us": round(elapsed * 1e6, 1)
        })
        _save_oplog(st.session_state.op_log)
        st.success(f"Inserted {len(new_keys)} random values in {elapsed*1000:.1f} ms - JSON synced")

    # ── Handle Insert ─────────────────────────────────────

    if insert_btn:
        t0 = _time.perf_counter()
        sd["traditional"]["levels"] = insert_traditional(
            sd["traditional"].get("levels", [[]]), insert_val
        )
        t_trad = _time.perf_counter() - t0

        t0 = _time.perf_counter()
        sd["esl"] = insert_esl(sd["esl"], insert_val)
        t_esl = _time.perf_counter() - t0

        st.session_state.last_inserted = insert_val
        st.session_state.op_log.append({
            "op": "INSERT", "key": insert_val,
            "trad_us": round(t_trad * 1e6, 1), "esl_us": round(t_esl * 1e6, 1)
        })
        _save_struct(sd)
        _save_oplog(st.session_state.op_log)
        st.success(f"Inserted **{insert_val}** - Traditional: {t_trad*1e6:.1f} us | ESL: {t_esl*1e6:.1f} us")
        _ts = _dt.datetime.now().isoformat(timespec='seconds')
        st.session_state.traverse_log["forward"].append({
            "timestamp": _ts, "op": "INSERT", "key": insert_val,
            "esl_path": [
                "Data: bisect insert (O(log n) position, O(n) shift)",
                "PDL: ~40% probabilistic promotion → position hint stored",
                "COIL: hierarchical roll p=0.25/level, inserted into L0..Lmax"
            ],
            "trad_path": ["Level 0..max: bisect insert at each promoted level (p=0.25)"]
        })
        _save_traverse_log(st.session_state.traverse_log)
    # ── Handle Delete ─────────────────────────────────────

    if delete_btn:
        key_in_trad = any(delete_val in lvl for lvl in sd["traditional"].get("levels", []))
        key_in_esl  = delete_val in sd["esl"].get("data", [])

        if not key_in_trad and not key_in_esl:
            st.warning(f"**{delete_val}** not found in either structure — nothing deleted.")
        else:
            t0 = _time.perf_counter()
            sd["traditional"]["levels"] = delete_traditional(
                sd["traditional"].get("levels", [[]]), delete_val
            )
            t_trad = _time.perf_counter() - t0

            t0 = _time.perf_counter()
            sd["esl"] = delete_esl(sd["esl"], delete_val)
            t_esl = _time.perf_counter() - t0

            st.session_state.op_log.append({
                "op": "DELETE", "key": delete_val,
                "trad_us": round(t_trad * 1e6, 1), "esl_us": round(t_esl * 1e6, 1)
            })
            _save_struct(sd)
            _save_oplog(st.session_state.op_log)
            st.success(f"Deleted **{delete_val}** - Traditional: {t_trad*1e6:.1f} us | ESL: {t_esl*1e6:.1f} us")
            _ts = _dt.datetime.now().isoformat(timespec='seconds')
            st.session_state.traverse_log["backward"].append({
                "timestamp": _ts, "op": "DELETE", "key": delete_val,
                "esl_path": [
                    "Data: remove key, shift tail, rebuild PDL positions",
                    "PDL: filter out deleted key entry",
                    "COIL L0..L3: scan and remove from each level"
                ],
                "trad_path": ["All levels: scan and remove key, trim empty top levels"]
            })
            _save_traverse_log(st.session_state.traverse_log)
    # ── Handle Search ─────────────────────────────────────

    if search_btn:
        t0 = _time.perf_counter()
        t_found, t_comp, t_steps, t_path = search_traditional(
            sd["traditional"].get("levels", []), search_val
        )
        t_trad = _time.perf_counter() - t0

        t0 = _time.perf_counter()
        e_found, e_comp, e_steps, e_path = search_esl(sd["esl"], search_val)
        t_esl = _time.perf_counter() - t0

        st.session_state.op_log.append({
            "op": "SEARCH", "key": search_val,
            "trad_us": round(t_trad * 1e6, 1), "esl_us": round(t_esl * 1e6, 1),
            "trad_comp": t_comp, "esl_comp": e_comp
        })
        _save_oplog(st.session_state.op_log)
        _ts = _dt.datetime.now().isoformat(timespec='seconds')
        st.session_state.traverse_log["forward"].append({
            "timestamp": _ts, "op": "SEARCH", "key": search_val, "found": e_found,
            "esl_path": [f"{p['level']}: {p['action']}" for p in e_path],
            "trad_path": [f"{p['level']}: {p['action']}" for p in t_path]
        })
        _save_traverse_log(st.session_state.traverse_log)

        st.markdown("---")
        st.subheader("Search Results")
        sr1, sr2 = st.columns(2)
        with sr1:
            status = "Found" if t_found else "Not found"
            st.markdown(f"**Traditional**: {status} - **{t_comp}** comparisons, **{t_steps}** steps")
            for p in t_path:
                marker = "✓" if p.get("hit") else "›"
                st.markdown(f"{marker} **{p['level']}**: {p['action']}")
        with sr2:
            status = "Found" if e_found else "Not found"
            st.markdown(f"**ESL**: {status} - **{e_comp}** comparisons, **{e_steps}** steps")
            for p in e_path:
                marker = "✓" if p.get("hit") else "›"
                st.markdown(f"{marker} **{p['level']}**: {p['action']}")
        if t_comp > 0 and e_comp > 0:
            ratio = t_comp / e_comp
            if ratio > 1:
                st.success(f"ESL used **{ratio:.1f}x fewer** comparisons than Traditional!")
            elif ratio < 1:
                st.info(f"Traditional used **{1/ratio:.1f}x fewer** comparisons (small dataset - ESL excels at scale).")
            else:
                st.info("Both used the same number of comparisons.")

    # ── Visualization ─────────────────────────────────────

    st.markdown("---")
    last_key = st.session_state.last_inserted
    col_trad, col_esl = st.columns(2)

    # ── Traditional ──────────────────────────────────────
    with col_trad:
        st.subheader("Traditional Skiplist")
        trad_levels = sd.get("traditional", {}).get("levels", [])

        if not trad_levels:
            st.info("No traditional skiplist data.")
        else:
            max_display = 40
            for idx, lvl in enumerate(trad_levels):
                level_num = len(trad_levels) - 1 - idx
                display = lvl[:max_display]
                suffix = f" ... (+{len(lvl)-max_display})" if len(lvl) > max_display else ""
                st.markdown(f"**Level {level_num}** ({len(lvl)} nodes)")
                st.code("HEAD -> " + " -> ".join(str(x) for x in display) + suffix + " -> NULL",
                        language=None)

            fig = go.Figure()
            for idx, lvl in enumerate(trad_levels):
                level_num = len(trad_levels) - 1 - idx
                display = lvl[:80]
                fig.add_trace(go.Scatter(
                    x=display,
                    y=[level_num] * len(display),
                    mode="markers",
                    name=f"Level {level_num}",
                    marker=dict(size=6, color="#aaaaaa"),
                    hovertemplate="Key: %{x}<br>Level: %{y}<extra></extra>"
                ))

            fig.update_layout(
                title="Traditional Skiplist Levels",
                xaxis_title="Key", yaxis_title="Level",
                yaxis=dict(dtick=1), height=400, showlegend=True
            )
            st.plotly_chart(fig, use_container_width=True)

    # ── ESL ──────────────────────────────────────────────
    with col_esl:
        st.subheader("ESL (Express Skiplist)")
        esl_data = sd.get("esl", {})
        coil = esl_data.get("coil", [])
        pdl = esl_data.get("pdl", [])
        data = esl_data.get("data", [])

        max_display = 40

        # COIL levels — display from sparsest (highest index) to densest (L0)
        st.markdown("**COIL (Cache-Optimized Index Levels):**")
        for level_num in range(len(coil) - 1, -1, -1):
            lvl = coil[level_num]
            display = lvl[:max_display]
            suffix = f" ... (+{len(lvl)-max_display})" if len(lvl) > max_display else ""
            st.markdown(f"**COIL Level {level_num}** ({len(lvl)} entries)")
            if lvl:
                st.code(" | ".join(str(x) for x in display) + suffix, language=None)
            else:
                st.code("(empty)", language=None)

        # PDL - shown as index with position references
        pdl_keys = [e["key"] for e in pdl] if pdl and isinstance(pdl[0], dict) else pdl
        st.markdown(f"**PDL (Position Description Layer)** ({len(pdl)} index entries)")
        if pdl and isinstance(pdl[0], dict):
            display = pdl[:max_display]
            parts = [f"{e['key']} (pos {e['data_pos']})" for e in display]
            suffix = f" ... (+{len(pdl)-max_display})" if len(pdl) > max_display else ""
            st.code(" -> ".join(parts) + suffix, language=None)
        elif pdl:
            st.code(" -> ".join(str(x) for x in pdl[:max_display]), language=None)
        else:
            st.code("(empty)", language=None)

        # Data Layer
        st.markdown(f"**Data Layer** ({len(data)} values)")
        display = data[:max_display]
        suffix = f" ... (+{len(data)-max_display})" if len(data) > max_display else ""
        if data:
            st.code(" -> ".join(str(x) for x in display) + suffix, language=None)
        else:
            st.code("(empty)", language=None)

        # PDL vs Data distinction callout
        if data and pdl:
            st.caption(f"PDL indexes {len(pdl)}/{len(data)} data entries ({100*len(pdl)/len(data):.0f}% coverage)")

        # Visual plot with highlight
        fig = go.Figure()

        # Data level at y=0
        if data:
            d_disp = data[:80]
            fig.add_trace(go.Scatter(
                x=d_disp, y=[0] * len(d_disp),
                mode="markers", name="Data Layer",
                marker=dict(size=5, color="#aaaaaa"),
                hovertemplate="Key: %{x}<extra>Data Layer</extra>"
            ))

        # PDL at y=1
        if pdl_keys:
            p_disp = pdl_keys[:80]
            fig.add_trace(go.Scatter(
                x=p_disp, y=[1] * len(p_disp),
                mode="markers", name="PDL (Index)",
                marker=dict(size=7, color="#cccccc", symbol="diamond"),
                hovertemplate="Key: %{x}<extra>PDL Index</extra>"
            ))

        # COIL levels at y=2,3,... — L0 (densest) at y=2, Ln-1 (sparsest) at top
        for level_num in range(len(coil)):
            lvl = coil[level_num]
            if lvl:
                c_disp = lvl[:80]
                fig.add_trace(go.Scatter(
                    x=c_disp, y=[level_num + 2] * len(c_disp),
                    mode="markers", name=f"COIL L{level_num}",
                    marker=dict(size=8, color="#eeeeee"),
                    hovertemplate="Key: %{x}<extra>COIL L" + str(level_num) + "</extra>"
                ))

        max_y = 2 + len(coil)
        tick_labels = ["Data", "PDL"] + [f"COIL L{i}" for i in range(len(coil))]
        fig.update_layout(
            title="ESL Structure",
            xaxis_title="Key", yaxis_title="Layer",
            yaxis=dict(tickvals=list(range(max_y)), ticktext=tick_labels, dtick=1),
            height=400, showlegend=True
        )
        st.plotly_chart(fig, use_container_width=True)

    # ── Size Comparison ──────────────────────────────────
    st.markdown("---")
    st.subheader("Size Comparison")

    trad_levels = sd.get("traditional", {}).get("levels", [])
    trad_total = sum(len(l) for l in trad_levels)
    pdl_count = len(pdl)
    esl_total = len(data) + pdl_count + sum(len(l) for l in coil)

    sc1, sc2 = st.columns(2)
    sc1.metric("Traditional Total Entries (across all levels)", f"{trad_total:,}")
    sc2.metric("ESL Total Entries (Data + PDL + COIL)", f"{esl_total:,}")

    fig = go.Figure(data=[
        go.Bar(name="Traditional", x=["Total Index Entries"], y=[trad_total], marker_color="#888888"),
        go.Bar(name="ESL", x=["Total Index Entries"], y=[esl_total], marker_color="#dddddd"),
    ])
    fig.update_layout(barmode="group", height=300, title="Total Entries Comparison")
    st.plotly_chart(fig, use_container_width=True)

    # ── Layer Breakdown ──────────────────────────────────
    st.subheader("ESL Layer Breakdown")
    layer_names = [f"COIL L{len(coil)-1-i}" for i in range(len(coil))] + ["PDL", "Data"]
    layer_sizes = [len(l) for l in coil] + [pdl_count, len(data)]
    fig = go.Figure(data=[
        go.Bar(x=layer_names, y=layer_sizes, marker_color="#aaaaaa")
    ])
    fig.update_layout(title="Entries per ESL Layer", height=300)
    st.plotly_chart(fig, use_container_width=True)

    # ── Operation Log ─────────────────────────────────────
    if st.session_state.op_log:
        st.markdown("---")
        st.subheader("Operation Log")
        log_df = pd.DataFrame(st.session_state.op_log)
        st.dataframe(log_df, use_container_width=True, hide_index=True)


# ════════════════════════════════════════════════════════════
# TAB 3 - Thread & BG Activity Logs
# ============================================================
elif page == "Thread & BG Logs":
    st.title("Thread & Background Indexer Activity")
    st.caption("Shows ESL lock-free architecture, index build stats, and live operation history")

    # ── Ensure session state has traverse_log ──────────────
    if "traverse_log" not in st.session_state:
        st.session_state.traverse_log = _load_traverse_log()
    if "op_log" not in st.session_state:
        st.session_state.op_log = _load_oplog()

    # ── ROWEX model ───────────────────────────────────────
    st.subheader("ROWEX Concurrency Model")
    st.markdown("""
| Thread | Role |
|---|---|
| **Main thread** | Insert: O(1) CAS-push to lock-free Treiber stacks (Data + PDL). No mutex, no queue. |
| **BG thread** | Waits idle until `waitForBG()` signals it to stop. Does not touch Data or PDL. |
| **Readers** | Lock-free search on immutable snapshots after `waitForBG()` — no read locks (ROWEX) |

> **waitForBG()** signals BG thread to exit, then builds sorted snapshots from the Treiber
> stacks in O(n log n) once. COIL is built from the sorted Data snapshot in O(n) — already in
> order so no extra sort needed. This is far cheaper than per-insert sorted insertion (O(n²)).

**Insert hot path (zero mutex):**
```
dataList.push(key)        // CAS-prepend to Data Treiber stack — O(1)
pdlList.push(key)         // CAS-prepend to PDL Treiber stack  — O(1), ~40% of keys
insertCount.fetch_add(1)  // atomic increment                  — O(1)
```
    """)

    # ── Forward traversal logs — LIVE from session state ──
    st.subheader("Forward Traversal Logs")
    st.caption("Recorded from INSERT and SEARCH operations — forward path: COIL → PDL → Data")
    _tlog = st.session_state.traverse_log  # live session state, not disk
    _fwd_logs = _tlog.get("forward", [])
    if _fwd_logs:
        _fwd_rows = []
        for _e in reversed(_fwd_logs[-100:]):
            _fwd_rows.append({
                "Timestamp":        _e.get("timestamp", ""),
                "Op":               _e.get("op", ""),
                "Key":              _e.get("key", ""),
                "Found":            _e.get("found", "—"),
                "ESL Path":         " → ".join(_e.get("esl_path", [])),
                "Traditional Path": " → ".join(_e.get("trad_path", [])),
            })
        st.dataframe(pd.DataFrame(_fwd_rows), use_container_width=True, hide_index=True)
    else:
        st.info("No forward logs yet. Use INSERT or SEARCH in the Structure Visualization tab.")

    # BG stats from benchmark
    if bench and bench.get("experiments"):
        st.subheader("Index Build Stats per Scale")
        stats_rows = []
        for exp in bench["experiments"]:
            e = exp["esl"]
            stats_rows.append({
                "Scale": f"{exp['scale']:,}",
                "Inserts": f"{e['bg_ops_processed']:,}",
                "Index Build Time (s)": f"{e.get('index_build_time', 0):.4f}",
                "COIL Hit Rate (%)": f"{e['coil_hit_rate']:.1f}",
                "COIL Levels": e.get('coil_levels', '—'),
                "COIL Sizes": str(e.get("coil_sizes", [])),
                "PDL Entries": f"{e['pdl_size']:,}",
            })
        st.dataframe(pd.DataFrame(stats_rows), use_container_width=True, hide_index=True)

        # Queue depth chart
        exp_100k = next((e for e in bench["experiments"] if e["scale"] == 100000), None)
        if exp_100k:
            esl_100k = exp_100k["esl"]
            cs = esl_100k.get("coil_sizes", [])
            if cs:
                fig = go.Figure(data=[
                    go.Bar(x=[f"COIL L{i}" for i in range(len(cs))], y=cs,
                           marker_color="#aaaaaa", name="COIL sizes")
                ])
                fig.add_hline(y=esl_100k["pdl_size"], line_dash="dash",
                              annotation_text=f"PDL ({esl_100k['pdl_size']:,})")
                fig.update_layout(title="Index Structure Sizes at 100K scale", height=300)
                st.plotly_chart(fig, use_container_width=True)

    # ── Backward traversal logs — LIVE from session state ─
    st.subheader("Backward Traversal Logs")
    st.caption("Recorded from DELETE operations — reverse removal path across all levels")
    _bwd_logs = st.session_state.traverse_log.get("backward", [])
    if _bwd_logs:
        _bwd_rows = []
        for _e in reversed(_bwd_logs[-100:]):
            _bwd_rows.append({
                "Timestamp":                _e.get("timestamp", ""),
                "Op":                       _e.get("op", ""),
                "Key":                      _e.get("key", ""),
                "ESL Removal Path":         " → ".join(_e.get("esl_path", [])),
                "Traditional Removal Path": " → ".join(_e.get("trad_path", [])),
            })
        st.dataframe(pd.DataFrame(_bwd_rows), use_container_width=True, hide_index=True)
    else:
        st.info("No backward logs yet. DELETE values in the Structure Visualization tab.")

    # Real-time operation log
    if st.session_state.get("op_log"):
        st.subheader("Real-Time Operation Log (from Structure Visualization tab)")
        log_df = pd.DataFrame(st.session_state.op_log)
        st.dataframe(log_df, use_container_width=True, hide_index=True)

        op_types = log_df["op"].value_counts()
        fig = go.Figure(data=[go.Bar(x=op_types.index.tolist(), y=op_types.values.tolist(),
                                     marker_color="#aaaaaa")])
        fig.update_layout(title="Operation Type Distribution", height=250)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Insert or search some values in the Structure Visualization tab to see the operation log here.")


# ════════════════════════════════════════════════════════════
# TAB 4 - Live Crypto Price Index (CoinGecko API + ESL)
# ════════════════════════════════════════════════════════════
elif page == "Practical API Demo":
    import urllib.request as _urllib
    import urllib.error as _urlerr

    st.title("Live Crypto Price Index — ESL in Practice")
    st.caption(
        "Fetches real-time cryptocurrency prices from CoinGecko (free, no API key). "
        "Prices are indexed into the ESL structure and Traditional Skiplist for instant lookup."
    )

    # ── Fetch helper ──────────────────────────────────────
    _COINGECKO_URL = (
        "https://api.coingecko.com/api/v3/coins/markets"
        "?vs_currency=usd&order=market_cap_desc&per_page=50&page=1"
    )

    def _fetch_coins():
        """Fetch top-50 coins from CoinGecko. Returns list of dicts or None on error."""
        try:
            req = _urllib.Request(_COINGECKO_URL, headers={"User-Agent": "ESL-Demo/1.0"})
            with _urllib.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode())
        except Exception as _e:
            return None

    # ── Session state ─────────────────────────────────────
    if "cg_coins" not in st.session_state:
        st.session_state.cg_coins = None
    if "cg_esl" not in st.session_state:
        st.session_state.cg_esl = {"coil": [[] for _ in range(NUM_COIL_LEVELS)], "pdl": [], "data": []}
    if "cg_trad" not in st.session_state:
        st.session_state.cg_trad = {"levels": [[]]}
    if "cg_price_map" not in st.session_state:
        st.session_state.cg_price_map = {}   # cents → [coin_name, ...]
    if "cg_fetched_at" not in st.session_state:
        st.session_state.cg_fetched_at = ""

    # ── Fetch button ──────────────────────────────────────
    fc1, fc2 = st.columns([1, 4])
    with fc1:
        fetch_btn = st.button("Fetch Live Prices", type="primary", use_container_width=True)
    with fc2:
        if st.session_state.cg_fetched_at:
            st.caption(f"Last fetched: {st.session_state.cg_fetched_at}  |  "
                       f"{len(st.session_state.cg_coins or [])} coins loaded  |  "
                       f"ESL data: {len(st.session_state.cg_esl['data'])} keys")

    if fetch_btn:
        with st.spinner("Fetching prices from CoinGecko…"):
            _coins = _fetch_coins()
        if _coins is None:
            st.error("Could not reach CoinGecko API. Check your internet connection and try again.")
        else:
            # Build ESL & Traditional from fresh data
            # ESL Data layer stores: integer price key (price_cents = round(price_usd * 100))
            # price_map stores full metadata: cents -> [{name, symbol, price_usd, change_24h, market_cap}]
            _new_esl  = {"coil": [[] for _ in range(NUM_COIL_LEVELS)], "pdl": [], "data": []}
            _new_trad = {"levels": [[]]}
            _price_map = {}   # cents -> list of full coin dicts
            for _c in _coins:
                _raw = _c.get("current_price") or 0
                _cents = max(1, round(_raw * 100))   # ESL key: integer cents
                _new_esl  = insert_esl(_new_esl, _cents)
                _new_trad["levels"] = insert_traditional(_new_trad["levels"], _cents)
                _price_map.setdefault(_cents, []).append({
                    "name":        _c.get("name", ""),
                    "symbol":      _c.get("symbol", "").upper(),
                    "price_usd":   _raw,
                    "change_24h":  round(_c.get("price_change_percentage_24h") or 0, 2),
                    "market_cap":  _c.get("market_cap", 0),
                })
            st.session_state.cg_coins      = _coins
            st.session_state.cg_esl        = _new_esl
            st.session_state.cg_trad       = _new_trad
            st.session_state.cg_price_map  = _price_map
            st.session_state.cg_fetched_at = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            st.success(f"Indexed {len(_coins)} coins into ESL and Traditional Skiplist.")

    if not st.session_state.cg_coins:
        st.info("Click **Fetch Live Prices** to load data from CoinGecko.")
        st.stop()

    _coins     = st.session_state.cg_coins
    _cg_esl    = st.session_state.cg_esl
    _cg_trad   = st.session_state.cg_trad
    _price_map = st.session_state.cg_price_map

    # ── What ESL stores — design callout ────────────────
    st.markdown("""
> **What each ESL node stores:**  
> The ESL **Data Layer** holds one **integer key per coin** — `price_cents = round(price_usd × 100)`.  
> For example, Bitcoin at $94,213.00 → key `9421300`.  
> **PDL** entries add a `{key, data_pos}` position hint (~40% of keys promoted).  
> **COIL** levels hold the same integer key at higher sparsity levels for fast range narrowing.  
> Full coin metadata (name, symbol, 24h change, market cap) is kept in a **separate lookup map** keyed by cents — the ESL index stays pure integers for O(log n) binary search.
    """)

    # ── Live price table ──────────────────────────────────
    st.markdown("---")
    st.subheader("Live Coin Prices")
    _rows = []
    for _c in _coins:
        _rows.append({
            "Rank":       _c.get("market_cap_rank", ""),
            "Coin":       _c.get("name", ""),
            "Symbol":     _c.get("symbol", "").upper(),
            "Price (USD)": f"${_c.get('current_price', 0):,.4f}",
            "24h Change %": round(_c.get("price_change_percentage_24h") or 0, 2),
            "Market Cap":  f"${_c.get('market_cap', 0):,.0f}",
        })
    _df = pd.DataFrame(_rows)
    st.dataframe(_df, use_container_width=True, hide_index=True)

    # ── ESL index state ───────────────────────────────────
    st.markdown("---")
    st.subheader("ESL Index State")
    st.caption(
        "Each key in the ESL Data Layer = `round(price_usd × 100)` (integer cents). "
        "PDL entries = {key, data_pos}. COIL entries = same integer key at higher levels."
    )
    si1, si2, si3, si4 = st.columns(4)
    si1.metric("Data Layer keys", len(_cg_esl["data"]))
    si2.metric("PDL index entries", len(_cg_esl["pdl"]))
    si3.metric("COIL L0 (densest)", len(_cg_esl["coil"][-1]))
    si4.metric("COIL L3 (sparsest)", len(_cg_esl["coil"][0]))

    _layer_names = [f"COIL L{len(_cg_esl['coil'])-1-i}" for i in range(len(_cg_esl["coil"]))] + ["PDL", "Data"]
    _layer_sizes = [len(l) for l in _cg_esl["coil"]] + [len(_cg_esl["pdl"]), len(_cg_esl["data"])]
    fig = go.Figure(data=[go.Bar(x=_layer_names, y=_layer_sizes, marker_color="#aaaaaa")])
    fig.update_layout(title="ESL Layer Sizes (price keys indexed)", height=260,
                      xaxis_title="Layer", yaxis_title="Keys")
    st.plotly_chart(fig, use_container_width=True)

    # ── ESL node contents: sorted data keys with metadata ─
    st.markdown("**ESL Data Layer — stored keys with resolved metadata**")
    _node_rows = []
    for _k in sorted(_price_map.keys()):
        for _m in _price_map[_k]:
            _node_rows.append({
                "ESL Key (cents)": _k,
                "Price (USD)":     f"${_m['price_usd']:,.4f}",
                "Coin":            _m["name"],
                "Symbol":          _m["symbol"],
                "24h Change %":    _m["change_24h"],
                "Market Cap":      f"${_m['market_cap']:,.0f}",
            })
    if _node_rows:
        st.dataframe(pd.DataFrame(_node_rows), use_container_width=True, hide_index=True)

    # ── Price search ──────────────────────────────────────
    st.markdown("---")
    st.subheader("Search a Price in the ESL Index")
    st.caption("Enter a USD price. It will be converted to integer cents and looked up in both structures.")

    ps1, ps2, ps3 = st.columns([2, 1, 1])
    with ps1:
        _search_price = st.number_input("Price (USD)", min_value=0.01, max_value=500000.0,
                                        step=0.01, value=float(_coins[0].get("current_price", 1.0) or 1.0),
                                        format="%.4f")
    with ps2:
        # Quick-pick: pick a coin's exact price
        _coin_names = [_c["name"] for _c in _coins]
        _pick_coin  = st.selectbox("Quick-pick coin price", options=["—"] + _coin_names)
    with ps3:
        st.markdown("<br>", unsafe_allow_html=True)
        _search_btn = st.button("Search", type="primary", use_container_width=True)

    if _pick_coin != "—":
        _picked = next((_c for _c in _coins if _c["name"] == _pick_coin), None)
        if _picked and _picked.get("current_price"):
            _search_price = float(_picked["current_price"])

    if _search_btn:
        _key = max(1, round(_search_price * 100))
        _t0  = _time.perf_counter()
        _tf, _tc, _ts, _tp = search_traditional(_cg_trad["levels"], _key)
        _t_trad = _time.perf_counter() - _t0
        _t0  = _time.perf_counter()
        _ef, _ec, _es, _ep = search_esl(_cg_esl, _key)
        _t_esl = _time.perf_counter() - _t0

        _matched = _price_map.get(_key, [])
        _label   = f"${_search_price:,.4f}  →  ESL key {_key} cents"

        st.markdown(f"**Query:** {_label}")
        if _matched:
            _names = ", ".join(_m["name"] for _m in _matched)
            st.success(f"Found in ESL index — node key `{_key}` resolves to: **{_names}**")
        else:
            st.warning("Price not in index (no coin priced exactly here)")

        sc1, sc2 = st.columns(2)
        with sc1:
            _s = "FOUND" if _tf else "NOT FOUND"
            st.markdown(f"**Traditional** — {_s} | {_tc} comparisons | {_ts} steps | {_t_trad*1e6:.2f} µs")
            for _p in _tp:
                st.markdown(f"{'✓' if _p.get('hit') else '›'} **{_p['level']}**: {_p['action']}")
        with sc2:
            _s = "FOUND" if _ef else "NOT FOUND"
            st.markdown(f"**ESL** — {_s} | {_ec} comparisons | {_es} steps | {_t_esl*1e6:.2f} µs")
            for _p in _ep:
                st.markdown(f"{'✓' if _p.get('hit') else '›'} **{_p['level']}**: {_p['action']}")

        if _tc > 0 and _ec > 0:
            _r = _tc / _ec
            if _r > 1:
                st.success(f"ESL used **{_r:.1f}x fewer** comparisons than Traditional.")
            elif _r < 1:
                st.info(f"Traditional used **{1/_r:.1f}x fewer** comparisons (small dataset — ESL excels at scale).")

    # ── Price bar chart ───────────────────────────────────
    st.markdown("---")
    st.subheader("Price Overview")
    _top20 = _coins[:20]
    fig = go.Figure(data=[go.Bar(
        x=[_c["symbol"].upper() for _c in _top20],
        y=[_c.get("current_price", 0) for _c in _top20],
        marker_color="#aaaaaa",
        hovertemplate="<b>%{x}</b><br>$%{y:,.4f}<extra></extra>"
    )])
    fig.update_layout(title="Top 20 Coins — Current Price (USD)", height=320,
                      xaxis_title="Coin", yaxis_title="Price (USD)")
    st.plotly_chart(fig, use_container_width=True)


    # ── Use-case scenario cards removed — replaced by live API above ──

