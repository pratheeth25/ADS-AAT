# Feature Implementation Status

This document lists every feature described in the ESL paper (2023) and marks whether
it is implemented, partially implemented, or not implemented in this project — with a
plain-language explanation for each decision.

---

## Legend

| Symbol | Meaning |
|---|---|
| ✅ | Fully implemented |
| ⚠️ | Partially implemented — core idea present, some aspects simplified |
| ❌ | Not implemented |

---

## Core Data Structure Features

### ✅ COIL — Cache-Optimized Index Levels

**What the paper says:** Replace traditional skiplist pointer levels with sorted
contiguous arrays so that traversal is cache-friendly and binary search applies.

**What is implemented:** COIL is built as a set of sorted `vector<int>` arrays.
The number of levels is computed dynamically: `max(4, min(8, (int)(log2(N)/3.5)+2))`,
giving 4 levels at 1K, 6 at 100K, 7 at 1M. Keys are probabilistically promoted with
p = 0.25 per level. COIL is built in `waitForBG()` from the sorted Data snapshot in
O(n) — keys are already in order so no extra sort is needed.

---

### ✅ PDL — Position Descriptor Layer

**What the paper says:** A sparse index layer between COIL and Data that stores
`{key, data_pos}` pairs — the exact index into the Data array where each PDL key
sits — so the final binary search in Data can be confined to a tight window.

**What is implemented:** About 40% of inserted keys are randomly promoted to PDL
using a lock-free Treiber stack. The sorted PDL snapshot is built in `waitForBG()`
and each entry stores `data_pos = bisect_left(data, key)`. The search path uses
two adjacent PDL entries to set `[lo, hi]` bounds on the Data search. The Python
dashboard visualization (`app.py`) also replicates this logic exactly.

---

### ✅ Data Layer — lock-free Treiber stack

**What the paper says:** All inserted keys go into a flat sorted layer. Insertion
should be O(1) and lock-free.

**What is implemented:** `LockFreeList` uses a pre-allocated arena of `LFNode` structs
(sized `expectedN/2 + 100` to prevent overflow). `push()` is a single CAS prepend
— no mutex, no sorted insertion, no memory allocation per insert. The sorted snapshot
is built once in `waitForBG()` via `snapshot()` which traverses the stack, collects
live (non-logically-deleted) nodes, sorts, and deduplicates.

---

### ✅ PDL Layer — lock-free Treiber stack

**What the paper says:** PDL entries should be insertable lock-free.

**What is implemented:** Identical `LockFreeList` as Data, with its own arena. Each
`insert()` call does a second CAS-push to `pdlList` with ~40% probability. No mutex
anywhere on the insert hot path.

---

### ✅ Arena allocation — no per-insert malloc

**What the paper says:** Dynamic memory allocation per node is a bottleneck in
high-throughput scenarios.

**What is implemented:** Both `dataList` and `pdlList` pre-allocate their node
arrays as `unique_ptr<LFNode[]>` upfront. All nodes come from this arena via an
`atomic<int> arenaIdx` counter. At `expectedN/2 + 100` slots each, there is no
dynamic allocation during inserts.

---

### ✅ Hierarchical search — COIL → PDL → Data

**What the paper says:** Search should narrow the range top-down through COIL levels,
then through PDL, then perform a final binary search in a tiny window of Data.

**What is implemented:** The C++ `search()` and Python `search_esl()` both implement
the full three-phase range-narrowing path. Each COIL level's sorted array narrows
`[lo, hi]`. PDL's sorted snapshot tightens it further using `data_pos` hints. The
final binary search only runs over `data[lo..hi]`.

---

### ✅ Dynamic COIL level count based on N

**What the paper says:** The number of COIL levels should scale with the dataset size
to keep each level's array small enough to fit in cache.

**What is implemented:**
```cpp
int coilLevels = max(4, min(8, (int)(log2(max(N,2)) / 3.5) + 2));
```
- N = 1,000 → 4 levels
- N = 100,000 → 6 levels
- N = 1,000,000 → 7 levels

---

### ✅ `waitForBG()` barrier pattern

**What the paper says:** A synchronisation barrier is needed before searches begin
to ensure all snapshots are built and immutable.

**What is implemented:** `waitForBG()` signals the background thread to exit, joins
it, then builds Data snapshot, PDL snapshot (with `data_pos` hints), and COIL levels
all at once. After it returns, all three layers are immutable sorted arrays — safe for
lock-free reads with no further synchronisation.

---

### ✅ Probabilistic level promotion (p = 0.25)

**What the paper says:** Keys are promoted into COIL levels with a geometric
probability (same as traditional skiplist), ensuring O(log n) expected levels per key.

