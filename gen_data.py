# -*- coding: utf-8 -*-
"""gen_data.py — 生成实验数据集（karate 俱乐部图 + baseline 顺序等分 4 块）
输出（均在 data/ 下）：
  karate.edgelist   原始无向边表 "u v"（0-indexed）
  meta.txt          baseline 分区映射 "vid block_id"（按顶点编号顺序等分 4 块）
  block_0..3.txt    各块邻接表 "vid nbr1 nbr2 ..."（出边即全图邻接，按块分组存储）
  config.json       实验参数 {i, l, M, p, q, seed}
  er_meta.txt       对照用：Erdos-Renyi 随机图 (n=34, p=0.15) 的 4 块顺序等分
  er_block_0..3.txt ER 随机图块文件
"""
import json
import os
import networkx as nx

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
N_BLOCKS = 4


def write_graph(G, prefix, data_dir):
    """写边表 + 顺序等分 meta + 分块邻接表，返回 meta 映射。"""
    nodes = sorted(G.nodes())
    n = len(nodes)
    # 边表
    with open(os.path.join(data_dir, f"{prefix}.edgelist"), "w", encoding="utf-8") as f:
        for u, v in sorted(G.edges()):
            f.write(f"{u} {v}\n")
    # 顺序等分：前 ceil 块各多分 1 个
    meta = {}
    base, rem = divmod(n, N_BLOCKS)
    idx = 0
    for b in range(N_BLOCKS):
        size = base + (1 if b < rem else 0)
        for v in nodes[idx:idx + size]:
            meta[v] = b
        idx += size
    with open(os.path.join(data_dir, f"{prefix}_meta.txt" if prefix != "karate" else "meta.txt"), "w", encoding="utf-8") as f:
        for v in nodes:
            f.write(f"{v} {meta[v]}\n")
    # 分块邻接表
    for b in range(N_BLOCKS):
        name = f"{prefix}_block_{b}.txt" if prefix != "karate" else f"block_{b}.txt"
        with open(os.path.join(data_dir, name), "w", encoding="utf-8") as f:
            for v in nodes:
                if meta[v] == b:
                    nbrs = " ".join(str(x) for x in sorted(G.neighbors(v)))
                    f.write(f"{v} {nbrs}\n")
    return meta


def main():
    os.makedirs(DATA, exist_ok=True)
    # 主数据集：Zachary karate 俱乐部图（34 点 / 78 边，天然双社区）
    G = nx.karate_club_graph()
    G = nx.convert_node_labels_to_integers(G, first_label=0)
    meta = write_graph(G, "karate", DATA)
    # 对照数据集：ER 随机图（无明显社区结构）
    G_er = nx.erdos_renyi_graph(34, 0.15, seed=42)
    write_graph(G_er, "er", DATA)
    # 参数
    cfg = {"i": 10, "l": 20, "M": 2, "n_blocks": N_BLOCKS, "p": 1.0, "q": 1.0, "seed": 2026}
    with open(os.path.join(DATA, "config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    # 摘要
    cross = sum(1 for u, v in G.edges() if meta[u] != meta[v])
    sizes = [sum(1 for v in meta if meta[v] == b) for b in range(N_BLOCKS)]
    print(f"karate: |V|={G.number_of_nodes()}, |E|={G.number_of_edges()}, "
          f"blocks={sizes}, cross-block edges={cross}/{G.number_of_edges()}")
    print(f"er: |V|={G_er.number_of_nodes()}, |E|={G_er.number_of_edges()}")
    print("data files written to", DATA)


if __name__ == "__main__":
    main()
