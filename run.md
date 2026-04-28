# Run Guide — ESL vs Traditional Skiplist Project

## Prerequisites

- **C++ Compiler**: g++ with C++17 support (MinGW/MSYS2 on Windows, or g++ on Linux/Mac)
- **Python 3.8+** with pip
- **Python packages**: `streamlit`, `plotly`, `pandas`

---

## Step 1: Install Python Dependencies

```bash
pip install streamlit plotly pandas
```

---

## Step 2: Compile the C++ Programs

### Benchmark

```bash
g++ -std=c++17 -O2 -pthread benchmark.cpp -o benchmark
```

### CLI Visualizer

```bash
g++ -std=c++17 -O2 -pthread visualize.cpp -o visualize
```

---

## Step 3: Run the Benchmark

```bash
./benchmark        # Linux / Mac
.\benchmark.exe    # Windows PowerShell
```

This runs **three scales** (1K, 100K, 1M operations — 50% insert, 50% search):

| Scale | Winner | Speedup |
|---|---|---|
| 1K ops | Traditional | ~1.1–1.4× |
| 100K ops | **ESL** | ~1.7–3.7× |
| 1M ops | **ESL** | ~2.0–5.9× |

Output:
- Detailed metrics dashboard printed to console
- `benchmark_results.json` exported (consumed by the Streamlit dashboard)

**Architecture note**: ESL inserts are fully lock-free — PDL and Data use Treiber
stacks (O(1) CAS push, no mutex). COIL levels are built once from the sorted Data
snapshot in `waitForBG()`.

---

## Step 4: Run the CLI Visualizer

```bash
./visualize        # Linux / Mac
.\visualize.exe    # Windows PowerShell
```

Interactive menu:
1. **Insert** — insert a key into both structures; auto-exports `structure.json`
2. **Search** — search with full path tracing and latency comparison
3. **Delete** — delete from both structures; auto-exports `structure.json`
4. **Export JSON** — writes `structure.json`
5. **Exit**

---

## Step 5: Run the Streamlit Dashboard

```bash
streamlit run app.py
```

Opens `http://localhost:8501` with three tabs:

### Tab 1 — Performance Comparison
- Metric comparison table (bold = winner) for all three scales
- Bar charts: Search Time, Throughput, Comparisons, Traversal Steps, Latency
- ESL Insights Panel: COIL hit rate, structure sizes

### Tab 2 — Structure Visualization (Real-Time)
- **Insert/Search/Delete** values directly in the browser
- **Refresh from Disk** button reloads `structure.json` if updated by the CLI tool
- Side-by-side display of Traditional Skiplist levels vs ESL layers (COIL, PDL, Data)
- PDL shown with position hints (e.g., `30 (pos 6)` = key 30 at data index 6)
- Operation Log table with per-operation timing in microseconds
- Plotly scatter plots and layer size charts

### Tab 3 — Thread & BG Logs
- ROWEX Concurrency Model diagram
- BG thread stats per benchmark scale

---

## ESL Architecture Overview

```
┌──────────────────────────────────────────────────┐
│  COIL L0..Lmax  — sorted arrays, built in waitForBG() from Data snapshot  │
├──────────────────────────────────────────────────┤
│  PDL  — lock-free Treiber stack; snapshot has {key, data_pos} hints       │
├──────────────────────────────────────────────────┤
│  Data — lock-free Treiber stack; snapshot is sorted array of all keys     │
└──────────────────────────────────────────────────┘
```

Insert hot path: **zero mutex** — two CAS operations + one atomic increment.

---

## File Summary

| File | Purpose |
|---|---|
| `benchmark.cpp` | Runs 1K/100K/1M ops, measures metrics, exports `benchmark_results.json` |
| `visualize.cpp` | CLI tool: insert/search/delete/export `structure.json` |
| `app.py` | Streamlit dashboard |
| `explanation.md` | Beginner-friendly guide to every concept in the project |
| `benchmark_results.json` | Auto-generated (gitignored) — benchmark output |
| `structure.json` | Auto-generated (gitignored) — CLI visualizer snapshot |
| `op_log.json` | Auto-generated (gitignored) — operation log |

---

## Quick Start (Copy-Paste)

```bash
# Compile
g++ -std=c++17 -O2 -pthread benchmark.cpp -o benchmark
g++ -std=c++17 -O2 -pthread visualize.cpp -o visualize

# Run benchmark (produces benchmark_results.json)
./benchmark

# Run CLI visualizer (insert some values, exports structure.json)
./visualize

# Launch Streamlit dashboard
streamlit run app.py
```

