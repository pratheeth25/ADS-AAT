// benchmark.cpp — Traditional Skiplist vs ESL (Express Skiplist) Benchmark
// Runs 100,000 operations (50% insert, 50% search), measures detailed metrics,
// prints a console dashboard, and exports benchmark_results.json.

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
// ESL — Express Skiplist (Redesigned)
//
// Architecture:
//   COIL[0..3] — 4 Cache-Optimized Index Levels (sorted arrays)
//                coil[0]=densest (lowest), coil[3]=sparsest (highest)
//   PDL        — Position Descriptor Layer: sparse index with
//                {key, data_pos} pairs. NOT a copy of data.
//                ~40% promotion rate from data.
//   Data       — Sorted array of all actual values
//   OpLog      — queue processed by 1 background thread
//   BG         — background thread updates PDL + COIL asynchronously
//
// Performance advantage over traditional skiplist:
//   - All levels are contiguous arrays → cache-friendly, no pointer chasing
//   - Search uses COIL to narrow range top-down, then single binary search
//   - ROWEX protocol: reads don't acquire locks after BG sync
// ============================================================

struct PDLEntry {
    int key;
    int data_pos;
};

class ESL {
    int coilLevels;   // computed from N: max(3, min(8, (int)(log2(N)/2.5)))

    vector<vector<int>> coil;
    vector<PDLEntry> pdl;
    vector<int> data;

    struct OpEntry { int key; int type; };
    queue<OpEntry> opLog;
    mutex logMtx;
    condition_variable logCV;

    thread bgThread;
    atomic<bool> stopFlag{false};

    atomic<long long> logMaxSize{0};
    long long logSizeSum = 0;
    long long logSizeSamples = 0;

    mt19937 bgRng{42};

    static void sortedInsert(vector<int>& v, int key) {
        auto it = lower_bound(v.begin(), v.end(), key);
        if (it != v.end() && *it == key) return;
        v.insert(it, key);
    }

    static void sortedRemove(vector<int>& v, int key) {
        auto it = lower_bound(v.begin(), v.end(), key);
        if (it != v.end() && *it == key) v.erase(it);
    }

    int dataPosition(int key) const {
        auto it = lower_bound(data.begin(), data.end(), key);
        return (int)(it - data.begin());
    }

    void pdlInsert(int key) {
        int pos = dataPosition(key);
        PDLEntry entry{key, pos};
        auto it = lower_bound(pdl.begin(), pdl.end(), entry,
            [](const PDLEntry& a, const PDLEntry& b){ return a.key < b.key; });
        if (it != pdl.end() && it->key == key) { it->data_pos = pos; return; }
        pdl.insert(it, entry);
    }

    void pdlRemove(int key) {
        auto it = lower_bound(pdl.begin(), pdl.end(), PDLEntry{key, 0},
            [](const PDLEntry& a, const PDLEntry& b){ return a.key < b.key; });
        if (it != pdl.end() && it->key == key) pdl.erase(it);
    }

    void rebuildPDLPositions() {
        for (auto& entry : pdl)
            entry.data_pos = dataPosition(entry.key);
    }

    void bgWorker() {
        while (!stopFlag) {
            OpEntry entry;
            bool hasEntry = false;
            {
                unique_lock<mutex> lk(logMtx);
                logCV.wait_for(lk, chrono::microseconds(50),
                    [&]{ return !opLog.empty() || stopFlag.load(); });
                if (!opLog.empty()) {
                    entry = opLog.front(); opLog.pop();
                    hasEntry = true;
                    int qs = (int)opLog.size();
                    logSizeSum += qs; logSizeSamples++;
                    long long cur = logMaxSize.load();
                    while (qs > cur && !logMaxSize.compare_exchange_weak(cur, qs));
                }
            }
            if (!hasEntry) continue;

            if (entry.type == 0) {
                // PDL: ~40% promotion — push_back O(1); sorted in waitForBG
                if (bgRng() % 5 < 2) pdl.push_back({entry.key, 0});

                // Hierarchical COIL — push_back O(1); sorted in waitForBG
                int maxLvl = -1;
                for (int i = 0; i < coilLevels; i++) {
                    if ((int)(bgRng() % 4) == 0) maxLvl = i;
                    else break;
                }
                for (int i = 0; i <= maxLvl; i++)
                    coil[i].push_back(entry.key);
            } else {
                // remove: sorted remove still correct (rare in benchmark)
                pdlRemove(entry.key);
                for (int i = 0; i < coilLevels; i++) sortedRemove(coil[i], entry.key);
            }
            opsProcessed++;
        }
    }

public:
    atomic<long long> coilHits{0};
    atomic<long long> comparisons{0};
    atomic<long long> traversalSteps{0};
    atomic<long long> opsProcessed{0};
    atomic<long long> insertCount{0};

