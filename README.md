# Verifier KCOV Coverage

基于 KCOV 的 eBPF verifier 覆盖率采集与分析工具。

当前项目把两类结果明确区分开：

- `执行路径`：按原始 `path_hash` / PC 序列区分
- `覆盖行集合`：按最终覆盖到的源码行集合区分

不要把两者混用。

## 功能概览

- 采集单个 `.o` 测试用例在内核 verifier 中触发的 KCOV PC 序列
- 生成执行路径指纹并保存到 SQLite
- 将 PC 批量解析到源码位置
- 保存完整覆盖行、执行路径顺序轨迹
- 查询执行路径摘要、覆盖行集合摘要、单个 testcase 覆盖详情
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
sudo python3 main.py run -t testcases/mini
```

## 命令

### 采集

```bash
sudo python3 main.py run
sudo python3 main.py run -t testcases/mini          
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
```

## 结果语义

### 执行路径

执行路径由原始 KCOV PC 序列生成 `path_hash`。

特点：

- 保留顺序信息
- 区分不同 verifier 执行轨迹
- 查询入口：`query --execution-paths`

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
