# -*- coding: utf-8 -*-
"""walk_sim.py — 图上随机游走块 I/O 模拟器（课程统一骨架，禁止修改核心语义）

《计算机系统设计》（研究生一年级）实验：node2vec / GraphWalker / SOWalker

语义约定（所有学生必须遵守，保证加载次数可比）：
  * 同步轮次：每轮调度器选定驻留集 R*（|R*| <= M），随后每个"可推进"的 walker 走 1 步。
  * 加载计数：仅当块 b ∈ R* 且 b ∉ R（当前驻留集）时计 1 次（含首轮冷启动）；
    cache hit 不计；被换出后再次调入重复计；全部游走结束后残留块不处置。
  * walker：{vid 当前点, pid 上一步点, step 已走步数, done}。
  * 可推进：一阶 ⟺ block(vid) ∈ R；二阶 ⟺ block(vid) ∈ R 且 block(pid) ∈ R
    （step==0 时无 pid，仅需 block(vid) ∈ R）。被卡的 walker 原地等待。
  * 换出策略 walk-conscious：R 中不在 R* 的块被换出（等价于直接以 R* 替换 R）。
  * 确定性：固定随机种子；调度平局一律取块号较小者。

内置调度策略：
  * stateaware（一阶 baseline，GraphWalker §3）：选含未结束 walker 最多的至多 M 块。
  * naive（二阶对照）：沿用一阶规则，无视 prev 块共驻需求 → 大量 non-updatable 等待。
  * auw（二阶 baseline，SOWalker §3）：枚举所有大小 ≤M 的块集合，
    选"可更新游走数 AUW = Σ W(i,j)"最大者（W 为 walk matrix）。

学生只允许提交：自定义分区文件（meta 格式）与自定义调度函数（经 run_sim 的
scheduler 参数注入）；本文件其余部分不得改动，评测以本骨架输出为准。
"""
import argparse
import itertools
import json
import os
import random


class Walker:
    __slots__ = ("vid", "pid", "step", "done")

    def __init__(self, vid):
        self.vid = vid
        self.pid = None
        self.step = 0
        self.done = False


