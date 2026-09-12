# -*- coding: utf-8 -*-
"""export_demo.py — 为课堂动态演示生成轨迹数据，并打包成单文件 HTML

用法：
    python export_demo.py                 # 生成 walk_io_demo.html（数据内嵌，双击即开）

产出 walk_io_demo.html 是一个零依赖、可离线的单文件演示：
  * 左：karate 图（节点=顶点，颜色=块，大小=该点 walker 数，灰环=non-updatable walker）
  * 右：4 个块卡片（内存/磁盘状态 + 各自累计 I/O 次数）+ 总 I/O 大数字 + I/O 增长曲线
  * 底：分区活跃 walker 热力图（时间轴 4×轮次 + 步数剖面 4×l：均匀 → 偏斜 → 归零）
  * 底：每轮事件流日志 + 二阶 walk matrix 热力表
  * 控制：播放/暂停/单步/回退/重置/速度/进度拖动；一阶、二阶、并排对比三种视图

数据完全由 walk_sim.py 的骨架产生（同一 seed、同一调度器），因此演示中的
total_loads 与 walk_sim.py 的命令行输出严格一致（52 / 80 / 101 / 42 / …）。
"""
import json
import math
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)

import walk_sim as W  # noqa: E402

DATA = os.path.join(HERE, "data")
OUT_HTML = os.path.join(HERE, "walk_io_demo.html")
DEADLOCK_STREAK = 25     # 连续 N 轮 0 推进即判定死锁并停止记录
HARD_CAP = 400           # 兜底帧数（收敛配置最多 87 轮，不会触发）
LAYOUT_PAD = 0.05        # 归一化后四周留白（比例）
LAYOUT_SEP = 0.095       # 节点最小间距（归一化单位，≈ 画布可用宽度的 9.5%）
LAYOUT_RATIO = 1.4       # 允许的各向异性拉伸上限（让竖长的图也能填满方形画布）
LAYOUT_TRIES = 8         # 重启起点数，按质量分择优（确定性：seed = 1000 + i）
# 同分区顶点的额外弱引力。实测 karate：0 → 边交叉 67 但四块完全混杂（紧致 0.81）；
# 0.05 → 交叉 74、紧致 0.68（甜点：几乎不增加交叉，四个分区肉眼可辨）；
# 0.14 → 交叉 122、紧致 0.56（图明显变乱）。取 0.05。
LAYOUT_BLOCK_PULL = (0.05,)


# ---------------------------------------------------------------- 布局
def _fr(adj, n, seed, iters=380, block_of=None, block_pull=0.0):
    """标准 Fruchterman-Reingold：斥力 k²/d（所有点对）、引力 d²/k（有边点对）。

    注意两个力不能写反 —— 反了就会出现「近处斥力趋 0、重合点永远分不开、
    整张图被吸成一小团」的典型退化（本文件早期版本正是踩了这个坑）。
    """
    rng = random.Random(seed)
    # 圆形初始化（比随机初始化更稳定，天然把低度点甩到外围）
    pos = [[0.5 + 0.40 * math.cos(2 * math.pi * i / n) + rng.uniform(-.03, .03),
            0.5 + 0.40 * math.sin(2 * math.pi * i / n) + rng.uniform(-.03, .03)]
           for i in range(n)]
    k = math.sqrt(1.0 / n)
    t = 0.10
    adj_set = [set(a) for a in adj]
    for _ in range(iters):
        disp = [[0.0, 0.0] for _ in range(n)]
        # 斥力：所有点对
        for u in range(n):
            for v in range(u + 1, n):
                dx, dy = pos[u][0] - pos[v][0], pos[u][1] - pos[v][1]
                d2 = dx * dx + dy * dy
                if d2 < 1e-10:                      # 重合 → 随机方向抖开
                    a = rng.random() * 2 * math.pi
                    dx, dy = math.cos(a) * 1e-3, math.sin(a) * 1e-3
                    d2 = 1e-6
                d = math.sqrt(d2)
                f = k * k / d
                ux, uy = dx / d * f, dy / d * f
                disp[u][0] += ux; disp[u][1] += uy
                disp[v][0] -= ux; disp[v][1] -= uy
        # 引力：有边点对
        for u in range(n):
            for v in adj[u]:
                if v <= u:
                    continue
                dx, dy = pos[u][0] - pos[v][0], pos[u][1] - pos[v][1]
                d = math.hypot(dx, dy) or 1e-9
                f = d * d / k
                ux, uy = dx / d * f, dy / d * f
                disp[u][0] -= ux; disp[u][1] -= uy
                disp[v][0] += ux; disp[v][1] += uy
        # 块内弱引力：让同一分区的顶点自然聚成簇，跨块边更醒目
        if block_pull > 0:
            for u in range(n):
                for v in range(u + 1, n):
                    if block_of[u] != block_of[v] or v in adj_set[u]:
                        continue
                    dx, dy = pos[u][0] - pos[v][0], pos[u][1] - pos[v][1]
                    d = math.hypot(dx, dy) or 1e-9
                    f = block_pull * d * d / k
                    ux, uy = dx / d * f, dy / d * f
                    disp[u][0] -= ux; disp[u][1] -= uy
                    disp[v][0] += ux; disp[v][1] += uy
        # 限速 + 冷却
        for u in range(n):
            dx, dy = disp[u]
            dd = math.hypot(dx, dy) or 1e-9
            lim = min(dd, t)
            pos[u][0] += dx / dd * lim
            pos[u][1] += dy / dd * lim
        t *= 0.988
    return pos


def _normalize(pos, pad=LAYOUT_PAD, max_ratio=LAYOUT_RATIO):
    """居中 + 缩放到 [pad, 1-pad]；允许有限各向异性拉伸（避免竖长的图只占画布中间一条）。"""
    xs = [p[0] for p in pos]; ys = [p[1] for p in pos]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    s = 1 - 2 * pad
    sx, sy = s / max(max(xs) - min(xs), 1e-9), s / max(max(ys) - min(ys), 1e-9)
    if sx / sy > max_ratio:
        sx = max_ratio * sy
    elif sy / sx > max_ratio:
        sy = max_ratio * sx
    return [[0.5 + (p[0] - cx) * sx, 0.5 + (p[1] - cy) * sy] for p in pos]


def _relax(pos, sep, iters=200, pad=LAYOUT_PAD):
    """去重叠松弛：任何两点间距 < sep 就沿连线对推开，保证节点圈不叠在一起。

    推开后 clamp 回画布内 —— 这样 bbox 不会变大，随后的 _normalize 只会放大、
    不会缩小，relax 建立的最小间距得以保留。
    """
    n = len(pos)
    lo, hi = pad * 0.5, 1 - pad * 0.5
    for _ in range(iters):
        moved = False
        for u in range(n):
            for v in range(u + 1, n):
                dx, dy = pos[v][0] - pos[u][0], pos[v][1] - pos[u][1]
                d = math.hypot(dx, dy)
                if d < 1e-9:
                    dx, dy, d = 1e-3, 0.0, 1e-3
                if d < sep:
                    push = (sep - d) / 2
                    ux, uy = dx / d * push, dy / d * push
                    pos[u][0] -= ux; pos[u][1] -= uy
                    pos[v][0] += ux; pos[v][1] += uy
                    moved = True
        for p in pos:
            p[0] = min(hi, max(lo, p[0])); p[1] = min(hi, max(lo, p[1]))
        if not moved:
            break
    return pos


def _seg_cross(p, q, r, s):
    def o(a, b, c):
        v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        return 0 if abs(v) < 1e-12 else (1 if v > 0 else 2)
    return o(p, q, r) != o(p, q, s) and o(r, s, p) != o(r, s, q)


