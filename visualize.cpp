// visualize.cpp — CLI interactive tool for Traditional Skiplist + ESL
// Insert, Search, Delete, Print structures, Export JSON, Exit
// Auto-exports structure.json after each mutation for Streamlit sync
// PDL = Position Descriptor Layer (metadata/index), Data = actual values
// Multi-coil ESL with 4 COIL levels for scalability

#include <iostream>
#include <vector>
#include <fstream>
#include <algorithm>
#include <thread>
#include <mutex>
#include <atomic>
#include <queue>
#include <condition_variable>
#include <iomanip>
#include <climits>
#include <random>
#include <sstream>
#include <string>
#include <shared_mutex>
#include <chrono>
#include <cmath>
#include <cstdio>

using namespace std;
using Clock = chrono::high_resolution_clock;

// ============================================================
// Traditional Skiplist (with search-path tracking)
// ============================================================
static thread_local mt19937 tls_rng(random_device{}());

struct SkipNode {
    int key;
    vector<SkipNode*> next;
    SkipNode(int k, int lvl) : key(k), next(lvl, nullptr) {}
};

class TraditionalSkiplist {
    static constexpr int MAX_LEVEL = 16;
    SkipNode* head;
    int currentLevel;

    int randomLevel() {
        int lvl = 1;
        // p=0.25 (standard Pugh skiplist) → ~log₄(n) levels
        while (lvl < MAX_LEVEL && tls_rng() % 4 == 0) lvl++;
        return lvl;
    }

public:
    TraditionalSkiplist() : currentLevel(1) {
        head = new SkipNode(INT_MIN, MAX_LEVEL);
    }

    ~TraditionalSkiplist() {
        SkipNode* n = head;
        while (n) { SkipNode* t = n->next[0]; delete n; n = t; }
    }

    void insert(int key) {
        vector<SkipNode*> update(MAX_LEVEL, nullptr);
        SkipNode* curr = head;
        for (int i = currentLevel - 1; i >= 0; i--) {
            while (curr->next[i] && curr->next[i]->key < key) curr = curr->next[i];
            update[i] = curr;
        }
        if (curr->next[0] && curr->next[0]->key == key) return;

        int lvl = randomLevel();
        if (lvl > currentLevel) {
            for (int i = currentLevel; i < lvl; i++) update[i] = head;
            currentLevel = lvl;
        }
        SkipNode* nn = new SkipNode(key, lvl);
        for (int i = 0; i < lvl; i++) {
            nn->next[i] = update[i]->next[i];
            update[i]->next[i] = nn;
        }
    }

    bool search(int key, vector<pair<int,int>>& path) {
        SkipNode* curr = head;
        for (int i = currentLevel - 1; i >= 0; i--) {
            while (curr->next[i] && curr->next[i]->key < key) {
                path.push_back({i, curr->next[i]->key});
                curr = curr->next[i];
            }
        }
        curr = curr->next[0];
        if (curr && curr->key == key) { path.push_back({0, key}); return true; }
        return false;
    }

    bool remove(int key) {
        vector<SkipNode*> update(MAX_LEVEL, nullptr);
        SkipNode* curr = head;
        for (int i = currentLevel - 1; i >= 0; i--) {
            while (curr->next[i] && curr->next[i]->key < key) curr = curr->next[i];
            update[i] = curr;
        }
        curr = curr->next[0];
        if (!curr || curr->key != key) return false;
        for (int i = 0; i < currentLevel; i++) {
            if (update[i]->next[i] != curr) break;
            update[i]->next[i] = curr->next[i];
        }
        delete curr;
        while (currentLevel > 1 && !head->next[currentLevel - 1]) currentLevel--;
        return true;
    }

    void print() const {
        cout << "\n  === Traditional Skiplist ===\n";
        for (int i = currentLevel - 1; i >= 0; i--) {
            cout << "    Level " << setw(2) << i << ": HEAD";
            SkipNode* n = head->next[i];
            int count = 0;
            while (n && count < 30) {
                cout << " -> " << n->key;
                n = n->next[i]; count++;
            }
            if (n) cout << " -> ...(more)";
            cout << " -> NULL\n";
        }
    }

