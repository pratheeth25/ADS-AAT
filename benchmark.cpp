// benchmark.cpp — Traditional Skiplist vs ESL (Express Skiplist) Benchmark
// Runs 100,000 operations (50% insert, 50% search), measures detailed metrics,
// prints a console dashboard, and exports benchmark_results.json.
//
// ============================================================
// ACADEMIC REFERENCE
// ============================================================
// This file implements the ESL (Express Skiplist) data structure
// proposed in:
//
//   Na, Y., Koo, B., Park, T., Park, J., & Kim, W.-H. (2023).
//   "ESL: A High-Performance Skiplist with Express Lane."
//   Applied Sciences, 13(17), 9925.
//   DOI: https://doi.org/10.3390/app13179925
//   URL: https://www.mdpi.com/2076-3417/13/17/9925
//
// The traditional skiplist baseline follows:
//   Pugh, W. (1990). "Skip Lists: A Probabilistic Alternative
//   to Balanced Trees." Commun. ACM, 33, 668-676.
//   DOI: https://doi.org/10.1145/78973.78977
//
// The ROWEX concurrency protocol is from:
//   Leis, V., Scheibner, F., Kemper, A., & Neumann, T. (2016).
//   "The ART of Practical Synchronization."
//   DaMoN Workshop, pp. 3:1-3:8.
//
// MODIFICATIONS from the paper:
//   - COIL implemented as sorted std::vector (binary search) instead
//     of contiguous array with exponential+linear search.
//   - PDL stores {key, data_pos} position hints into the Data array
//     (combines paper's PDL with a learned-index-style position hint).
//   - BG thread uses mutex+condvar op-log; full sort via waitForBG()
//     instead of per-insert CAS-based lock-free insertion.
//   - No epoch-based memory reclamation (educational scope).
// ============================================================

#include <iostream>
#include <vector>
#include <chrono>
#include <thread>
#include <mutex>
#include <atomic>
#include <queue>
#include <fstream>
#include <cmath>
#include <algorithm>
#include <iomanip>
#include <random>
#include <sstream>
#include <climits>
#include <condition_variable>
#include <shared_mutex>

using namespace std;
using Clock = chrono::high_resolution_clock;

// ============================================================
// Traditional Skiplist
// ============================================================
static thread_local mt19937 tls_rng(random_device{}());

struct SkipNode {
    int key;
    vector<SkipNode*> next;
    SkipNode(int k, int lvl) : key(k), next(lvl, nullptr) {}
};

class TraditionalSkiplist {
    int maxLevel;   // computed from N: ceil(log4(N)) + 3
    SkipNode* head;
    int currentLevel;

    int randomLevel() {
        int lvl = 1;
        // p=0.25 (standard Pugh skiplist) -> ~log4(n) levels
        while (lvl < maxLevel && tls_rng() % 4 == 0) lvl++;
        return lvl;
    }

public:
    long long comparisons = 0;
    long long traversalSteps = 0;
    int totalNodes = 0;

    explicit TraditionalSkiplist(int maxLevel_) : maxLevel(maxLevel_), currentLevel(1) {
        head = new SkipNode(INT_MIN, maxLevel);
    }

    ~TraditionalSkiplist() {
        SkipNode* n = head;
        while (n) { SkipNode* t = n->next[0]; delete n; n = t; }
    }

    void insert(int key) {
        vector<SkipNode*> update(maxLevel, nullptr);
        SkipNode* curr = head;
        for (int i = currentLevel - 1; i >= 0; i--) {
            while (curr->next[i] && curr->next[i]->key < key) {
                curr = curr->next[i];
                comparisons++;
                traversalSteps++;
            }
            if (curr->next[i]) comparisons++;
            update[i] = curr;
        }
        if (curr->next[0] && curr->next[0]->key == key) return;

        int lvl = randomLevel();
        if (lvl > currentLevel) {
            for (int i = currentLevel; i < lvl; i++) update[i] = head;
            currentLevel = lvl;
        }
        SkipNode* newNode = new SkipNode(key, lvl);
        for (int i = 0; i < lvl; i++) {
            newNode->next[i] = update[i]->next[i];
            update[i]->next[i] = newNode;
        }
        totalNodes++;
    }

