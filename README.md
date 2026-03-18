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

`run` 需要 `sudo`。并且它会清理本次运行相关产物：

- 数据库旧数据
- PC lookup cache
- 结果目录
- 遗留的 `verifier_pcs.txt`

```bash
sudo python3 main.py run
```

指定测试用例目录：

```bash
sudo python3 main.py run -t testcases
```

如果测试用例很多，可以开启并发（多进程）采集来大幅提升效率：

```bash
sudo python3 main.py run -t testcases -p
```

## 命令

### 采集

```bash
# 单进程采集
sudo python3 main.py run -t testcases  

# 多进程并发采集（推荐测试用例较多时使用）
sudo python3 main.py run -t testcases -p
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

覆盖行集合摘要：

```bash
python3 main.py query --coverage-groups
python3 main.py query --coverage-groups -v
```

- `--coverage-groups`：按最终覆盖到的源码行集合区分
- `-v`：展开每个文件覆盖到的行集合

单个 testcase：

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

## 结果语义

### 执行路径

执行路径由原始 KCOV PC 序列生成 `path_hash`。

特点：

- 保留顺序信息
- 区分不同 verifier 执行轨迹
- 查询入口：`query --execution-paths`

### 稳定路径 (Stable Path)

稳定路径是该项目的核心概念，旨在解决原始执行轨迹（PC序列）由于循环次数微小抖动或无关分支变动导致哈希“碎片化”的问题。它由 `verifier.c` 内的归一化控制流骨架生成 `stable_path_hash`。

**稳定路径算法的核心步骤如下：**

1. **事件归一化 (Event Normalization):**
   - 过滤掉所有不属于 `kernel/bpf/verifier.c` 的源码位置。
   - 将原始的执行序列映射为 `(function_name, line_number)` 对的连续事件组，并去除连续重复的同一事件。

2. **锚点提取 (Anchor Extraction):**
   并非轨迹上的每行代码都会被保留，算法只选取具有控制流结构代表性的“锚点 (Anchor)”：
   - 序列的**首个事件**和**末尾事件**。
   - **函数边界**：当上一个事件或下一个事件位于不同的函数时（即发生了函数调用或返回）。
   - **分支节点**：在全局轨迹中具有多个前驱 (Predecessors) 或多个后继 (Successors) 的控制流收束与分叉点。

3. **行号分桶 (Line Bucketing):**
   - 对于选出的锚点，不直接使用其绝对行号，而是按照一个可配置的步长（默认 `64` 行，见 `config/kcov_config.yaml` 中的 `stable_path_line_bucket`）向下取整进行“分桶 (Bucket)”。
   - 例如，行号 3030 会被分桶为 `3008` (3030 // 64 * 64)。这一步有效消减了内核版本微小迭代或极小范围内的线性指令顺序带来的波动。

4. **序列去重生成特征串:**
   - 使用格式化的 `{function_name}:{bucketed_line}` 表示每个锚点。
   - 保留首次出现的锚点列表并**严格保持到达顺序**，但在同一序列中再次出现的相同锚点（例如某个大循环内的小内部循环）将被丢弃去重。
   - 将这些有序特征串连接并做 `SHA-256` 哈希，取前 16 位，即为最终的 `stable_path_hash`。

**特点与用途：**

- 保留了粗粒度的**顺序信息**与核心验证流。
- 有效忽略了一部分重复嵌套回环和局部动态抖动。
- 极大收敛了本质相同的验证案例流，适合做“相对稳定”的路径聚类分组（如按大类型的 BPF 验证路径分组）。
- 查询入口：`query --stable-paths` （带上 `-v` 可显示归一化后保留的锚点序列）。

### 覆盖行集合

覆盖行集合由 testcase 最终命中的 `file:line` 集合归并得到。

特点：

- 不保留顺序
- 适合看“覆盖面是否一致”
- 查询入口：`query --coverage-groups`

### 单个 testcase 覆盖详情

`query -tc` 显示的是该 testcase 命中的完整覆盖行，不等同于执行轨迹。

## 已知限制

- 某些程序类型不能作为普通 standalone testcase 直接加载，例如 `freplace/*`。这类 case 可能在进入 verifier 主检查前就因缺少 attach target 而失败。
- `bpftool` 只适合作为调试手段，不是当前项目采集链路的一部分。
- 更换 `vmlinux` 后必须重新执行：

```bash
python3 scripts/auto_config.py ./vmlinux ./config/kcov_config.yaml
sudo python3 main.py run -t ...
```

不要继续复用旧数据库结果。

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

### 3. 两个 testcase 看起来完全一样

先确认不是旧数据库结果：

```bash
sudo python3 main.py run -t testcases/mini
```

`run` 会重新生成除配置外的运行产物，不应复用旧结果。

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


# 虚拟机执行方法
cd ~/workspace/image
ssh -q -i bookworm.id_rsa -p 10086 -o 'StrictHostKeyChecking no' root@127.0.0.1
mount -t 9p -o trans=virtio,version=9p2000.L bpf /mnt/root
mkdir -p /sys/fs/bpf
mount -t bpf bpf /sys/fs/bpf

## 虚拟机内操作
cd /mnt/root 进入代码目录
接下来执行上述命令即可，不需要再使用sudo，因为此时已经是root权限了