**What is implemented:** In `waitForBG()`, each key in sorted Data is promoted to
successive COIL levels with 25% probability at each step. In the Python dashboard,
the same dice-roll logic is applied during interactive inserts.

---

## Concurrency Features

### ⚠️ ROWEX Concurrency — insert path is lock-free; true concurrent R+W not implemented

**What the paper says:** Read-Optimized Write-EXclusion (ROWEX) allows inserts and
reads to proceed concurrently. Writers use lock-free CAS on shared structures; readers
use immutable snapshots and never acquire locks.

**What is implemented:** The insert hot path is fully lock-free: two CAS-pushes (Data
+ PDL) and one atomic increment — zero mutex operations. Readers search immutable
sorted snapshots (built once in `waitForBG()`) with no locks.

**What is NOT implemented:** True concurrent insert + search (write while reading).
In this project the benchmark separates phases: all inserts happen first, then
`waitForBG()` builds snapshots, then all searches run. The paper supports inserting
new keys while readers are actively searching; those new keys would not appear in
current reader snapshots but would be visible after the next `waitForBG()`. This
requires a version counter or epoch-based snapshot management that is not implemented
here.

**Why not implemented:** The benchmark is designed to measure the structural performance
advantage (cache-friendly arrays, lock-free inserts), not the full concurrent access
control. Adding true concurrent R+W would require version management and make the
insert/search timing measurements harder to interpret.

---

### ❌ OpLog queue — background thread draining inserts asynchronously

**What the paper says:** Inserts push a `{key, type}` operation record onto a
concurrent lock-free queue (the OpLog). A background thread continuously drains
the OpLog and incrementally builds COIL + PDL structures while inserts are still happening.

**What was implemented and then removed:** An earlier version of `benchmark.cpp` had
exactly this: each `insert()` pushed to a `std::queue` protected by a mutex, and a
background thread drained it. It was removed because the mutex overhead on the OpLog
push added 200–300 ns per insert, erasing all the lock-free insert savings and making
ESL slower at 100K than a simple `std::mutex`-free Traditional skiplist.

**Why not implemented in final version:** The OpLog's lock-free concurrent queue
(as described in the paper) requires a complex multi-producer, single-consumer
lock-free queue (e.g. Michael-Scott queue). The paper's implementation uses a custom
SPSC or MPSC queue. Implementing one correctly in C++ without introducing subtle ABA
problems was out of scope. Instead, the BG thread was simplified to just wait for a
stop signal, and all index building was moved to `waitForBG()`.

---

### ❌ Incremental COIL + PDL builds during insert phase

**What the paper says:** As the BG thread drains the OpLog, it incrementally inserts
new keys into the COIL arrays (maintaining sorted order) and updates PDL entries.

**What is implemented instead:** COIL and PDL are built in a single O(n log n) pass in
`waitForBG()` after all inserts finish. This gives the same final result but does not
overlap compute with insert time. For the benchmark's separated-phase design this makes
no practical difference — the build time is included in `insert_plus_build_time`.

**Why not implemented:** Incremental sorted insertion into a vector is O(n) per insert
(element shift), making the total O(n²). Incremental binary-indexed tree or skip-list-
based sorted structure would be needed to do this efficiently. The paper's approach
likely maintains a more complex in-memory structure than a plain sorted vector.

---

### ❌ Concurrent insert + search (true MVCC)

**What the paper says:** Multiple reader threads can search concurrently with writer
threads inserting new keys. Readers see a consistent snapshot; new inserts appear in
the next snapshot.

**What is implemented instead:** Phases are separated. Search only starts after
`waitForBG()` completes.

**Why not implemented:** Multi-version concurrency control (MVCC) or epoch-based
reclamation is required to safely hand off snapshot ownership. This adds substantial
complexity (memory reclamation, version counters, reader registration) and is outside
the scope of this benchmark, which focuses on structural performance.

---

## Search & Traversal Features

### ⚠️ Exponential + linear search within COIL levels

**What the paper says:** Within each COIL level, use an exponential search (doubling
step) to quickly bound the range, then linear search to find the exact position. This
is faster than binary search for small ranges and warm-cache scenarios.

**What is implemented instead:** Standard binary search (`bisect_left` in Python;
`std::lower_bound` in C++) within each COIL level and PDL.

**Why binary search instead:** Pure binary search is simpler to implement correctly
and is asymptotically optimal. Exponential + linear search is better for small
arrays (< ~32 elements) that fit in a single cache line, but for the array sizes
seen here (COIL L0 at 1M has 122K entries), binary search dominates. The paper's
hybrid approach would lower the crossover point (ESL would win earlier at ~50K instead
of ~300K), but it was not implemented to keep the code straightforward.

