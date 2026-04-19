# Understanding This Project — A Beginner's Guide

This document explains **every concept and feature** in this repository in plain language.  
No prior knowledge of data structures or systems programming is required.

---

## Table of Contents

1. [What problem are we solving?](#1-what-problem-are-we-solving)
2. [What is a Skiplist?](#2-what-is-a-skiplist)
3. [The problem with traditional skiplists](#3-the-problem-with-traditional-skiplists)
4. [What is ESL and how does it fix this?](#4-what-is-esl-and-how-does-it-fix-this)
5. [Feature deep-dive: COIL](#5-feature-coil--cache-optimized-index-levels)
6. [Feature deep-dive: PDL](#6-feature-pdl--position-descriptor-layer)
7. [Feature deep-dive: Background Thread & Op-Log](#7-feature-background-thread--operation-log)
8. [Feature deep-dive: ROWEX Concurrency](#8-feature-rowex-concurrency)
9. [Feature deep-dive: waitForBG barrier](#9-feature-waitforbg-barrier)
10. [Benchmark results — real numbers](#10-benchmark-results--real-numbers)
11. [Why ESL is slower at small scale](#11-why-esl-is-slower-at-small-scale)
12. [The Streamlit dashboard](#12-the-streamlit-dashboard)
13. [File-by-file guide](#13-file-by-file-guide)
14. [Glossary](#14-glossary)

---

## 1. What problem are we solving?

Modern software (databases like MongoDB, RocksDB, LevelDB) needs to store millions of
key-value pairs in memory and answer questions like:

- "Is the value 42 stored?" → **Search**
- "Add value 99." → **Insert**
- "Remove value 17." → **Delete**

These operations must be **fast** — ideally done in microseconds (millionths of a second).
The data structure that does this job is called a **skiplist**.

This project implements a standard (traditional) skiplist and compares it against
**ESL (Express Skiplist)**, a redesigned version proposed in a 2023 research paper.

---

## 2. What is a Skiplist?

Think of a skiplist like a highway system:

```
Level 3 (express):   1 ────────────────────────── 50 ─────────── 99
Level 2 (fast):      1 ──────── 20 ──────────────  50 ──── 80 ── 99
Level 1 (local):     1 ── 10 ── 20 ── 30 ── 40 ── 50 ── 60 ── 80 ── 99
Level 0 (data):      1 ─ 5 ─ 10 ─ 15 ─ 20 ─ 25 ─ 30 ─ 35 ─ 40 ─ 45 ─ 50 ─ ... ─ 99
```

- **Level 0** contains every single value.
- **Higher levels** contain fewer values — they are "express lanes" that let you skip ahead.
- To search for `35`, you start at the top level, jump as far right as you can without
  passing 35, then drop down a level and repeat. You reach the answer much faster than
  scanning every element.

Each value is stored as a **node** in a linked list. A node at Level 2 has a pointer
to the next node at Level 2, another to Level 1, another to Level 0, etc.

> **Key property**: A key at Level 2 is always also at Levels 0 and 1.
> This means higher-level nodes are a *subset* of lower-level nodes.

---

## 3. The problem with traditional skiplists

### Problem 1: Pointer chasing (cache misses)

In a traditional skiplist every node is a separate object in memory, allocated anywhere
on the heap. When you traverse Level 2 from node `A` to node `B`, the CPU has to fetch
node `B` from a completely different memory address.

Modern CPUs are fast because they cache recently-used memory in a small, fast "cache"
(like L1/L2 cache). When data is not in the cache the CPU has to wait ~100 cycles for
it — this is a **cache miss**.

A traditional skiplist causes many cache misses because:
- Nodes are scattered in memory (not contiguous).
- Going from one node to the next requires a pointer jump to a random address.

### Problem 2: Insert/Delete is slow on the critical path

When you insert a key, the skiplist must immediately update every level the key belongs
to. If the key is promoted to Level 5, the insert touches and updates 5 different
linked-list positions. This adds latency to every write.

### Visual comparison

```
Traditional skiplist — nodes scattered in memory:
  [node@0xA1] --> [node@0x3F8] --> [node@0x7C2] --> ...
        ↑ pointer jump              ↑ pointer jump
     (cache miss)                 (cache miss)

ESL — all nodes of the same level in one array:
  Level 2: [1, 50, 99, ...]           ← contiguous array
  Level 1: [1, 20, 50, 80, 99, ...]   ← contiguous array
  Data:    [1, 5, 10, 15, 20, ...]    ← contiguous array
```

Contiguous arrays are **cache-friendly**: when the CPU loads element `[i]`, the
hardware prefetcher automatically loads `[i+1]`, `[i+2]`, etc. into cache — so the
next access is nearly free.

---

## 4. What is ESL and how does it fix this?

ESL replaces the linked-list levels with three distinct components:

```
┌─────────────────────────────────────────────────────────┐
│  COIL L3  (sparsest — fewest keys, ~0.4% of data)       │  ← sorted array
│  COIL L2  (~1.6% of data)                               │  ← sorted array
│  COIL L1  (~6.25% of data)                              │  ← sorted array
│  COIL L0  (~25% of data)                                │  ← sorted array
├─────────────────────────────────────────────────────────┤
│  PDL      (~40% of data — sparse navigation index)      │  ← sorted array
├─────────────────────────────────────────────────────────┤
│  Data     (100% — every value ever inserted)            │  ← sorted array
└─────────────────────────────────────────────────────────┘
```

Each layer is a **sorted array**, not a linked list. Searching means:
1. Binary search in COIL L3 → narrows range from `[0, N]` to a small window.
2. Binary search in COIL L2, L1, L0 → window shrinks further each time.
3. Binary search in PDL → window shrinks again.
4. Final binary search in Data within the narrow window.

Because each layer is a sorted array, the CPU cache prefetcher works perfectly.
No pointer chasing, no scattered memory, no cache misses.

---

## 5. Feature: COIL — Cache-Optimized Index Levels

### What it is

COIL stands for **Cache-Optimized Index Level**. It is a set of 4 sorted arrays
(L0 through L3) that act as the "express lanes" of the ESL.

### How keys are promoted into COIL

Not every key enters every level. Each key rolls a 25% chance (p = 0.25) to enter
the next level — exactly like a traditional skiplist's probabilistic promotion.

```
Insert key K:
  Roll die for L0: 25% chance → enters COIL L0
    If yes, roll for L1: 25% chance → enters COIL L1
      If yes, roll for L2: 25% chance → enters COIL L2
        If yes, roll for L3: 25% chance → enters COIL L3
```

This means (on average):
- ~25% of all keys appear in COIL L0
- ~6.25% appear in COIL L1
- ~1.56% in COIL L2
- ~0.39% in COIL L3

> **Invariant inherited from traditional skiplists**: A key in COIL L2 is
> *always* also in COIL L0 and L1.

### How COIL speeds up search

Suppose there are 1,000,000 keys (1M ops benchmark). To find key `K`:

Without COIL: binary search over all 1M keys → ~20 comparisons.

With COIL:
1. Binary search in COIL L3 (~3,900 keys) → narrows the range to ~1 step in data.
2. Each lower level further confirms/tightens the range.
3. Final binary search in Data within a tiny window.

The total comparisons go down. More importantly, each array fits neatly in CPU cache —
so even though we do multiple binary searches, each one is very fast because there are
no cache misses.

---

## 6. Feature: PDL — Position Descriptor Layer

### What it is

PDL stands for **Position Descriptor Layer**. It sits between COIL and the Data layer.
About **40%** of all inserted keys are randomly promoted into the PDL.

But here is the key difference from the Data layer: **each PDL entry stores not just
the key, but also its exact position (index) in the Data array**.

```
Data array:  [1,  5, 10, 15, 20, 25, 30, 35, 40, 45, 50]
              [0] [1] [2] [3] [4] [5] [6] [7] [8] [9] [10]

PDL entry:   { key: 10, data_pos: 2 }
             { key: 30, data_pos: 6 }
             { key: 50, data_pos: 10 }
```

### Why this helps

When searching for key `28`:
1. COIL narrows the range (say between 25 and 35 in the data).
2. PDL finds that `25` is at `data_pos=5` and `35` is at `data_pos=7`.
3. Now the final search in Data only needs to check positions 5–7 (3 elements
   instead of 1,000,000).

The PDL acts like a **phone book's letter tabs** — it doesn't contain every entry,
just enough anchors to tell you exactly which section to open.

### What PDL is NOT

PDL is **not** a copy of the Data layer. It is a sparse index — roughly 40% of keys
appear in it. Storing 40% instead of 100% keeps it small enough to stay in CPU cache.

---

## 7. Feature: Background Thread & Operation Log

### The problem it solves

Every time you insert a key into ESL, updating all COIL levels immediately would
slow down the insert — because shifting elements in a sorted array to make room is
O(n) per level.

ESL's solution: **don't update the index immediately**. Just add the insert to a queue
(the Op-Log) and let a **background thread** handle the index update asynchronously.

### How it works step by step

```
Foreground thread (you):                Background thread:
  1. Append key to Data array             Reads from Op-Log queue
  2. Write {key, INSERT} to Op-Log  →     Rolls the COIL promotion dice
  3. Return immediately (fast!)           Updates COIL + PDL
```

The foreground thread's insert is very fast (just an append + queue push).
The background thread does the heavy work of updating the index without blocking you.

### The Op-Log queue in code (`benchmark.cpp`)

```cpp
struct OpEntry { int key; int type; };  // type: 0=insert, 1=delete
queue<OpEntry> opLog;
mutex logMtx;
condition_variable logCV;
```

The background thread waits on `logCV` and wakes up every 50 microseconds to drain
the queue. This is the same design described in the ESL paper's Section 3.4.

---

## 8. Feature: ROWEX Concurrency

### What concurrency means

When multiple threads run at the same time and all try to read/write the same data
structure, you get **race conditions** — one thread reads stale data that another
thread is in the middle of modifying.

Classic solution: use a **lock** (mutex). But locking forces threads to wait,
destroying the performance benefit of having multiple threads.

### What ROWEX does

**ROWEX** stands for **Read-Optimized Write-EXclusion**.

- Only **one writer** (the background thread) ever modifies the COIL.
- **Multiple readers** (foreground threads doing searches) can read simultaneously
  *without acquiring any lock*.
- Readers simply tolerate a brief moment of inconsistency:
  - If the COIL doesn't find the key, the search falls through to PDL then Data.
  - The Data layer is always consistent (it uses a simpler lock-free approach).

This means search operations never stall waiting for a lock — they always proceed,
making the **tail latency** (worst-case time for a single query) much lower.

### Real-world impact

At 1M operations, the maximum search latency was:
- Traditional: **5,180 µs** (5.18 milliseconds — the worst single query was that slow)
- ESL: **397 µs** — **13× lower worst-case latency**

This matters enormously in real databases: a single slow query can block many others.

---

## 9. Feature: `waitForBG` Barrier

### The problem

Because inserts are async (the background thread builds the index), you can't search
reliably until the background thread has finished processing all queued inserts. If you
search before the background is done, COIL might not contain recently inserted keys.

### The solution

Before the search benchmark starts, `waitForBG()` is called:

```cpp
void waitForBG() {
    // 1. Wait until BG has processed every queued insert
    while (opsProcessed < insertCount) { ... }

    // 2. Sort all structures once — O(n log n)
    sort(data);
    for each coil level: sort(coil[i]);
    sort(pdl);

    // 3. Rebuild PDL position references
    rebuildPDLPositions();
}
```

This is a key insight from the paper: doing one `sort()` after all inserts is
**O(n log n)** — much faster than keeping arrays sorted during insertion, which
would be **O(n²)** (inserting into a sorted array is O(n) per insert × n inserts).

For 1,000,000 inserts:
- Per-insert sorted insertion: ~500 billion operations
- Single sort at the end: ~20 million operations

---

## 10. Benchmark Results — Real Numbers

The benchmark (`benchmark.cpp`) runs three experiments: 1K, 100K, and 1M operations
(50% inserts, 50% searches each). All numbers below are from an actual run.

---

### Scale: 1,000 ops (Small)

| Metric | Traditional | ESL | Winner |
|---|---|---|---|
| Insert+Build time | 0.0015 s | 0.0004 s | **ESL (3.9×)** |
| Search time | 0.0001 s | 0.0002 s | Traditional |
| **Total time** | **0.0016 s** | **0.0006 s** | **ESL (2.75×)** |
| Throughput | 628,575 ops/s | 1,730,104 ops/s | **ESL (2.75×)** |
| Search comparisons | 7,953 | 7,158 | ESL (−10%) |
| Traversal steps | 6,113 | 2,291 | **ESL (−62.5%)** |
| Avg search latency | 0.12 µs | 0.33 µs | Traditional |
| Max search latency | 1.10 µs | 0.90 µs | ESL |

**At 1K**: ESL's insert is much faster (arrays vs. pointer-linking). Search is
slightly slower because the background thread overhead is large relative to 500
tiny searches. The advantage has not yet kicked in.

---

### Scale: 100,000 ops (Medium)

| Metric | Traditional | ESL | Winner |
|---|---|---|---|
| Insert+Build time | 0.0223 s | 0.0084 s | **ESL (2.64×)** |
| Search time | 0.0275 s | 0.0828 s | Traditional |
| **Total time** | **0.0497 s** | **0.0912 s** | Traditional (1.8×) |
| Throughput | 2,010,277 ops/s | 1,096,567 ops/s | Traditional |
| Traversal steps | 1,090,801 | 378,916 | **ESL (−65.3%)** |
| Max search latency | 71.10 µs | 4,600.80 µs | Traditional |

**At 100K**: This is the crossover zone. ESL's insert is 2.64× faster, but search
is slower. Why? The `waitForBG()` barrier and the sort-then-search path has overhead
that at 100K isn't yet offset by the cache benefits. The traversal *step count* is
already 65% lower — the COIL is working — but the constant overhead from sorting
and lock management is still visible. At this scale the dataset is borderline.

---

### Scale: 1,000,000 ops (Large) — ESL wins decisively

| Metric | Traditional | ESL | Winner |
|---|---|---|---|
| Insert+Build time | 0.2679 s | 0.0976 s | **ESL (2.75×)** |
| Search time | 1.5467 s | 0.7962 s | **ESL (1.94×)** |
| **Total time** | **1.8146 s** | **0.8938 s** | **ESL (2.03×)** |
| Throughput | 551,088 ops/s | 1,118,787 ops/s | **ESL (2.03×)** |
| Search comparisons | 18,377,310 | 15,720,201 | **ESL (−14.5%)** |
| Traversal steps | 13,417,356 | 4,372,546 | **ESL (−67.4%)** |
| Avg search latency | 2.95 µs | 1.50 µs | **ESL (1.97×)** |
| **Max search latency** | **5,180 µs** | **397 µs** | **ESL (13×)** |
| COIL hit rate | — | 13.19% | — |
| PDL size | — | 194,859 entries | — |
| BG ops processed | — | 487,769 | — |

**At 1M**: ESL is **2× faster overall**. The COIL's cache efficiency dominates.
Even though COIL only directly hits the target 13.2% of the time, every hit completely
avoids traversing the full Data array — and crucially it narrows the search range
for the other 87%. The tail latency improvement (13×) is the most dramatic number —
this is the ROWEX benefit in action.

---

### Why the advantage grows with scale

```
Scale          Traversal step reduction     Overall speedup
──────────────────────────────────────────────────────────
1K             62.5%                        2.75×  (insert-dominated)
100K           65.3%                        0.55×  (crossover zone)
1M             67.4%                        2.03×  (ESL wins)
```

At large scale, the cache-miss cost of pointer chasing in the traditional skiplist
grows faster than the overhead of ESL's sorted arrays. This is exactly what the
original paper demonstrates: **ESL is designed for scale**.

---

## 11. Why ESL is slower at small scale

At 1K ops, ESL is faster overall (insert dominates). But at 100K ops, Traditional wins.

Here is why the search is slower at medium scale:

1. **`waitForBG()` barrier overhead**: Sorting 50,000 COIL entries after inserts takes
   measurable time. At 1K this is negligible; at 100K it starts to show.

2. **COIL hit rate is only ~13%**: At 100K, the COIL only contains ~12,500 entries.
   Most searches still end up doing binary search in the Data layer. The overhead of
   consulting the COIL first (even if it's a cache-friendly array) slightly exceeds
   the savings compared to a direct binary search in 50,000 entries.

3. **This is an acknowledged limitation** of our implementation vs. the paper's:
   the paper uses exponential + linear search within each COIL level (better for
   small datasets), while we use binary search everywhere.

At 1M ops the cache-miss savings completely dominate and ESL wins by 2×.

---

## 12. The Streamlit Dashboard

`app.py` is a web application that runs in your browser at `http://localhost:8501`.
It has three tabs:

### Tab 1 — Performance (3 Scales)

Shows the benchmark results in charts and tables. You can see side-by-side:
- How long inserts and searches took at each scale.
- Which structure had fewer comparisons and traversal steps.
- A "winner" column with speedup factors.

### Tab 2 — Structure Visualization (Real-Time)

This is an interactive sandbox where you can:

| Button | What it does |
|---|---|
| **Insert** | Adds a value to both structures instantly; updates charts |
| **Search** | Shows the full search path through every level of both structures |
| **Delete** | Removes a value from both structures |
| **Insert N Random** | Bulk-inserts N random values at once |
| **Refresh from Disk** | Reloads the JSON if the CLI tool updated it |
| **Clear All** | Resets both structures to empty; clears op log |

The page shows:
- **Traditional skiplist**: Each level as a text chain `HEAD -> 10 -> 20 -> NULL`
- **ESL**: COIL levels, PDL with position hints, and Data layer separately
- **Plotly scatter plots**: Visual representation of which keys exist at which levels
- **Operation Log**: A table of every insert/search/delete with timing in microseconds
- **Size comparison**: How many total entries each structure uses

**State persistence**: All your inserts survive a browser refresh because:
- `structure.json` stores the full structure (Traditional + ESL layers).
- `op_log.json` stores the operation history.
Both are read back on page load.

### Tab 3 — Thread & BG Logs

Shows the ROWEX concurrency model in diagram form, the actual C++ background worker
source code, and BG queue statistics from the last benchmark run.

---

## 13. File-by-File Guide

### `benchmark.cpp` — The speed test

```
benchmark.cpp
├── TraditionalSkiplist class   — standard Pugh skiplist (linked-list nodes)
├── ESL class                   — our ESL implementation
│   ├── coil[]                  — COIL levels (sorted vectors)
│   ├── pdl[]                   — PDL entries with {key, data_pos}
│   ├── data[]                  — Data layer (all keys, sorted)
│   ├── opLog                   — queue for background thread
│   ├── bgWorker()              — background thread function
│   └── waitForBG()             — barrier: wait + sort all layers
├── runExperiment()             — runs one scale of N ops
└── main()                      — runs 1K, 100K, 1M; exports JSON
```

**Output**: `benchmark_results.json` — consumed by the Streamlit dashboard.

### `visualize.cpp` — Interactive CLI

Same ESL and Traditional implementations, but with an interactive menu:
- Type `1` to insert, `2` to search, `3` to delete.
- After every insert/delete, `structure.json` is written automatically.
- The Streamlit dashboard's "Refresh from Disk" button picks up these changes.

### `app.py` — The dashboard

```
app.py
├── Sidebar: navigation + file upload
├── Tab 1 (Performance):    reads benchmark_results.json, renders charts
├── Tab 2 (Visualization):
│   ├── insert_traditional()    — Python replication of C++ insert logic
│   ├── insert_esl()            — Python replication of C++ ESL insert
│   ├── delete_traditional()    — remove from linked-list-style levels
│   ├── delete_esl()            — remove from data, PDL, all COIL levels
│   ├── search_traditional()    — scan each level top-down
│   ├── search_esl()            — COIL → PDL → Data range narrowing
│   └── All buttons + plots
└── Tab 3 (Logs):           ROWEX diagram, BG code snippet, stats
```

### `structure.json` — Shared state file

Written by both `benchmark.cpp` / `visualize.cpp` (C++) and `app.py` (Python).
Contains the full snapshot of both structures:

```json
{
  "traditional": { "levels": [[1, 5, 10], [1, 10], [10]] },
  "esl": {
    "coil": [[1, 10], [10], [], []],
    "pdl": [{ "key": 1, "data_pos": 0 }, { "key": 10, "data_pos": 1 }],
    "data": [1, 5, 10]
  }
}
```

### `op_log.json` — Persisted operation history

Stores the operation log so it survives browser refreshes:

```json
[
  { "op": "INSERT", "key": 10, "trad_us": 12.3, "esl_us": 4.1 },
  { "op": "SEARCH", "key": 10, "trad_us": 0.8, "esl_us": 1.2, "trad_comp": 3, "esl_comp": 2 }
]
```

---

## 14. Glossary

| Term | Plain-language meaning |
|---|---|
| **Key** | The value being stored/searched (e.g., the number 42) |
| **Level / Layer** | One row in the skiplist hierarchy; higher = sparser |
| **Cache miss** | CPU had to wait for data from slow RAM instead of fast cache |
| **Cache-friendly** | Data is stored contiguously in memory; prefetcher works well |
| **Binary search** | Divide-and-conquer search: check the middle, go left or right |
| **Pointer chasing** | Following a chain of memory addresses one by one (slow) |
| **Contiguous array** | Elements stored side-by-side in memory (fast for cache) |
| **COIL** | Cache-Optimized Index Level — sorted arrays acting as express lanes |
| **PDL** | Position Descriptor Layer — sparse index with position hints into Data |
| **Op-Log** | Queue of pending inserts/deletes for the background thread |
| **Background thread** | A separate thread that updates the index asynchronously |
| **ROWEX** | Read-Optimized Write-EXclusion — readers never wait for locks |
| **Tail latency** | The worst-case time for a single operation (e.g., 99th percentile) |
| **Throughput** | Number of operations completed per second |
| **Microsecond (µs)** | One millionth of a second (1 µs = 0.000001 s) |
| **waitForBG()** | Barrier that waits for the background thread to finish, then sorts |
| **Probabilistic** | Uses random numbers to decide structure (e.g., which level a key reaches) |
| **Hierarchical promotion** | A key in Level 2 is guaranteed to also be in Levels 0 and 1 |
| **Streamlit** | Python web framework that turns Python scripts into interactive apps |
| **Session state** | Browser-local memory that survives button clicks but not page refresh |
| **JSON** | Text-based data format used to save/share the structure snapshots |
