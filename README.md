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

## 虚拟机准备
测试实际在虚拟机内执行，但现在默认从宿主机直接触发即可：`python3 main.py run ...` 会根据配置自动连接虚拟机、探测工作目录并在 guest 中执行流水线。

如果需要手工排查虚拟机环境，可以进入 guest 后按以下顺序准备：

### 连接
```bash
ssh -q -i bookworm.id_rsa -p 10086 -o 'StrictHostKeyChecking no' root@127.0.0.1
```

### 挂载
```bash
mount -t virtiofs hostshare /mnt/root
mount -t debugfs none /sys/kernel/debug
mkdir -p /sys/fs/bpf
mount -t bpf bpf /sys/fs/bpf
cd /mnt/root
```

进入虚拟机后默认已经是 `root`，后续命令不需要再加 `sudo`。


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

然后直接在宿主机执行路径采集：

```bash
# 默认采集所有信息
python3 main.py run -t testcases

# 仅抽取稳定路径骨架（极大提升速度，降低数据库体积）
python3 main.py run -t testcases --path-type stable

# 提取完整执行路径和源码覆盖
python3 main.py run -t testcases --path-type full
```

如果测试用例很多，可以开启并发（多进程）采集来大幅提升效率：

```bash
python3 main.py run -t mid-seeds --path-type full -p
```


### 分析

```bash
python3 main.py analyze --report
python3 main.py analyze --stats
python3 main.py analyze --detail
```

当前分析口径已经收紧为只统计 `verifier.c`，不会再把头文件等其他文件混入覆盖率、PC 数或覆盖集合摘要中。

如果要看**所有测试用例对 verifier 的总覆盖率**，直接使用：

```bash
python3 main.py analyze --stats
```

`analyze --stats` 重点关注输出中的：

- `已覆盖源码行数`
- `代码行覆盖率`
- `已采集唯一 PC 数`
- `PC 覆盖率`

如果需要查看**测试用例级**覆盖明细，使用：

```bash
python3 main.py analyze --detail
```

`analyze --report` 当前默认输出：

- 总体统计
- 覆盖行集合摘要

覆盖行集合摘要默认按**用例数从多到少**排序，列为：

- `覆盖签名`
- `覆盖PC数`
- `唯一行数`
- `用例数`
- `测试用例集合`

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

统一配置文件是 [config/kcov_config.yaml](/home/clhiker/ver_kcov/config/kcov_config.yaml)。

覆盖率采集相关关键字段：

```yaml
vmlinux_path: ../vmlinux
kcov_runner_path: ../kcov_runner
verifier_start_addr: '0x...'
verifier_end_addr: '0x...'
testcase_dir: ../testcases
lookup_table_cache: ../cache/pc_lookup_table.txt
db_path: ../kcov_coverage.db
stable_path_line_bucket: 64
agent_source_code_dir: ../mid-cases/code
agent_bytecode_dir: ../mid-seeds
```

与当前 agent / run 流程直接相关的字段：

- `testcase_dir`
  仅在 `python3 main.py run` 未显式传 `-t/--testcases` 时，作为默认测试用例目录。
- `agent_source_code_dir`
  agent 反查 seed 源码 `.c` 时使用的源码目录。
- `agent_bytecode_dir`
  agent 反查 seed 时使用的字节码目录；会按相对路径映射到 `agent_source_code_dir`。
- `vm_ssh_key` / `vm_ssh_port` / `vm_ssh_host` / `vm_guest_mount_point`
  宿主机自动连接虚拟机并执行 `run` / `campaign` 时使用。

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
相比于完整执行路径暴露出的几十、上百种零散轨迹，“稳定路径”可以将**具有本质相同验证目标的测试用例**收敛为若干核心验证流。
- **查询入口**：`query --stable-paths -v`

注意：当前 agent 路径探索链路已经统一只围绕 **full execution path** 工作，不再把 stable path 作为 seed 选择、价值判断或 campaign 回灌依据。稳定路径仍可用于离线分析和查询，但不参与 agent 的自动变异决策。


## 已知限制

- 某些程序类型不能作为普通 standalone testcase 直接加载，例如 `freplace/*`。这类 case 可能在进入 verifier 主检查前就因缺少 attach target 而失败。
- `bpftool` 只适合作为调试手段，不是当前项目采集链路的一部分。