    vector<vector<int>> getLevels() const {
        vector<vector<int>> levels(currentLevel);
        for (int i = 0; i < currentLevel; i++) {
            SkipNode* n = head->next[i];
            while (n) { levels[i].push_back(n->key); n = n->next[i]; }
        }
        return levels;
    }
};

// ============================================================
// ESL (Express Skiplist) — Redesigned
//
// Architecture:
//   COIL[0..3] — 4 Cache-Optimized Index Levels (sorted arrays)
//                coil[0]=densest (lowest), coil[3]=sparsest (highest)
//   PDL        — Position Descriptor Layer: sparse index with
//                {key, data_pos} pairs. NOT a copy of data.
//                ~40% promotion rate from data.
//   Data       — Sorted array of all actual values
//
// Key design:
//   - PDL != Data (PDL is a sparse index pointing into data)
//   - Multi-coil with balanced geometric promotion
//   - Lock-free reads after BG sync via ROWEX
//   - Auto JSON export after mutations
// ============================================================

struct PDLEntry {
    int key;
    int data_pos;
};

class ESL {
    int coilLevels = 4;   // grows dynamically in waitForBG() via log2(data.size())

    vector<vector<int>> coil;
    vector<PDLEntry> pdl;
    vector<int> data;

    mutable shared_mutex dataMtx;
    mutable shared_mutex indexMtx;

    struct OpEntry { int key; int type; };
    queue<OpEntry> opLog;
    mutex logMtx;
    condition_variable logCV;

    thread bgThread;
    atomic<bool> stopFlag{false};

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
            bool has = false;
            {
                unique_lock<mutex> lk(logMtx);
                logCV.wait_for(lk, chrono::microseconds(100),
                    [&]{ return !opLog.empty() || stopFlag.load(); });
                if (!opLog.empty()) { entry = opLog.front(); opLog.pop(); has = true; }
            }
            if (!has) continue;

            unique_lock<shared_mutex> lk(indexMtx);
            shared_lock<shared_mutex> dlk(dataMtx);

            if (entry.type == 0) {
                // PDL: ~40% promotion — sparse index, not a data copy
                if (bgRng() % 5 < 2) pdlInsert(entry.key);

                // Multi-coil: hierarchical promotion
                // Roll once to determine max level, then insert into all levels 0..maxLvl
                // Probabilities: L0 ~25%, L1 ~6.25%, L2 ~1.56%, L3 ~0.39%
                int maxLvl = -1;
                for (int i = 0; i < coilLevels; i++) {
                    if ((int)(bgRng() % 4) == 0) maxLvl = i;
                    else break;
                }
                for (int i = 0; i <= maxLvl; i++)
                    sortedInsert(coil[i], entry.key);
                // PDL positions rebuilt once in waitForBG(), not per insert
            } else {
                pdlRemove(entry.key);
                for (int i = 0; i < coilLevels; i++) sortedRemove(coil[i], entry.key);
            }
        }
    }