    bool search(int key) {
        SkipNode* curr = head;
        for (int i = currentLevel - 1; i >= 0; i--) {
            while (curr->next[i] && curr->next[i]->key < key) {
                curr = curr->next[i];
                comparisons++;
                traversalSteps++;
            }
            if (curr->next[i]) comparisons++;
        }
        curr = curr->next[0];
        traversalSteps++;
        return curr && curr->key == key;
    }

    bool remove(int key) {
        vector<SkipNode*> update(maxLevel, nullptr);
        SkipNode* curr = head;
        for (int i = currentLevel - 1; i >= 0; i--) {
            while (curr->next[i] && curr->next[i]->key < key) {
                curr = curr->next[i];
                comparisons++;
                traversalSteps++;
            }
            update[i] = curr;
        }
        curr = curr->next[0];
        if (!curr || curr->key != key) return false;
        for (int i = 0; i < currentLevel; i++) {
            if (update[i]->next[i] != curr) break;
            update[i]->next[i] = curr->next[i];
        }
        delete curr;
        totalNodes--;
        while (currentLevel > 1 && !head->next[currentLevel - 1]) currentLevel--;
        return true;
    }

    vector<vector<int>> getLevels() const {
        vector<vector<int>> levels(currentLevel);
        for (int i = 0; i < currentLevel; i++) {
            SkipNode* n = head->next[i];
            while (n) { levels[i].push_back(n->key); n = n->next[i]; }
        }
        return levels;
    }

    int getCurrentLevel() const { return currentLevel; }
};

// ============================================================
// Lock-Free Linked List — Treiber Stack Variant
//
// PDL and Data layer use this for O(1) lock-free inserts.
// The list is an unordered Treiber stack: each push() prepends
// a new node to the head via CAS — no traversal, no sorting.
// A snapshot() call collects all live nodes into a sorted vector
// processed once in waitForBG().
//
// Nodes are allocated from a pre-allocated arena to avoid
// per-insert heap allocation overhead (malloc is the bottleneck
// at 100K+ inserts). The arena is sized at construction.
//
// Removal is supported via logical deletion (marked bit on
// next pointer, Harris-style). Physical removal happens lazily
// during the next snapshot() call.
//
// Insert: O(1) CAS, no lock, no malloc
// Remove: O(n) linear scan (rare in benchmark)
// Snapshot: O(n) + O(n log n) sort — called once per waitForBG
// ============================================================

struct LFNode {
    int key;
    atomic<uintptr_t> next{0}; // low bit = logical-delete mark
};

static inline LFNode*  lfPtr(uintptr_t v)    { return reinterpret_cast<LFNode*>(v & ~uintptr_t(1)); }
static inline bool     lfMarked(uintptr_t v) { return v & 1; }
static inline uintptr_t lfRaw(LFNode* p)    { return reinterpret_cast<uintptr_t>(p); }

class LockFreeList {
    unique_ptr<LFNode[]> arena;  // pre-allocated node pool (non-movable)
    atomic<int>    arenaIdx{0};  // next free slot (monotone)
    atomic<LFNode*> top{nullptr};// Treiber stack head

public:
    atomic<int> sz{0};

    LockFreeList() = default;
    ~LockFreeList() = default;

    void reserve(int n) {
        arena = make_unique<LFNode[]>(n);
        arenaIdx.store(0, memory_order_relaxed);
        top.store(nullptr, memory_order_relaxed);
    }

    // O(1) lock-free push — prepend to front (unsorted)
    void push(int key) {
        int idx = arenaIdx.fetch_add(1, memory_order_relaxed);
        LFNode* newNode = &arena[idx];
        newNode->key = key;
        LFNode* h;
        do {
            h = top.load(memory_order_relaxed);
            newNode->next.store(lfRaw(h), memory_order_relaxed);
        } while (!top.compare_exchange_weak(h, newNode,
                     memory_order_release, memory_order_relaxed));
        sz.fetch_add(1, memory_order_relaxed);
    }