## 故障排查

### 1. 无法打开 KCOV

```bash
mount -t debugfs none /sys/kernel/debug
chmod 666 /sys/kernel/debug/kcov
```

检查内核配置：

```bash
zgrep KCOV /proc/config.gz
```

### 2. 查询结果里没有 `verifier.c`

优先检查：

- 当前 `vmlinux` 是否和运行内核匹配
- 是否重新执行过 `auto_config.py`
- 是否重新执行过 `python3 main.py run`


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

由于 Fuzz 或测试用例生成工具会产生大量因 Verifier 报错而提前截断的用例，这些用例在其未执行的部分可能包含随意填充的大段无用指令，导致文件臃肿且混淆了 Fuzzer 的变异空间。
为了纯化属于同一块“错误检测等价类”的测试用例种子，本项目提供了一套**基于 Verifier 真实日志的精准裁剪工具**：`scripts/reduce_testcase.py`。

## 基本原理
1. 在宿主机将 `.c` 文件初步编译为 BPF 汇编代码与全量 `.o` 对象。
2. 通过 SSH 将对象发送至虚拟机内，依赖 `kcov_runner` 加载并提取 Verifier 回显的验证日志。
3. 从日志中精确抽取出所有被实质性扫描过的指令索引。
4. 返回宿主机，同步裁切未执行的死代码（并在截断越界处通过 `exit` 垫片修复），最终重新编译成纯粹精简的 `*_reduced.o` 或者原生 `*.o` 文件。

## 使用方法

脚本设计为全自动的批量处理闭环，所有临时文件会自动消亡。支持一次性纯化一整个目录：

```bash
# -i 指定输入源码目录，-o 指定纯化后 .o 生成的目标目录
python3 scripts/reduce_testcase.py -i testcases/code/ -o target-seeds
```

**运行结果分类**：
- **正常/完美案例**（未发生报错截断，100% 被接受）：脚本将不作破坏，原样保留带有完整调试信息（`-g`）和 BTF 的对象为 `n.o` 存入输出目录。
- **需要裁剪的案例**（发生了错误截断）：脚本会自动抹除越界部分汇编，修补成极简结构后，命名为 `re_n.o` 存入输出目录。
最终在 `target-seeds` 中，您可以获得一份既高度纯化又不折损任何控制流信息的初始种子库，可直接用于下一步的变异扩充。


# AI 变异器设计

为了在现有 testcase 之外继续扩展稀疏路径、触发新的 verifier 逻辑，本项目提供了一个带反馈闭环的 AI 变异器：`scripts/agent_mutator.py`。

它不是简单地“把整份汇编丢给大模型重写”，而是按下面的思路设计的。

## 设计目标

1. 尽量复用现有 testcase 的可加载结构，而不是让模型从零生成一个新程序。
2. 把模型的修改空间限制在少量关键 BPF 指令上，降低无意义改坏和语法崩坏的概率。
3. 每次变异都必须经过真实 verifier + KCOV + 路径数据库回灌，不能只看模型自我评估。

## Agent 工作流

### 1. 选择种子和目标

Agent 支持两种入口：

- **手工模式**：显式指定 `-s/--seed` 和 `-t/--target`
- **自动模式**：省略 `-s/-t`，由脚本自行选择

自动模式会：

1. 从 SQLite 中读取已有执行路径摘要和顺序轨迹。
2. 根据 `--objective` 决定工作方式：
   - `enrich_sparse`：显式寻找“和某条稀疏路径最相近的稠密路径”，再从稠密路径的 seed 朝稀疏路径迁移
   - `generate_new`：从高频路径选择 seed，并结合 `verifier.c` 覆盖 gap 去生成新路径
3. 将字节码路径按配置里的 `agent_bytecode_dir -> agent_source_code_dir` 映射回源码 `.c`

也就是说，当前 agent 不是盲目变异，而是在“路径补强”和“路径生成”两种目标之间切换。

### 2. 构建可变异指令视图

这是当前设计里最关键的一步。

最初直接把完整 `.s` 文件交给模型会遇到几个问题：

