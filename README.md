# Verifier KCOV Coverage

基于 KCOV 的 eBPF verifier 覆盖率采集与分析工具。

当前项目把三类结果明确区分开：

- `执行路径`：按原始 `path_hash` / PC 序列区分
- `稳定路径`：按归一化后的 `stable_path_hash` / 控制流骨架区分
- `覆盖行集合`：按最终覆盖到的源码行集合区分

不要把三者混用。

## 功能概览

- 采集单个 `.o` 测试用例在内核 verifier 中触发的 KCOV PC 序列
- 生成执行路径指纹并保存到 SQLite
- 将 PC 批量解析到源码位置
- 保存完整覆盖行、执行路径顺序轨迹
- 查询执行路径摘要、稳定路径摘要、覆盖行集合摘要、单个 testcase 覆盖详情
- 导出 JSON / text 报告

## 目录

```text
ver_kcov/
├── analysis/
├── config/
├── core/
├── pipeline/
├── scripts/
├── utils/
├── kcov_runner.c
├── main.py
├── Makefile
└── README.md
```

## 服务启动
我们在虚拟机中执行测试
```bash
cd ~/workspace/image
ssh -q -i bookworm.id_rsa -p 10086 -o 'StrictHostKeyChecking no' root@127.0.0.1
```

## 挂载
```bash
mount -t virtiofs hostshare /mnt/root
mount -t debugfs none /sys/kernel/debug
mkdir -p /sys/fs/bpf
mount -t bpf bpf /sys/fs/bpf
cd /mnt/root
```

## 虚拟机内操作
进入代码目录
接下来执行上述命令即可，不需要再使用sudo，因为此时已经是root权限了


## 环境要求

- Linux 内核启用 `CONFIG_KCOV=y`
- 可访问 `/sys/kernel/debug/kcov`
- 带调试信息的 `vmlinux`
- `llvm-symbolizer`
- `libbpf`
- Python 3

常用依赖安装：

```bash
pip install pyyaml
make
```

## 快速开始

### 1. 准备 `vmlinux`

把当前系统对应、带调试信息的 `vmlinux` 放到项目根目录：

```bash
cp /path/to/vmlinux ./vmlinux
```

确认包含调试信息：

```bash
file vmlinux
```

### 2. 自动配置 verifier 地址范围

```bash
python3 scripts/auto_config.py ./vmlinux ./config/kcov_config.yaml
```

当前 `auto_config.py` 会按 `kernel/bpf/verifier.c` 的实际符号簇推导范围，不再使用过时的固定偏移估算。

### 3. 编译采集器

```bash
make
```

### 4. 运行采集

由于直接运行 `run` 不再自动清空数据库中的历史痕迹，如果需要重新开始，必须先执行数据的清理：

```bash
python3 main.py clear
```

然后执行路径采集（在虚拟机 root 环境中不再需要 sudo）：

```bash
# 默认采集所有信息（完整源码覆盖 + 稳定骨架）
python3 main.py run -t testcases

# 仅抽取稳定路径骨架（极大提升速度，降低数据库体积）
python3 main.py run -t testcases --path-type stable

# 同时执行稳定路径和提取每条语句的全量上下文
python3 main.py run -t testcases --path-type full
```

如果测试用例很多，可以开启并发（多进程）采集来大幅提升效率：

```bash
python3 main.py run -t testcases --path-type full -p
```


### 分析

```bash
python3 main.py analyze --report
python3 main.py analyze --stats
```

### 查询

执行路径摘要：

```bash
python3 main.py query --execution-paths
python3 main.py query --execution-paths -v
```

- `--execution-paths`：按原始执行路径区分
- `-v`：打印该执行路径的已持久化顺序轨迹，格式类似 `13860 -> 13880`

稳定路径摘要：

```bash
python3 main.py query --stable-paths
python3 main.py query --stable-paths -v
```

- `--stable-paths`：按稳定路径骨架区分
- `-v`：打印对应原始 `path_hash` 集合和稳定骨架锚点序列

### 相似度与统计

统计与分析前十大路径的用例分布以及他们之间的序列相似度（Jaccard 和序列比对）：

```bash
python3 analysis/path_similarity.py
# 或指定不同的 DB：python3 analysis/path_similarity.py my_database.db
```

单用例详情：

```bash
python3 main.py query -tc 3.o
```

文件 / 行查询：

```bash
python3 main.py query -f kernel/bpf/verifier.c
python3 main.py query -l "kernel/bpf/verifier.c:1234"
```

### 导出

```bash
python3 main.py export -o report.json
python3 main.py export -o report.txt --format text
```

## 配置文件

配置文件默认是 [config/kcov_config.yaml](/home/clhiker/ver_kcov/config/kcov_config.yaml)。

关键字段：

```yaml
vmlinux_path: ../vmlinux
kcov_runner_path: ../kcov_runner
verifier_start_addr: '0x...'
verifier_end_addr: '0x...'
testcase_dir: ../testcases
lookup_table_cache: ../cache/pc_lookup_table.txt
db_path: ../kcov_coverage.db
stable_path_line_bucket: 64
```

## 各类路径的设计思路与定义

在捕捉 Linux 内核 BPF Verifier 这类极端复杂的系统状态时，单凭最基础的 PC 覆盖率难以衡量出“测试用例到底走了哪条验证逻辑”。为此，本项目设计出**两套差异化的路径概念**以满足不同的分析需要。

