# ESL vs Traditional Skiplist — Implementation & Visualization

An educational implementation and interactive benchmark of the **Express Skiplist (ESL)** compared to a traditional Pugh skiplist, with a real-time Streamlit dashboard.

---

## Reference

This project implements the data structure proposed in:

> **Na, Y., Koo, B., Park, T., Park, J., & Kim, W.-H. (2023).**  
> *ESL: A High-Performance Skiplist with Express Lane.*  
> *Applied Sciences, 13*(17), 9925.  
> DOI: [https://doi.org/10.3390/app13179925](https://doi.org/10.3390/app13179925)  
> URL: [https://www.mdpi.com/2076-3417/13/17/9925](https://www.mdpi.com/2076-3417/13/17/9925)

The traditional skiplist baseline follows the original algorithm from:

> **Pugh, W. (1990).**  
> *Skip Lists: A Probabilistic Alternative to Balanced Trees.*  
> *Communications of the ACM, 33*, 668–676.  
> DOI: [https://doi.org/10.1145/78973.78977](https://doi.org/10.1145/78973.78977)

The ROWEX (Read-Optimized Write-EXclusion) concurrency protocol used in the ESL design originates from:

> **Leis, V., Scheibner, F., Kemper, A., & Neumann, T. (2016).**  
> *The ART of Practical Synchronization.*  
> International Workshop on Data Management on New Hardware (DaMoN), pp. 3:1–3:8.

---

## What We Implemented vs. What the Paper Proposes

| Aspect | Paper (Na et al., 2023) | This Implementation |
|---|---|---|
| **COIL** | Contiguous arrays per level with exponential + linear search | Sorted `std::vector` / Python list with binary search (`bisect`) |
| **PDL** | Lock-free linked list, managed by BG thread | Sorted vector of `{key, data_pos}` structs; position rebuilt after each mutation |
| **Data Level** | Lock-free linked list (all keys) | Sorted array (all keys) |
| **BG Thread** | Lock-free op-log queue (CAS-based); async COIL + PDL build | Mutex + condition-variable op-log; batched sort after `waitForBG()` |
| **Concurrency** | Full ROWEX for COIL; lock-free for PDL/Data | Simplified: `shared_mutex` on COIL; single BG thread drains queue |
| **PDL promotion** | Probabilistic (~40% of keys become PDL entries) | Same — `rng % 5 < 2` ≈ 40% |
| **COIL promotion** | Hierarchical p=0.25 per level (like standard skiplist) | Same — per-level Bernoulli trial, key in COIL Lk is guaranteed in L0..L(k-1) |
| **Memory reclamation** | Epoch-based | Not implemented (educational scope) |
| **Visualization** | N/A | Interactive Streamlit dashboard with real-time insert/search/delete |

### Modifications and additions we introduced

- **`waitForBG()` barrier**: After all inserts, the benchmark forces a full sort of COIL + PDL before measuring search latency. This replaces per-insert sorted insertion (O(n²)) with a single O(n log n) bulk sort, staying true to the paper's asynchronous spirit.
- **Sorted-array PDL with `data_pos`**: Instead of a linked list, we store position references into the Data array so the search can directly narrow its binary-search range, combining the paper's PDL idea with a learned-index-style position hint.
- **Streamlit real-time dashboard** (`app.py`): Not in the paper — our addition for interactive visualization and benchmarking at three scales. Comprises four tabs: Performance Comparison, Structure Visualization, Thread & BG Logs, and Practical API Demo.
- **JSON state persistence** (`structure.json`, `op_log.json`, `traverse_logs.json`): Allows the CLI visualizer (`visualize.cpp`) and the Streamlit UI to share state across processes. `traverse_logs.json` stores forward traversal paths (INSERT/SEARCH: COIL → PDL → Data) and backward removal paths (DELETE) for every operation.
- **Practical API Demo tab**: Loads a configurable dataset (500–10,000 records) into both structures and simulates real-world API scenarios — batch lookups with configurable hit rate, single key path tracing, and range queries — demonstrating ESL's advantage in practical use cases (leaderboards, product catalogs, session stores).

---

## How to Run

See [run.md](run.md) for the full step-by-step guide.

```bash
# Compile
g++ -std=c++17 -O2 -pthread benchmark.cpp -o benchmark
g++ -std=c++17 -O2 -pthread visualize.cpp -o visualize

# Run benchmark (exports benchmark_results.json)
./benchmark

# Run interactive CLI (exports structure.json)
./visualize

# Launch dashboard
streamlit run app.py
```

---

## File Overview

| File | Purpose |
|---|---|
| `benchmark.cpp` | Runs 100K / 1M / 10M ops benchmark; exports `benchmark_results.json` |
| `visualize.cpp` | CLI: insert / search / delete / print / export `structure.json` |
| `app.py` | Streamlit dashboard: 4 tabs — Performance, Structure Visualization, Thread & BG Logs, Practical API Demo |
| `benchmark_results.json` | Auto-generated benchmark data |
| `structure.json` | Auto-generated structure snapshot |
| `op_log.json` | Persisted operation log (survives browser refresh) |
| `traverse_logs.json` | Persisted forward (INSERT/SEARCH) and backward (DELETE) traversal logs |

---

## License

This repository is for educational and academic purposes.  
All credit for the ESL algorithm design goes to Na, Koo, Park, Park, and Kim (2023).