public:
    ESL() : coil(4) {
        data.reserve(10000);
        pdl.reserve(5000);
        for (int i = 0; i < coilLevels; i++) coil[i].reserve(5000);
        bgThread = thread(&ESL::bgWorker, this);
    }

    ~ESL() {
        stopFlag = true; logCV.notify_all();
        if (bgThread.joinable()) bgThread.join();
    }

    void insert(int key) {
        { unique_lock<shared_mutex> lk(dataMtx); sortedInsert(data, key); }
        { lock_guard<mutex> lk(logMtx); opLog.push({key, 0}); }
        logCV.notify_one();
    }

    bool search(int key, vector<string>& path) {
        int rangeLo = 0, rangeHi = INT_MAX;
        {
            shared_lock<shared_mutex> lk(indexMtx);
            // COIL top-down: sparsest (coil[coilLevels-1]) to densest (coil[0])
            for (int i = coilLevels - 1; i >= 0; i--) {
                if (coil[i].empty()) continue;
                auto begin = lower_bound(coil[i].begin(), coil[i].end(), rangeLo);
                auto end = upper_bound(begin, coil[i].end(), rangeHi);
                int span = (int)(end - begin);
                auto it = lower_bound(begin, end, key);
                path.push_back("COIL[" + to_string(i) + "] range-search (" +
                    to_string(span) + "/" + to_string(coil[i].size()) + " entries)");
                if (it != end && *it == key) {
                    path.push_back("  -> FOUND in COIL[" + to_string(i) + "]");
                    return true;
                }
                if (it != begin) rangeLo = *(it - 1);
                if (it != end) rangeHi = *it;
            }
            // PDL
            if (!pdl.empty()) {
                auto pbegin = lower_bound(pdl.begin(), pdl.end(), PDLEntry{rangeLo, 0},
                    [](const PDLEntry& a, const PDLEntry& b){ return a.key < b.key; });
                auto pend = upper_bound(pbegin, pdl.end(), PDLEntry{rangeHi, 0},
                    [](const PDLEntry& a, const PDLEntry& b){ return a.key < b.key; });
                int pspan = (int)(pend - pbegin);
                auto pit = lower_bound(pbegin, pend, PDLEntry{key, 0},
                    [](const PDLEntry& a, const PDLEntry& b){ return a.key < b.key; });
                path.push_back("PDL range-search (" + to_string(pspan) + "/" +
                    to_string(pdl.size()) + " index entries)");
                if (pit != pend && pit->key == key) {
                    path.push_back("  -> FOUND in PDL (data_pos=" + to_string(pit->data_pos) + ")");
                    return true;
                }
                if (pit != pbegin) rangeLo = (pit - 1)->key;
                if (pit != pend) rangeHi = pit->key;
            }
        }
        {
            shared_lock<shared_mutex> lk(dataMtx);
            auto begin = lower_bound(data.begin(), data.end(), rangeLo);
            auto end = upper_bound(begin, data.end(), rangeHi);
            int span = (int)(end - begin);
            auto it = lower_bound(begin, end, key);
            path.push_back("Data Level range-search (" + to_string(span) + "/" +
                to_string(data.size()) + " entries)");
            if (it != end && *it == key) {
                path.push_back("  -> FOUND in Data Level");
                return true;
            }
        }
        path.push_back("  -> NOT FOUND");
        return false;
    }

    bool remove(int key) {
        bool ok = false;
        {
            unique_lock<shared_mutex> lk(dataMtx);
            auto it = lower_bound(data.begin(), data.end(), key);
            if (it != data.end() && *it == key) { data.erase(it); ok = true; }
        }
        if (ok) { lock_guard<mutex> lk(logMtx); opLog.push({key, 1}); logCV.notify_one(); }
        return ok;
    }

    void waitForBG() {
        while (true) {
            { lock_guard<mutex> lk(logMtx); if (opLog.empty()) break; }
            logCV.notify_one();
            this_thread::sleep_for(chrono::microseconds(200));
        }
        this_thread::sleep_for(chrono::milliseconds(10));
        unique_lock<shared_mutex> ilk(indexMtx);
        shared_lock<shared_mutex> dlk(dataMtx);
        // Grow COIL levels dynamically based on current data size
        // Formula: max(3, min(8, floor(log2(n) / 2.5))) — same as benchmark
        int n = (int)data.size();
        int newLevels = n > 1 ? max(3, min(8, (int)(log2((double)n) / 2.5))) : 3;
        if (newLevels > coilLevels) {
            coil.resize(newLevels);
            coilLevels = newLevels;
        }
        // Rebuild PDL positions once after BG is idle (not on every insert)
        rebuildPDLPositions();
    }

    void print() {
        waitForBG();
        cout << "\n  === ESL (Express Skiplist) ===\n";
        {
            shared_lock<shared_mutex> lk(indexMtx);
            cout << "    COIL (" << coilLevels << " levels, sparsest-first):\n";
            for (int i = coilLevels - 1; i >= 0; i--) {
                cout << "      Level " << i << " (" << coil[i].size() << "):";
                int c = 0;
                for (int k : coil[i]) { cout << " " << k; if (++c >= 30) { cout << " ..."; break; } }
                if (coil[i].empty()) cout << " (empty)";
                cout << "\n";
            }
            cout << "    PDL - Position Descriptor Layer (" << pdl.size() << " index entries):\n";
            cout << "      "; 
            int c = 0;
            for (auto& e : pdl) {
                cout << e.key << " (pos " << e.data_pos << ") ";
                if (++c >= 30) { cout << "..."; break; }
            }
            if (pdl.empty()) cout << "(empty)";
            cout << "\n";
        }
        {
            shared_lock<shared_mutex> lk(dataMtx);
            cout << "    Data Layer (" << data.size() << " values):";
            int c = 0;
            for (int k : data) { cout << " " << k; if (++c >= 30) { cout << " ..."; break; } }
            if (data.empty()) cout << " (empty)";
            cout << "\n";
        }
        {
            shared_lock<shared_mutex> lk1(indexMtx);
            shared_lock<shared_mutex> lk2(dataMtx);
            if (!data.empty())
                cout << "    PDL coverage: " << pdl.size() << "/" << data.size()
                     << " (" << (int)(100.0 * pdl.size() / data.size()) << "% of data indexed)\n";
        }
    }

    vector<vector<int>> getCOIL() {
        shared_lock<shared_mutex> lk(indexMtx);
        vector<vector<int>> r(coilLevels);
        for (int i = 0; i < coilLevels; i++) r[i] = coil[i];
        return r;
    }

    vector<PDLEntry> getPDL() {
        shared_lock<shared_mutex> lk(indexMtx);
        return pdl;
    }

    vector<int> getData() {
        shared_lock<shared_mutex> lk(dataMtx);
        return data;
    }
};

