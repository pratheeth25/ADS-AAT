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

> On Windows with MinGW you may need `-lws2_32` or just ensure pthreads are available.

---

## Step 3: Run the Benchmark

```bash
./benchmark
```

**Windows:**
```powershell
.\benchmark.exe
```

This will:
- Run 100,000 operations (50% insert, 50% search)
- Print a detailed metrics dashboard to the console
- Export `benchmark_results.json`

---

## Step 4: Run the CLI Visualizer

```bash
./visualize
```

**Windows:**
```powershell
.\visualize.exe
```

Interactive menu:
1. **Insert** — insert a key into both structures, shows latency (us) for each; auto-exports `structure.json`
2. **Search** — search with full path tracing and latency comparison
3. **Delete** — delete from both structures; auto-exports `structure.json`
4. **Export JSON** — writes `structure.json`
5. **Exit**

> **Auto-Export** is always ON: `structure.json` is updated automatically after every insert/delete. The Streamlit dashboard can detect these changes via its **Refresh from Disk** button.

> **Tip:** Insert a few values (e.g., 10, 20, 30, 40, 50), then switch to the Streamlit dashboard to see the structures live.

---

## Step 5: Run the Streamlit Dashboard

```bash
streamlit run app.py
```

This opens a browser at `http://localhost:8501` with two tabs:

### Tab 1 — Performance Comparison
- Metric comparison table (bold = winner)
- Bar charts: Search Time, Throughput, Comparisons, Traversal Steps, Latency
- ESL Insights Panel: COIL hit rate, BG efficiency, queue stats

### Tab 2 — Structure Visualization (Real-Time)
- **Insert values** directly in the browser — both Traditional and ESL structures update instantly
- **Search values** with full path tracing: see comparisons, steps, and latency for both structures
- **Refresh from Disk** button reloads `structure.json` if updated externally (e.g., by the CLI tool)
- **Side-by-side** display of Traditional Skiplist levels vs ESL layers
- **PDL (Position Description Layer)** shown as a proper indexing layer with position references (e.g., `3 (pos 2)` = key 3 at data position 2), distinct from the Data Layer
- **Multi-coil ESL** with 4 COIL levels — hierarchical (a key in COIL L2 is always in L0 and L1)
- **ESL Layer Breakdown** chart showing entries per COIL level, PDL, and Data
- **Operation Log** table tracking all inserts and searches with timing (microseconds)
- Interactive plotly scatter plots for both structures
- Size comparison metrics

### Architecture: PDL vs Data Layer

The ESL structure has a clear separation of concerns:

| Layer | Role |
|---|---|
| **Data Layer** | Stores all actual values (sorted) |
| **PDL** | Sparse indexing layer (~40% of data keys) with position references into the Data Layer |
| **COIL L0–L3** | Express-lane index levels with hierarchical promotion (p=0.25 per level; L0: ~25%, L1: ~6.25%, L2: ~1.56%, L3: ~0.39%) |

> PDL is NOT a copy of the Data Layer. It acts as a navigation index, storing only sampled keys with their positions in the data array.

### Multi-Coil Scalability

The ESL uses 4 COIL levels by default. Promotion is **hierarchical**: a key first rolls for L0 (p=0.25), then L1 (p=0.25 again), etc. A key in COIL L2 is guaranteed to also be in L0 and L1 — just like a traditional skiplist's level invariant.

> You can also upload JSON files via the sidebar if the files are in a different location.

---

## File Summary

| File | Purpose |
|---|---|
| `benchmark.cpp` | Runs 100K ops, measures metrics, exports `benchmark_results.json` |
| `visualize.cpp` | CLI tool: insert/search/delete/print/export `structure.json` |
| `app.py` | Streamlit dashboard reading both JSON files |
| `benchmark_results.json` | Auto-generated benchmark data |
| `structure.json` | Auto-generated structure snapshot (PDL as index layer, multi-coil COIL) |

---

## Quick Start (Copy-Paste)

```bash
# Compile
g++ -std=c++17 -O2 -pthread benchmark.cpp -o benchmark
g++ -std=c++17 -O2 -pthread visualize.cpp -o visualize

# Run benchmark
./benchmark

# Run CLI (insert some values, export JSON)
./visualize

# Launch dashboard
streamlit run app.py
```
