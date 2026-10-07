# 交接意见优化：历史结果再分析

这是已知结果的回顾性分析，未重新调用TranStar。原实验、约束判定和报告全部保留。

源数据先核验实际进程证据及原始输出指标；有封存清单的检查字节一致性，其余严格核验原运行目录及时间戳。逐项方式见review.json/audit_mode。

## 新旧推荐对照

| 任务 | 原任务冻结建议 | 旧跨需求筛选 | 容差v2跨需求筛选 |
| --- | --- | --- | --- |
| 20260908_100153_bb7198 | S3_combined | S3_combined | S2_turn |
| 20260908_075938_e7444a | S1_parking | 保留基准 | 保留基准 |
| 20260911_021806_858461 | robust_p3_1 | robust_p3_1 | robust_p3_1 |
| 20260911_024329_7304a4 | baseline | 保留基准 | 保留基准 |

容差：[0.001个百分点、0.01个百分点、0.001km]；不是输出误差或统计显著性估计。复杂度按不同措施数、停车1级措施数依次排序。
精确Pareto前沿与近似相同组分开显示；微小优势可以被简单措施取代，但失败或缺测候选不能被容差放行。

## 20260908_100153_bb7198

| 合格候选 | 最差全网变化% | 最差邻区变化% | 最差新增邻区km | 复杂度 |
| --- | ---: | ---: | ---: | --- |
| S2_turn | -0.085915785 | -5.949884897 | 0.000000000 | [1, 0] |
| S3_combined | -0.085950706 | -5.948085279 | 0.000000000 | [2, 0] |

### 局部恶化披露

新增全网过饱和为毛增量，不被其他路段改善抵消；耗时仍仅为路段Σ(qL/v)。

| 场景 | 原约束 | 核心耗时变化% | 邻区耗时变化% | 全网耗时变化% | 新增全网过饱和km | 所有恶化路段耗时增量pcu·h | 最差路段增量pcu·h |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| S1_parking | 拒绝 | -12.174835 | 0.097153 | 0.000796 | 0.000000 | 6.592596 | 11516→10552：0.409668 |
| S2_turn | 通过 | -53.672135 | -10.977463 | -0.145784 | 8.858574 | 789.259887 | 10532→5221：30.095040 |
| S3_combined | 通过 | -54.558394 | -10.977150 | -0.145827 | 8.858574 | 789.260547 | 10532→5221：30.095040 |
| demand_0p900_S1_parking | 通过 | -10.369168 | -0.001008 | -0.000711 | 0.426860 | 5.143073 | 188→236：0.141558 |
| demand_0p900_S2_turn | 通过 | -51.518012 | -9.090404 | -0.085916 | 6.081112 | 463.230345 | 10548→10549：8.065795 |
| demand_0p900_S3_combined | 通过 | -52.249673 | -9.090004 | -0.085951 | 6.081112 | 463.232590 | 10548→10549：8.065795 |
| demand_1p100_S1_parking | 拒绝 | -6.827975 | 0.169496 | 0.001635 | 0.000000 | 6.883420 | 4800→4799：2.658811 |
| demand_1p100_S2_turn | 通过 | -52.144523 | -5.949885 | -0.246769 | 12.588473 | 1456.978754 | 10552→11517：80.571808 |
| demand_1p100_S3_combined | 通过 | -53.289752 | -5.948085 | -0.246815 | 12.588473 | 1456.986547 | 10552→11517：80.571808 |

### 范围与阈值扫描

