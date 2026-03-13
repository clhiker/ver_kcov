# Verifier 覆盖率采集框架

基于 KCOV 的 eBPF Verifier 自动化覆盖率采集框架

## 快速开始

### 1. 安装依赖

```bash
pip install pyyaml
```

### 2. 准备环境

```bash
# 准备 vmlinux 文件（带调试符号的内核镜像）
cp /path/to/vmlinux ./vmlinux

# 编译 KCOV 采集程序
make
# 或者手动编译
gcc -o kcov_runner kcov_runner.c -lbpf
```

### 3. 配置 Verifier 地址范围

```bash
# 自动提取符号地址并更新配置文件
python3 scripts/auto_config.py ./vmlinux ./config/kcov_config.yaml
```

脚本会自动：
- 从 vmlinux 提取 bpf_check 等符号地址
- 自动计算 Verifier 地址范围
- 更新配置文件 `config/kcov_config.yaml`

配置示例（自动配置后）：
```yaml
vmlinux_path: "./vmlinux"
kcov_runner_path: "./kcov_runner"
verifier_start_addr: "0xffffffff81dcd390"  # 自动提取
verifier_end_addr: "0xffffffff81e17390"    # 自动计算
testcase_dir: "./testcases"
```

**手动配置（可选）**：
如果自动配置失败，可以手动提取地址：
```bash
# 查看符号地址
nm -n vmlinux | grep bpf_check

# 手动编辑配置文件
vim config/kcov_config.yaml
```

### 4. 运行覆盖率采集

```bash
# 基本运行
python3 main.py run

# 指定测试用例目录
python3 main.py run -t ./my_testcases
```

### 5. 分析结果

```bash
# 查看覆盖率报告
python3 main.py analyze --report
```

## 核心功能

### 路径指纹 (Path Fingerprinting)
1. **过滤**：只保留 verifier.c 地址范围内的 PC
2. **折叠**：连续重复的 PC 只保留一个
3. **哈希**：生成 SHA-256 指纹（前 16 位）

### 全局地址解析
- 收集所有测试用例的唯一 PC 地址
- 一次性批量运行 `llvm-symbolizer`
- 建立 O(1) 查找表

### 数据库存储
使用 SQLite 存储：
- 测试用例信息
- 路径指纹
- 源码覆盖信息

### 覆盖率分析
- 路径分布统计
- 未覆盖行检测

## 命令行接口

```bash
# 运行覆盖率采集
python3 main.py run

# 分析数据
python3 main.py analyze --report    # 生成报告

# 查询信息
python3 main.py query -f kernel/bpf/verifier.c  # 查询文件
python3 main.py query -l "verifier.c:1234"      # 查询行

# 导出数据
python3 main.py export -o report.json  # JSON 格式
python3 main.py export -o report.txt   # 文本格式
```

## 项目结构

```
ver_kcov/
├── core/                      # 核心模块
│   ├── kcov_collector.py      # KCOV 数据采集
│   ├── path_fingerprinter.py  # 路径指纹生成
│   ├── pc_resolver.py         # PC 地址解析
│   └── coverage_db.py         # SQLite 数据库
├── pipeline/                  # 流水线模块
│   └── runner.py              # 自动化控制器
├── analysis/                  # 分析模块
│   ├── coverage_analyzer.py   # 覆盖率分析
│   └── path_cluster.py        # 路径聚类
├── utils/                     # 工具模块
│   └── config.py              # 配置管理
├── scripts/                   # 辅助脚本
│   ├── extract_symbols.sh     # 符号地址提取
│   └── llvm_symbolizer.py     # 批量地址解析
├── config/                    # 配置文件
│   └── kcov_config.yaml       # 主配置
├── testcases/                 # 测试用例目录
├── main.py                    # 主入口
├── kcov_runner.c              # KCOV 采集程序源码
├── Makefile                   # 编译配置
└── README.md                  # 本文档
```

## 输出示例

```
============================================================
Verifier 覆盖率分析报告
============================================================
测试用例总数：100
唯一路径数：25
覆盖文件数：3
覆盖行数：1500
覆盖率：45.5%
============================================================
```

## 高级用法

### 增量测试识别

```python
from core.coverage_db import CoverageDatabase

with CoverageDatabase("kcov_coverage.db") as db:
    # 找到覆盖修改代码行的所有测试用例
    test_cases = db.find_test_cases_for_line("verifier.c", 1234)
    print(f"需要回归测试的用例：{test_cases}")
```

## 支持的环境

本框架支持以下两种运行环境：

### 1. 宿主机（WSL2）
- 需要自定义编译的内核（启用 `CONFIG_KCOV=y`）
- 直接运行，性能更好
- 推荐用于日常开发和测试

### 2. QEMU 虚拟机
- 适用于需要更完整内核模拟的场景
- 需要配置虚拟机和内核镜像
- 推荐用于深度调试和验证

## 故障排查

### KCOV 采集失败

**问题 1: 打开 /sys/kernel/debug/kcov 失败**

```bash
# 挂载 debugfs
sudo mount -t debugfs none /sys/kernel/debug

# 设置权限
sudo chmod 666 /sys/kernel/debug/kcov
```

**问题 2: KCOV_INIT_TRACE 失败**

检查内核是否支持 KCOV：
```bash
zgrep KCOV /proc/config.gz
# 应该看到：CONFIG_KCOV=y
```

**问题 3: 缓冲区溢出**

如果收集到的 PC 超过缓冲区大小，修改 `kcov_runner.c`：
```c
#define KCOV_BUFFER_SIZE (2 << 20)  // 增大到 2MB
```

**问题 4: KCOV_DISABLE 失败**

```
[ERROR] KCOV_DISABLE 失败：Invalid argument
```

这是**正常现象**。eBPF 程序加载后会触发内核 KCOV 插桩，即使调用 `KCOV_DISABLE` ioctl，内核仍可能继续收集。程序会正确保存已收集的 PC 数据，不影响使用。

### llvm-symbolizer 解析失败

```bash
# 检查 vmlinux 是否包含调试信息
file vmlinux
# 应该显示 "with debug_info"

# 手动测试
llvm-symbolizer -e vmlinux 0xffffffff81dcd390
```

## 与现有脚本兼容

框架保留了 `verifier_path_mapper.py` 作为简单映射工具，新功能使用 `main.py`。

## 许可证

MIT License
