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
# 提取符号地址
./scripts/extract_symbols.sh ./vmlinux

# 编辑配置文件
vim config/kcov_config.yaml
```

配置示例：
```yaml
vmlinux_path: "./vmlinux"
kcov_runner_path: "./kcov_runner"
verifier_start_addr: "0xffffffff81dcd390"  # 根据 extract_symbols.sh 输出调整
verifier_end_addr: "0xffffffff81e17e30"
testcase_dir: "./testcases"
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

# 获取测试集瘦身建议
python3 main.py analyze --reduce
```

## 核心功能

### 路径指纹 (Path Fingerprinting)
1. **过滤**：只保留 verifier.c 地址范围内的 PC
2. **折叠**：连续重复的 PC 只保留一个
3. **哈希**：生成 SHA-256 指纹（前 16 位）

### 全局地址解析
- 收集所有测试用例的唯一 PC 地址
- 一次性批量运行 `addr2line`
- 建立 O(1) 查找表

### 数据库存储
使用 SQLite 存储：
- 测试用例信息
- 路径指纹
- 源码覆盖信息
- 等价关系

### 覆盖率分析
- 路径分布统计
- 等价测试用例识别
- 测试集瘦身建议
- 未覆盖行检测

## 命令行接口

```bash
# 运行覆盖率采集
python3 main.py run

# 分析数据
python3 main.py analyze --report    # 生成报告
python3 main.py analyze --equivalent # 查看等价类
python3 main.py analyze --reduce     # 瘦身建议

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
│   └── batch_addr2line.py     # 批量地址解析
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

等价测试用例组数：10
可精简测试用例数：40 (40%)
============================================================
```

## 高级用法

### 测试集瘦身

```python
from analysis.coverage_analyzer import CoverageAnalyzer
from core.coverage_db import CoverageDatabase

with CoverageDatabase("kcov_coverage.db") as db:
    analyzer = CoverageAnalyzer(db)
    reduction = analyzer.suggest_test_suite_reduction()
    
    print(f"原始用例数：{reduction['original_count']}")
    print(f"精简后：{reduction['reduced_count']}")
    print(f"可移除：{reduction['removable_count']} ({reduction['reduction_rate']:.1f}%)")
```

### 增量测试识别

```python
from core.coverage_db import CoverageDatabase

with CoverageDatabase("kcov_coverage.db") as db:
    # 找到覆盖修改代码行的所有测试用例
    test_cases = db.find_test_cases_for_line("verifier.c", 1234)
    print(f"需要回归测试的用例：{test_cases}")
```

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

### addr2line 解析失败

```bash
# 检查 vmlinux 是否包含调试信息
file vmlinux
# 应该显示 "with debug_info"

# 手动测试
addr2line -e vmlinux -f 0xffffffff81dcd390
```

## 与现有脚本兼容

框架保留了 `verifier_path_mapper.py` 作为简单映射工具，新功能使用 `main.py`。

## 许可证

MIT License