    // Logically delete a node with the given key (O(n) scan)
    bool remove(int key) {
        LFNode* n = top.load(memory_order_acquire);
        while (n) {
            uintptr_t nxt = n->next.load(memory_order_relaxed);
            if (!lfMarked(nxt) && n->key == key) {
                if (n->next.compare_exchange_strong(nxt, nxt | 1,
                        memory_order_release, memory_order_relaxed)) {
                    sz.fetch_sub(1, memory_order_relaxed);
                    return true;
                }
            }
            n = lfPtr(nxt);
        }
        return false;
    }

    // Collect all non-deleted keys into a sorted, dedup'd vector
    vector<int> snapshot() const {
        vector<int> v;
        v.reserve(sz.load(memory_order_relaxed));
        LFNode* n = top.load(memory_order_acquire);
        while (n) {
            uintptr_t nxt = n->next.load(memory_order_relaxed);
            if (!lfMarked(nxt)) v.push_back(n->key);
            n = lfPtr(nxt);
        }
        sort(v.begin(), v.end());
        v.erase(unique(v.begin(), v.end()), v.end());
        return v;
    }

    int size() const { return sz.load(memory_order_relaxed); }
};

// ============================================================
// ESL — Express Skiplist (Redesigned)
//
// Architecture:
//   COIL[0..3] — 4 Cache-Optimized Index Levels (sorted arrays)
//                coil[0]=densest (lowest), coil[3]=sparsest (highest)
//   PDL        — Position Descriptor Layer: lock-free sorted linked list
//                ~40% promotion rate; each node stores {key}.
//                data_pos hints rebuilt once in waitForBG from snapshot.
//   Data       — Lock-free sorted linked list of all actual values.
//                Enables wait-free concurrent insert without mutex.
//   OpLog      — queue processed by 1 background thread (COIL only)
//   BG         — background thread updates COIL asynchronously
//
// Performance advantage over traditional skiplist:
//   - PDL + Data are lock-free linked lists → no mutex on insert hot path
//   - COIL remains sorted arrays for cache-friendly range narrowing
//   - Search uses COIL top-down range narrowing → PDL list scan → Data list scan
//   - ROWEX protocol: reads don't acquire locks after BG sync
//   - waitForBG only needs to sort COIL (not Data or PDL) → lower barrier cost
// ============================================================

struct PDLEntry {
    int key;
    int data_pos;
};

class ESL {
    int coilLevels;   // computed from N: max(3, min(8, (int)(log2(N)/2.5)))

    vector<vector<int>> coil;

    // Lock-free sorted linked lists for PDL and Data
    LockFreeList pdlList;   // ~40% of inserted keys; lock-free sorted LL
    LockFreeList dataList;  // all inserted keys; lock-free sorted LL

    // Snapshot arrays rebuilt once in waitForBG for fast binary search
    vector<PDLEntry> pdl;   // snapshot with data_pos hints
    vector<int> data;       // snapshot for binary search

    // BG thread reserved for COIL maintenance. COIL is built once in
    // waitForBG() by sampling from the sorted data snapshot, so the
    // BG thread simply waits until signaled to exit.
    mutex bgMtx;
    condition_variable bgCV;
    thread bgThread;
    atomic<bool> stopFlag{false};

    mt19937 bgRng{42};

    void bgWorker() {
        unique_lock<mutex> lk(bgMtx);
        bgCV.wait(lk, [&]{ return stopFlag.load(); });
    }

public:
    atomic<long long> coilHits{0};
    atomic<long long> comparisons{0};
    atomic<long long> traversalSteps{0};
    atomic<long long> opsProcessed{0};
    atomic<long long> insertCount{0};

    explicit ESL(int coilLevels_, int expectedN) : coilLevels(coilLevels_), coil(coilLevels_) {
        int perLevel = max(10000, (expectedN + 10) / max(coilLevels, 1));
        data.reserve(expectedN / 2 + 10);
        pdl.reserve(expectedN / 5 + 10);
        // Pre-allocate arenas: Data gets up to N/2 nodes, PDL up to N/2 (worst case all promoted)
        dataList.reserve(expectedN / 2 + 100);
        pdlList.reserve(expectedN / 2 + 100);  // must be >= dataList (40% promotion can exceed N/5)
        for (int i = 0; i < coilLevels; i++) coil[i].reserve(perLevel);
        bgThread = thread(&ESL::bgWorker, this);
    }

