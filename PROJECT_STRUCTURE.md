# Verifier 覆盖率采集框架 - 项目结构

## 目录结构

```
ver_kcov/
├── core/                      # 核心模块
│   ├── __init__.py
│   ├── kcov_collector.py      # KCOV 数据采集器
│   ├── path_fingerprinter.py  # 路径指纹生成器
│   ├── pc_resolver.py         # PC 地址解析器
│   └── coverage_db.py         # SQLite 数据库
│
├── pipeline/                  # 流水线模块
│   ├── __init__.py
│   └── runner.py              # 自动化采集控制器
│
├── analysis/                  # 分析模块
│   ├── __init__.py
│   ├── coverage_analyzer.py   # 覆盖率分析器
│   └── path_cluster.py        # 路径聚类分析
│
├── utils/                     # 工具模块
│   ├── __init__.py
│   └── config.py              # 配置管理
│
├── scripts/                   # 辅助脚本
│   ├── extract_symbols.sh     # 符号地址提取
│   └── batch_addr2line.py     # 批量地址解析
│
├── config/                    # 配置文件
│   └── kcov_config.yaml       # 主配置
│
├── testcases/                 # 测试用例目录
├── main.py                    # 主入口程序
├── kcov_runner.c              # KCOV 采集程序源码
├── Makefile                   # 编译配置
├── requirements.txt           # Python 依赖
├── verifier_path_mapper.py    # 简单映射脚本（兼容旧版）
├── README.md                  # 使用文档
├── QUICKSTART.md              # 快速开始指南
├── PROJECT_STRUCTURE.md       # 本文档
└── .gitignore                 # Git 忽略文件
```

## 核心文件说明

### 主要程序
- **main.py** - 命令行入口，支持 run/analyze/query/export 命令
- **kcov_runner.c** - KCOV 数据采集程序（需要编译）
- **verifier_path_mapper.py** - 简单映射脚本（向后兼容）

### 核心模块
- **kcov_collector.py** - 调用 kcov_runner 收集 PC 序列
- **path_fingerprinter.py** - 生成路径指纹（过滤 + 折叠 + 哈希）
- **pc_resolver.py** - 批量解析 PC 到源码位置
- **coverage_db.py** - SQLite 数据库存储和查询

### 分析模块
- **coverage_analyzer.py** - 生成覆盖率报告、测试集瘦身建议
- **path_cluster.py** - 路径聚类分析

### 配置文件
- **config/kcov_config.yaml** - 配置 Verifier 地址范围、路径等

## 使用方法

### 1. 编译 kcov_runner
```bash
make
```

### 2. 配置
```bash
./scripts/extract_symbols.sh ./vmlinux
vim config/kcov_config.yaml
```

### 3. 运行采集
```bash
python3 main.py run
```

### 4. 分析结果
```bash
python3 main.py analyze --report
```

## 文件大小

- **总大小**: ~1.4 GB（主要是 vmlinux）
- **代码**: ~50 KB
- **文档**: ~15 KB
- **vmlinux**: 1.4 GB（可删除，使用自己的内核镜像）

## 可删除的文件（如果需要）

- **vmlinux** - 使用自己的内核镜像替换
- **testcases/test.o** - 示例测试用例
- **verifier_path_mapper.py** - 旧版兼容脚本（如不需要）

## 技术要点

### 支持的环境

本框架支持两种运行环境：

1. **宿主机（WSL2）**
   - 需要自定义编译的内核（启用 `CONFIG_KCOV=y`）
   - 直接运行，性能更好
   - 推荐用于日常开发和测试

2. **QEMU 虚拟机**
   - 适用于需要更完整内核模拟的场景
   - 需要配置虚拟机和内核镜像
   - 推荐用于深度调试和验证

### KCOV 缓冲区大小

默认 2MB，可容纳约 260,000 个 PC。如需修改：
```c
// kcov_runner.c
#define KCOV_BUFFER_SIZE (2 << 20)  // 2MB
```

**注意**：如果收集到的 PC 数量超过缓冲区大小，会导致数据丢失。增大缓冲区可以解决这个问题。

### Verifier 地址范围

需要根据你的内核配置：
```bash
./scripts/extract_symbols.sh ./vmlinux
```

输出示例：
```
bpf_check: 0xffffffff81dcd390
do_check:  0xffffffff81e08050
```

配置到 `config/kcov_config.yaml`：
```yaml
verifier_start_addr: "0xffffffff81dcd390"
verifier_end_addr: "0xffffffff81e17e30"
```

### KCOV_DISABLE 失败说明

运行时会看到：
```
[ERROR] KCOV_DISABLE 失败：Invalid argument
```

这是**正常现象**，原因是：
- eBPF 程序加载后会触发内核 KCOV 插桩
- 即使调用 `KCOV_DISABLE` ioctl，内核仍可能继续收集
- 程序会正确保存已收集的 PC 数据，不影响使用