| 邻区跳数 | 阈值变体（其余保持原值） | 统一推荐 | 合格候选数 |
| ---: | --- | --- | ---: |
| 1 | original | 保留基准 | 0 |
| 1 | max_neighbor_time_increase_pct=0 | 保留基准 | 0 |
| 1 | max_neighbor_time_increase_pct=0.25 | 保留基准 | 0 |
| 1 | max_neighbor_time_increase_pct=1 | 保留基准 | 0 |
| 1 | max_network_time_increase_pct=0 | 保留基准 | 0 |
| 1 | max_network_time_increase_pct=0.05 | 保留基准 | 0 |
| 1 | max_network_time_increase_pct=0.2 | 保留基准 | 0 |
| 1 | max_new_oversaturated_km=0 | 保留基准 | 0 |
| 1 | max_new_oversaturated_km=0.05 | 保留基准 | 0 |
| 1 | max_new_oversaturated_km=0.2 | 保留基准 | 0 |
| 2 | original | S2_turn | 2 |
| 2 | max_neighbor_time_increase_pct=0 | S2_turn | 2 |
| 2 | max_neighbor_time_increase_pct=0.25 | S2_turn | 2 |
| 2 | max_neighbor_time_increase_pct=1 | S2_turn | 2 |
| 2 | max_network_time_increase_pct=0 | S2_turn | 2 |
| 2 | max_network_time_increase_pct=0.05 | S2_turn | 2 |
| 2 | max_network_time_increase_pct=0.2 | S2_turn | 2 |
| 2 | max_new_oversaturated_km=0 | S2_turn | 2 |
| 2 | max_new_oversaturated_km=0.05 | S2_turn | 2 |
| 2 | max_new_oversaturated_km=0.2 | S2_turn | 2 |
| 3 | original | S2_turn | 2 |
| 3 | max_neighbor_time_increase_pct=0 | S2_turn | 2 |
| 3 | max_neighbor_time_increase_pct=0.25 | S2_turn | 2 |
| 3 | max_neighbor_time_increase_pct=1 | S2_turn | 2 |
| 3 | max_network_time_increase_pct=0 | S2_turn | 2 |
| 3 | max_network_time_increase_pct=0.05 | S2_turn | 2 |
| 3 | max_network_time_increase_pct=0.2 | S2_turn | 2 |
| 3 | max_new_oversaturated_km=0 | 保留基准 | 0 |
| 3 | max_new_oversaturated_km=0.05 | 保留基准 | 0 |
| 3 | max_new_oversaturated_km=0.2 | S2_turn | 2 |

### 容差敏感性

| 默认容差倍数 | 统一推荐 | 近似相同组 |
| ---: | --- | --- |
| 0 | S3_combined | S3_combined |
| 0.1 | S2_turn | S2_turn |
| 1 | S2_turn | S2_turn, S3_combined |
| 10 | S2_turn | S2_turn, S3_combined |
## 20260908_075938_e7444a

| 合格候选 | 最差全网变化% | 最差邻区变化% | 最差新增邻区km | 复杂度 |
| --- | ---: | ---: | ---: | --- |

### 局部恶化披露

新增全网过饱和为毛增量，不被其他路段改善抵消；耗时仍仅为路段Σ(qL/v)。

| 场景 | 原约束 | 核心耗时变化% | 邻区耗时变化% | 全网耗时变化% | 新增全网过饱和km | 所有恶化路段耗时增量pcu·h | 最差路段增量pcu·h |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| S1_parking | 通过 | -31.498714 | 0.000015 | -0.005958 | 0.000000 | 0.000086 | 3236→3235：0.000086 |
| S2_oneway | 拒绝 | -84.768737 | 171.898522 | -0.039409 | 15.687400 | 1241.227112 | 3115→3241：164.045067 |
| S3_combined | 拒绝 | -85.525406 | 171.898522 | -0.039552 | 15.687400 | 1241.227112 | 3115→3241：164.045067 |
| demand_0p900_S1_parking | 拒绝 | -26.123709 | 7.286306 | -0.002415 | 0.000000 | 8.478455 | 3238→3237：3.560172 |
| demand_0p900_S2_oneway | 拒绝 | -87.631783 | 167.229813 | 0.012547 | 17.876400 | 1441.717769 | 3115→3241：367.387281 |
| demand_0p900_S3_combined | 拒绝 | -87.543759 | 167.714987 | 0.008326 | 17.876400 | 1437.989811 | 3115→3241：367.387281 |
| demand_1p100_S1_parking | 通过 | -35.107149 | -0.000064 | -0.007173 | 0.000000 | 0.005228 | 4102→3104：0.001040 |
| demand_1p100_S2_oneway | 拒绝 | -84.453948 | 175.220278 | -0.303609 | 11.742400 | 1620.567881 | 4082→3235：175.240265 |
| demand_1p100_S3_combined | 拒绝 | -85.807480 | 175.220278 | -0.303885 | 11.742400 | 1620.567881 | 4082→3235：175.240265 |