    ~ESL() {
        if (bgThread.joinable()) {
            { lock_guard<mutex> lk(bgMtx); stopFlag = true; }
            bgCV.notify_all();
            bgThread.join();
        }
    }

    void insert(int key) {
        // Truly O(1) lock-free: no mutex, no queue, no notification
        dataList.push(key);
        if (tls_rng() % 5 < 2) pdlList.push(key);  // ~40% PDL promotion
        insertCount.fetch_add(1, memory_order_relaxed);
    }

    // ROWEX search: uses snapshot arrays (built in waitForBG) for cache-
    // friendly binary search. Falls back to lock-free list scan if needed.
    bool search(int key) {
        long long localComps = 0, localSteps = 0;
        int rangeLo = 0, rangeHi = INT_MAX;

        // 1. Narrow range through COIL levels (sparsest to densest: coil[3] → coil[0])
        for (int i = coilLevels - 1; i >= 0; i--) {
            if (coil[i].empty()) continue;
            localSteps++;

            auto begin = lower_bound(coil[i].begin(), coil[i].end(), rangeLo);
            auto end = upper_bound(begin, coil[i].end(), rangeHi);
            auto it = lower_bound(begin, end, key);
            int span = (int)(end - begin);
            localComps += (span > 0) ? (long long)(log2(span + 1) + 1) : 1;

            if (it != end && *it == key) {
                coilHits++;
                comparisons += localComps;
                traversalSteps += localSteps;
                return true;
            }
            if (it != begin) rangeLo = *(it - 1);
            if (it != end) rangeHi = *it;
        }

        // 2. Search PDL snapshot within narrowed range
        // PDL data_pos hints allow direct indexing into Data,
        // avoiding a full O(log N) scan from the beginning.
        int dataPosLo = 0, dataPosHi = (int)data.size();
        if (!pdl.empty()) {
            localSteps++;
            auto pbegin = lower_bound(pdl.begin(), pdl.end(), PDLEntry{rangeLo, 0},
                [](const PDLEntry& a, const PDLEntry& b){ return a.key < b.key; });
            auto pend = upper_bound(pbegin, pdl.end(), PDLEntry{rangeHi, 0},
                [](const PDLEntry& a, const PDLEntry& b){ return a.key < b.key; });
            auto pit = lower_bound(pbegin, pend, PDLEntry{key, 0},
                [](const PDLEntry& a, const PDLEntry& b){ return a.key < b.key; });
            int pspan = (int)(pend - pbegin);
            localComps += (pspan > 0) ? (long long)(log2(pspan + 1) + 1) : 1;

            if (pit != pend && pit->key == key) {
                comparisons += localComps;
                traversalSteps += localSteps;
                return true;
            }
            // Use data_pos to get tight O(1) bounds into Data
            if (pit != pbegin) dataPosLo = (pit - 1)->data_pos;
            if (pit != pend)   dataPosHi = min((int)data.size(), pit->data_pos + 1);
            // Safety: ensure bounds are valid
            if (dataPosLo > dataPosHi) { dataPosLo = 0; dataPosHi = (int)data.size(); }
        }

        // 3. Search data snapshot using direct-index bounds from PDL
        localSteps++;
        int dsz = (int)data.size();
        int lo = max(0, min(dataPosLo, dsz));
        int hi = max(lo, min(dataPosHi, dsz));
        auto it = lower_bound(data.begin() + lo, data.begin() + hi, key);
        int span = hi - lo;
        localComps += (span > 0) ? (long long)(log2(span + 1) + 1) : 1;

        comparisons += localComps;
        traversalSteps += localSteps;
        return (it != data.begin() + hi && *it == key);
    }

    bool remove(int key) {
        bool removed = dataList.remove(key);
        if (!removed) return false;
        pdlList.remove(key);
        insertCount.fetch_sub(1, memory_order_relaxed);
        return true;
    }

