# -*- coding: utf-8 -*-
"""audit_experiment.py — 实验设计自检脚本（教师复查用，不属于学生作业骨架）

逐项验算 README.md 声明的基线数据，并暴露三个指标的独立性/语义问题：
  1. 基线数字是否可复现（total_loads / rounds / updating_rate）
  2. io_utilization 的分子是否已饱和（若恒等于 2|E|，则该指标退化为 loads 的倒数）
  3. walk_updating_rate 在收敛时是否恒等于 l/rounds（若成立则与 rounds 冗余）
  4. 死锁检测：不收敛配置从第几轮起 advances 恒为 0（比 updating_rate 更硬的判据）
  5. 单一种子的敏感性：换 seed 后各分区的排名是否稳定（评估过拟合风险）

用法：python audit_experiment.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)

import walk_sim as W  # noqa: E402

DATA = os.path.join(HERE, "data")

CFGS = [
    ("1st stateaware", 1, "stateaware", None),
    ("1st roundrobin", 1, "roundrobin", None),
    ("1st +KL", 1, "stateaware", "data/community_meta.txt"),
    ("1st +ref", 1, "stateaware", "data/ref_meta.txt"),
    ("2nd auw", 2, "auw", None),
    ("2nd naive", 2, "naive", None),
    ("2nd roundrobin", 2, "roundrobin", None),
    ("2nd auw +KL", 2, "auw", "data/community_meta.txt"),
]


def per_round_advances(order, policy, meta=None, seed=2026, max_rounds=20000):
    """重跑一遍并记录每轮推进数（run_sim 的轻量复刻，仅用于审计）。"""
    cfg = json.load(open(os.path.join(DATA, "config.json"), encoding="utf-8"))
    i, l, M = cfg["i"], cfg["l"], cfg["M"]
    meta = meta or os.path.join(DATA, "meta.txt")
    sched = W.SCHEDULERS[policy]
    store = W.GraphStore(os.path.join(DATA, "karate.edgelist"), meta)
    walkers = [W.Walker(v) for v in sorted(store.block_of) for _ in range(i)]
    rng = __import__("random").Random(seed)
    resident, adv_hist = set(), []
    r = 0
    while any(not w.done for w in walkers) and r < max_rounds:
        r += 1
        resident = set(sched(walkers, resident, M, store, order))
        adv = 0
        for w in W.alive(walkers):
            if W.updatable(w, resident, order, store.block_of):
                z = W.sample_next(w, order, cfg["p"], cfg["q"], rng, store)
                w.pid, w.vid = w.vid, z
                w.step += 1
                adv += 1
                if w.step >= l:
                    w.done = True
        adv_hist.append(adv)
    return adv_hist


def main():
    print("=" * 92)
    print("[1] 基线复现 + 指标独立性检查")
    print("=" * 92)
    hdr = (f"{'config':<18}{'conv':>6}{'rounds':>7}{'loads':>7}{'io_util':>9}"
           f"{'usedE':>7}{'edgesL':>8}{'urate':>8}{'l/rounds':>10}")
    print(hdr)
    print("-" * 92)
    for name, order, pol, meta in CFGS:
        s, _, _ = W.run_sim(DATA, order=order, policy=pol, meta=meta)
        used = round(s["io_utilization"] * s["edges_loaded"])
        print(f"{name:<18}{str(s['converged']):>6}{s['rounds']:>7}{s['total_loads']:>7}"
              f"{s['io_utilization']:>9.4f}{used:>7}{s['edges_loaded']:>8}"
              f"{s['walk_updating_rate']:>8.4f}{20 / s['rounds']:>10.4f}")
    print("\n注：usedE 若恒为 156 (=2|E|) 说明分子饱和；urate 与 l/rounds 若逐行相等"
          "说明该指标与 rounds 冗余。\n")

    print("=" * 92)
    print("[2] 死锁检测：不收敛配置从第几轮起 advances 恒为 0")
    print("=" * 92)
    for name, order, pol, meta in [("2nd naive", 2, "naive", None),
                                   ("2nd roundrobin", 2, "roundrobin", None)]:
        h = per_round_advances(order, pol, meta, max_rounds=400)
        # 从末尾往回找最后一个非零轮
        last = max(k for k, a in enumerate(h) if a > 0) + 1
        print(f"{name:<18} 前 {len(h)} 轮中最后有推进的是第 {last} 轮，"
              f"之后 {len(h) - last} 轮 advances=0（真死锁）"
              f"；前 20 轮 advances={h[:20]}")
    print()

    print("=" * 92)
    print("[3] 种子敏感性：换 seed 后分区排名是否稳定（一阶 stateaware）")
    print("=" * 92)
    parts = {"sequential": None, "KL": "data/community_meta.txt",
             "ref": "data/ref_meta.txt"}
    seeds = [2026, 1, 7, 42, 123, 999]
    print(f"{'seed':>6}" + "".join(f"{k:>14}" for k in parts))
    res = {k: [] for k in parts}
    for sd in seeds:
        row = f"{sd:>6}"
        for k, mp in parts.items():
            mp = os.path.join(HERE, mp) if mp else None
            s, _, _ = W.run_sim(DATA, order=1, meta=mp, seed=sd)
            res[k].append(s["total_loads"])
            row += f"{s['total_loads']:>14}"
        print(row)
    print(f"{'mean':>6}" + "".join(f"{sum(v) / len(v):>14.1f}" for v in res.values()))
    print(f"{'std':>6}" + "".join(
        f"{(sum((x - sum(v) / len(v)) ** 2 for x in v) / len(v)) ** 0.5:>14.1f}"
        for v in res.values()))
    print("\n注：若某分区仅在 seed=2026 上最优，则作业评分存在过拟合风险。\n")

    print("=" * 92)
    print("[4] 异步（re-entry）对照：同步轮次 vs 一次加载内走到底")
    print("=" * 92)
    for M in (2, 3):
        for pol in ("stateaware", "roundrobin"):
            s, _, _ = W.run_sim(DATA, order=1, policy=pol,
                                cfg={"i": 10, "l": 20, "M": M})
            print(f"  M={M} {pol:<12} 同步: rounds={s['rounds']:>3} loads={s['total_loads']:>4}")
    print("  -> 同步语义下调度只省 I/O、不省轮次（两者 rounds 相同），"
          "GraphWalker 的异步更新未在本实验中体现。")


if __name__ == "__main__":
    main()