- `.s` 文件里夹杂了大量 `.debug_*` / BTF / 注释元数据
- prompt 体积过大，远端模型和本地模型都容易变慢
- 模型很容易破坏标签、段定义或调试信息，导致根本无法编译

因此当前实现不会让模型直接回传整份汇编，而是：

1. 先把 `.c` 编译成 `.s`
2. 从中筛出真正可变异的 BPF 指令，如：
   - `rX = ...`
   - `wX = ...`
   - `if ... goto ...`
   - `goto ...`
   - `call ...`
   - `exit`
3. 给这些指令编号，形成一个很小的“可变异指令视图”

模型最终只需要返回：

```json
{
  "analysis": "...",
  "hypothesis": "...",
  "edits": [
    {"index": 6, "line": "if w0 s< 0 goto LBB0_2"}
  ]
}
```

然后脚本再把这些 edit 应回完整汇编。这样能显著降低 prompt 体积，并让修改更聚焦在真正影响 verifier 约束的指令上。

## 真实反馈闭环

Agent 的每一次尝试都走完整验证闭环，而不是只靠语言模型做“纸面推理”：

1. 在宿主机上将变异后的汇编编译为 `.o`
2. 通过虚拟机中的 `kcov_runner` 加载 BPF object
3. 收集：
   - verifier 日志
   - KCOV PC 序列
4. 将 PC 序列送回宿主机，计算：
   - `path_hash`
   - 路径状态：`new / sparse / dense / no-pcs`
   - 是否命中目标 `verifier.c` 行
5. 结果写入本地输出目录，供后续继续迭代

这意味着 agent 的评价标准并不是“模型说自己探索到了新逻辑”，而是：

- 是否真的编译成功
- 是否真的触发 verifier
- 是否收集到了 PC
- 最终路径在数据库里到底是全新、稀疏还是稠密

## 成功判定

当前 agent 的评估会结合目标一起打分：

1. 在 `enrich_sparse` 模式下，优先奖励命中目标 sparse path hash
2. 在 `generate_new` 模式下，优先奖励真正的 `new path`
3. 其次考虑 `target_hit`、`sparse path`、`verifier_ok` 和 `pc_count`
4. 同一轮运行内还会维护一个按 `path_hash` 去重的 diversity pool，而不是只保留单个 best attempt

需要注意：

- 即使 verifier 最终拒绝，很多 case 依然能留下有价值的 KCOV 路径
- 因此 agent 不会把“程序被 verifier 拒绝”简单等价于“这次尝试完全失败”
- 但 `verifier_ok` 的判定已经显式排除了 `failed to load`、`infinite loop detected` 等 rejection 场景

## Agent 配置

Agent / campaign 的默认参数已经统一写入 [config/kcov_config.yaml](/home/clhiker/ver_kcov/config/kcov_config.yaml)。

当前推荐直接在配置文件中维护：

- `agent_provider`
- `agent_model`
- `agent_temperature`
- `agent_objective`
- `agent_max_iterations`
- `agent_top_k`
- `agent_nearby_budget`
- `agent_campaign_output_root`
- `agent_campaign_sleep_seconds`
- `agent_source_code_dir`
- `agent_bytecode_dir`
- `ollama_host`
- `openai_base_url`
- `openai_api_key`
- `openai_model`
- `google_api_key`
- `vm_ssh_key`
- `vm_ssh_port`
- `vm_ssh_host`
- `vm_guest_mount_point`

同样地，旧脚本如：

- `scripts/reduce_testcase.py`
- `scripts/enrich.py`

现在也会默认读取同一份 `kcov_config.yaml` 中的 VM 参数，不再推荐在脚本里各自维护一套 SSH 默认值。

### 本地 Ollama

目前推荐优先使用本地 Ollama，避免远端网关超时或模型名不兼容。

例如本机已有：

```bash
ollama list
```

若存在：

```text
qwen3.5:9b
```

则可直接运行：

```bash
python3 scripts/agent_mutator.py \
  -s /path/to/seed.c \
  -t "target verifier line 1234" \
  --max-iterations 1 \
  -o mutated-cases/agent_ollama_smoke
```

也可以让 agent 自动挑 seed 和 target：