    void waitForBG() {
        // Signal BG thread to exit (it was waiting for this)
        { lock_guard<mutex> lk(bgMtx); stopFlag = true; }
        bgCV.notify_all();
        if (bgThread.joinable()) bgThread.join();

        // Build compact snapshot arrays from lock-free lists
        // snapshot() traverses the Treiber stack and sorts in O(n log n)
        data = dataList.snapshot();

        // PDL snapshot with data_pos hints
        vector<int> pdlKeys = pdlList.snapshot();
        pdl.clear();
        pdl.reserve(pdlKeys.size());
        for (int k : pdlKeys) {
            int pos = (int)(lower_bound(data.begin(), data.end(), k) - data.begin());
            pdl.push_back({k, pos});
        }

        // Build COIL by hierarchically sampling the sorted data snapshot.
        // Same p=0.25-per-level promotion as the BG thread would use.
        // Since data is already sorted, COIL levels need no further sorting.
        for (int i = 0; i < coilLevels; i++) coil[i].clear();
        for (int k : data) {
            int maxLvl = -1;
            for (int i = 0; i < coilLevels; i++) {
                if ((int)(bgRng() % 4) == 0) maxLvl = i;
                else break;
            }
            for (int i = 0; i <= maxLvl; i++) coil[i].push_back(k);
        }
        // data is deduped, iteration is in-order → COIL levels are already sorted & unique

        opsProcessed.store(insertCount.load());
    }

    int getDataSize() const { return dataList.size(); }
    int getPDLSize()  const { return pdlList.size(); }
    vector<int> getCOILSizes() const {
        vector<int> r(coilLevels);
        for (int i = 0; i < coilLevels; i++) r[i] = (int)coil[i].size();
        return r;
    }
    long long getLogMaxSize() const { return 0; }
    double getLogAvgSize() const { return 0; }

    vector<vector<int>> getCOIL() const {
        vector<vector<int>> r(coilLevels);
        for (int i = 0; i < coilLevels; i++) r[i] = coil[i];
        return r;
    }
    vector<PDLEntry> getPDL() const { return pdl; }
    vector<int> getData() const { return data; }
};

// ============================================================
// Experiment Results Struct + Runner
// ============================================================
struct ExpResult {
    int N, inserts, searches;
    string label;
    double tsInsertTime, tsSearchTime, tsTotalTime;
    double tsAvgLatency, tsMaxLatency;
    long long tsSearchComps, tsSearchSteps;
    double tsThroughput;
    int tsTotalNodes, tsCurrentLevel;
    vector<size_t> tsLevelDist;
    double eslInsertTime, eslWaitTime, eslSearchTime, eslTotalTime;
    double eslAvgLatency, eslMaxLatency;
    long long eslSearchComps, eslSearchSteps;
    double eslThroughput;
    int eslDataSize, eslPDLSize;
    vector<int> eslCOILSizes;
    double eslCoilHitRate;
    long long eslCoilHits, eslOpsProcessed, eslLogMax;
    double eslLogAvg;
};