### 范围与阈值扫描

| 邻区跳数 | 阈值变体（其余保持原值） | 统一推荐 | 合格候选数 |
| ---: | --- | --- | ---: |
| 1 | original | 保留基准 | 0 |
| 1 | max_neighbor_time_increase_pct=0 | 保留基准 | 0 |
| 1 | max_neighbor_time_increase_pct=0.25 | 保留基准 | 0 |
| 1 | max_neighbor_time_increase_pct=1 | 保留基准 | 0 |
| 1 | max_network_time_increase_pct=0 | 保留基准 | 0 |
| 1 | max_network_time_increase_pct=0.05 | 保留基准 | 0 |
| 1 | max_network_time_increase_pct=0.2 | 保留基准 | 0 |
| 1 | max_new_oversaturated_km=0 | 保留基准 | 0 |
| 1 | max_new_oversaturated_km=0.05 | 保留基准 | 0 |
| 1 | max_new_oversaturated_km=0.2 | 保留基准 | 0 |
| 2 | original | 保留基准 | 0 |
| 2 | max_neighbor_time_increase_pct=0 | 保留基准 | 0 |
| 2 | max_neighbor_time_increase_pct=0.25 | 保留基准 | 0 |
| 2 | max_neighbor_time_increase_pct=1 | 保留基准 | 0 |
| 2 | max_network_time_increase_pct=0 | 保留基准 | 0 |
| 2 | max_network_time_increase_pct=0.05 | 保留基准 | 0 |
| 2 | max_network_time_increase_pct=0.2 | 保留基准 | 0 |
| 2 | max_new_oversaturated_km=0 | 保留基准 | 0 |
| 2 | max_new_oversaturated_km=0.05 | 保留基准 | 0 |
| 2 | max_new_oversaturated_km=0.2 | 保留基准 | 0 |
| 3 | original | 保留基准 | 0 |
| 3 | max_neighbor_time_increase_pct=0 | 保留基准 | 0 |
| 3 | max_neighbor_time_increase_pct=0.25 | 保留基准 | 0 |
| 3 | max_neighbor_time_increase_pct=1 | 保留基准 | 0 |
| 3 | max_network_time_increase_pct=0 | 保留基准 | 0 |
| 3 | max_network_time_increase_pct=0.05 | 保留基准 | 0 |
| 3 | max_network_time_increase_pct=0.2 | 保留基准 | 0 |
| 3 | max_new_oversaturated_km=0 | 保留基准 | 0 |
| 3 | max_new_oversaturated_km=0.05 | 保留基准 | 0 |
| 3 | max_new_oversaturated_km=0.2 | 保留基准 | 0 |

### 容差敏感性

| 默认容差倍数 | 统一推荐 | 近似相同组 |
| ---: | --- | --- |
| 0 | 保留基准 | 无 |
| 0.1 | 保留基准 | 无 |
| 1 | 保留基准 | 无 |
| 10 | 保留基准 | 无 |
## 20260911_021806_858461

| 合格候选 | 最差全网变化% | 最差邻区变化% | 最差新增邻区km | 复杂度 |
| --- | ---: | ---: | ---: | --- |
| robust_p2_1 | -0.036857708 | 0.003117418 | 0.000000000 | [1, 0] |
| robust_p3_1 | -0.051705261 | 0.019115308 | 0.056000000 | [1, 1] |

### 局部恶化披露

新增全网过饱和为毛增量，不被其他路段改善抵消；耗时仍仅为路段Σ(qL/v)。

