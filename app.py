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
pio.templates.default = "plotly_dark"

st.set_page_config(page_title="ESL vs Traditional Skiplist", layout="wide")

# ── Sidebar ──────────────────────────────────────────────
st.sidebar.title("Navigation")
page = st.sidebar.radio("Select Tab", ["Performance (3 Scales)", "Structure Visualization", "Thread & BG Logs"])

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

    import bisect, math, time as _time
    from random import Random
    _rng = Random()

    NUM_COIL_LEVELS = 4

    # ── Helper Functions ──────────────────────────────────

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

    def normalize_esl(esl_data):
        """Fix old PDL format (identical to data) and ensure multi-coil layers."""
        pdl = esl_data.get("pdl", [])
        data = esl_data.get("data", [])
        coil = esl_data.get("coil", [])

        # Ensure enough coil levels (stored top-to-bottom)
        while len(coil) < NUM_COIL_LEVELS:
            coil.insert(0, [])

        # Convert old PDL format: list of ints → list of {"key", "data_pos"}
        if pdl and isinstance(pdl[0], (int, float)):
            if sorted(pdl) == sorted(data):
                # PDL identical to Data -> sample ~50% as index anchors
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
        # p=0.25 (standard Pugh skiplist) -> ~log4(n) levels
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

        # 1. Always insert into Data layer (sorted)
        bisect.insort(data, key)

        # 2. Probabilistic promotion to PDL (~40% - NOT all keys)
        if _rng.random() < 0.4:
            pos = bisect.bisect_left(data, key)
            pdl.append({"key": key, "data_pos": pos})
            pdl.sort(key=lambda x: x["key"])

        # 3. Hierarchical multi-coil promotion
        # Roll once to determine max level; insert into all levels 0..maxLvl
        # coil[0] = highest/sparsest, coil[-1] = lowest/densest
        # Stored top-to-bottom: coil[-1]=L0 (densest), coil[0]=L3 (sparsest)
        n = len(coil)
        max_lvl = -1
        for i in range(n):
            level_from_bottom = n - 1 - i  # coil[n-1]=L0, coil[0]=L3
            if _rng.random() < 0.25:
                max_lvl = level_from_bottom
            else:
                break
        # Insert into all coil levels from L0 up to max_lvl
        if max_lvl >= 0:
            for i in range(n):
                level_from_bottom = n - 1 - i
                if level_from_bottom <= max_lvl:
                    bisect.insort(coil[i], key)

        # 4. Rebuild PDL position references (shifted after data insert)
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
        # Drop empty levels from the top (except always keep at least one)
        while len(levels) > 1 and not levels[0]:
            levels.pop(0)
        return levels

    def delete_esl(esl_data, key):
        """Remove key from ESL data layer, PDL, and all COIL levels."""
        data = esl_data["data"]
        pdl  = esl_data["pdl"]
        coil = esl_data["coil"]

        # Remove from Data
        try:
            data.remove(key)
        except ValueError:
            pass

        # Remove from PDL
        esl_data["pdl"] = [e for e in pdl if e["key"] != key]

        # Remove from every COIL level
        for lvl in coil:
            try:
                lvl.remove(key)
            except ValueError:
                pass

        # Rebuild PDL position references after data deletion
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

        # COIL narrowing (top-to-bottom; coil[0]=highest)
        for level_idx, lvl in enumerate(coil):
            level_num = len(coil) - 1 - level_idx
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

        # PDL narrowing
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

        # Data level (narrowed range)
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

        # COIL levels
        st.markdown("**COIL (Cache-Optimized Index Levels):**")
        for idx, lvl in enumerate(coil):
            level_num = len(coil) - 1 - idx
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

        # COIL levels at y=2,3,...
        for idx, lvl in enumerate(reversed(coil)):
            level_num = idx
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
    st.caption("For guide presentation - shows ESL BG thread design, stats, and operation history")

    # ── ROWEX model ───────────────────────────────────────
    st.subheader("ROWEX Concurrency Model")
    st.markdown("""
| Thread | Role |
|---|---|
| **Main thread** | Handles insert/delete; pushes `{key, type}` to OpLog queue in O(1) |
| **BG thread** | Drains OpLog asynchronously; builds COIL + PDL index structures |
| **Readers** | Lock-free search after `waitForBG()` - no read locks needed (ROWEX) |

> **waitForBG()** waits for opsProcessed == insertCount, then sorts all structures
> in O(n log n) once. This is far cheaper than per-insert sortedInsert (O(n^2) total).
    """)

    # ── BG worker code snippet ────────────────────────────
    st.subheader("BG Worker - Source Code")
    st.code("""\
void bgWorker() {
    while (!stopFlag) {
        OpEntry entry;
        {
            unique_lock<mutex> lk(logMtx);
            // wait up to 50us for work
            logCV.wait_for(lk, chrono::microseconds(50),
                [&]{ return !opLog.empty() || stopFlag.load(); });
            if (!opLog.empty()) {
                entry = opLog.front(); opLog.pop();
                // track queue depth for stats
                int qs = (int)opLog.size();
                logSizeSum += qs; logSizeSamples++;
                long long cur = logMaxSize.load();
                while (qs > cur && !logMaxSize.compare_exchange_weak(cur, qs));
            }
        }

        if (entry.type == 0) {           // INSERT
            // PDL: ~40% sparse promotion - O(1) push_back
            if (bgRng() % 5 < 2)
                pdl.push_back({entry.key, 0});

            // Hierarchical COIL (p=0.25 per level, like standard skiplist)
            int maxLvl = -1;
            for (int i = 0; i < COIL_LEVELS; i++) {
                if ((int)(bgRng() % 4) == 0) maxLvl = i;
                else break;
            }
            // Key in COIL L2 is guaranteed to also be in L0, L1
            for (int i = 0; i <= maxLvl; i++)
                coil[i].push_back(entry.key);
        } else {                          // DELETE
            pdlRemove(entry.key);
            for (int i = 0; i < COIL_LEVELS; i++) sortedRemove(coil[i], entry.key);
        }
        opsProcessed++;  // signals waitForBG() that this item is done
    }
}

void waitForBG() {
    // Spin until BG has processed every queued insert
    long long target = insertCount.load();
    while (opsProcessed.load() < target) {
        logCV.notify_one();
        this_thread::sleep_for(chrono::microseconds(200));
    }
    // Sort all structures once - O(n log n) total
    sort(data.begin(), data.end());
    data.erase(unique(data.begin(), data.end()), data.end());
    for (int i = 0; i < COIL_LEVELS; i++) {
        sort(coil[i].begin(), coil[i].end());
        coil[i].erase(unique(coil[i].begin(), coil[i].end()), coil[i].end());
    }
    sort(pdl.begin(), pdl.end(), [](auto& a, auto& b){ return a.key < b.key; });
    rebuildPDLPositions(); // fixes data_pos references after sort
}""", language="cpp")

    # ── BG stats from benchmark ───────────────────────────
    if bench and bench.get("experiments"):
        st.subheader("BG Thread Stats per Scale")
        stats_rows = []
        for exp in bench["experiments"]:
            e = exp["esl"]
            stats_rows.append({
                "Scale": f"{exp['scale']:,}",
                "BG Ops Processed": f"{e['bg_ops_processed']:,}",
                "BG Efficiency (%)": f"{e['bg_efficiency']:.1f}",
                "Queue Max Size": f"{e['queue_max_size']:,}",
                "Queue Avg Size": f"{e['queue_avg_size']:.1f}",
                "Index Build Time (s)": f"{e.get('index_build_time', 0):.4f}",
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

    # ── Search path explanation ───────────────────────────
    st.subheader("ESL Search Path (ROWEX lock-free)")
    st.code("""\
bool search(int key) {
    int rangeLo = 0, rangeHi = INT_MAX;

    // COIL: sparsest to densest (L3 -> L0), binary search, narrows range
    for (int i = COIL_LEVELS-1; i >= 0; i--) {
        if (coil[i].empty()) continue;
        auto it = lower_bound(coil[i], key, range=[rangeLo, rangeHi]);
        if (*it == key) { coilHits++; return true; }   // early exit
        rangeLo = *(it-1);  rangeHi = *it;             // narrow range
    }
    // PDL: binary search on sparse index within narrowed range
    auto pit = lower_bound(pdl, key, range=[rangeLo, rangeHi]);
    if (pit->key == key) return true;
    rangeLo = (pit-1)->key;  rangeHi = pit->key;

    // Data: final binary search on tiny remaining range
    return binary_search(data[rangeLo..rangeHi], key);
}
// No locks - safe after waitForBG() under ROWEX protocol""", language="cpp")

    # ── Real-time operation log ───────────────────────────
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