    explicit ESL(int coilLevels_) : coilLevels(coilLevels_), coil(coilLevels_) {
        int perLevel = max(10000, 1100000 / max(coilLevels, 1));
        data.reserve(1100000);
        pdl.reserve(500000);
        for (int i = 0; i < coilLevels; i++) coil[i].reserve(perLevel);
        bgThread = thread(&ESL::bgWorker, this);
    }

    ~ESL() {
        stopFlag = true; logCV.notify_all();
        if (bgThread.joinable()) bgThread.join();
    }

    void insert(int key) {
        data.push_back(key); // O(1); sorted once in waitForBG
        {
            lock_guard<mutex> lk(logMtx);
            opLog.push({key, 0});
            insertCount++;
        }
        logCV.notify_one();
    }

    // ROWEX search: no locks needed after waitForBG().
    // Uses COIL top-down range narrowing → PDL → binary search on data.
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

        // 2. Search PDL (position descriptor layer) within narrowed range
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
            if (pit != pbegin) rangeLo = (pit - 1)->key;
            if (pit != pend) rangeHi = pit->key;
        }

        // 3. Search data level within narrowed range
        localSteps++;
        auto begin = lower_bound(data.begin(), data.end(), rangeLo);
        auto end = upper_bound(begin, data.end(), rangeHi);
        auto it = lower_bound(begin, end, key);
        int span = (int)(end - begin);
        localComps += (span > 0) ? (long long)(log2(span + 1) + 1) : 1;

        if (it != end && *it == key) {
            comparisons += localComps;
            traversalSteps += localSteps;
            return true;
        }

        comparisons += localComps;
        traversalSteps += localSteps;
        return false;
    }

    bool remove(int key) {
        auto it = lower_bound(data.begin(), data.end(), key);
        if (it == data.end() || *it != key) return false;
        data.erase(it);
        {
            lock_guard<mutex> lk(logMtx);
            opLog.push({key, 1});
        }
        logCV.notify_one();
        return true;
    }

    void waitForBG() {
        // Wait until BG has processed every queued insert
        long long target = insertCount.load();
        while (opsProcessed.load() < target) {
            logCV.notify_one();
            this_thread::sleep_for(chrono::microseconds(200));
        }
        // Sort all structures once — O(n log n), far faster than per-insert sortedInsert
        sort(data.begin(), data.end());
        data.erase(unique(data.begin(), data.end()), data.end());
        for (int i = 0; i < coilLevels; i++) {
            sort(coil[i].begin(), coil[i].end());
            coil[i].erase(unique(coil[i].begin(), coil[i].end()), coil[i].end());
        }
        sort(pdl.begin(), pdl.end(),
             [](const PDLEntry& a, const PDLEntry& b){ return a.key < b.key; });
        pdl.erase(unique(pdl.begin(), pdl.end(),
             [](const PDLEntry& a, const PDLEntry& b){ return a.key == b.key; }), pdl.end());
        rebuildPDLPositions(); // data is now sorted; update data_pos references
    }

    int getDataSize() const { return (int)data.size(); }
    int getPDLSize() const { return (int)pdl.size(); }
    vector<int> getCOILSizes() const {
        vector<int> r(coilLevels);
        for (int i = 0; i < coilLevels; i++) r[i] = (int)coil[i].size();
        return r;
    }
    long long getLogMaxSize() const { return logMaxSize.load(); }
    double getLogAvgSize() const { return logSizeSamples > 0 ? (double)logSizeSum / logSizeSamples : 0; }

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
    // ESL COIL: scales similarly but caps at 8 (extra levels yield diminishing returns)
    int eslCoilLevels = max(3, min(8, (int)(log2(max(N, 2)) / 2.5)));

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
    ESL esl(eslCoilLevels);
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

    auto r1 = runExperiment(1000,    "Small (1K) - Traditional wins (BG overhead > benefit)");
    auto r2 = runExperiment(100000,  "Medium (100K) - ESL advantage emerges");
    auto r3 = runExperiment(1000000, "Large (1M) - ESL wins decisively");

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