---

### ✅ PDL `data_pos` position hints for O(1) Data range lookup

**What the paper says:** Each PDL entry should store the exact index into Data so
that the Data binary search starts from a precisely bounded range rather than the
full array.

**What is implemented:** `data_pos = bisect_left(data, key)` is stored for every PDL
entry at snapshot build time. During search, two adjacent PDL entries' `data_pos`
values directly give `[lo, hi]` for the final Data binary search.

---

## Delete Features

### ⚠️ Logical deletion in lock-free list

**What the paper says:** Deletions in the lock-free linked list should use logical
deletion — mark the node's `next` pointer's low bit as a "deleted" flag, then
physically remove it lazily during the next traversal.

**What is implemented:** `LockFreeList::remove()` walks the stack and sets the
low-bit mark on matching nodes. The `snapshot()` method skips logically-deleted nodes.
This is Harris-style logical deletion applied to the Treiber stack.

**What is NOT implemented:** `remove()` is O(n) (linear scan of the stack). The paper's
lock-free delete is O(1) for the logical mark but may require helping and retrying for
physical removal. In the benchmark, delete is not exercised (the benchmark only does
inserts and searches). The `visualize.cpp` interactive tool supports delete but calls
the O(n) `remove()` directly without a background cleanup thread.

---

### ❌ Background delete compaction

**What the paper says:** Physical removal of logically-deleted nodes from the Treiber
stack should happen lazily in the background to avoid O(n) compaction on the critical
path.

**Why not implemented:** The benchmark workload is 50% insert + 50% search; deletes
are not benchmarked. Background compaction adds significant complexity (safe memory
reclamation, hazard pointers or epoch-based reclamation) with no benefit for the
measured workload.

---

## Range Query Features

### ❌ Range scan / iterator

**What the paper says:** ESL should support efficient range queries (e.g., return all
keys in [lo, hi]) by scanning the sorted Data snapshot within bounds.

**Why not implemented:** The benchmark and dashboard only use point queries (insert,
search, delete). A range scan over the sorted Data snapshot would be trivial to add
(just `lower_bound + iterate`), but it was not needed for the performance comparison.

---

## Infrastructure Features

### ✅ Benchmark JSON export

Implemented. `benchmark.cpp` writes `benchmark_results.json` with all timing, latency,
comparison count, COIL hit rate, PDL size, and COIL level size data for all three scales.

---

### ✅ Interactive CLI (visualize.cpp)

Implemented. Supports insert, search, delete with live `structure.json` output that
the Streamlit dashboard auto-syncs.

---

### ✅ Streamlit dashboard

Implemented with three tabs: Performance (Tab 1), Structure Visualization (Tab 2),
Thread & BG Logs (Tab 3). Tab 3 shows the live operation log in real time from session
state.

---

### ❌ Persistence / Write-ahead log (WAL)

**Not in ESL paper scope.** ESL is an in-memory data structure. Persistence is a
storage-layer concern. Not implemented.

---

### ❌ Network / client-server API

**Not in ESL paper scope.** The paper focuses on the in-memory index structure.
Not implemented.

---

## Summary Table

| Feature | Status | Notes |
|---|---|---|
| COIL sorted-array levels | ✅ | Fully implemented |
| PDL with `data_pos` hints | ✅ | Fully implemented |
| Data Layer — Treiber stack | ✅ | CAS-push, arena-allocated |
| PDL Layer — Treiber stack | ✅ | CAS-push, arena-allocated |
| Arena allocation (no per-insert malloc) | ✅ | `expectedN/2 + 100` per list |
| COIL → PDL → Data hierarchical search | ✅ | Full range-narrowing path |
| Dynamic COIL level count | ✅ | log2-based formula |
| `waitForBG()` barrier | ✅ | Builds snapshots once, O(n log n) |
| Probabilistic level promotion (p=0.25) | ✅ | Same as traditional skiplist |
| ROWEX insert hot path (lock-free) | ✅ | Zero mutex on insert |
| ROWEX concurrent R+W (true MVCC) | ❌ | Phases separated; no version management |
| OpLog queue (BG drain) | ❌ | Removed: mutex overhead > savings |
| Incremental COIL/PDL build | ❌ | Replaced by single bulk build in waitForBG |
| Exponential+linear COIL search | ⚠️ | Binary search used instead |
| Logical deletion (mark bit) | ⚠️ | Implemented; O(n) remove, no BG compaction |
| Background delete compaction | ❌ | Not needed for benchmark workload |
| Range scan / iterator | ❌ | Trivial to add; not needed |
| Persistence / WAL | ❌ | Out of paper scope |
| Network API | ❌ | Out of paper scope |