| 场景 | 原约束 | 核心耗时变化% | 邻区耗时变化% | 全网耗时变化% | 新增全网过饱和km | 所有恶化路段耗时增量pcu·h | 最差路段增量pcu·h |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| robust_p1_1.1 | 拒绝 | -35.703146 | 1.172826 | -0.059916 | 0.000000 | 28.707012 | 4085→3240：12.000386 |
| robust_p2_1.1 | 通过 | -24.227022 | 0.003117 | -0.036858 | 0.000000 | 0.224960 | 3109→3111：0.163886 |
| robust_p2_1 | 通过 | -29.424374 | -0.000132 | -0.046817 | 0.000000 | 0.046560 | 3109→3111：0.004682 |
| robust_p2_0.9 | 通过 | -35.254979 | 0.002107 | -0.044213 | 0.000000 | 0.076184 | 3268→3267：0.010333 |
| robust_p3_1.1 | 通过 | -33.983974 | 0.004103 | -0.051705 | 0.000000 | 0.307272 | 3109→3111：0.224758 |
| robust_p3_1 | 通过 | -38.173004 | 0.019115 | -0.060434 | 0.056000 | 0.895672 | 3113→3241：0.239287 |
| robust_p3_0.9 | 通过 | -45.484653 | 0.002505 | -0.057044 | 0.000000 | 0.095133 | 3274→4102：0.011650 |

### 范围与阈值扫描

| 邻区跳数 | 阈值变体（其余保持原值） | 统一推荐 | 合格候选数 |
| ---: | --- | --- | ---: |
| 1 | original | robust_p3_1 | 2 |
| 1 | max_neighbor_time_increase_pct=0 | 保留基准 | 0 |
| 1 | max_neighbor_time_increase_pct=0.25 | robust_p3_1 | 2 |
| 1 | max_neighbor_time_increase_pct=1 | robust_p3_1 | 2 |
| 1 | max_network_time_increase_pct=0 | robust_p3_1 | 2 |
| 1 | max_network_time_increase_pct=0.05 | robust_p3_1 | 2 |
| 1 | max_network_time_increase_pct=0.2 | robust_p3_1 | 2 |
| 1 | max_new_oversaturated_km=0 | robust_p2_1 | 1 |
| 1 | max_new_oversaturated_km=0.05 | robust_p2_1 | 1 |
| 1 | max_new_oversaturated_km=0.2 | robust_p3_1 | 2 |
| 2 | original | robust_p3_1 | 2 |
| 2 | max_neighbor_time_increase_pct=0 | 保留基准 | 0 |
| 2 | max_neighbor_time_increase_pct=0.25 | robust_p3_1 | 2 |
| 2 | max_neighbor_time_increase_pct=1 | robust_p3_1 | 2 |
| 2 | max_network_time_increase_pct=0 | robust_p3_1 | 2 |
| 2 | max_network_time_increase_pct=0.05 | robust_p3_1 | 2 |
| 2 | max_network_time_increase_pct=0.2 | robust_p3_1 | 2 |
| 2 | max_new_oversaturated_km=0 | robust_p2_1 | 1 |
| 2 | max_new_oversaturated_km=0.05 | robust_p2_1 | 1 |
| 2 | max_new_oversaturated_km=0.2 | robust_p3_1 | 2 |
| 3 | original | robust_p3_1 | 2 |
| 3 | max_neighbor_time_increase_pct=0 | 保留基准 | 0 |
| 3 | max_neighbor_time_increase_pct=0.25 | robust_p3_1 | 2 |
| 3 | max_neighbor_time_increase_pct=1 | robust_p3_1 | 2 |
| 3 | max_network_time_increase_pct=0 | robust_p3_1 | 2 |
| 3 | max_network_time_increase_pct=0.05 | robust_p3_1 | 2 |
| 3 | max_network_time_increase_pct=0.2 | robust_p3_1 | 2 |
| 3 | max_new_oversaturated_km=0 | robust_p2_1 | 1 |
| 3 | max_new_oversaturated_km=0.05 | robust_p2_1 | 1 |
| 3 | max_new_oversaturated_km=0.2 | robust_p3_1 | 2 |

### 容差敏感性

| 默认容差倍数 | 统一推荐 | 近似相同组 |
| ---: | --- | --- |
| 0 | robust_p3_1 | robust_p3_1 |
| 0.1 | robust_p3_1 | robust_p3_1 |
| 1 | robust_p3_1 | robust_p3_1 |
| 10 | robust_p3_1 | robust_p3_1 |
## 20260911_024329_7304a4