### 1. 完整执行路径 (Full / Execution Path)

**设计思路**：提供绝对真值，不丢失任何上下文。
完整执行路径是由内核 KCOV 按照时间顺序吐出的**最原始 PC 轨迹**映射出的全量数组，代表程序执行流的完整快照。

- **极度敏感**：即使目标循环多执行了一次、遇到一个无关紧要的小型分支（如临时的锁/打桩跳过逻辑），也会生成出两条**完全不同**的完整路径哈希 (`path_hash`)。
- **优缺点分析**：
  - **优势**：用于 100% 精确的 Crash 回放或深度诊断。只要两个执行路径的 hash 相同，说明它们的底层机器指令流完全 1:1 一比一复现。
  - **劣势**：粒度过于“碎片化”。100 个本质完全相同的验证目标（例如测试 100 种无效的内存越界 read），在底层极易因为某个临时状态稍有不同，被分散识别成几十个独立执行路径。
- **查询入口**：`query --execution-paths`

### 2. 稳定骨架路径 (Stable Path)

**设计思路**：解决“完整路径”严重的碎片化现象，提取能代表“验证模式”逻辑的核心骨架。
由于 Verifier 充满了各种验证黑盒、大循环（如遍历指令块 `do_check` 等），如何对相似逻辑的测试群聚？我们创新设计了**控制流稳定骨架提取算法**，产出 `stable_path_hash`：

1. **事件归一化 (Event Normalization)**：
   - 将所有散乱的 PC 全部合并为 `(函数名, 行号)` 的语义级事件序列，完全过滤掉不属于核心验证主逻辑（如在 `verifier.c` 之外引发）的过程调用。
   - 去除连续重复的死循环命中行。

2. **锚点择优选定 (Anchor Extraction)**：
   并非所有语句都是代表逻辑的关键点。算法会自动剔除线性直走代码段，只保留：
   - 序列的**始末跨度节点**。
   - 所有会导致作用域跳跃的**函数边界**。
   - 全局控制流交融的**核心分支/收束节点**。

3. **模糊行号分桶 (Line Bucketing)**：
   - 由于不同内核版本间，核心分支可能会上下偏移几行（或者新增无关日志代码），算法并不使用绝对行号，而是配置了以块为单位的行号段（如向下按照 64 行抹平误差 `config.yaml: stable_path_line_bucket`）。
   - 例如行号 `3030` 会被划入 `3008` 所在的块。

4. **序列去重**：
   - 保留首个锚点的穿越顺序，彻底抛弃递归重入式的套娃。

**意义**：
相比于完整执行路径暴露出的几十、上百种零散轨迹，“稳定路径”完美地将**具有本质相同验证目标的测试用例**强势收敛为几个核心验证流。它让你在进行模糊测试或大型分析时不需要在汪洋大海里迷失：你可以依据**稳定路径**聚类去定位新触发的核心验证模式！
- **查询入口**：`query --stable-paths -v`


## 已知限制

- 某些程序类型不能作为普通 standalone testcase 直接加载，例如 `freplace/*`。这类 case 可能在进入 verifier 主检查前就因缺少 attach target 而失败。
- `bpftool` 只适合作为调试手段，不是当前项目采集链路的一部分。
- 更换 `vmlinux` 后必须重新执行：



## 故障排查

### 1. 无法打开 KCOV

```bash
sudo mount -t debugfs none /sys/kernel/debug
sudo chmod 666 /sys/kernel/debug/kcov
```

检查内核配置：

```bash
zgrep KCOV /proc/config.gz
```

### 2. 查询结果里没有 `verifier.c`

优先检查：

- 当前 `vmlinux` 是否和运行内核匹配
- 是否重新执行过 `auto_config.py`
- 是否重新执行过 `sudo python3 main.py run`


### 4. 手工验证配置

```bash
python3 - <<'PY'
from scripts.auto_config import extract_symbol_addresses
print(extract_symbol_addresses('vmlinux'))
PY
```

## 开发说明

- `kcov_runner.c` 负责打开 KCOV、加载 BPF object、导出原始 PC
- [core/kcov_collector.py](/home/clhiker/ver_kcov/core/kcov_collector.py) 负责调用 runner，并保证每个 testcase 使用独立输出文件
- [core/pc_resolver.py](/home/clhiker/ver_kcov/core/pc_resolver.py) 使用 `llvm-symbolizer` 批量解析 PC
- [pipeline/runner.py](/home/clhiker/ver_kcov/pipeline/runner.py) 负责完整流水线
- [core/coverage_db.py](/home/clhiker/ver_kcov/core/coverage_db.py) 负责数据库存储与查询


# 代码缩减（基于完整执行路径）
由于代码中包含大量由于verifier报错而截断的测试用例，这些测试用例的PC序列归于同样的等价类。
我们需要对同一等价类的测试用例进行缩减，我们的目标是在保证现有的PC路线不变的情况下缩减测试用例。
我们的缩减基于汇编代码和verifier的日志。通过verifier日志判断哪些汇编没有被执行，可以直接删除。
我们以testcases 目录下面的代码为例，testcases/code 记录 源代码可以供我们生成汇编。
记住该操作应该在虚拟机中执行