class GraphStore:
    """外存模拟：按块组织邻接表；读块 = 一次块加载。"""

    def __init__(self, edgelist_path, meta_path):
        self.adj = {}
        with open(edgelist_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                u, v = map(int, line.split()[:2])
                self.adj.setdefault(u, []).append(v)
                self.adj.setdefault(v, []).append(u)
        for v in self.adj:
            self.adj[v].sort()
        self.block_of = {}
        with open(meta_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                v, b = map(int, line.split()[:2])
                self.block_of[v] = b
        self.blocks = sorted(set(self.block_of.values()))
        # 每块边数（以块内顶点为尾的边），用于 I/O 利用率
        self.block_edges = {b: sum(len(self.adj[v]) for v in self.block_of
                                   if self.block_of[v] == b) for b in self.blocks}

    def n_vertices(self):
        return len(self.block_of)


def alive(walkers):
    return [w for w in walkers if not w.done]


def updatable(w, resident, order, block_of):
    if block_of[w.vid] not in resident:
        return False
    if order == 2 and w.step > 0:
        return block_of[w.pid] in resident
    return True


# ---------------- 内置调度策略 ----------------

def sched_stateaware(walkers, resident, M, store, order):
    """一阶 baseline：含未结束 walker 最多的至多 M 块；平局取块号小者。"""
    score = {b: 0 for b in store.blocks}
    for w in alive(walkers):
        score[store.block_of[w.vid]] += 1
    ranked = sorted(store.blocks, key=lambda b: (-score[b], b))
    chosen = [b for b in ranked if score[b] > 0][:M]
    return set(chosen)


def sched_auw(walkers, resident, M, store, order):
    """二阶 baseline（SOWalker benefit-aware）：选 AUW 最大的块集合。
    平局规则：先比可更新游走数，再比与当前驻留集交集（少加载），再取块号小者。"""
    best, best_key = None, None
    for size in range(1, M + 1):
        for S in itertools.combinations(store.blocks, size):
            S = set(S)
            auw = sum(1 for w in alive(walkers)
                      if updatable(w, S, 2, store.block_of))
            if auw == 0:
                continue
            key = (auw, len(S & resident), tuple(-b for b in sorted(S)))
            if best_key is None or key > best_key:
                best, best_key = S, key
    return best if best is not None else set()


SCHEDULERS = {
    "stateaware": sched_stateaware,
    "naive": sched_stateaware,   # 二阶对照：与一阶同规则，无视双块共驻
    "auw": sched_auw,
}


class RoundRobin:
    """逐块迭代（对照用稻草人）：无视 walker 分布，按固定循环顺序装载块。
    对应 GraphWalker §1 批评的 iteration-based 模型。"""

    def __init__(self):
        self.k = 0

    def __call__(self, walkers, resident, M, store, order):
        B = store.blocks
        S = {B[(self.k + j) % len(B)] for j in range(min(M, len(B)))}
        self.k += M
        return S


SCHEDULERS["roundrobin"] = RoundRobin()


# ---------------- 采样 ----------------

def sample_next(w, order, p, q, rng, store):
    nbrs = store.adj[w.vid]
    if order == 1 or w.step == 0:
        return nbrs[rng.randrange(len(nbrs))]
    t = w.pid  # order==2 且 step>0 时 pid 必存在
    nt = set(store.adj[t])
    weights = []
    for x in nbrs:
        if x == t:
            weights.append(1.0 / p)
        elif x in nt:
            weights.append(1.0)
        else:
            weights.append(1.0 / q)
    return rng.choices(nbrs, weights=weights, k=1)[0]


# ---------------- 主模拟 ----------------

def run_sim(data_dir, order=1, policy=None, p=1.0, q=1.0, seed=2026,
            edgelist=None, meta=None, cfg=None, scheduler=None, verbose=False,
            max_rounds=20000, record_wm=False):
    cfg = cfg or json.load(open(os.path.join(data_dir, "config.json"), encoding="utf-8"))
    i, l, M = cfg["i"], cfg["l"], cfg["M"]
    edgelist = edgelist or os.path.join(data_dir, "karate.edgelist")
    meta = meta or os.path.join(data_dir, "meta.txt")
    policy = policy or ("stateaware" if order == 1 else "auw")
    sched = scheduler or SCHEDULERS[policy]
    store = GraphStore(edgelist, meta)

    walkers = [Walker(v) for v in sorted(store.block_of) for _ in range(i)]
    rng = random.Random(seed)
    resident, loads, edges_loaded, advances, rounds = set(), 0, 0, 0, 0
    used_edges = set()   # 被真实用于推进的有向边 (u,v)，u 为提供邻接表的尾点
    load_trace = []
    wm_history = []      # record_wm=True 时，每轮的 walk matrix（调度决策前快照）

    while any(not w.done for w in walkers):
        rounds += 1
        if rounds > max_rounds:
            # 调度策略无法使游走收敛（如二阶 naive 的 non-updatable 饥饿）：
            # 不报错，返回部分结果并标记 converged=False，供报告分析
            break
        R_star = sched(walkers, resident, M, store, order)
        R_star = set(R_star)
        assert len(R_star) <= M, "驻留集超过 M 上限"
        if record_wm:
            # walk matrix 快照：W[bp][bc] = 处于 (prev块=bp, curr块=bc) 的未结束 walker 数
            # （step==0 的 walker 无 prev，计入对角线 W[bc][bc]，仅需单块）
            nb = len(store.blocks)
            W = [[0] * nb for _ in range(nb)]
            for w in alive(walkers):
                bc = store.block_of[w.vid]
                bp = store.block_of[w.pid] if w.step > 0 else bc
                W[bp][bc] += 1
            wm_history.append({"round": rounds, "W": W})
        for b in sorted(R_star - resident):
            loads += 1
            edges_loaded += store.block_edges[b]
            load_trace.append((rounds, b))
        resident = R_star
        # walk-conscious 换出已隐含在 resident = R_star 中
        for w in alive(walkers):
            if updatable(w, resident, order, store.block_of):
                z = sample_next(w, order, p, q, rng, store)
                assert z in store.adj[w.vid], "oracle: 非法边"  # 正确性校验
                used_edges.add((w.vid, z))
                w.pid, w.vid = w.vid, z
                w.step += 1
                advances += 1
                if w.step >= l:
                    w.done = True
        if verbose and rounds <= 10:
            print(f"round {rounds}: R={sorted(resident)} loads={loads} "
                  f"alive={len(alive(walkers))}")

    converged = all(w.done for w in walkers)
    n_w = len(walkers)
    summary = {
        "order": order, "policy": policy, "p": p, "q": q, "M": M,
        "converged": converged, "walkers": n_w, "rounds": rounds,
        "total_loads": loads,
        "io_utilization": round(len(used_edges) / edges_loaded, 4) if edges_loaded else 0,
        "walk_updating_rate": round(advances / (rounds * n_w), 4),
        "advances": advances, "edges_loaded": edges_loaded,
    }
    if converged:
        # oracle：每个 walker 恰好走满 l 步
        assert all(w.step == l for w in walkers), "oracle: 游走步数不符"
    if record_wm:
        summary["wm_history"] = wm_history
    return summary, load_trace, walkers


def main():
    ap = argparse.ArgumentParser(description="随机游走块 I/O 模拟器")
    ap.add_argument("--data", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
    ap.add_argument("--order", type=int, choices=[1, 2], default=1)
    ap.add_argument("--policy", default=None,
                    choices=["stateaware", "naive", "auw", "roundrobin"])
    ap.add_argument("--p", type=float, default=1.0)
    ap.add_argument("--q", type=float, default=1.0)
    ap.add_argument("--meta", default=None, help="自定义分区文件（vid block_id）")
    ap.add_argument("--edgelist", default=None)
    ap.add_argument("--out", default=None, help="结果输出目录（summary.json + loads.log）")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    summary, trace, _ = run_sim(args.data, order=args.order, policy=args.policy,
                                p=args.p, q=args.q, meta=args.meta,
                                edgelist=args.edgelist, verbose=args.verbose)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        with open(os.path.join(args.out, "summary.json"), "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        with open(os.path.join(args.out, "loads.log"), "w", encoding="utf-8") as f:
            for r, b in trace:
                f.write(f"{r} {b}\n")


if __name__ == "__main__":
    main()