| 合格候选 | 最差全网变化% | 最差邻区变化% | 最差新增邻区km | 复杂度 |
| --- | ---: | ---: | ---: | --- |

### 局部恶化披露

新增全网过饱和为毛增量，不被其他路段改善抵消；耗时仍仅为路段Σ(qL/v)。

| 场景 | 原约束 | 核心耗时变化% | 邻区耗时变化% | 全网耗时变化% | 新增全网过饱和km | 所有恶化路段耗时增量pcu·h | 最差路段增量pcu·h |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| robust_p1_1.1 | 拒绝 | 5.059178 | 0.963586 | 0.016429 | 2.139000 | 170.885634 | 3116→3115：18.914627 |
| robust_p2_1.1 | 拒绝 | 0.004765 | 0.004298 | 0.000023 | 0.000000 | 0.332326 | 3117→3118：0.072936 |

### 范围与阈值扫描

| 邻区跳数 | 阈值变体（其余保持原值） | 统一推荐 | 合格候选数 |
| ---: | --- | --- | ---: |
| 1 | original | 保留基准 | 0 |
| 1 | max_neighbor_time_increase_pct=0 | 保留基准 | 0 |
| 1 | max_neighbor_time_increase_pct=0.25 | 保留基准 | 0 |
| 1 | max_neighbor_time_increase_pct=1 | 保留基准 | 0 |
| 1 | max_network_time_increase_pct=0 | 保留基准 | 0 |
| 1 | max_network_time_increase_pct=0.05 | 保留基准 | 0 |
| 1 | max_network_time_increase_pct=0.2 | 保留基准 | 0 |
| 1 | max_new_oversaturated_km=0 | 保留基准 | 0 |
| 1 | max_new_oversaturated_km=0.05 | 保留基准 | 0 |
| 1 | max_new_oversaturated_km=0.2 | 保留基准 | 0 |
| 2 | original | 保留基准 | 0 |
| 2 | max_neighbor_time_increase_pct=0 | 保留基准 | 0 |
| 2 | max_neighbor_time_increase_pct=0.25 | 保留基准 | 0 |
| 2 | max_neighbor_time_increase_pct=1 | 保留基准 | 0 |
| 2 | max_network_time_increase_pct=0 | 保留基准 | 0 |
| 2 | max_network_time_increase_pct=0.05 | 保留基准 | 0 |
| 2 | max_network_time_increase_pct=0.2 | 保留基准 | 0 |
| 2 | max_new_oversaturated_km=0 | 保留基准 | 0 |
| 2 | max_new_oversaturated_km=0.05 | 保留基准 | 0 |
| 2 | max_new_oversaturated_km=0.2 | 保留基准 | 0 |
| 3 | original | 保留基准 | 0 |
| 3 | max_neighbor_time_increase_pct=0 | 保留基准 | 0 |
| 3 | max_neighbor_time_increase_pct=0.25 | 保留基准 | 0 |
| 3 | max_neighbor_time_increase_pct=1 | 保留基准 | 0 |
| 3 | max_network_time_increase_pct=0 | 保留基准 | 0 |
| 3 | max_network_time_increase_pct=0.05 | 保留基准 | 0 |
| 3 | max_network_time_increase_pct=0.2 | 保留基准 | 0 |
| 3 | max_new_oversaturated_km=0 | 保留基准 | 0 |
| 3 | max_new_oversaturated_km=0.05 | 保留基准 | 0 |
| 3 | max_new_oversaturated_km=0.2 | 保留基准 | 0 |

### 容差敏感性

| 默认容差倍数 | 统一推荐 | 近似相同组 |
| ---: | --- | --- |
| 0 | 保留基准 | 无 |
| 0.1 | 保留基准 | 无 |
| 1 | 保留基准 | 无 |
| 10 | 保留基准 | 无 |

## 边界

固定原核心和已测措施，仅改变评价范围或判定规则；不会补齐主动搜索提前拒绝后缺失的需求点。
全网风险是新增披露项，未事后改变原任务合格标准；新硬约束须在新任务仿真前预设。
这不是另一台机器的复跑，也不是新区域盲测。原始证据索引、所有候选和每项扫描可查同目录review.json。
