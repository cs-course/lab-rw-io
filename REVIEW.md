# 实验设计复查报告

复查对象：`lab-rw-io/`（README.md 规则、walk_sim.py 骨架、data/ 数据集、两个 notebook）
复查方式：重跑全部基线配置 + 指标独立性验算 + 死锁检测 + 多种子敏感性测试
复查脚本：`audit_experiment.py`（可重复执行，教师自查用）

---

## 一、基线数据复现：全部通过 ✅

| 配置 | README 声明 | 实测 | 结论 |
|---|---|---|---|
| 一阶 + roundrobin | 40 轮 / 80 / 0.500 | 40 / 80 / 0.5000 | ✅ |
| 一阶 + stateaware（L0） | 40 轮 / 52 / 0.500 | 40 / 52 / 0.5000 | ✅ |
| 二阶 + naive | >20000 / 42 / 0.0004 | 20001 / 42 / 0.0004 | ✅ |
| 二阶 + roundrobin | >20000 / 40000 / 0.0003 | 20001 / 40000 / 0.0003 | ✅ |
| 二阶 + AUW（L2） | 87 轮 / 101 / 0.230 | 87 / 101 / 0.2299 | ✅ |
| 一阶 stateaware + KL 分区 | 47 轮 / 72 / 0.426 | 47 / 72 / 0.4255 | ✅ |
| 一阶 stateaware + ref 分区 | — / 42 / — | **37 轮 / 42 / 0.5405** | ✅（可补全） |
| 二阶 AUW + KL 分区 | 83 轮 / 84 / 0.241 | 83 / 84 / 0.2410 | ✅ |

核心教学结论（"切边最少 ≠ 加载最少""naive 的低 I/O 是饥饿假象"）**成立且可复现**。

---

## 二、发现的逻辑漏洞（按严重度排序）

### 🔴 P0-1 `io_utilization` 分子饱和，且**同样会奖励饥饿配置**

实测 8 个配置的分子（被使用有向边数）**全部等于 156 = 2|E|**：

```
config            io_util  usedE(=util×edges_loaded)  edges_loaded
1st stateaware    0.0758      156                       2058
1st roundrobin    0.0500      156                       3120
1st +KL           0.0562      156                       2776
1st +ref          0.1020      156                       1529
2nd auw           0.0408      156                       3820
2nd naive         0.0960      156                       1625   ← 不收敛！
2nd roundrobin    0.0001      156                    1560000
2nd auw +KL       0.0462      156                       3374
```

两条问题：

1. **分子饱和**：340 walker × 20 步 = 6800 步在 karate（156 条有向边）上足以覆盖全图，
   分子恒为 156，于是 `io_utilization ≡ 156 / edges_loaded`，退化成 `total_loads` 的
   单调函数 —— **不是独立指标**，README 要求"讨论三指标权衡"实际只有两个自由度。
2. **它和 total_loads 一样会误判饥饿**：`2nd naive` 的 io_utilization = **0.0960**，
   是全部配置中最高的，比收敛的 `2nd auw`（0.0408）高 **2.35 倍**。
   README 只批评了"低加载次数没意义"，但没意识到 io_utilization 同样在奖励死锁配置。

> **建议**：改用 `io_per_step = total_loads / advances`（每推进一步平均消耗多少次块 I/O），
> 只在 `converged=true` 时有效，含义直白且不会饱和：
> 一阶 stateaware 0.00765 ｜ roundrobin 0.01176 ｜ +ref 0.00618 ｜ 二阶 AUW 0.01485 ｜
> 二阶 AUW+KL 0.01235 ｜ 二阶 roundrobin 18.48（灾难）。
> **二阶/一阶 = 1.94×**，正好把 SOWalker 论文"node2vec 的块 I/O 可达 DeepWalk 数倍"
> 的论断量化出来，是个比 io_utilization 好得多的教学数字。

### 🔴 P0-2 `walk_updating_rate` 在收敛时**恒等于 l/rounds**，与轮次完全冗余

验算（收敛配置逐行相等）：

| 配置 | walk_updating_rate | l/rounds = 20/rounds |
|---|---|---|
| 1st stateaware | 0.5000 | 0.5000 |
| 1st +ref | 0.5405 | 0.5405 |
| 2nd auw | 0.2299 | 0.2299 |
| 2nd auw +KL | 0.2410 | 0.2410 |

收敛时 `advances ≡ 340×20 = 6800`，故 `rate = 6800/(rounds×340) = 20/rounds`。
它衡量的是"总耗时"，不是"每轮有多少 walker 被卡住"。

另有两个定义缺陷：
- 分母 `rounds × n_w` 用的是**全部** walker 数，而非每轮存活数。末段只剩少量 walker 时，
  分母仍按 340 计，系统性低估更新率。
- 对不收敛配置，数值取决于 `max_rounds` 截断位置（naive 0.0004 是 20001 轮的产物，
  若截断到 100 轮就是 0.087）—— **指标随实验参数漂移**，不宜作为评分依据。

> **建议**：保留原字段（对齐论文），另加 `walk_updating_rate_alive = advances / Σ_r alive_r`
> 作为"真实的每轮推进比例"；并把**死锁轮次**作为一等的判定量（见下）。

### 🟠 P1-1 `roundrobin` 调度器是模块级单例，**跨次运行状态泄漏**（可复现性 Bug）

`walk_sim.py` 里 `SCHEDULERS["roundrobin"] = RoundRobin()`，`self.k` 从不重置。
同进程内连续跑两次：

```
4 块:  80, 80, 80            ← 偶然自洽（rounds×M % 4 == 0 恒成立）
8 块:  130, 128, 128         ← ❌ 第一次与后续不一致
```