def _score(pos, edges, block_of, sep):
    """布局质量分：不重叠(2.0) + 边长均匀(1.2) + 少交叉(1.0) + 同块紧凑(0.8)。"""
    n = len(pos)
    md = min(math.hypot(pos[u][0] - pos[v][0], pos[u][1] - pos[v][1])
             for u in range(n) for v in range(u + 1, n))
    lens = [math.hypot(pos[u][0] - pos[v][0], pos[u][1] - pos[v][1]) for u, v in edges]
    mean = sum(lens) / len(lens)
    cv = (sum((x - mean) ** 2 for x in lens) / len(lens)) ** .5 / mean
    cross = sum(1 for i in range(len(edges)) for j in range(i + 1, len(edges))
                if not ({edges[i][0], edges[i][1]} & {edges[j][0], edges[j][1]})
                and _seg_cross(pos[edges[i][0]], pos[edges[i][1]],
                               pos[edges[j][0]], pos[edges[j][1]]))
    nb = max(block_of) + 1
    intra = [math.hypot(pos[u][0] - pos[v][0], pos[u][1] - pos[v][1])
             for u in range(n) for v in range(u + 1, n) if block_of[u] == block_of[v]]
    allp = [math.hypot(pos[u][0] - pos[v][0], pos[u][1] - pos[v][1])
            for u in range(n) for v in range(u + 1, n)]
    compact = (sum(intra) / len(intra)) / (sum(allp) / len(allp)) if intra and nb > 1 else 1.0
    return (2.0 * min(md / sep, 1.0) + 1.2 / (1 + cv) + 1.0 / (1 + cross / 8.0)
            + 0.8 / (1 + compact)), md, cv, cross, compact


def fr_layout(adj, n, edges, block_of=None, sep=LAYOUT_SEP, tries=LAYOUT_TRIES):
    """多起点重启 + 质量择优的 FR 布局（零依赖、确定性、跨机器一致）。"""
    best = None
    for pull in LAYOUT_BLOCK_PULL:
        for i in range(tries):
            pos = _normalize(_fr(adj, n, 1000 + i, block_of=block_of, block_pull=pull))
            _relax(pos, sep)
            pos = _normalize(pos)          # relax 后 bbox 只会变小 → 这里放大，间距 ≥ sep
            sc, md, cv, cross, comp = _score(pos, edges, block_of, sep)
            if best is None or sc > best[0]:
                best = (sc, pos, pull, md, cv, cross, comp)
    sc, pos, pull, md, cv, cross, comp = best
    print(f"  布局：块引力={pull}  质量分={sc:.3f}  最小间距={md:.4f}（下限 {sep}）"
          f"  边长CV={cv:.2f}  交叉={cross}  同块紧致={comp:.2f}")
    return pos


def build_graph_meta():
    cfg = json.load(open(os.path.join(DATA, "config.json"), encoding="utf-8"))
    store = W.GraphStore(os.path.join(DATA, "karate.edgelist"),
                         os.path.join(DATA, "meta.txt"))
    n = store.n_vertices()
    adj = [sorted(store.adj.get(v, [])) for v in range(n)]
    edges = sorted({(min(u, v), max(u, v))
                    for u in range(n) for v in adj[u]})
    block_of = [store.block_of[v] for v in range(n)]
    pos = fr_layout(adj, n, edges, block_of)
    sizes = [sum(1 for b in block_of if b == x) for x in store.blocks]
    cross = sum(1 for u, v in edges if block_of[u] != block_of[v])
    return {
        "n": n, "n_blocks": len(store.blocks), "M": cfg["M"],
        "i": cfg["i"], "l": cfg["l"], "seed": cfg["seed"],
        "block_sizes": sizes,
        "block_edges": [store.block_edges[b] for b in store.blocks],
        "n_edges": len(edges), "cross_edges": cross,
        "partition": "baseline 顺序等分（meta.txt）",
        "nodes": [{"v": v, "x": round(pos[v][0], 4), "y": round(pos[v][1], 4),
                   "b": block_of[v], "deg": len(adj[v])} for v in range(n)],
        "edges": [list(e) for e in edges],
    }, cfg, store


# ---------------------------------------------------------------- 轨迹
def gen_trace(order, policy, cfg, store, label, note):
    """复刻 run_sim 主循环，逐轮记录演示所需快照（与骨架同 seed、同调度器）。

    收敛配置记录全部轮次；不收敛配置在检测到死锁（连续 DEADLOCK_STREAK 轮
    0 推进）后停止记录，并额外跑一次全量（max_rounds=20000）取真实统计，
    保证演示里报告的 total_loads 与 walk_sim.py 命令行输出一致。
    """
    i, l, M = cfg["i"], cfg["l"], cfg["M"]
    # roundrobin 是模块级单例、内部带游标；每次导出前重置，保证可复现
    sched = W.SCHEDULERS[policy]
    if hasattr(sched, "k"):
        sched.k = 0
    walkers = [W.Walker(v) for v in sorted(store.block_of) for _ in range(i)]
    rng = random.Random(cfg["seed"])
    n = store.n_vertices()
    nb = len(store.blocks)
    resident, cum = set(), 0
    io_cnt = [0] * nb
    frames, advanced, rounds = [], 0, 0
    stuck_streak = 0

    while any(not w.done for w in walkers):
        rounds += 1
        if rounds > HARD_CAP or stuck_streak > DEADLOCK_STREAK:
            break
        R = set(sched(walkers, resident, M, store, order))
        newly = sorted(R - resident)
        for b in newly:
            cum += 1
            io_cnt[b] += 1
        resident = R

        cnt = [0] * n
        nu = [0] * n
        sc = sp = sb = 0          # 卡住原因：仅缺 curr / 仅缺 prev / 两者都缺
        for w in W.alive(walkers):
            cnt[w.vid] += 1
            bc = store.block_of[w.vid]
            if order == 2 and w.step > 0:
                bp = store.block_of[w.pid]
                if bc not in R and bp not in R:
                    sb += 1
                elif bc not in R:
                    sc += 1
                elif bp not in R:
                    sp += 1
                else:
                    continue
                nu[w.vid] += 1
            elif bc not in R:
                sc += 1
                nu[w.vid] += 1
        blk = [0] * nb
        blk_nu = [0] * nb
        for v in range(n):
            blk[store.block_of[v]] += cnt[v]
            blk_nu[store.block_of[v]] += nu[v]
        # 活跃 walker 的「分区 × 已走步数」分布（轮初快照；行和恒等于 blk）
        H = [[0] * l for _ in range(nb)]
        for w in W.alive(walkers):
            H[store.block_of[w.vid]][min(w.step, l - 1)] += 1
        Wm = None
        if order == 2:
            Wm = [[0] * nb for _ in range(nb)]
            for w in W.alive(walkers):
                bc = store.block_of[w.vid]
                bp = store.block_of[w.pid] if w.step > 0 else bc
                Wm[bp][bc] += 1

        adv = 0
        for w in W.alive(walkers):
            if W.updatable(w, resident, order, store.block_of):
                z = W.sample_next(w, order, cfg["p"], cfg["q"], rng, store)
                w.pid, w.vid = w.vid, z
                w.step += 1
                adv += 1
                if w.step >= l:
                    w.done = True
        advanced += adv
        stuck_streak = stuck_streak + 1 if adv == 0 else 0

        frames.append({
            "r": rounds, "R": sorted(R), "L": newly, "a": adv,
            "alive": sum(1 for w in walkers if not w.done),
            "ca": advanced,          # 到本轮为止的累计推进数（用于逐帧更新率）
            "cnt": cnt, "nu": nu, "io": list(io_cnt), "cum": cum,
            "blk": blk, "blk_nu": blk_nu, "sc": sc, "sp": sp, "sb": sb,
            "W": Wm, "H": H,
        })

    converged = all(w.done for w in walkers)
    if converged:
        # 终帧：游走全部走满 l 步 → 四个分区的活跃 walker 同时归零
        # （热力图的收尾列；不产生 I/O、不推进，仅用于呈现「完成 → 归零」）
        last = frames[-1]
        frames.append({
            "r": rounds + 1, "fin": True, "R": last["R"], "L": [], "a": 0,
            "alive": 0, "ca": advanced,
            "cnt": [0] * n, "nu": [0] * n, "io": list(io_cnt), "cum": cum,
            "blk": [0] * nb, "blk_nu": [0] * nb, "sc": 0, "sp": 0, "sb": 0,
            "W": ([[0] * nb for _ in range(nb)] if order == 2 else None),
            "H": [[0] * l for _ in range(nb)],
        })

    tr = {
        "order": order, "policy": policy, "label": label, "note": note,
        "converged": converged, "rounds": rounds, "total_loads": cum,
        "advances": advanced, "walkers": len(walkers),
        "target_steps": len(walkers) * l,
        "truncated": not converged,
        "frames": frames,
    }
    if converged:
        tr.update({"rounds_full": rounds, "total_loads_full": cum,
                   "advances_full": advanced, "deadlock_round": None})
    else:
        sched = W.SCHEDULERS[policy]
        if hasattr(sched, "k"):
            sched.k = 0
        full, _, _ = W.run_sim(DATA, order=order, policy=policy)
        # 死锁起始轮次：最后一轮有推进的轮次 + 1
        last = max((f["r"] for f in frames if f["a"] > 0), default=0)
        tr.update({"rounds_full": full["rounds"],
                   "total_loads_full": full["total_loads"],
                   "advances_full": full["advances"],
                   "deadlock_round": last + 1})
    return tr