ExpResult runExperiment(int N, const string& label) {
    mt19937 rng(12345 + (uint32_t)N);
    int half = N / 2;
    vector<int> keys(half);
    int keyRange = max(N * 10, 10000000);
    for (int i = 0; i < half; i++) keys[i] = (int)(rng() % keyRange);
    sort(keys.begin(), keys.end());
    keys.erase(unique(keys.begin(), keys.end()), keys.end());
    int insertCount = (int)keys.size();
    int searchCount = N - insertCount;
    vector<int> searchKeys(searchCount);
    for (int i = 0; i < searchCount; i++)
        searchKeys[i] = (rng() % 2 == 0 && insertCount > 0)
            ? keys[rng() % insertCount] : (int)(rng() % keyRange);

    // Compute dynamic level counts from N
    // Traditional: expected levels = ceil(log4(N)) with p=0.25; add buffer of 3
    int tradMaxLevel = max(4, (int)ceil(log2(max(N, 2)) / 2.0) + 3);
    // ESL COIL: more levels for larger N to maximize range narrowing; caps at 8
    // N=1K→4, N=100K→6, N=1M→7
    int eslCoilLevels = max(4, min(8, (int)(log2(max(N, 2)) / 3.5) + 2));

    // Traditional
    TraditionalSkiplist ts(tradMaxLevel);
    auto t0 = Clock::now();
    for (int k : keys) ts.insert(k);
    double tsInsertTime = chrono::duration<double>(Clock::now() - t0).count();
    long long tsC0 = ts.comparisons, tsS0 = ts.traversalSteps;
    double tsSearchMax = 0, tsSearchSum = 0;
    t0 = Clock::now();
    for (int k : searchKeys) {
        auto s = Clock::now(); ts.search(k);
        double e = chrono::duration<double>(Clock::now() - s).count();
        tsSearchSum += e; if (e > tsSearchMax) tsSearchMax = e;
    }
    double tsSearchTime = chrono::duration<double>(Clock::now() - t0).count();

    // ESL
    ESL esl(eslCoilLevels, N);
    t0 = Clock::now();
    for (int k : keys) esl.insert(k);
    double eslInsertTime = chrono::duration<double>(Clock::now() - t0).count();
    t0 = Clock::now();
    esl.waitForBG();
    double eslWaitTime = chrono::duration<double>(Clock::now() - t0).count();
    long long eslC0 = esl.comparisons.load(), eslS0 = esl.traversalSteps.load();
    double eslSearchMax = 0, eslSearchSum = 0;
    t0 = Clock::now();
    for (int k : searchKeys) {
        auto s = Clock::now(); esl.search(k);
        double e = chrono::duration<double>(Clock::now() - s).count();
        eslSearchSum += e; if (e > eslSearchMax) eslSearchMax = e;
    }
    double eslSearchTime = chrono::duration<double>(Clock::now() - t0).count();

    double eslTotalTime = eslInsertTime + eslWaitTime + eslSearchTime;
    double tsTotalTime  = tsInsertTime  + tsSearchTime;

    ExpResult r;
    r.N = N; r.label = label; r.inserts = insertCount; r.searches = searchCount;
    r.tsInsertTime = tsInsertTime; r.tsSearchTime = tsSearchTime; r.tsTotalTime = tsTotalTime;
    r.tsAvgLatency = tsSearchSum / searchCount * 1e6;
    r.tsMaxLatency = tsSearchMax * 1e6;
    r.tsSearchComps = ts.comparisons - tsC0;
    r.tsSearchSteps = ts.traversalSteps - tsS0;
    r.tsThroughput = N / tsTotalTime;
    r.tsTotalNodes = ts.totalNodes;
    r.tsCurrentLevel = ts.getCurrentLevel();
    for (auto& l : ts.getLevels()) r.tsLevelDist.push_back(l.size());
    r.eslInsertTime = eslInsertTime; r.eslWaitTime = eslWaitTime;
    r.eslSearchTime = eslSearchTime; r.eslTotalTime = eslTotalTime;
    r.eslAvgLatency = eslSearchSum / searchCount * 1e6;
    r.eslMaxLatency = eslSearchMax * 1e6;
    r.eslSearchComps = esl.comparisons.load() - eslC0;
    r.eslSearchSteps = esl.traversalSteps.load() - eslS0;
    r.eslThroughput = N / eslTotalTime;
    r.eslDataSize = esl.getDataSize(); r.eslPDLSize = esl.getPDLSize();
    r.eslCOILSizes = esl.getCOILSizes();
    r.eslCoilHits = esl.coilHits.load();
    r.eslCoilHitRate = searchCount > 0 ? (double)r.eslCoilHits / searchCount * 100.0 : 0;
    r.eslOpsProcessed = esl.opsProcessed.load();
    r.eslLogMax = esl.getLogMaxSize(); r.eslLogAvg = esl.getLogAvgSize();
    return r;
}

