# 实验：图上随机游走的块 I/O 优化（node2vec / GraphWalker / SOWalker）

《计算机系统设计》（研究生一年级）课堂演示暨课后两阶段作业。

围绕三篇工作的核心思想，在小图上复现"外存随机游走"的 I/O 瓶颈与优化：

| 论文 | 核心思想 | 实验对应 |
|---|---|---|
| node2vec (Grover & Leskovec, 2016) | 二阶偏置游走（p 控回跳、q 控探索），转移依赖上一步节点 t | 阶段二：转移概率需 d(t,x)∈{0,1,2} |
| GraphWalker (Wang et al., 2020) | state-aware I/O：优先加载含 walker 最多的块；walk-conscious 换出 | 阶段一：一阶游走 + stateaware 调度 |
| SOWalker (Wu et al., 2023) | 二阶游走需 prev+curr 双块共驻（否则为 non-updatable），walk matrix + benefit-aware（AUW）集式调度 | 阶段二：naive vs AUW 调度对比 |

## 目录结构

```
lab-rw-io/
├── README.md            本文件（规则与评分）
├── walk_sim.py          统一 I/O 模拟骨架（禁止修改核心语义）
├── gen_data.py          数据集生成脚本（已执行，无需重跑）
├── teacher_demo.ipynb   教师版：1 课时（45 分钟）现场演示脚本
├── student_lab.ipynb    学生版：课后两阶段作业骨架
├── deployment.md        合鲸 ModelWhale / 头歌 EduCoder 部署建议
└── data/
    ├── karate.edgelist      karate 俱乐部图（34 点 / 78 边）
    ├── meta.txt             baseline 分区：按顶点编号顺序等分 4 块 [9,9,8,8]
    ├── block_0..3.txt       baseline 分块邻接表（供查看，模拟器按 meta 动态构建块）
    ├── community_meta.txt   参考分区①：KL 递归二分（均衡 4 块，切边少）
    ├── ref_meta.txt         参考分区②：面向一阶加载次数的局部搜索结果
    ├── er.edgelist / er_meta.txt / er_block_*.txt   对照用 ER 随机图（n=34, p=0.15）
    └── config.json          i=10, l=20, M=2, p=1, q=1, seed=2026
```

## 实验规则（所有学生统一遵守，保证加载次数可比）

1. **图与分块**：karate 图划分为 4 块；内存驻留集 R 最多容纳 **M=2** 块，超出须替换。
2. **游走任务**：每个顶点发起 **i=10** 次、步长 **l=20** 的游走（共 340 个 walker，6800 步）。
3. **推进语义（同步轮次）**：每轮调度器选定 R*（|R*|≤M），随后每个"可推进"的 walker 走 1 步。
   - 可推进（一阶）：`block(vid) ∈ R`
   - 可推进（二阶）：`block(vid) ∈ R` 且 `block(pid) ∈ R`（step=0 时仅需前者）
   - 被卡的 walker 原地等待（这正是 SOWalker 的 non-updatable walk）。
4. **加载计数**：仅当块 b∈R* 且 b∉R 时计 1 次（含首轮冷启动）；cache hit 不计；换出后重入重复计；全部结束后残留块不处置。总次数 `total_loads` 越小越好。
5. **换出**：walk-conscious（驻留集中不再被选入 R* 的块被换出）；**禁用 LRU 等其他策略**。
6. **确定性**：统一随机种子 `seed=2026`；调度平局一律取块号较小者。
7. **分区约束（阶段一/二的自定义分区）**：必须恰好 4 块，每块大小 ∈ [n/8, n/2]（即 4–17 点），防止"空块/巨块"退化。
8. **多指标报告**：除 `total_loads` 外，须同时报告 `walk_updating_rate`（平均每轮推进的 walker 比例）与 `io_utilization`（被使用的有向边 / 已加载边数），并讨论三者权衡。**不收敛（converged=false）的"低加载次数"没有意义**——二阶 naive 即为例证。

## 基线实测数据（教师校准用，karate 图，seed=2026）

| 配置 | 收敛 | 轮次 | total_loads | updating_rate |
|---|---|---|---|---|
| 一阶 + roundrobin（逐块迭代，稻草人） | ✅ | 40 | 80 | 0.500 |
| 一阶 + stateaware（baseline L0） | ✅ | 40 | **52** | 0.500 |
| 二阶 + naive（一阶调度直接套用） | ❌ | >20000 | 42 | 0.0004 |
| 二阶 + roundrobin | ❌ | >20000 | 40000 | 0.0003 |
| 二阶 + AUW（baseline L2） | ✅ | 87 | **101** | 0.230 |
| 一阶 stateaware + KL 均衡社区分区 | ✅ | 47 | 72 | 0.426 |
| 一阶 stateaware + 局部搜索分区（ref_meta） | ✅ | — | **42** | — |
| 二阶 AUW + KL 均衡社区分区 | ✅ | 83 | **84** | 0.241 |

教学要点：① 二阶 naive 的 42 次"低加载"是饥饿假象（更新率 0.0004，游走根本不推进）；
② KL 分区切边更少（31 vs 47）但一阶加载反而更多（72 vs 52）——**边切割最小化 ≠ walker 局部性最大化**；
③ 局部搜索把一阶压到 42（跨块边却升至 55），说明指标必须对着目标优化。

## 课后作业

### 阶段一（一阶，一周）
1. 跑通 baseline：`python walk_sim.py --order 1`（应复现 total_loads=52）。
2. 提交自定义分区 `mypart.txt`（vid block_id，4 行约束见规则 7），重跑并报告三指标。
3. ≤2 页报告：方法、结果、你认为的加载次数下界及理由。
- 评分（100）：正确性 40 + 提交完整 20 + 优化加分：L ≤ 0.90·L0（≤47）+20；L ≤ 0.80·L0（≤42）+40，封顶 100。

### 阶段二（二阶，一周）
1. 对比二阶 naive 与 AUW：`--order 2 --policy naive / auw`，解释 naive 为何不收敛（联系 SOWalker §2 的 non-updatable walks 与"无用 walk I/O"）。
2. 自定义调度函数（经 `run_sim(..., scheduler=fn)` 注入）和/或自定义分区，联合优化二阶加载次数。
3. 报告：必须解释"为何二阶加载次数系统性高于一阶"。
- 评分（100）：正确性 50 + 提交完整 20 + 优化加分：L ≤ 0.85·L2（≤86）+15；L ≤ 0.70·L2（≤71）+30，封顶 100。

### 加分项（+10，两阶段均可）
- 极端 p/q 实验（如 p=0.25 强回跳、q=0.25 类 DFS）对块访问分布的影响。
- 8 块 / M=2（驻留比 25%）重跑，观察 I/O 瓶颈变化（更接近论文真实场景）。
- ER 对照图（无社区结构）上重跑，讨论社区结构对分区优化空间的作用。

## 快速开始

```bash
pip install networkx matplotlib          # 建议 Python ≥ 3.10
python walk_sim.py --order 1                          # 一阶 baseline → 52
python walk_sim.py --order 1 --policy roundrobin      # 稻草人对照 → 80
python walk_sim.py --order 2 --policy naive           # 二阶 naive → 不收敛
python walk_sim.py --order 2 --policy auw             # 二阶 AUW → 101
python walk_sim.py --order 2 --policy auw --meta data/community_meta.txt
jupyter notebook teacher_demo.ipynb    # 教师演示
jupyter notebook student_lab.ipynb     # 学生作业
```