# ---------------------------------------------------------------- HTML
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>随机游走块 I/O 动态演示 · karate / 4 块 / M=2</title>
<style>
:root{
  --bg:#f6f7f9; --panel:#ffffff; --line:#e3e6ea; --ink:#1b1f24; --ink2:#5b6472;
  --ink3:#8b95a3; --accent:#2563eb; --warn:#dc2626; --ok:#059669;
  --b0:#3b82f6; --b1:#f59e0b; --b2:#10b981; --b3:#a855f7;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:14px/1.5 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:1440px;margin:0 auto;padding:14px 18px 28px}
h1{font-size:17px;margin:0 0 2px;font-weight:650}
.sub{color:var(--ink2);font-size:12.5px;margin-bottom:12px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px}
.topbar{display:flex;flex-wrap:wrap;gap:14px;align-items:center;
  padding:10px 14px;margin-bottom:12px}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden}
.seg button{border:0;background:#fff;padding:6px 14px;cursor:pointer;color:var(--ink2);
  font-size:13px;border-right:1px solid var(--line)}
.seg button:last-child{border-right:0}
.seg button.on{background:var(--accent);color:#fff;font-weight:600}
select,input[type=range]{font:inherit}
select{padding:5px 8px;border:1px solid var(--line);border-radius:7px;background:#fff;color:var(--ink)}
.ctrl{display:flex;gap:8px;align-items:center}
.btn{border:1px solid var(--line);background:#fff;border-radius:7px;padding:6px 12px;
  cursor:pointer;color:var(--ink);font-size:13px}
.btn:hover{background:#eef2f7}
.btn.primary{background:var(--accent);color:#fff;border-color:var(--accent)}
.spacer{flex:1}
.hint{color:var(--ink3);font-size:12px}
.row{display:flex;gap:12px;align-items:stretch}
.pane{flex:1;min-width:0;display:flex;flex-direction:column}
.side{width:330px;flex:none}
svg{display:block;width:100%;height:auto}
.ttl{font-size:12px;font-weight:650;color:var(--ink2);padding:9px 12px;
  border-bottom:1px solid var(--line);letter-spacing:.3px}
.ttl .tag{float:right;font-weight:400;color:var(--ink3)}
.bodyP{padding:10px 12px}
.ioval{font-size:40px;font-weight:700;line-height:1;color:var(--accent);
  font-variant-numeric:tabular-nums}
.ioval.warn{color:var(--warn)}
.iolab{font-size:11.5px;color:var(--ink3);margin-top:2px}
.blocks{display:grid;grid-template-columns:repeat(4,1fr);gap:7px;margin-top:11px}
.blk{border:1.5px solid var(--line);border-radius:8px;padding:7px 4px;text-align:center;
  background:#fbfcfd;transition:.18s}
.blk .bn{font-weight:700;font-size:13px}
.blk .bv{font-size:19px;font-weight:700;font-variant-numeric:tabular-nums;margin:2px 0}
.blk .bs{font-size:10px;color:var(--ink3)}
.blk.res{background:#fff;border-width:2px;box-shadow:0 2px 8px rgba(37,99,235,.16)}
.blk.res .bs{color:var(--accent);font-weight:650}
.blk.dim{opacity:.5}
.blk.flash{animation:fl .5s ease}
@keyframes fl{0%{background:#fef08a;transform:scale(1.06)}100%{background:#fff;transform:none}}
.stats{display:grid;grid-template-columns:1fr 1fr;gap:6px 10px;margin-top:11px;
  font-size:12.5px}
.stats div span{color:var(--ink3)}
.stats b{font-variant-numeric:tabular-nums}
.bar{height:5px;background:#eceff3;border-radius:3px;overflow:hidden;margin:5px 0 0}
.bar i{display:block;height:100%;background:var(--accent);width:0}
.bar i.g{background:var(--ok)} .bar i.r{background:var(--warn)}
.wm{border-collapse:collapse;font-size:11px;margin-top:8px;width:100%}
.wm td,.wm th{border:1px solid var(--line);text-align:center;padding:3px 2px;
  font-variant-numeric:tabular-nums;color:var(--ink2)}
.wm th{color:var(--ink3);font-weight:500;background:#fafbfc}
.wm td.on{background:#dbeafe;color:#1d4ed8;font-weight:700}
.wm td.dg{background:#f1f5f9}
.log{height:150px;overflow-y:auto;font:12px/1.65 ui-monospace,Consolas,monospace;
  padding:8px 12px;color:var(--ink2)}
.log div{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.log .hl{color:var(--warn);font-weight:600}
.log .ok{color:var(--ok)}
.banner{background:#fef2f2;border:1px solid #fecaca;color:var(--warn);
  border-radius:8px;padding:7px 12px;font-size:12.5px;margin-top:10px;display:none}
.banner.show{display:block}
.legend{display:flex;flex-wrap:wrap;gap:10px;padding:7px 12px;font-size:11.5px;
  color:var(--ink2);border-top:1px solid var(--line)}
.legend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:4px;
  vertical-align:-1px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.big2{font-size:30px}
.note{font-size:12px;color:var(--ink2);padding:0 12px 10px}
/* ---- 分区活跃 walker 热力图 ---- */
.hmblk{margin-bottom:14px}
.hmblk:last-child{margin-bottom:0}
.hmttl{font-size:11.5px;font-weight:650;color:var(--ink2);margin-bottom:6px}
.hmttl .tag{float:right;font-weight:400;color:var(--ink3);font-variant-numeric:tabular-nums}
.hmttl .tag b{color:var(--ink);font-weight:700}
.hmrow{display:grid;grid-template-columns:26px 1fr 54px;gap:6px;align-items:center;
  margin-bottom:2px}
.hmrow .lb{font-size:10.5px;font-weight:700;font-variant-numeric:tabular-nums}
.hm{display:grid;gap:1px}
.hm i{display:block;height:14px;border-radius:1px;background:#f1f3f6}
.hm.big i{height:17px}
.hm i.cur{outline:1.6px solid #1b1f24;outline-offset:0;position:relative;z-index:2}
/* 内存行：每列的 2×2 小格 = 该轮驻留的 M 块（黑框 = 本轮新换入） */
.hm i.memc{height:18px;background:#fff;display:grid;
  grid-template-columns:1fr 1fr;grid-template-rows:1fr 1fr;gap:1px}
.hm i.memc b{background:#eceff3;border-radius:1px}
.hm i.memc b.new{box-shadow:inset 0 0 0 1.6px #1b1f24}
.hmrow .vv{font-size:10.5px;text-align:right;color:var(--ink3);
  font-variant-numeric:tabular-nums}
.hmrow .vv b{color:var(--ink);font-size:11.5px}
.hmax{font-size:10.5px;color:var(--ink3);margin:4px 0 0 32px;
  display:flex;align-items:center;gap:5px}
.hgrad{display:inline-block;width:110px;height:8px;border-radius:4px;
  background:linear-gradient(90deg,#f1f5f9,#bfdbfe,#60a5fa,#2563eb,#1e3a8a)}
.hmNote{font-size:11.5px;color:var(--ink2);line-height:1.7}
.hmNote b{color:var(--ink)}
.hmSub{font-size:11px;color:var(--ink3);margin:2px 0 6px 32px}
kbd{background:#f1f3f6;border:1px solid var(--line);border-bottom-width:2px;
  border-radius:4px;padding:0 4px;font-size:11px}
</style>
</head>
<body>
<div class="wrap">
  <h1>外存随机游走的块 I/O 动态演示</h1>
  <div class="sub" id="sub">加载中…</div>

  <div class="card topbar">
    <div class="seg" id="modeSeg">
      <button data-m="1" class="on">一阶游走</button>
      <button data-m="2">二阶游走</button>
      <button data-m="c">并排对比</button>
    </div>
    <div class="ctrl" id="polWrap">
      <span class="hint" id="polLab">调度策略</span>
      <select id="policy"></select>
    </div>
    <div class="ctrl">
      <button class="btn" id="btnPrev">◀ 上一步</button>
      <button class="btn primary" id="btnPlay">▶ 播放</button>
      <button class="btn" id="btnNext">下一步 ▶</button>
      <button class="btn" id="btnReset">⟲ 重置</button>
    </div>
    <div class="ctrl"><span class="hint">速度</span>
      <input type="range" id="speed" min="1" max="10" value="4" style="width:96px">
    </div>
    <div class="spacer"></div>
    <span class="hint"><kbd>空格</kbd> 播放/暂停 · <kbd>←</kbd><kbd>→</kbd> 单步</span>
  </div>

  <div class="card" style="padding:10px 14px;margin-bottom:12px">
    <input type="range" id="seek" min="0" max="1" value="0" style="width:100%">
    <div class="hint" id="seekLab" style="margin-top:2px">轮次 0</div>
  </div>

  <div class="row">
    <div class="pane" id="panes"></div>
    <div class="side" id="side"></div>
  </div>

  <div class="card" style="margin-top:12px">
    <div class="ttl">分区活跃 walker 热力图 <span class="tag">色标固定 · 起始均匀 → 推进偏斜 → 完成归零</span></div>
    <div id="heatHost" style="padding:10px 14px 2px"></div>
    <div class="hmNote" style="padding:2px 14px 12px;border-top:1px dashed var(--line);
      margin-top:8px;padding-top:9px">
      <b>怎么看（每行 = 一个分区 B0–B3，颜色越深 = 该分区此刻的活跃 walker 越多）</b><br>
      ① <b>起始均匀</b>：每个顶点发起 10 个 walker、每个还要走满 20 步，块大小 9/9/8/8
      → 四块 90/90/80/80，且全部落在步数剖面的「已走 0 步」列，几乎完全均匀（CV≈0.06）；<br>
      ② <b>推进偏斜</b>：只有驻留块里的 walker 能往前走，非驻留块的 walker 原地积压 ——
      步数剖面上各行的「波前」参差不齐，时间轴上四行颜色分化，极差与 CV 持续上升；
      偏斜的根源不是游走本身，而是<b>调度决定谁能走</b>；<br>
      ③ <b>完成归零</b>：走满 20 步的 walker 直接离开热力图，最后一列四个分区同时归零；
      若配置死锁（如二阶 naive），分布会<b>冻结</b>在某个非零状态，永远等不到归零 ——
      这正是 SOWalker 说的 non-updatable walks。
      「内存」行把调度器的选择摊开：彩色格 = 该块本轮在内存，黑框 = 本轮新换入（1 次块 I/O）。
      round-robin 每一列都是「两个黑框 + 两个灰格」，命中 0 次；state-aware / AUW 会出现
      「两个彩色但都不是黑框」的列，那就是省下的 I/O。
    </div>
  </div>

  <div class="card" style="margin-top:12px">
    <div class="ttl">事件流 <span class="tag">每轮：调度出驻留集 → 换入/保留/换出 → 可推进者走一步</span></div>
    <div class="log" id="log"></div>
  </div>
</div>
<script>
const DATA = __DEMO_DATA__;
const M = DATA.meta.M, NB = DATA.meta.n_blocks, N = DATA.meta.n, LW = DATA.meta.l;
const COL = ['#3b82f6','#f59e0b','#10b981','#a855f7','#ef4444','#0ea5e9','#84cc16','#ec4899'];
let mode='1', polKey=null, playing=false, timer=null, speed=4, gidx=0;
let panes=[];   // 当前展示的 trace 列表

/* ---------- 工具 ---------- */
const el=(t,c,h)=>{const e=document.createElement(t);if(c)e.className=c;if(h!=null)e.innerHTML=h;return e;};
const fmt=n=>n.toLocaleString('en-US');

function traceList(){
  const all=Object.keys(DATA.traces);
  // 并排模式下左栏固定为一阶 state-aware，下拉只负责选右栏的二阶策略
  return all.filter(k=>{
    const t=DATA.traces[k];
    return mode==='c' ? t.order===2 : t.order===(+mode);
  });
}
function curTrace(){return DATA.traces[polKey];}

/* ---------- 图渲染 ---------- */
function buildSVG(host,tr){
  const W0=470,H0=470,pad=34;
  const ns=DATA.meta.nodes, es=DATA.meta.edges;
  const X=v=>pad+ns[v].x*(W0-2*pad), Y=v=>pad+ns[v].y*(H0-2*pad);
  let s=`<svg viewBox="0 0 ${W0} ${H0}">`;
  s+=`<g stroke="#d6dae0" stroke-width="1">`;
  for(const [u,v] of es) s+=`<line x1="${X(u).toFixed(1)}" y1="${Y(u).toFixed(1)}" x2="${X(v).toFixed(1)}" y2="${Y(v).toFixed(1)}"/>`;
  s+=`</g>`;
  for(const nd of ns){
    s+=`<circle id="${host}-r${nd.v}" cx="${X(nd.v).toFixed(1)}" cy="${Y(nd.v).toFixed(1)}" r="7" fill="none" stroke="#dc2626" stroke-width="2.6" opacity="0"/>`;
    s+=`<circle id="${host}-n${nd.v}" cx="${X(nd.v).toFixed(1)}" cy="${Y(nd.v).toFixed(1)}" r="4" fill="${COL[nd.b]}" stroke="#fff" stroke-width="1.2"/>`;
  }
  s+=`</svg>`;
  return s;
}

function renderFrame(){
  panes.forEach(p=>drawPane(p));
  drawSide(); drawLog(); drawSeek();
}
function drawPane(p){
  const tr=p.tr, f=tr.frames[p.idx];
  if(!f) return;
  const R=new Set(f.R);
  // 节点
  for(let v=0;v<N;v++){
    const c=p.svg.querySelector('#'+p.host+'-n'+v), r=p.svg.querySelector('#'+p.host+'-r'+v);
    const inR=R.has(DATA.meta.nodes[v].b);
    const cnt=f.cnt[v], nu=f.nu[v];
    const rad=Math.min(13.5, cnt? 3.4+Math.sqrt(cnt)*1.35 : 2.9);   // 上限 < 布局最小间距的一半
    c.setAttribute('r',rad.toFixed(2));
    c.setAttribute('fill',COL[DATA.meta.nodes[v].b]);
    c.setAttribute('opacity', inR?1:0.42);
    c.setAttribute('stroke', inR?'#1b1f24':'#fff');
    c.setAttribute('stroke-width', inR?1.5:1);
    // 灰红环：卡住的 walker 占比
    if(nu>0&&cnt>0){
      const frac=nu/cnt;
      r.setAttribute('r',(rad+2.4).toFixed(2));
      r.setAttribute('opacity',(0.25+0.6*frac).toFixed(2));
      r.setAttribute('stroke-dasharray',(2*Math.PI*(rad+2.4)*frac).toFixed(1)+' 999');
    } else r.setAttribute('opacity',0);
  }
  p.head.innerHTML=`<b>${tr.label}</b> <span style="color:var(--ink3);font-weight:400">${tr.note}</span>`;
  p.ioEl.textContent=fmt(f.cum);
  p.ioEl.className='ioval big2'+(f.a===0?' warn':'');
  p.rdEl.textContent=f.fin?`完成 / ${tr.rounds}`:`${f.r} / ${tr.rounds}${tr.truncated?'+':''}`;
  p.adEl.innerHTML=`本轮推进 <b>${f.a}</b> / 存活 ${f.alive}`;
  // 与 walk_sim.py 的 walk_updating_rate 同定义：累计推进 /（轮次 × walker 数）
  const rr=f.fin? tr.rounds : f.r;
  p.rtEl.innerHTML=`累计更新率 <b>${(f.ca/(rr*tr.walkers)).toFixed(3)}</b>`;
  // 块卡片
  p.blkEls.forEach((be,b)=>{
    be.querySelector('.bv').textContent=f.io[b];
    const res=R.has(b);
    be.className='blk '+(res?'res':'dim');
    be.querySelector('.bs').textContent =
      (res?`内存`:`磁盘`)+` · ${f.blk[b]} 活${f.blk_nu[b]?` / ${f.blk_nu[b]} 卡`:''}`;
    if(f.L.includes(b)){be.classList.add('flash');setTimeout(()=>be.classList.remove('flash'),480);}
  });
  // 卡住原因
  if(tr.order===2){
    p.stEl.innerHTML=`本轮卡住：<b>${f.sc+f.sp+f.sb}</b> 　`
      +`仅缺 curr 块 <b>${f.sc}</b> ｜ 仅缺 prev 块 <b style="color:#b45309">${f.sp}</b> ｜ 双缺 <b>${f.sb}</b>`;
    p.stEl.style.display='';
  }else{
    p.stEl.innerHTML=`本轮卡住（curr 块不在内存）：<b>${f.sc}</b>`;
    p.stEl.style.display='';
  }
  if(f.W) drawWM(p.wmEl,f.W,R);
  else p.wmEl.innerHTML='';
  if(tr.truncated){
    p.ftEl.style.color='#b45309';
    p.ftEl.innerHTML=`演示在第 ${tr.rounds} 轮截断：自第 <b>${tr.deadlock_round}</b> 轮起 0 推进（死锁）。`
      +`若按 walk_sim.py 跑满 20000 轮，真实结果为 I/O <b>${fmt(tr.total_loads_full)}</b>、`
      +`仅完成 ${fmt(tr.advances_full)}/${fmt(tr.target_steps)} 步。`;
  } else p.ftEl.innerHTML='';
  drawCurve(p.cvEl,tr,p.idx);
  drawHeat(p);
}
function drawWM(host,W,R){
  let s='<table class="wm"><tr><th></th>';
  for(let j=0;j<NB;j++)s+=`<th>cur B${j}</th>`;
  s+='</tr>';
  for(let i=0;i<NB;i++){
    s+=`<tr><th>prev B${i}</th>`;
    for(let j=0;j<NB;j++){
      const on=R.has(i)&&R.has(j);
      s+=`<td class="${on?'on':(i===j?'dg':'')}">${W[i][j]||''}</td>`;
    }
    s+='</tr>';
  }
  s+='</table><div class="hint" style="margin-top:4px">蓝格 = 本轮驻留集可更新的 (prev,curr) 块对；'
   + 'AUW = 蓝格数字之和</div>';
  host.innerHTML=s;
}
function drawCurve(host,tr,idx){
  const w=300,h=64,maxR=Math.max(10,tr.frames.length),maxY=Math.max(10,tr.frames[tr.frames.length-1].cum);
  let d='';
  for(let i=0;i<=idx;i++){
    const f=tr.frames[i];
    d+=(i?'L':'M')+((i/maxR*w).toFixed(1))+','+(h-(f.cum/maxY)*(h-6)).toFixed(1)+' ';
  }
  host.innerHTML=`<svg viewBox="0 0 ${w} ${h}" style="width:100%;height:${h}px">
    <path d="${d}" fill="none" stroke="#2563eb" stroke-width="2"/>
    <line x1="0" y1="${h}" x2="${w}" y2="${h}" stroke="#e3e6ea"/></svg>
    <div class="hint">横轴=轮次，纵轴=累计块 I/O（当前 ${tr.frames[idx].cum}）</div>`;
}
/* ---------- 分区活跃 walker 热力图 ---------- */
/* 固定色标（白→深蓝），sqrt 压缩使尾部小值仍可见；max 取整条轨迹的全局最大值，
   因此不同轮次、不同策略之间颜色可直接比较。 */
const HSTOPS=[[0,[241,245,249]],[.25,[191,219,254]],[.55,[96,165,250]],
              [.8,[37,99,235]],[1,[30,58,138]]];
function heat(v,mx){
  if(!v||!mx) return '#f1f3f6';
  const t=Math.sqrt(Math.min(1,v/mx));
  for(let i=1;i<HSTOPS.length;i++){
    if(t<=HSTOPS[i][0]||i===HSTOPS.length-1){
      const a=HSTOPS[i-1],b=HSTOPS[i],k=(t-a[0])/((b[0]-a[0])||1);
      const c=[0,1,2].map(j=>Math.round(a[1][j]+(b[1][j]-a[1][j])*k));
      return `rgb(${c[0]},${c[1]},${c[2]})`;
    }
  }
  return '#1e3a8a';
}
function mkCells(id,n,big){
  const host=document.getElementById(id);
  let h=''; for(let j=0;j<n;j++) h+='<i></i>';
  host.innerHTML=h;
  if(big) host.className='hm big';
  return [...host.children];
}
function buildHeat(p){
  const tr=p.tr, T=tr.frames.length;
  let mb=1,mc=1;
  tr.frames.forEach(f=>{
    f.blk.forEach(v=>{if(v>mb)mb=v;});
    f.H.forEach(row=>row.forEach(v=>{if(v>mc)mc=v;}));
  });
  p.hMaxB=mb; p.hMaxC=mc;
  p.hit=tr.frames.filter(f=>!f.fin&&f.L.length===0).length;   // 无换入（命中）的轮数
  const gw=n=>`grid-template-columns:repeat(${n},1fr)`;
  let s=`<div class="hmttl"><span style="color:${tr.order===2?'#7c3aed':'#1d4ed8'}">■</span> `
    +`${tr.label}　活跃 walker 分布 <span class="tag" id="${p.host}-hp"></span></div>`;
  s+=`<div class="hmSub">每列 = 一个轮初快照（第 1 轮 → 第 ${tr.rounds} 轮 → 完成）</div>`;
  s+=`<div class="hmrow"><div class="lb" style="color:var(--ink3)">内存</div>`
    +`<div class="hm" id="${p.host}-mem" style="${gw(T)}"></div>`
    +`<div class="vv">M=${M}</div></div>`;
  for(let b=0;b<NB;b++)
    s+=`<div class="hmrow"><div class="lb" style="color:${COL[b]}">B${b}</div>`
      +`<div class="hm" id="${p.host}-t${b}" style="${gw(T)}"></div>`
      +`<div class="vv" id="${p.host}-tv${b}"></div></div>`;
  s+=`<div class="hmrow"><div class="lb" style="color:var(--ink3)">Σ</div>`
    +`<div class="hm" id="${p.host}-ts" style="${gw(T)}"></div>`
    +`<div class="vv" id="${p.host}-tvs"></div></div>`;
  s+=`<div class="hmax"><span>0</span><span class="hgrad"></span><span>${mb} 个/块</span></div>`;
  s+=`<div class="hmSub">「内存」行：每列的 2×2 小格按 B0 B1 / B2 B3 排列，`
    +`彩色 = 该块本轮驻留内存，<b>黑框 = 本轮新换入</b>（产生 1 次块 I/O），灰格 = 在磁盘。</div>`;
  s+=`<div class="hmSub" style="margin-top:10px">当前快照 · 每列 = 已走 s 步（0 → ${LW-1}，`
    +`再走 1 步即离开热力图）</div>`;
  for(let b=0;b<NB;b++)
    s+=`<div class="hmrow"><div class="lb" style="color:${COL[b]}">B${b}</div>`
      +`<div class="hm" id="${p.host}-s${b}" style="grid-template-columns:repeat(${LW},30px);`
      +`justify-content:start"></div>`
      +`<div class="vv" id="${p.host}-sv${b}"></div></div>`;
  s+=`<div class="hmax"><span>0</span><span class="hgrad"></span><span>${mc} 个/格</span></div>`;
  s+=`<div class="hmNote" id="${p.host}-hn" style="margin:8px 0 0 32px"></div>`;
  p.hmHost.innerHTML=s;
  p.hCells=[]; for(let b=0;b<NB;b++) p.hCells.push(mkCells(p.host+'-t'+b,T,false));
  p.hCellsSum=mkCells(p.host+'-ts',T,false);
  p.hStep=[];  for(let b=0;b<NB;b++) p.hStep.push(mkCells(p.host+'-s'+b,LW,true));
  p.hMem=mkCells(p.host+'-mem',T,false);
  p.hMemB=p.hMem.map(c=>{c.className='memc';c.innerHTML='<b></b><b></b><b></b><b></b>';
    return [...c.children];});
  p.hmDrawn=-1; p.hmCur=null;
}
function drawHeat(p){
  const tr=p.tr, f=tr.frames[p.idx];
  if(!f||!f.H) return;
  if(p.idx<p.hmDrawn){                       // 拖动回退 → 整条重画
    p.hCells.forEach(row=>row.forEach(c=>{c.style.background='#f1f3f6';c.className='';}));
    p.hCellsSum.forEach(c=>{c.style.background='#f1f3f6';c.className='';});
    p.hMemB.forEach(bs=>bs.forEach(b=>{b.style.background='#eceff3';b.className='';}));
    p.hmDrawn=-1; p.hmCur=null;
  }
  for(let j=p.hmDrawn+1;j<=p.idx;j++){       // 只补画新增的列
    const fj=tr.frames[j], tot=fj.blk.reduce((a,b)=>a+b,0);
    for(let b=0;b<NB;b++) p.hCells[b][j].style.background=heat(fj.blk[b],p.hMaxB);
    p.hCellsSum[j].style.background=heat(tot,tr.walkers);
    p.hCellsSum[j].title=(fj.fin?'完成':('第 '+fj.r+' 轮'))+' · 全图活跃 '+tot;
    // 内存行：驻留 = 彩色，本轮新换入 = 黑框
    const Rs=new Set(fj.R), Ls=new Set(fj.L), bs=p.hMemB[j];
    for(let b=0;b<NB;b++){
      bs[b].style.background=Rs.has(b)?COL[b]:'#eceff3';
      bs[b].className=Ls.has(b)?'new':'';
      bs[b].title=(fj.fin?'完成':('第 '+fj.r+' 轮'))+' · B'+b
        +(Rs.has(b)?(Ls.has(b)?' 本轮换入内存':' 已在内存（命中）'):' 在磁盘');
    }
  }
  if(p.hmCur!=null){
    for(let b=0;b<NB;b++) p.hCells[b][p.hmCur].classList.remove('cur');
    p.hCellsSum[p.hmCur].classList.remove('cur');
  }
  for(let b=0;b<NB;b++) p.hCells[b][p.idx].classList.add('cur');
  p.hCellsSum[p.idx].classList.add('cur');
  p.hmCur=p.idx; p.hmDrawn=p.idx;

  let tot=0;
  for(let b=0;b<NB;b++){
    const v=f.blk[b]; tot+=v;
    const ev=document.getElementById(p.host+'-tv'+b); if(ev) ev.innerHTML='<b>'+v+'</b>';
    let rs=0;
    for(let s=0;s<LW;s++){
      const vv=f.H[b][s]||0; rs+=vv;
      const c=p.hStep[b][s];
      c.style.background=heat(vv,p.hMaxC);
      c.title='B'+b+' · 已走 '+s+' 步 · '+vv+' 个 walker';
    }
    const es=document.getElementById(p.host+'-sv'+b); if(es) es.innerHTML='<b>'+rs+'</b>';
  }
  const es2=document.getElementById(p.host+'-tvs'); if(es2) es2.innerHTML='<b>'+tot+'</b>';
  // 偏斜度量：极差 + 变异系数 CV（0 = 四块完全均匀）
  const mean=tot/NB; let vs=0;
  f.blk.forEach(v=>{vs+=(v-mean)*(v-mean);});
  const cv=mean?Math.sqrt(vs/NB)/mean:0;
  const rg=Math.max.apply(null,f.blk)-Math.min.apply(null,f.blk);
  const hp=document.getElementById(p.host+'-hp');
  if(hp) hp.innerHTML=`活跃 <b>${tot}</b>/${tr.walkers}　极差 <b>${rg}</b>　CV <b>${cv.toFixed(3)}</b>`;
  const hn=document.getElementById(p.host+'-hn');
  if(hn){
    const phase = f.fin
      ? `<span style="color:var(--ok)">✅ 全部 walker 走满 ${LW} 步，四个分区活跃数<b>同时归零</b>。</span>`
        : (tr.truncated
        ? `<span style="color:#b45309">⚠ 死锁：活跃分布冻结在 ${f.blk.join(' / ')}，<b>永不归零</b>`
          +(cv<0.15
            ?' —— 而且是「近乎均匀地」卡死：四块谁也走不动，这种均匀毫无意义。</span>'
            :'。</span>')
        : (cv<0.15?'当前仍接近均匀（波前尚未拉开）。'
          :cv<0.6?'已明显偏斜：驻留块里的 walker 先走完，非驻留块在左侧积压。'
          :'严重偏斜：活跃 walker 几乎全挤在少数几块上。'));
    hn.innerHTML=`当前活跃 <b>${tot}</b>，已完成 <b>${tr.walkers-tot}</b>；`
      +`四块 [${f.blk.join(' / ')}]，极差 <b>${rg}</b>、CV <b>${cv.toFixed(3)}</b>。${phase}`
      +`<br>`+(f.fin
        ? `游走结束，最后驻留 <b>{${f.R.map(b=>'B'+b).join(',')}}</b>`
        : `本轮内存 <b>{${f.R.map(b=>'B'+b).join(',')}}</b>`
          +(f.L.length
            ? `　新换入 <b>${f.L.map(b=>'B'+b).join(',')}</b>（+${f.L.length} 次 I/O，累计 ${f.cum}）`
            : `　<span style="color:var(--ok)">命中，无换入</span>（累计 I/O 仍为 ${f.cum}）`))
      +`　全程命中 <b>${p.hit}</b>/${tr.rounds} 轮`
      +(p.hit===0?`（每轮整轮换掉 —— 这就是 M=${M} 下 round-robin 的浪费所在）`:'');
  }
}
function drawSide(){
  const host=document.getElementById('side'); host.innerHTML='';
  if(mode==='c'){
    const c=el('div','card');
    c.innerHTML=`<div class="ttl">并排对比 <span class="tag">同一时间轴</span></div><div class="bodyP">
      <div class="hint" style="line-height:1.75">左一右二同步推进，注意三点差异：<br>
      ① <b>I/O 增长斜率</b>：二阶每步要 prev+curr 双块共驻，I/O 涨得更快；<br>
      ② <b>红弧（卡住的 walker）</b>：二阶明显更多，多出来的那部分正是「只缺 prev 块」；<br>
      ③ 把策略切到「二阶 · naive」→ I/O 几乎不涨，但游走从第 65 轮起彻底停滞，
      这就是 SOWalker 说的 non-updatable walks；<br>
      ④ 下方<b>活跃 walker 热力图</b>：右栏（二阶）的偏斜出现得更早、更陡，
      且 naive 那一行会「冻结」在深色上，永远不会归零。</div></div>`;
    host.appendChild(c);
    const s2=el('div','card'); s2.style.marginTop='12px';
    let h=`<div class="ttl">I/O 对比（当前轮次 ${gidx}）</div><div class="bodyP"><div class="stats" style="grid-template-columns:1fr">`;
    panes.forEach(p=>{
      const f=p.tr.frames[p.idx];
      h+=`<div>${p.tr.label}：<b>${f.cum}</b> <span style="color:var(--ink3)">`
        +`(终值 ${fmt(p.tr.total_loads_full)}${p.tr.converged?'':'，不收敛'})</span></div>`;
    });
    if(panes.length===2){
      const a=panes[0].tr.frames[panes[0].idx].cum, b=panes[1].tr.frames[panes[1].idx].cum;
      h+=`<div style="margin-top:4px;color:var(--ink2)">二阶 / 一阶 I/O 比值：<b>${a?(b/a).toFixed(2):'—'}×</b></div>`;
    }
    h+=`</div></div>`;
    s2.innerHTML=h; host.appendChild(s2);
    return;
  }
  const t=curTrace(), f=t.frames[panes[0].idx];
  const c=el('div','card');
  c.innerHTML=`<div class="ttl">配置说明 <span class="tag">${t.converged?'已收敛':'未收敛'}</span></div>
    <div class="bodyP"><div class="hint" style="line-height:1.7">${t.note}<br>
    图：karate ${N} 点 / ${DATA.meta.n_edges} 边，${DATA.meta.partition}，
    块大小 ${JSON.stringify(DATA.meta.block_sizes)}，跨块边 ${DATA.meta.cross_edges}/${DATA.meta.n_edges}。<br>
    内存最多驻留 M=${M} 块；每点 ${DATA.meta.i} 个 walker，步长 ${DATA.meta.l}
    → 共 ${t.walkers} walkers / ${fmt(t.target_steps)} 步。<br>
    结果：<b>${t.converged?'收敛':'未收敛'}</b>，${t.rounds_full} 轮，
    累计块 I/O <b>${fmt(t.total_loads_full)}</b>，完成 ${fmt(t.advances_full)}/${fmt(t.target_steps)} 步。<br>
    下方热力图追踪四个分区的活跃 walker：起始 ${DATA.meta.block_sizes.map(s=>s*DATA.meta.i).join('/')}（均匀）
    → 推进中偏斜（极差 / CV 上升）→ ${t.converged?'完成归零':'死锁冻结'}。</div></div>`;
  host.appendChild(c);

  const s2=el('div','card'); s2.style.marginTop='12px';
  s2.innerHTML=`<div class="ttl">本轮总览</div><div class="bodyP">
    <div class="ioval">${f?fmt(f.cum):0}</div><div class="iolab">累计块 I/O 次数</div>
    <div class="stats">
      <div><span>轮次</span> <b>${f?f.r:0}</b></div>
      <div><span>本轮推进</span> <b>${f?f.a:0}</b></div>
      <div><span>存活 walker</span> <b>${f?f.alive:t.walkers}</b></div>
      <div><span>驻留块</span> <b>${f?f.R.map(b=>'B'+b).join(','):'-'}</b></div>
    </div>
    <div class="bar"><i id="pb"></i></div>
    <div class="hint" id="pbt">完成度 0%</div>
    </div>`;
  host.appendChild(s2);
  const pct=Math.round(100*f.ca/t.target_steps);
  s2.querySelector('#pb').style.width=pct+'%';
  s2.querySelector('#pb').className=t.converged?'g':'';
  s2.querySelector('#pbt').textContent=`累计完成 ${fmt(f.ca)}/${fmt(t.target_steps)} 步（${pct}%）`;
}
function drawLog(){
  const L=document.getElementById('log'); L.innerHTML='';
  if(mode==='c'){L.style.display='grid';L.style.gridTemplateColumns='1fr 1fr';L.style.gap='0 20px';}
  else L.style.display='block';
  panes.forEach(p=>{
    const col=el('div');
    if(mode==='c'){
      const h=el('div',null,`<b style="color:#1b1f24">${p.tr.label}</b>`);
      col.appendChild(h);
    }
    for(let i=Math.max(0,p.idx-28);i<=p.idx;i++){
      const f=p.tr.frames[i];
      if(f.fin){
        col.appendChild(el('div','ok',`✓ 全部 walker 走满 ${LW} 步 —— 四分区活跃数归零`
          +`（共 ${p.tr.rounds} 轮，累计 I/O ${f.cum}）`));
        continue;
      }
      // 内存变化拆成三段：保留 / 换入 / 换出（round-robin 下换入恒等于整轮驻留集，
      // 因为它每轮都把 M 块全部换掉；拆开写才看得出「命中 0 次」）
      const prevR=i>0?new Set(p.tr.frames[i-1].R):new Set();
      const kept=f.R.filter(b=>prevR.has(b));
      const evict=[...prevR].filter(b=>f.R.indexOf(b)<0).sort();
      const mem=`内存 {${f.R.map(b=>'B'+b).join(',')}}`
        +(f.L.length
          ? ` ← 换入 ${f.L.map(b=>'B'+b).join(',')}`
            +(kept.length?`（保留 ${kept.map(b=>'B'+b).join(',')}）`:'（整轮换掉）')
            +(evict.length?` 换出 ${evict.map(b=>'B'+b).join(',')}`:'')
            +` → I/O #${f.cum}`
          : ` 命中（保留 ${kept.length} 块，无换入，I/O 仍为 #${f.cum}）`);
      const stuck=f.sc+f.sp+f.sb;
      const cls=(f.a===0)?'hl':(f.L.length?'':'ok');
      col.appendChild(el('div',cls,`r${String(f.r).padStart(3)}  ${mem}`
        +`  → 推进 ${f.a}${stuck?`，卡住 ${stuck}`:''}${f.sp?`（缺 prev 块 ${f.sp}）`:''}`));
    }
    L.appendChild(col);
  });
  L.scrollTop=L.scrollHeight;
}
function drawSeek(){
  const sk=document.getElementById('seek');
  const mx=Math.max(0,maxFrames()-1);
  sk.max=mx; sk.value=Math.min(gidx,mx);
  const parts=panes.map(p=>{
    const f=p.tr.frames[p.idx];
    return `${p.tr.label}：${f.fin?'完成':'r'+f.r}　I/O <b>${f.cum}</b>/${fmt(p.tr.total_loads_full)}`
      +`　活跃 <b>${f.blk.reduce((a,b)=>a+b,0)}</b>`;
  });
  document.getElementById('seekLab').innerHTML=
    `全局轮次 ${gidx} / ${mx}　·　`+parts.join('　｜　');
}

/* ---------- 面板构建 ---------- */
function buildPanes(){
  const host=document.getElementById('panes'); host.innerHTML='';
  document.getElementById('heatHost').innerHTML='';
  panes=[];
  const keys = mode==='c' ? ['1-stateaware', (polKey&&polKey.startsWith('2-'))?polKey:'2-auw'] : [polKey];
  keys.forEach((k,ix)=>{
    const tr=DATA.traces[k]; if(!tr) return;
    const two = keys.length>1;
    const box=el('div','card');
    box.style.flex='1'; box.style.minWidth='0'; box.style.marginBottom='12px';
    let h=`<div class="ttl" id="h${ix}"></div><div class="bodyP">
      <div class="ioval big2" id="io${ix}">0</div><div class="iolab">累计块 I/O 次数　轮次 <span id="rd${ix}"></span></div>
      <div class="blocks" id="bb${ix}">`;
    for(let b=0;b<NB;b++) h+=`<div class="blk dim" id="b${ix}_${b}"><div class="bn" style="color:${COL[b]}">B${b}</div>
       <div class="bv">0</div><div class="bs">磁盘</div></div>`;
    h+=`</div>
      <div class="stats" style="grid-template-columns:1fr 1fr">
        <div id="ad${ix}"></div><div id="rt${ix}"></div></div>
      <div class="hint" id="st${ix}" style="margin-top:8px"></div>
      <div id="cv${ix}" style="margin-top:8px"></div>
      <div id="wm${ix}"></div>
      <div class="hint" id="ft${ix}" style="margin-top:8px"></div>
      <div class="banner" id="bn${ix}">⚠ 连续多轮 0 推进 —— 死锁（non-updatable walks），游走已停滞</div>
      </div>
      <div class="legend">
        <span><i style="background:${COL[0]}"></i>B0</span>
        <span><i style="background:${COL[1]}"></i>B1</span>
        <span><i style="background:${COL[2]}"></i>B2</span>
        <span><i style="background:${COL[3]}"></i>B3</span>
        <span style="color:#dc2626">◯ 红弧 = 该点上被卡住的 walker 占比</span>
        <span>淡色 = 块在磁盘（未驻留）</span>
      </div>`;
    box.innerHTML=h;
    host.appendChild(box);
    const p={tr,idx:0,host:'p'+ix,svg:null,
      head:box.querySelector('#h'+ix), ioEl:box.querySelector('#io'+ix),
      rdEl:box.querySelector('#rd'+ix), adEl:box.querySelector('#ad'+ix),
      rtEl:box.querySelector('#rt'+ix), stEl:box.querySelector('#st'+ix),
      cvEl:box.querySelector('#cv'+ix), wmEl:box.querySelector('#wm'+ix),
      bnEl:box.querySelector('#bn'+ix), ftEl:box.querySelector('#ft'+ix),
      blkEls:[...Array(NB)].map((_,b)=>box.querySelector('#b'+ix+'_'+b))};
    // 插入 svg
    const svgWrap=el('div'); svgWrap.innerHTML=buildSVG('p'+ix,tr);
    svgWrap.style.padding='4px 12px 0';
    box.insertBefore(svgWrap, box.children[1]);
    p.svg=svgWrap.querySelector('svg');
    // 分区活跃 walker 热力图（整幅宽，放在下方独立卡片里）
    const hbox=el('div','hmblk');
    document.getElementById('heatHost').appendChild(hbox);
    p.hmHost=hbox; buildHeat(p);
    panes.push(p);
  });
  if(mode==='c'){
    host.style.display='grid'; host.style.gridTemplateColumns='1fr 1fr'; host.style.gap='12px';
  }else{host.style.display='block';}
  syncIdx(panes[0].idx);
}
function maxFrames(){return Math.max(...panes.map(p=>p.tr.frames.length));}
function syncIdx(v){
  gidx=Math.max(0,v);
  panes.forEach(p=>{
    p.idx=Math.min(gidx,p.tr.frames.length-1);
    let streak=0;
    for(let i=Math.max(0,p.idx-3);i<=p.idx;i++) if(p.tr.frames[i].a===0&&!p.tr.frames[i].fin) streak++;
    p.bnEl.classList.toggle('show', streak>=4);
  });
}
function step(d){
  const mx=maxFrames()-1;
  let v=gidx+d;
  if(v<0)v=0;
  if(v>mx){v=mx;pause();}
  syncIdx(v); renderFrame();
}
function play(){
  if(playing)return;
  if(gidx>=maxFrames()-1) syncIdx(0);   // 到底后自动从头重播
  playing=true;document.getElementById('btnPlay').textContent='⏸ 暂停';
  timer=setInterval(()=>step(1), 1000/speed*1.0);
}
function pause(){playing=false;document.getElementById('btnPlay').textContent='▶ 播放';clearInterval(timer);}

/* ---------- 初始化 ---------- */
function initPolicy(){
  const sel=document.getElementById('policy'), list=traceList();
  sel.innerHTML='';
  document.getElementById('polLab').textContent = mode==='c' ? '右栏策略' : '调度策略';
  list.forEach(k=>{
    const t=DATA.traces[k];
    const o=el('option',null, mode==='c'
      ? `一阶 state-aware  vs  二阶 ${t.policy}（${t.converged?'收敛':'不收敛'}）`
      : `${t.order===1?'一阶':'二阶'} · ${t.policy}`
        +`（I/O ${fmt(t.total_loads_full)}${t.converged?'':'，不收敛'}）`);
    o.value=k; sel.appendChild(o);
  });
  polKey=list[0]; sel.value=polKey;
}
document.getElementById('modeSeg').addEventListener('click',e=>{
  const b=e.target.closest('button'); if(!b)return;
  [...e.currentTarget.children].forEach(x=>x.classList.remove('on'));
  b.classList.add('on'); mode=b.dataset.m;
  pause(); initPolicy(); buildPanes(); renderFrame();
});
document.getElementById('policy').addEventListener('change',e=>{
  polKey=e.target.value; pause(); buildPanes(); renderFrame();
});
document.getElementById('btnPlay').onclick=()=>playing?pause():play();
document.getElementById('btnNext').onclick=()=>{pause();step(1);};
document.getElementById('btnPrev').onclick=()=>{pause();step(-1);};
document.getElementById('btnReset').onclick=()=>{pause();syncIdx(0);renderFrame();};
document.getElementById('speed').oninput=e=>{speed=+e.target.value;if(playing){pause();play();}};
document.getElementById('seek').oninput=e=>{pause();syncIdx(+e.target.value);renderFrame();};
window.addEventListener('keydown',e=>{
  if(e.code==='Space'){e.preventDefault();playing?pause():play();}
  if(e.code==='ArrowRight'){pause();step(1);}
  if(e.code==='ArrowLeft'){pause();step(-1);}
});

document.getElementById('sub').textContent=
  `karate 图 ${N} 点 / ${DATA.meta.n_edges} 边 · ${DATA.meta.partition} `
  +`[${DATA.meta.block_sizes.join(', ')}] · 跨块边 ${DATA.meta.cross_edges} · `
  +`内存 M=${M} 块 · ${DATA.meta.i} walkers/点 × ${DATA.meta.l} 步 · seed=${DATA.meta.seed} · `
  +`底部热力图：分区活跃 walker 均匀 → 偏斜 → 归零 · `
  +`数据与 walk_sim.py 完全一致`;
initPolicy(); buildPanes(); renderFrame();
</script>
</body>
</html>
"""


def main():
    meta, cfg, store = build_graph_meta()
    order_meta = meta  # noqa: F841  (命名保持一致)
    traces = {}

    specs = [
        (1, "stateaware", "一阶 · state-aware", "GraphWalker §3：优先加载含 walker 最多的块"),
        (1, "roundrobin", "一阶 · round-robin", "稻草人：逐块迭代，无视 walker 分布"),
        (2, "auw", "二阶 · AUW", "SOWalker §3：选可更新游走数最大的块集合"),
        (2, "naive", "二阶 · naive", "一阶调度直接套用 → non-updatable 饥饿"),
        (2, "roundrobin", "二阶 · round-robin", "逐块迭代 + 双块约束 → I/O 灾难"),
    ]
    for order, policy, label, note in specs:
        key = f"{order}-{policy}"
        tr = gen_trace(order, policy, cfg, store, label, note)
        traces[key] = tr
        # 自测：轨迹必须与 walk_sim.py 的输出对账
        last = tr["frames"][-1]
        assert last["cum"] == tr["total_loads"], f"{key}: 末帧 I/O 与总 I/O 不符"
        if tr["converged"]:
            assert last["ca"] == tr["target_steps"], f"{key}: 收敛但未走满步数"
            assert tr["total_loads_full"] == tr["total_loads"]
            sched = W.SCHEDULERS[policy]
            if hasattr(sched, "k"):
                sched.k = 0
            ref, _, _ = W.run_sim(DATA, order=order, policy=policy)
            assert ref["total_loads"] == tr["total_loads"], f"{key}: 与 walk_sim 不一致"
            assert ref["rounds"] == tr["rounds"], f"{key}: 轮次与 walk_sim 不一致"
        else:
            assert tr["deadlock_round"] is not None and tr["deadlock_round"] <= tr["rounds"]
        print(f"  {key:<16} 轮={tr['rounds']:<4} I/O={tr['total_loads']:<6} "
              f"收敛={str(tr['converged']):<5} 帧={len(tr['frames']):<3} "
              f"跑满 I/O={tr['total_loads_full']}")

    payload = {"meta": meta, "traces": traces}
    js = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    html = HTML_TEMPLATE.replace("__DEMO_DATA__", js)
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n已生成 {OUT_HTML}  ({len(html) / 1024:.0f} KB，数据 {len(js) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