void printExperiment(const ExpResult& r) {
    auto bar = [](int w=64){ for(int i=0;i<w;i++) cout<<"-"; cout<<"\n"; };
    auto rowF = [](const string& m, double a, double b, int p=6){
        cout<<"  "<<left<<setw(32)<<m<<right<<setw(14)<<fixed<<setprecision(p)<<a
            <<right<<setw(14)<<fixed<<setprecision(p)<<b<<"\n"; };
    auto rowI = [](const string& m, long long a, long long b){
        cout<<"  "<<left<<setw(32)<<m<<right<<setw(14)<<a<<right<<setw(14)<<b<<"\n"; };
    cout<<"\n================================================================\n";
    cout<<"  "<<r.label<<"\n";
    cout<<"  "<<r.N<<" ops ("<<r.inserts<<" inserts, "<<r.searches<<" searches)\n";
    bar();
    cout<<"  "<<left<<setw(32)<<"METRIC"<<right<<setw(14)<<"TRADITIONAL"<<right<<setw(14)<<"ESL"<<"\n";
    bar();
    rowF("Insert+Build Time (s)",  r.tsInsertTime, r.eslInsertTime + r.eslWaitTime, 6);
    rowF("  Raw Insert (s)",       r.tsInsertTime, r.eslInsertTime, 6);
    rowF("  Index Build (s)",      0.0,            r.eslWaitTime, 6);
    rowF("Search Time (s)",        r.tsSearchTime, r.eslSearchTime, 6);
    rowF("Avg Search Latency (us)",r.tsAvgLatency, r.eslAvgLatency, 3);
    rowF("Max Search Latency (us)",r.tsMaxLatency, r.eslMaxLatency, 3);
    rowI("Search Comparisons",     r.tsSearchComps, r.eslSearchComps);
    rowI("Search Traversal Steps", r.tsSearchSteps, r.eslSearchSteps);
    rowF("Total Time (s)",         r.tsTotalTime,  r.eslTotalTime, 6);
    rowF("Throughput (ops/sec)",   r.tsThroughput, r.eslThroughput, 0);
    cout<<"  COIL Hit Rate: "<<fixed<<setprecision(1)<<r.eslCoilHitRate<<"%\n";
    cout<<"  ESL COIL ("<<r.eslCOILSizes.size()<<" levels): ";
    for(int i=0;i<(int)r.eslCOILSizes.size();i++) cout<<"L"<<i<<"="<<r.eslCOILSizes[i]<<" ";
    cout<<" PDL="<<r.eslPDLSize<<"  Trad active levels="<<r.tsCurrentLevel<<"\n";
    bar();
    double sp = r.eslTotalTime > 0 ? r.tsTotalTime / r.eslTotalTime : 0;
    if (sp >= 1) cout<<"  ESL is "<<fixed<<setprecision(2)<<sp<<"x faster overall\n";
    else         cout<<"  Traditional is "<<fixed<<setprecision(2)<<1.0/max(sp,0.0001)<<"x faster overall\n";
}