正好命中 README 加分项"8 块 / M=2 重跑"的场景。修复（不改变 4 块下的任何数字）：

```python
class RoundRobin:
    def reset(self):          # 新增
        self.k = 0
...
# run_sim() 内，sched 取到之后加一行：
sched = scheduler or SCHEDULERS[policy]
if hasattr(sched, "reset"):   # 新增：有状态调度器每次运行前重置
    sched.reset()
```

### 🟠 P1-2 单一种子评分存在过拟合风险

6 个 seed 下的一阶 `total_loads`：

| 分区 | 2026 | 1 | 7 | 42 | 123 | 999 | 均值 | 标准差 |
|---|---|---|---|---|---|---|---|---|
| sequential（baseline） | 52 | 52 | 52 | 53 | 49 | 54 | 52.0 | 1.5 |
| KL 均衡社区 | 72 | 71 | 70 | 69 | 72 | 67 | 70.2 | 1.8 |
| 局部搜索（ref_meta） | **42** | 47 | 41 | 47 | 46 | 45 | **44.7** | 2.4 |

好消息：**排名完全稳定**（ref < sequential < KL），教学结论不受影响。
坏消息：ref 分区的 42 是 seed=2026 的幸运值，均值 44.7；在 seed=1/42 上是 47，
**刚好压在"L ≤ 47 得 +20"的门槛线上**，"L ≤ 42 得 +40"在 6 个 seed 里只命中 1 次。

> **建议**：评分改用 **3 个种子（2026 / 7 / 123）的均值**，或明确写"一律以 seed=2026 为准，
> 不接受其他种子的结果"（后者更省事，但要在 README 里写死，避免争议）。

### 🟡 P2-1 分区约束下界自相矛盾

README 规则 7 与 notebook 都写"每块大小 ∈ [n/8, n/2]（即 **4–17** 点）"，
但 n=34 时 n/8 = 4.25，整数下界应为 **5**。需改成 5–17，或把约束写成 `[ceil(n/8), floor(n/2)]`。

### 🟡 P2-2 无死锁早停，且轮次有 off-by-one

- 二阶 naive 从第 **65** 轮起 `advances` 恒为 0（400 轮内后 336 轮零推进），
  二阶 roundrobin 从第 **41** 轮起恒为 0，剩余 19960 轮纯属空转（每轮还在加载，故 I/O 冲到 40000）。
- 骨架仍跑到 `max_rounds` 才停，且上报 `rounds=20001`（break 前已自增）。

> **建议**：连续 20 轮零推进即 break，并上报 `deadlock_round`（最后一轮有推进的轮次 + 1）。
> 这个数字本身就是比 updating_rate 更硬的"饥饿"证据，适合直接进课堂 PPT。

### 🟡 P2-3 换图必须同时换分区文件

`run_sim` 的默认 meta 是 karate 的 `meta.txt`。若只传 `--edgelist data/er.edgelist`
而不传 `--meta data/er_meta.txt`，程序不会报错（顶点集都是 0–33），但跑出来的是
"karate 分区 + ER 图"的混合体。README 快速开始里没强调这点。

### 🟢 P3 教学缺口：同步轮次掩盖了 GraphWalker 的另一半贡献

实测 M=2 时两种策略的**轮次完全相同**（40 vs 40），只有 I/O 不同（52 vs 80）：

```
M=2 stateaware   40 轮 / 52 I/O
M=2 roundrobin   40 轮 / 80 I/O     ← 轮次一样
M=3 stateaware   29 轮 / 31 I/O
M=3 roundrobin   32 轮 / 34 I/O     ← 收益从 35% 缩到 9.7%
```

也就是说：本实验的**同步轮次**语义下，state-aware 只省 I/O、不省轮次；
GraphWalker 论文里"加快 walk 推进"那一半来自**异步 re-entry**（一次加载内走到撞边界），
实验中并未体现。teacher_demo §5 互动第 4 问提到了这点，但正文 §2 的对比容易让学生
误以为"GraphWalker = 贪心选块"。

> **建议**：在 §2 结尾加一句明确的限定语，或把"实现异步 re-entry 版本并对比轮次"设为加分项。
> 顺带可以给出 M=3 的数据说明"资源越宽松，调度的边际收益越小"（35% → 9.7%）。

---

## 三、修复清单（按优先级）

| 优先级 | 改动 | 影响面 |
|---|---|---|
| 高 | `RoundRobin` 加 `reset()` 并在 `run_sim` 开头调用 | 仅修 8 块可复现性，4 块数字不变 |
| 高 | README 评分改为多种子均值，或写死"仅认 seed=2026" | 评分规则 |
| 中 | summary 增加 `io_per_step`、`deadlock_round`、`updating_rate_alive`；加死锁早停 | 新增字段，不改既有字段 |
| 中 | README 补一句"io_utilization 在 karate 上分子饱和，判优以 total_loads + converged + 更新率为准" | 文案 |
| 低 | 约束下界 4 → 5；补全 ref 分区的 37 轮 / 0.541 | 文案 |
| 低 | 强调"换图必须换 meta" | 文案 |

以上均未直接改动 `walk_sim.py`（它是学生评测骨架，改动需你确认）。

---

## 四、演示文件

`walk_io_demo.html`（139 KB，零依赖单文件，双击即开）+ 生成脚本 `export_demo.py`。

演示内所有数字由 `walk_sim.py` 骨架产生并**逐项对账**（52 / 80 / 101 / 42 / 40000），
`export_demo.py` 内置断言，与骨架不一致会直接报错。
