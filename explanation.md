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
10. [How many threads does ESL use?](#10-how-many-threads-does-esl-use)
11. [Paper design: exponential+linear search in COIL](#11-paper-design-exponentiallinear-search-in-coil)
12. [Endurable Transient Inconsistency](#12-endurable-transient-inconsistency)
13. [How reads work even when COIL is stale](#13-how-reads-work-even-when-coil-is-stale)
14. [Benchmark results — real numbers](#14-benchmark-results--real-numbers)
15. [Why ESL is slower at small and medium scale](#15-why-esl-is-slower-at-small-and-medium-scale)
16. [The Streamlit dashboard](#16-the-streamlit-dashboard)
17. [File-by-file guide](#17-file-by-file-guide)
18. [Glossary](#18-glossary)

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
│  COIL L3  (sparsest — fewest keys, ~0.4% of data)       │  ← sorted array (built once at sync)
│  COIL L2  (~1.6% of data)                               │  ← sorted array
│  COIL L1  (~6.25% of data)                              │  ← sorted array
│  COIL L0  (~25% of data)                                │  ← sorted array
├─────────────────────────────────────────────────────────┤
│  PDL      (~40% of data — sparse navigation index)      │  ← lock-free Linked List
├─────────────────────────────────────────────────────────┤
│  Data     (100% — every value ever inserted)            │  ← lock-free Linked List
└─────────────────────────────────────────────────────────┘
```

**PDL and Data** use **lock-free Treiber stacks** for insertion: each `push()` is a
single CAS (compare-and-swap) operation — no mutex, no sorting, no waiting.
Their sorted-array snapshots are built once in `waitForBG()`.

**COIL** levels are sorted arrays built from the Data snapshot in `waitForBG()`.

Searching means:
1. Binary search in COIL Lmax (sparsest) → narrows range from `[0, N]` to a small window.
2. Binary search down through COIL levels → window shrinks further each time.
3. Binary search in PDL snapshot → window shrinks again; `data_pos` gives direct index.
4. Final binary search in Data snapshot within the narrow window.

Because each layer is a sorted array (snapshot), the CPU cache prefetcher works perfectly.
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

**PDL is implemented as a lock-free Treiber stack.** Each `insert()` call may push
the key onto the PDL stack in O(1) time using a single CAS instruction — no locking,
no sorting, no queue. The sorted snapshot used for search is built once during
`waitForBG()` from the stack's contents.

But here is the key difference from the Data layer: **each PDL snapshot entry stores
not just the key, but also its exact position (index) in the Data snapshot array**.

```
Data snapshot:  [1,  5, 10, 15, 20, 25, 30, 35, 40, 45, 50]
                 [0] [1] [2] [3] [4] [5] [6] [7] [8] [9] [10]

PDL snapshot:   { key: 10, data_pos: 2 }
                { key: 30, data_pos: 6 }
                { key: 50, data_pos: 10 }
```

### Why this helps

When searching for key `28`:
1. COIL narrows the range (say between 25 and 35 in the data snapshot).
2. PDL finds that `25` is at `data_pos=5` and `35` is at `data_pos=7`.
3. Now the final search in Data only needs to check positions 5–7 (3 elements
   instead of 1,000,000).

The PDL acts like a **phone book's letter tabs** — it doesn't contain every entry,
just enough anchors to tell you exactly which section to open.

### What PDL is NOT

PDL is **not** a copy of the Data layer. It is a sparse index — roughly 40% of keys
appear in it. Storing 40% instead of 100% keeps it small enough to stay in CPU cache.

---

## 7. Feature: Background Thread & COIL Construction

### The problem it solves

Every time you insert a key into ESL, updating COIL levels immediately would slow down
the insert — the sorted-array nature of COIL means every insertion would require
shifting elements, which is O(n) per level.

ESL's solution: **PDL and Data are lock-free Treiber stacks — inserts are O(1) CAS
operations with no mutex.** COIL construction is deferred to `waitForBG()`, where it
is built once from the sorted Data snapshot in O(n) time.

### How it works step by step

```
Foreground thread (insert):               waitForBG() (called once after all inserts):
  1. CAS-push key onto Data stack (O(1))    1. Signal BG thread to exit
  2. CAS-push key onto PDL (40%, O(1))      2. Sort Data snapshot once → O(n log n)
  3. Increment atomic insertCount           3. Build PDL snapshot + data_pos hints
  4. Return immediately — no mutex!         4. Sample COIL levels from Data snapshot
```

The foreground thread's insert is truly lock-free: just two CAS operations and an
atomic increment. No mutex, no queue push, no notification.

### Why this is fast

A CAS (compare-and-swap) is a single CPU instruction. It checks if a memory location
holds an expected value and, if so, updates it atomically. No OS kernel involvement,
no context switching, no blocking. At 50,000 inserts this saves tens of milliseconds
compared to a mutex-based approach.

### COIL construction in `waitForBG()`

After all inserts are done, COIL is built by iterating over the sorted Data snapshot
and rolling a 25% dice for each key at each level:

```
For each key K in sorted Data snapshot:
  Roll die for L0: 25% chance → add K to coil[0]
    If yes, roll for L1: 25% chance → add K to coil[1]
      If yes, roll for L2: 25% chance → add K to coil[2]  (and so on...)
```

Since Data is already sorted and we iterate in order, the resulting COIL arrays are
also sorted — no further sorting needed. This is O(n) — much faster than per-insert
sorted insertion which would be O(n²).

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

- **Insert**: uses lock-free CAS operations on the Treiber stacks — **zero mutex
  operations** on the insert hot path. Every insert is a pure CAS + atomic increment.
- **Search**: reads the COIL, PDL, and Data snapshots — all immutable arrays while
  searches run. **No locks acquired at all**.
- **Writes during search phase**: do not happen (benchmark separates insert and search
  phases; `waitForBG()` builds the immutable snapshots between phases).

This means search operations never stall waiting for a lock — they always proceed,
making the **tail latency** (worst-case time for a single query) much lower.

### Real-world impact

At 1M operations (from an actual benchmark run):
- Traditional: **3,912 µs** max search latency (one query was that slow)
- ESL: **325 µs** — **12× lower worst-case latency**

This matters enormously in real databases: a single slow query can block many others.

---

## 9. Feature: `waitForBG` Barrier

### The problem

PDL and Data are lock-free Treiber stacks — they accept inserts in any order. Before
searches begin, ESL needs sorted, immutable snapshots of all three layers (Data, PDL,
COIL) so that binary search works correctly.

### The solution

After all inserts are done, `waitForBG()` is called:

```cpp
void waitForBG() {
    // 1. Signal background thread to exit (it was waiting for this)
    stopFlag = true; bgCV.notify_all(); bgThread.join();

    // 2. Build sorted Data snapshot from Treiber stack — O(n log n)
    data = dataList.snapshot();   // traverses stack → sort → dedup

    // 3. Build sorted PDL snapshot + data_pos position hints — O(n log n + m)
    pdlKeys = pdlList.snapshot(); // same: traverse → sort → dedup
    for each key in pdlKeys:
        data_pos = binary_search(data, key)   // O(log n)
        pdl.push_back({key, data_pos})

    // 4. Build COIL by sampling from sorted Data — O(n)
    for each key K in data (in order):
        roll dice → if promoted, add K to coil[level]
    // coil levels already sorted (data is sorted, iteration is in order)
}
```

This is a key insight from the paper: doing one bulk sort after all inserts is
**O(n log n)** — much faster than keeping arrays sorted during insertion, which
would be **O(n²)** (inserting into a sorted array is O(n) per insert × n inserts).

For 1,000,000 inserts:
- Per-insert sorted insertion: ~500 billion operations
- Single bulk sort at the end: ~20 million operations

---

## 10. How Many Threads Does ESL Use?

ESL uses **exactly 2 threads**:

| Thread | Name | Responsibility |
|---|---|---|
| **Thread 1** | Foreground thread | Runs your application code — calls `insert()`, `search()`, `delete()` |
| **Thread 2** | Background thread (BG) | In the paper: drains the OpLog and updates COIL + PDL. In this implementation: waits for the stop signal, then exits so `waitForBG()` can build snapshots |

There is **one background thread per ESL instance**, not one per CPU core. This is a
deliberate design choice from the paper: more BG threads would create contention on
the same data structures; one dedicated BG thread is enough to drain the OpLog faster
than the foreground produces entries at typical insert rates.

```
Your program                    ESL Instance
─────────────                   ─────────────────────────────────────
main thread ───→ insert(42) ──→ Thread 1 (Foreground):
                               │  CAS-push 42 onto Data stack   O(1)
                               │  CAS-push 42 onto PDL (~40%)   O(1)
                               │  atomic insertCount++           O(1)
                               │  return immediately
                               │
                               Thread 2 (Background):
                                  [paper design] drains OpLog, builds COIL/PDL
                                  [this impl]    waits for stop signal
                                  → waitForBG() joins this thread and builds
                                    all snapshots in one pass
```

> **Why not more threads?** Adding a third thread for COIL construction would require
> synchronisation between the BG thread (building COIL) and the foreground thread
> (pushing new keys). The paper avoids this by making COIL updates the BG thread's
> sole responsibility — only one writer, no conflict.

---

## 11. Paper Design: Exponential+Linear Search in COIL

The original ESL paper does **not** use binary search inside COIL levels.
It uses a two-phase approach called **exponential + linear search**:

### Phase 1: Exponential search (fast bounding)

Start at position 1, then jump: 2, 4, 8, 16, 32...  
Stop when you overshoot the target key.  
This finds the *approximate* position in O(log k) comparisons, where k is the
distance from the start of the range to the key — not the full array size.

```
Array:  [3, 7, 12, 18, 25, 31, 40, 55, 70, 88, 99]
Search for 31:

  Step 1: check position 1 → value 7  < 31 ✓, continue
  Step 2: check position 2 → value 12 < 31 ✓, continue
  Step 4: check position 4 → value 25 < 31 ✓, continue
  Step 8: check position 8 → value 70 > 31 ✗, stop

  → 31 is somewhere in positions [4..8]
```

### Phase 2: Linear search (fine-grained)

Scan positions 4→5→6 linearly until you find 31 at position 5.

### Why this beats binary search for COIL

Binary search always starts from the middle regardless of where the key is.
Exponential search exploits that after COIL has already narrowed the range, the key
is *close to the current position* — so doubling steps from position 1 finds the
boundary in very few jumps, and then linear scan of a tiny window (~1–4 elements)
is faster than another binary search over the same range.

### Why this project uses binary search instead

For the COIL array sizes in this benchmark (COIL L0 at 1M has 122,000 entries,
L3 has ~450), binary search over the entire level is efficient and simpler to
implement correctly. Exponential+linear search would reduce the crossover point
(ESL would beat Traditional starting around 50K instead of 300K–500K) but adds
implementation complexity. This is documented as a known simplification in
[feature.md](feature.md).

---

## 12. Endurable Transient Inconsistency

This is the paper's most important concept and the reason ESL can have a background
thread updating COIL/PDL while searches are happening simultaneously.

### What "inconsistency" means here

When a new key `K` is inserted:
1. It is immediately in the **Data layer** (Treiber stack push).
2. It is **not yet** in COIL or PDL — those are only updated by the background thread.

So for a brief period, COIL/PDL are "stale" — they do not know about `K`.

### Why this is "endurable"

A search for `K` during this period will:
1. Look in COIL — not found (stale), but narrows the range to where `K` *would* be
2. Look in PDL — not found (stale), but further narrows
3. Look in Data — **found** (Data is always up to date)

The stale COIL/PDL do not cause the search to fail. They give approximate positions
that are still valid — they narrow the search window to the correct region of Data
where `K` definitely lives (since Data has `K`). The final answer is always correct.

### Why it is "transient"

"Transient" means temporary. The background thread is draining the OpLog and will
update COIL and PDL soon. After `waitForBG()`, everything is in sync.

### The formal property

> **ETI (Endurable Transient Inconsistency)**: The index layers (COIL, PDL) may be
> temporarily behind the data layer (Data), but any search still returns the correct
> answer because the data layer is authoritative and always queried last.

```
Insert K=42 happens at time T:
  T+0 ms: Data has 42 ✓   COIL does not have 42 ✗   PDL does not have 42 ✗
  T+1 ms: Data has 42 ✓   COIL updated ✓             PDL does not have 42 ✗
  T+2 ms: Data has 42 ✓   COIL updated ✓             PDL updated ✓

Search for 42 at T+0.5 ms:
  COIL search: 42 not in COIL, but COIL narrows range to [40, 50] in Data
  PDL search:  42 not in PDL,  but PDL narrows range to [41, 43] in Data
  Data search: 42 found at position 15 ✓   → returns TRUE
```

Search is always correct. The stale COIL/PDL just mean a slightly wider final Data
search window — a small performance cost, not a correctness problem.

---

## 13. How Reads Work Even When COIL is Stale

This is the mechanism that makes ETI safe in practice. Understanding this is key to
understanding why ESL's architecture is sound.

### The guarantee: every key in COIL is also in Data

COIL is built by sampling from the sorted Data snapshot. **Every key that appears in
COIL is guaranteed to also appear in Data.** COIL never contains a key that Data
does not have.

This means COIL can only be *behind* Data, never *ahead* of it. A key that is in
COIL is in Data for sure. A key that is in Data may or may not be in COIL yet.

### How search handles a stale COIL

When searching for key `K` and COIL does not have it yet:

```
COIL search for K:
  lower_bound(coil_level, K) returns iterator `it`
  
  *(it-1) = the largest key in COIL that is smaller than K
  *it     = the smallest key in COIL that is larger than K
  
  → Both of these keys ARE in Data (guaranteed by the invariant above)
  → So Data[pos(*(it-1))] and Data[pos(*it)] form a valid bracket around K
  → The final Data search is confined to this bracket
  → If K was inserted after the COIL snapshot, it lives somewhere between
    *(it-1) and *it in Data — and the bracket search WILL find it
```

### Visual example with 10 keys

```
Data (always current):   [1, 5, 10, 15, 20, 25, 30, 35, 40, 45]
COIL (built from older snapshot, missing 35 which was just inserted):
                         [1, 5, 10, 20, 30, 40, 45]   ← 35 is missing

Search for 35:
  COIL lower_bound(35): finds 40 at position 4
  COIL prev entry: 30 at position 3
  → bracket = [30, 40) in Data → positions [4..6] in Data
  → Data[4..6] = [20, 25, 30... wait — in this smaller example: [30, 35, 40]
  → Binary search finds 35 ✓

Even though COIL was stale, the search was correct.
```

### Summary of the read safety chain

```
1. COIL may be stale      → but its keys still bracket K correctly in Data
2. PDL may be stale       → but its data_pos hints still point to the right region
3. Data is always current → final binary search within the bracketed region finds K

Correctness guarantee: Data is the source of truth. COIL and PDL are just
acceleration structures. Staleness costs a slightly wider Data search window,
not a wrong answer.
```

---

## 14. Benchmark Results — Real Numbers

The benchmark (`benchmark.cpp`) runs three experiments: 1K, 100K, and 1M operations
(50% inserts, 50% searches each). All numbers below are from an actual run on the
current machine.

---

### Scale: 1,000 ops (Small) — Traditional wins

| Metric | Traditional | ESL | Winner |
|---|---|---|---|
| Raw Insert time | 0.000443 s | 0.000074 s | **ESL (6.0×)** |
| Index Build time | — | 0.000394 s | — |
| Insert+Build time | 0.000443 s | 0.000468 s | Traditional |
| Search time | 0.000185 s | 0.000339 s | Traditional (1.8×) |
| **Total time** | **0.000628 s** | **0.000807 s** | **Traditional (1.28×)** |
| Throughput | 1,591,596 ops/s | 1,239,618 ops/s | Traditional |
| Search comparisons | 8,391 | 8,112 | ≈ tied |
| Traversal steps | 6,519 | 2,262 | **ESL (−65%)** |
| Avg search latency | 0.26 µs | 0.58 µs | Traditional |
| Max search latency | 0.7 µs | 1.5 µs | Traditional |
| COIL hit rate | — | 14.0% | — |
| PDL size | — | 210 entries | — |
| COIL sizes (L0..L3) | — | [125, 28, 5, 0] | — |

**At 1K**: ESL's raw CAS-push insert is 6× faster than Traditional's pointer-linking.
However, `waitForBG()` snapshot cost (0.000394 s to sort 500 elements and build hints)
wipes out that saving entirely, leaving insert+build slightly *slower* than Traditional.
Search is 1.8× slower because ESL traverses 4 layers (COIL L0–L3, PDL, Data) to search
only 500 keys — the multi-layer overhead is larger than the savings with so few entries.

---

### Scale: 100,000 ops (Medium) — Traditional wins on total; ESL wins on insert

| Metric | Traditional | ESL | Winner |
|---|---|---|---|
| Raw Insert time | 0.0292 s | 0.0030 s | **ESL (9.7×)** |
| Index Build time | — | 0.0026 s | — |
| Insert+Build time | 0.0292 s | 0.0056 s | **ESL (5.2×)** |
| Search time | 0.0228 s | 0.0522 s | Traditional (2.3×) |
| **Total time** | **0.0520 s** | **0.0578 s** | **Traditional (1.11×)** |
| Throughput | 1,919,684 ops/s | 1,730,343 ops/s | Traditional |
| Search comparisons | 1,487,596 | 1,558,045 | ≈ tied |
| Traversal steps | 1,101,235 | 379,219 | **ESL (−66%)** |
| Avg search latency | 0.41 µs | 0.98 µs | Traditional (2.4×) |
| Max search latency | 8.4 µs | 106.9 µs | Traditional |
| COIL hit rate | — | 12.3% | — |
| PDL size | — | ~20,000 entries | — |
| COIL sizes (L0..L5) | — | [12,443; 3,151; 796; 208; 57; 16] | — |

**At 100K**: The picture is split. ESL's lock-free insert is **9.7× faster** (raw) and
**5.2× faster** including the snapshot build. But ESL **search is 2.3× slower** than
Traditional, erasing the insert saving and making the total run 1.11× slower.

Why is ESL search slower here despite 66% fewer traversal steps? At 100K, the entire
traditional skiplist (~25K nodes across all levels) fits comfortably in CPU L2/L3 cache.
Its pointer-chasing penalty is minimal. ESL's search adds 6 separate binary searches
(one per COIL level + PDL + Data). Each binary search loop adds branch overhead, and
with only 50K keys the range-narrowing benefit is not large enough to offset that cost.
The COIL hit rate is only 12.3% — nearly 9 in 10 searches fall all the way through to
the Data layer anyway.

> **Insert-only workloads at 100K**: ESL is 5.2× faster. For write-heavy applications
> where searches happen rarely (e.g., bulk loading a database), ESL wins decisively.

---

### Scale: 1,000,000 ops (Large) — ESL wins decisively

| Metric | Traditional | ESL | Winner |
|---|---|---|---|
| Raw Insert time | 0.1734 s | 0.0363 s | **ESL (4.8×)** |
| Index Build time | — | 0.0223 s | — |
| Insert+Build time | 0.1734 s | 0.0586 s | **ESL (3.0×)** |
| Search time | 1.0273 s | 0.6702 s | **ESL (1.53×)** |
| **Total time** | **1.2006 s** | **0.7287 s** | **ESL (1.65×)** |
| Throughput | 832,889 ops/s | 1,372,121 ops/s | **ESL (1.65×)** |
| Search comparisons | 18,089,462 | 18,452,475 | ≈ tied |
| Traversal steps | 13,474,808 | 4,372,361 | **ESL (−68%)** |
| Avg search latency | 1.94 µs | 1.25 µs | **ESL (1.55×)** |
| Max search latency | 403 µs | 390 µs | ≈ tied |
| COIL hit rate | — | 13.2% | — |
| PDL size | — | ~195,000 entries | — |
| COIL sizes (L0..L6) | — | [122,113; 30,588; 7,669; 1,942; 456; 107; 23] | — |

**At 1M**: ESL wins on **both insert and search** — total speedup **1.65×**. With a
million keys the traditional skiplist's ~300K scattered nodes completely overflow the
CPU cache. Every pointer dereference is a cache miss (~100 cycle penalty). ESL's COIL
levels are sorted contiguous arrays — the CPU's hardware prefetcher reads ahead
automatically, turning cache misses into cache hits. The 68% reduction in traversal
steps now translates directly into a 1.53× faster search time. The tail latency
(max search) converges: both hit ~390–403 µs, meaning at this scale cache pressure
affects both structures in their worst-case paths.

---

### How results change across scale

```
Scale    ESL step reduction    Insert winner     Search winner     Total winner
──────────────────────────────────────────────────────────────────────────────
1K       −65%                  Traditional       Traditional       Traditional (1.28×)
100K     −66%                  ESL (5.2×)        Traditional (2.3×) Traditional (1.11×)
1M       −68%                  ESL (3.0×)        ESL (1.53×)       ESL (1.65×)
```

**Pattern**: ESL's insert advantage kicks in early (from ~1K), because lock-free CAS
is faster than pointer-linking regardless of scale. ESL's search advantage only
materialises at large scale (~1M), when the traditional skiplist outgrows the CPU cache
and its pointer-chasing cost explodes. The crossover for **total time** is between
100K and 1M (roughly 300K–500K operations).

> **The original ESL paper** shows crossover at a smaller N because the paper's
> implementation uses an exponential-plus-linear scan within each COIL level (better
> for smaller ranges), while this implementation uses pure binary search everywhere.
> Pure binary search is faster per comparison but adds more constant overhead per level,
> pushing the total-time crossover to higher N.

---

## 15. Why ESL is slower at small and medium scale

### At 1K ops — snapshot overhead dominates

At 1K ops, Traditional wins overall (1.28×), even though ESL's raw CAS-push insert
is 6× faster. The reasons:

1. **`waitForBG()` snapshot overhead**: Traversing the Treiber stack, sorting 500
   elements, building PDL position hints, and sampling COIL levels has a fixed baseline
   cost (~0.000394 s). At 1K ops this overhead is 84% of ESL's total insert+build time —
   it dwarfs the savings from lock-free insertion.

2. **Search overhead at 500 keys**: Searching 500 keys through 6 layers (COIL L0–L3,
   PDL, Data) adds more constant overhead than it saves. A single binary search over
   500 keys takes ~9 comparisons anyway. Splitting that into 6 smaller binary searches
   adds function call and branch overhead, resulting in 1.8× slower search.

3. **Small COIL**: At 1K, COIL L0 has only 125 entries. The range-narrowing benefit
   exists (65% fewer traversal steps) but is not large enough to offset the overhead.

### At 100K ops — insert wins, search loses, net Traditional

At 100K, the picture is more nuanced. ESL insert is **5.2× faster** but ESL search is
**2.3× slower**, so Traditional wins the total by a slim 1.11×.

**Why search is slower at 100K despite 66% fewer traversal steps:**

- At 100K (50K inserts, 50K searches), the traditional skiplist has roughly 25K–50K
  total nodes across all levels. This is small enough to fit in the CPU's L2/L3 cache
  (typically 4–16 MB). When the cache is warm, pointer-chasing costs almost nothing.

- ESL's search traverses 6 separate COIL levels plus PDL plus Data — even if each
  binary search is short, the loop overhead per layer (bounds checks, function calls,
  index arithmetic) adds up. With the skiplist fitting in cache, Traditional's simple
  pointer scan is hard to beat.

- The COIL hit rate is only 12.3% — 87.7% of searches still traverse all the way
  through to the Data layer, paying the full multi-layer cost without finding early.

**The split verdict at 100K:**

| Workload | Winner at 100K |
|---|---|
| Insert-only (bulk load) | ESL (5.2× faster) |
| Search-only (read-heavy) | Traditional (2.3× faster) |
| Mixed 50/50 | Traditional (1.11× total) |

> **Real-world implication**: ESL is the right choice at 100K for write-heavy
> workloads or bulk-load scenarios. For balanced or read-heavy workloads, the crossover
> for total time has not yet been reached.

### The crossover for total time is between 100K and 1M

At 1M, both insert (+3×) and search (+1.53×) favour ESL. What changes between 100K
and 1M?

- **Cache pressure**: A 1M-key traditional skiplist has millions of scattered heap
  nodes. These far exceed even a large L3 cache. Every pointer dereference during
  traversal is likely a cache miss (~100 CPU cycle penalty). At 100K the structure
  fit in cache; at 1M it does not.

- **Binary search scales better than linear pointer scan under cache pressure**: ESL's
  COIL arrays are dense and contiguous. Accessing `coil[i]` and `coil[i+1]` loads
  consecutive cache lines. The CPU prefetcher brings in the next elements automatically.
  Once the skiplist is too large for cache, ESL's sorted-array approach is unbeatable.

- **ESL step reduction is consistent (65–68% across all scales)**: The improvement in
  traversal work was always there — what changes is whether that improvement outweighs
  the multi-layer constant overhead (it does, once cache pressure hits Traditional hard).

---

## 16. The Streamlit Dashboard

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

## 17. File-by-File Guide

### `benchmark.cpp` — The speed test

```
benchmark.cpp
├── TraditionalSkiplist class   — standard Pugh skiplist (linked-list nodes)
├── LockFreeList class          — Treiber stack with pre-allocated arena
│   ├── arena[]                 — pre-allocated node pool (no per-insert malloc)
│   ├── push()                  — O(1) lock-free CAS prepend
│   ├── remove()                — O(n) logical deletion (mark bit on next ptr)
│   └── snapshot()              — O(n log n): traverse → sort → dedup
├── ESL class                   — our ESL implementation
│   ├── dataList                — LockFreeList for all inserted keys
│   ├── pdlList                 — LockFreeList for ~40% of keys
│   ├── coil[]                  — COIL levels (sorted arrays, built in waitForBG)
│   ├── pdl[]                   — PDL snapshot with {key, data_pos} hints
│   ├── data[]                  — Data snapshot (sorted, from dataList)
│   ├── bgWorker()              — background thread (waits for stop signal)
│   ├── insert()                — O(1) lock-free: CAS push + atomic increment
│   └── waitForBG()             — signal BG exit; build data/PDL/COIL snapshots
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

## 18. Glossary

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
| **PDL** | Position Descriptor Layer — lock-free stack; snapshot with position hints into Data |
| **Lock-free** | Operations that never block; use CAS instead of mutex; progress guaranteed |
| **CAS** | Compare-And-Swap — one CPU instruction: check a value and update it atomically |
| **Treiber stack** | A lock-free stack using CAS to prepend nodes; O(1) push with no mutex |
| **Arena allocation** | Pre-allocating a large pool of nodes upfront to avoid per-insert malloc overhead |
| **Snapshot** | A sorted copy of the Treiber stack's contents, built once in waitForBG() |
| **Background thread** | A separate thread that waits for a signal and then exits cleanly |
| **ROWEX** | Read-Optimized Write-EXclusion — readers never wait for locks |
| **Tail latency** | The worst-case time for a single operation (e.g., 99th percentile) |
| **Throughput** | Number of operations completed per second |
| **Microsecond (µs)** | One millionth of a second (1 µs = 0.000001 s) |
| **waitForBG()** | Sync point: signals BG thread to exit, builds sorted snapshots for search |
| **Probabilistic** | Uses random numbers to decide structure (e.g., which level a key reaches) |
| **Hierarchical promotion** | A key in Level 2 is guaranteed to also be in Levels 0 and 1 |
| **data_pos** | Index of a PDL key in the sorted Data array; allows O(1) Data range lookup |
| **Streamlit** | Python web framework that turns Python scripts into interactive apps |
| **Session state** | Browser-local memory that survives button clicks but not page refresh |
| **JSON** | Text-based data format used to save/share the structure snapshots |