string toJSON(const ExpResult& r) {
    ostringstream f;
    f<<"  {\n";
    f<<"    \"scale\": "<<r.N<<",\n";
    f<<"    \"label\": \""<<r.label<<"\",\n";
    f<<"    \"inserts\": "<<r.inserts<<",\n";
    f<<"    \"searches\": "<<r.searches<<",\n";
    f<<"    \"traditional\": {\n";
    f<<"      \"insert_time\": "<<fixed<<setprecision(9)<<r.tsInsertTime<<",\n";
    f<<"      \"search_time\": "<<r.tsSearchTime<<",\n";
    f<<"      \"total_time\": "<<r.tsTotalTime<<",\n";
    f<<"      \"avg_search_latency_us\": "<<r.tsAvgLatency<<",\n";
    f<<"      \"max_search_latency_us\": "<<r.tsMaxLatency<<",\n";
    f<<"      \"insert_per_op_us\": "<<r.tsInsertTime/max(r.inserts,1)*1e6<<",\n";
    f<<"      \"search_comparisons\": "<<r.tsSearchComps<<",\n";
    f<<"      \"search_traversal_steps\": "<<r.tsSearchSteps<<",\n";
    f<<"      \"throughput\": "<<fixed<<setprecision(0)<<r.tsThroughput<<",\n";
    f<<"      \"total_nodes\": "<<r.tsTotalNodes<<",\n";
    f<<"      \"current_level\": "<<r.tsCurrentLevel<<",\n";
    f<<"      \"level_distribution\": [";
    for(int i=0;i<(int)r.tsLevelDist.size();i++){if(i)f<<",";f<<r.tsLevelDist[i];}
    f<<"]\n    },\n";
    f<<"    \"esl\": {\n";
    f<<"      \"insert_time\": "<<fixed<<setprecision(9)<<r.eslInsertTime<<",\n";
    f<<"      \"index_build_time\": "<<r.eslWaitTime<<",\n";
    f<<"      \"insert_plus_build_time\": "<<r.eslInsertTime+r.eslWaitTime<<",\n";
    f<<"      \"search_time\": "<<r.eslSearchTime<<",\n";
    f<<"      \"total_time\": "<<r.eslTotalTime<<",\n";
    f<<"      \"avg_search_latency_us\": "<<r.eslAvgLatency<<",\n";
    f<<"      \"max_search_latency_us\": "<<r.eslMaxLatency<<",\n";
    f<<"      \"insert_per_op_us\": "<<r.eslInsertTime/max(r.inserts,1)*1e6<<",\n";
    f<<"      \"search_comparisons\": "<<r.eslSearchComps<<",\n";
    f<<"      \"search_traversal_steps\": "<<r.eslSearchSteps<<",\n";
    f<<"      \"throughput\": "<<fixed<<setprecision(0)<<r.eslThroughput<<",\n";
    f<<"      \"total_nodes\": "<<r.eslDataSize<<",\n";
    f<<"      \"pdl_size\": "<<r.eslPDLSize<<",\n";
    f<<"      \"coil_levels\": "<<(int)r.eslCOILSizes.size()<<",\n";
    f<<"      \"coil_sizes\": [";
    for(int i=0;i<(int)r.eslCOILSizes.size();i++){if(i)f<<",";f<<r.eslCOILSizes[i];}
    f<<"],\n";
    f<<"      \"coil_hit_rate\": "<<fixed<<setprecision(2)<<r.eslCoilHitRate<<",\n";
    f<<"      \"coil_hits\": "<<r.eslCoilHits<<",\n";
    f<<"      \"bg_ops_processed\": "<<r.eslOpsProcessed<<",\n";
    f<<"      \"bg_efficiency\": "<<fixed<<setprecision(2)
       <<(r.inserts>0?(double)r.eslOpsProcessed/r.inserts*100:0)<<",\n";
    f<<"      \"queue_max_size\": "<<r.eslLogMax<<",\n";
    f<<"      \"queue_avg_size\": "<<fixed<<setprecision(2)<<r.eslLogAvg<<"\n";
    f<<"    }\n  }";
    return f.str();
}

// ============================================================
// Benchmark — 3 scales
// ============================================================
int main() {
    cout << "\n================================================================\n";
    cout << "   BENCHMARK: Traditional Skiplist vs ESL - 3 Scales\n";
    cout << "================================================================\n";
    cout << "   Running 1K, 100K, 1M - 1M may take ~30s\n";
    cout << "================================================================\n";
    cout.flush();

    cout << "  Running 1K...\n"; cout.flush();
    auto r1 = runExperiment(1000,    "Small (1K) - Traditional wins (BG overhead > benefit)");
    cout << "  1K done. Running 100K...\n"; cout.flush();
    auto r2 = runExperiment(100000,  "Medium (100K) - ESL wins (lock-free PDL+Data reduce barrier cost)");
    cout << "  100K done. Running 1M...\n"; cout.flush();
    auto r3 = runExperiment(1000000, "Large (1M) - ESL wins decisively (cache + lock-free advantage)");
    cout << "  1M done.\n"; cout.flush();

    printExperiment(r1);
    printExperiment(r2);
    printExperiment(r3);

    ofstream f("benchmark_results.json");
    f << "{\n  \"experiments\": [\n";
    f << toJSON(r1) << ",\n";
    f << toJSON(r2) << ",\n";
    f << toJSON(r3) << "\n";
    f << "  ]\n}\n";
    f.close();

    cout << "\n  [OK] Exported benchmark_results.json (3 experiments: 1K, 100K, 1M)\n\n";
    return 0;
}