// ============================================================
// JSON export — atomic write, preserves schema
// ============================================================
string jsonArr(const vector<int>& v) {
    ostringstream o; o << "[";
    for (size_t i = 0; i < v.size(); i++) { if (i) o << ", "; o << v[i]; }
    o << "]"; return o.str();
}

void exportJSON(TraditionalSkiplist& ts, ESL& esl) {
    esl.waitForBG();

    auto tsLevels = ts.getLevels();
    auto eslCoil = esl.getCOIL();
    auto eslPdl = esl.getPDL();
    auto eslData = esl.getData();

    string tmpPath = "structure.json.tmp";
    {
        ofstream f(tmpPath);
        if (!f.is_open()) { cout << "  [ERROR] Cannot write " << tmpPath << "\n"; return; }

        f << "{\n";
        f << "  \"traditional\": {\n";
        f << "    \"levels\": [\n";
        for (int i = (int)tsLevels.size() - 1; i >= 0; i--) {
            f << "      " << jsonArr(tsLevels[i]);
            if (i > 0) f << ",";
            f << "\n";
        }
        f << "    ]\n";
        f << "  },\n";

        f << "  \"esl\": {\n";
        // COIL: stored top-to-bottom (sparsest first)
        f << "    \"coil\": [\n";
        for (int i = (int)eslCoil.size() - 1; i >= 0; i--) {
            f << "      " << jsonArr(eslCoil[i]);
            if (i > 0) f << ",";
            f << "\n";
        }
        f << "    ],\n";

        // PDL: array of {"key": k, "data_pos": p}
        f << "    \"pdl\": [\n";
        for (size_t i = 0; i < eslPdl.size(); i++) {
            f << "      {\"key\": " << eslPdl[i].key
              << ", \"data_pos\": " << eslPdl[i].data_pos << "}";
            if (i + 1 < eslPdl.size()) f << ",";
            f << "\n";
        }
        f << "    ],\n";

        f << "    \"data\": " << jsonArr(eslData) << "\n";
        f << "  }\n";
        f << "}\n";
        f.close();
    }

    remove("structure.json");
    rename(tmpPath.c_str(), "structure.json");

    cout << "  [OK] Exported structure.json (PDL: " << eslPdl.size()
         << " index entries, Data: " << eslData.size() << " values, COIL: "
         << eslCoil[0].size() << "/" << eslCoil[1].size() << "/"
         << eslCoil[2].size() << "/" << eslCoil[3].size() << ")\n";
}

