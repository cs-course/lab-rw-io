# 平台部署建议：合鲸 ModelWhale / 头歌 EduCoder

本实验的计算负载极轻（纯 CPU、单机秒级、依赖仅 networkx + matplotlib），两个平台都能胜任，但**分工不同**。

## 结论先行

| 环节 | 推荐平台 | 理由 |
|---|---|---|
| 1 课时课堂演示 | **和鲸 ModelWhale**（或本地投屏） | 云端统一环境免配置、学生可 fork 教师 Canvas 现场改参重跑 |
| 课后两阶段作业 + 自动评测 | **头歌 EduCoder** | 关卡式实训 + 评测脚本自动判分，与您已有的头歌成绩流程（按学号对齐导出）无缝衔接 |
| 只选一个平台 | **头歌 EduCoder** | 演示可用头歌的 Jupyter 实训承担；自动判分是刚需，ModelWhale 判分能力弱 |

## 一、头歌 EduCoder（课后作业主平台）

**实训结构设计（2 个关卡 + 1 个挑战关）：**

| 关卡 | 任务 | 评测方式 |
|---|---|---|
| 关卡 1 | 阶段一：复现 baseline（52 次）+ 提交自定义分区 `mypart.txt` | 评测脚本运行 `walk_sim.py --order 1 --meta mypart.txt`，断言：converged=true、块数/块大小约束、total_loads 阈值分档给分（≤47 / ≤42） |
| 关卡 2 | 阶段二：naive vs AUW 观察 + 自定义调度/分区 | 评测脚本注入学生 `my_scheduler`，断言收敛性与阈值（≤86 / ≤71）；naive 部分以"不收敛 + updating_rate<0.01"为观察点自动判定 |
| 挑战关（加分） | 极端 p/q、8 块/M=2、ER 对照图 | 开放提交，报告人工评阅 |

**配置要点：**
- 环境镜像：Python 3.10+，预装 `networkx matplotlib`（本实验无需 GPU，选最小 CPU 镜像即可）。
- 将 `walk_sim.py`、`data/` 设为**只读实训资料**，学生代码写在被评测文件（如 `answer.py`，实现 `my_partition` / `my_scheduler` 两个函数）中——防止改骨架刷分，呼应规则 8。
- 评测脚本直接 `import walk_sim` 并调用 `run_sim(...)`，比对 `summary.json` 字段；判分逻辑 30 行以内，示例可基于 README 的基线表编写。
- 成绩按学号导出后可直接并入期末成绩汇总流程（与大数据 2301–2302 班头歌实验同一处理方式）。

## 二、和鲸 ModelWhale（课堂演示平台）

- **课程空间**：创建课程项目，上传 `teacher_demo.ipynb` + `walk_sim.py` + `data/`，挂载为共享数据集；定制镜像（Python 3.13 + networkx + matplotlib + jupyter）发布给学生。
- **课堂互动**：学生 fork 教师 Canvas，在 §4 分区对比处现场替换 meta 文件重跑——对应演示 25–35' 环节的"换分区文件看加载次数下降"。
- **算力**：该实验单次运行秒级，最低配 CPU 配额足够，无需担心资源成本。
- 若学校未采购 ModelWhale，退化为本地 Jupyter + 投屏即可，notebook 不依赖任何平台特性。

## 三、落地清单

1. ✅ 本地已就绪：`walk_sim.py`（骨架）、`data/`（数据集 + 3 种分区）、`teacher_demo.ipynb`（已预跑含输出）、`student_lab.ipynb`（空白待填）。
2. 头歌侧：建实训 → 传只读资料 → 写评测脚本（阈值取自 README 基线表）→ 两个关卡 + 挑战关。
3. ModelWhale 侧：建课程项目 → 定制镜像 → 发布演示 Canvas。
4. 课前自检：在头歌评测环境完整跑一遍关卡 1/2 的标准答案（社区分区 + AUW），确认镜像内结果与本地一致（同种子确定性保证可复现）。