```bash
python3 scripts/agent_mutator.py \
  --max-iterations 1 \
  -o mutated-cases/agent_ollama_auto
```

## 输出产物

每次运行都会在输出目录中留下完整中间产物，方便复盘：

- `*_seed.s`：原始种子汇编
- `<run_dir>_attempt_XX.s`：应用 edit 后的完整汇编
- `<run_dir>_attempt_XX.o`：编译产物
- `<run_dir>_attempt_XX.pcs`：KCOV PC 序列
- `<run_dir>_attempt_XX.verifier.log`：verifier 日志
- `<run_dir>_attempt_XX.proposal.json`：模型给出的分析与 edit 方案
- `<run_dir>_attempt_XX.result.json`：该轮真实评估结果
- `summary.json`：本次运行选出的最佳尝试、路径池摘要和可回灌候选
- `run_metadata.json`：本次运行的 seed、target、模型和自动选择信息

## 一键持续运行

为了让 agent 持续进行路径补强 / 新路径生成，并把每轮产生的 `.o` 自动回灌进覆盖率数据库，本项目新增了 `scripts/agent_campaign.py`。

它会自动完成以下步骤：

1. 确认虚拟机共享目录已挂载到 `/mnt/root`
2. 运行一轮 `scripts/agent_mutator.py`
3. 自动筛选出有价值的 full-path 结果，再通过 guest 里的 `python3 main.py run -t ... --path-type full -p` 回灌进数据库
4. 在同一个持续运行窗口里打印数据库前后的路径统计增量与当前路径发现情况
5. 继续下一轮

### 一轮测试

```bash
python3 scripts/agent_campaign.py \
  --objective generate_new \
  --max-iterations 1 \
  --rounds 1 \
  --output-root mutated-cases/campaign_test
```

### 持续运行

```bash
python3 scripts/agent_campaign.py \
  --objective enrich_sparse \
  --max-iterations 3 \
  --rounds 0 \
  --sleep-seconds 10 \
  --output-root mutated-cases/campaign
```

说明：

- `--rounds 0` 表示一直运行
- `--objective enrich_sparse`：持续做“稠密 -> 稀疏”的路径补强
- `--objective generate_new`：持续做“gap -> 新路径”的路径生成

`scripts/agent_campaign.py` 会在同一个持续运行窗口里实时打印：

- 当前轮次和输出目录
- 当前卡在 seed 选择、模型请求、编译、guest verifier、入库的哪个阶段
- 本轮结束后的数据库增量
- 当前 sparse path / dense path / execution path 的总体情况
- 最近几轮 campaign 的摘要

纯 `dense` 的重复结果不会再自动回灌进数据库，因此不会污染后续 seed 池。

## 当前限制

当前版本的 AI 变异器仍有几个明显限制：

- 目标 gap 选择仍然是启发式的，只基于 `if` 边界局部缺口，不是真正的路径约束求解
- 目前 edit 主要是“单步局部修改”，还没有显式做多点协同变异搜索
- 自动 seed 选择目前仍然主要基于 full-path 频次与相似度启发式，没有引入更强的 verifier 结构化约束
- 本地模型虽然更稳定，但推理速度会明显慢于轻量云端模型

## Prompt 模板

agent 的主提示词已经从 Python 代码中抽离，当前模板位于：

- [prompts/agent_mutator_prompt.txt](/home/clhiker/ver_kcov/prompts/agent_mutator_prompt.txt)

后续如果需要调整 agent 的策略、输出格式或 full-path 目标描述，优先修改该模板文件，而不是直接改 `scripts/agent_mutator.py` 中的逻辑。

尽管如此，这套设计已经足够支撑“从现有种子出发，利用真实 verifier 反馈持续做定向路径探索”的工作流。

## 其他辅助脚本

- `scripts/check_seed_mapping.py`
  用于检查 `agent_bytecode_dir -> agent_source_code_dir` 的 seed 反查是否成立，适合排查 `找不到可反查回源码 seed` 这类问题。
- `scripts/analyze_verifier_map.py`
  用于基于 `verifier.c` 和数据库覆盖结果生成初版 verifier 图谱报告，输出到 `cache/verifier_map_report.json`。