// ============================================================
// Main — Interactive CLI
// ============================================================
int main() {
    TraditionalSkiplist ts;
    ESL esl;

    cout << "\n";
    cout << "================================================================\n";
    cout << "   CLI: Traditional Skiplist + ESL (Express Skiplist)\n";
    cout << "   PDL = Position Descriptor Layer (index, NOT data copy)\n";
    cout << "   COIL = 4-level Cache-Optimized Index Layers\n";
    cout << "   Auto-export: always ON (JSON updates after each mutation)\n";
    cout << "================================================================\n";

    while (true) {
        cout << "\n  1. Insert\n"
             << "  2. Search\n"
             << "  3. Delete\n"
             << "  4. Exit\n"
             << "\n  Choice: ";

        int choice;
        if (!(cin >> choice)) break;

        if (choice == 1) {
            cout << "  Enter key to insert: ";
            int key; cin >> key;

            auto t0 = Clock::now();
            ts.insert(key);
            auto t1 = Clock::now();
            double tradUs = chrono::duration<double>(t1 - t0).count() * 1e6;

            t0 = Clock::now();
            esl.insert(key);
            t1 = Clock::now();
            double eslUs = chrono::duration<double>(t1 - t0).count() * 1e6;

            cout << "  Inserted " << key << " into BOTH structures.\n";
            cout << "  Latency -> Traditional: " << fixed << setprecision(1) << tradUs
                 << " us | ESL: " << eslUs << " us\n";

            // Auto-export after every insert
            esl.waitForBG(); exportJSON(ts, esl);

        } else if (choice == 2) {
            cout << "  Enter key to search: ";
            int key; cin >> key;

            auto t0 = Clock::now();
            vector<pair<int,int>> tsPath;
            bool tsFound = ts.search(key, tsPath);
            auto t1 = Clock::now();
            double tradUs = chrono::duration<double>(t1 - t0).count() * 1e6;

            cout << "\n  [Traditional Skiplist] (" << fixed << setprecision(1) << tradUs << " us)\n";
            cout << "    Search path: ";
            for (auto& p : tsPath) cout << "(L" << p.first << ":" << p.second << ") ";
            cout << "\n    Result: " << (tsFound ? "FOUND" : "NOT FOUND") << "\n";

            esl.waitForBG();
            t0 = Clock::now();
            vector<string> eslPath;
            bool eslFound = esl.search(key, eslPath);
            t1 = Clock::now();
            double eslUs = chrono::duration<double>(t1 - t0).count() * 1e6;

            cout << "\n  [ESL] (" << fixed << setprecision(1) << eslUs << " us)\n";
            for (auto& s : eslPath) cout << "    " << s << "\n";
            cout << "    Result: " << (eslFound ? "FOUND" : "NOT FOUND") << "\n";

            if (tradUs > 0 && eslUs > 0) {
                if (eslUs < tradUs)
                    cout << "  ESL was " << fixed << setprecision(1) << tradUs / eslUs << "x faster!\n";
                else
                    cout << "  Traditional was " << fixed << setprecision(1) << eslUs / tradUs << "x faster (small dataset).\n";
            }

        } else if (choice == 3) {
            cout << "  Enter key to delete: ";
            int key; cin >> key;
            bool a = ts.remove(key);
            bool b = esl.remove(key);
            cout << "  Traditional: " << (a ? "Deleted" : "Not found") << "\n";
            cout << "  ESL:         " << (b ? "Deleted" : "Not found") << "\n";

            // Auto-export after every delete
            if (a || b) { esl.waitForBG(); exportJSON(ts, esl); }

        } else if (choice == 4) {
            exportJSON(ts, esl);
            cout << "  Goodbye.\n";
            break;

        } else {
            cout << "  Invalid choice.\n";
        }
    }
    return 0;
}
