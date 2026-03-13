# 自动化配置使用指南

## 快速开始（推荐）

### 一键配置

```bash
# 一键完成所有配置
./scripts/setup.sh ./vmlinux
```

这个脚本会自动：
1. ✅ 检查 vmlinux 文件
2. ✅ 自动提取符号地址
3. ✅ 更新配置文件
4. ✅ 检查 kcov_runner
5. ✅ 检查测试用例

### 运行覆盖率采集

配置完成后，直接运行：

```bash
sudo python3 main.py run
```

---

## 分步配置

### 方法 1：自动配置（推荐）

```bash
# 自动提取符号并更新配置
python3 scripts/auto_config.py ./vmlinux ./config/kcov_config.yaml
```

**输出示例：**
```
============================================================
自动配置 Verifier 地址范围
============================================================
[*] 从 ./vmlinux 提取符号地址...

[*] 提取到的地址信息:
  bpf_check 起始：0xffffffff81dcd390
  bpf_check 结束：0xffffffff81e17390
  do_check: 0xffffffff81e08050

[*] 更新配置文件：config/kcov_config.yaml
✓ verifier_start_addr: 0xffffffff81dcd390
✓ verifier_end_addr: 0xffffffff81e17390
✓ 配置文件已更新

============================================================
✓ 自动配置完成！
============================================================
```

### 方法 2：手动配置

如果自动配置失败，可以手动配置：

```bash
# 1. 查看符号地址
nm -n vmlinux | grep bpf_check

# 2. 手动编辑配置文件
vim config/kcov_config.yaml
```

---

## 配置文件说明

配置完成后，`config/kcov_config.yaml` 应该包含：

```yaml
# vmlinux 文件路径
vmlinux_path: "./vmlinux"

# KCOV 采集程序路径
kcov_runner_path: "./kcov_runner"

# Verifier 地址范围（自动提取）
verifier_start_addr: "0xffffffff81dcd390"  # bpf_check 函数开始
verifier_end_addr: "0xffffffff81e17390"    # 估算的结束地址

# 测试用例目录
testcase_dir: "./testcases"

# 使用 llvm-symbolizer
use_llvm_symbolizer: true
```

---

## 验证配置

### 检查配置文件

```bash
cat config/kcov_config.yaml
```

### 测试解析

```bash
python3 -c "
from utils.config import Config
from core.pc_resolver import PCResolver

config = Config.from_yaml('config/kcov_config.yaml')
print(f'Verifier 地址范围：{hex(config.verifier_start_addr)} - {hex(config.verifier_end_addr)}')
print(f'使用 llvm-symbolizer: {config.use_llvm_symbolizer}')
"
```

---

## 常见问题

### Q: 自动配置失败怎么办？

**A:** 手动提取符号地址：

```bash
# 查看 bpf_check 地址
nm -n vmlinux | grep " bpf_check$"

# 输出示例：
# ffffffff81dcd390 T bpf_check

# 手动编辑配置文件
vim config/kcov_config.yaml
```

### Q: 如何确定地址范围是否正确？

**A:** 查看提取的符号：

```bash
nm -n vmlinux | grep -E " (bpf_check|do_check)"
```

确保：
- `verifier_start_addr` 是 `bpf_check` 的地址
- `verifier_end_addr` 在 `bpf_check` 和 `do_check` 之间或之后

### Q: 找不到 vmlinux 文件？

**A:** vmlinux 通常位于：
- `/boot/vmlinux-$(uname -r)`
- 或从内核源码编译：`make vmlinux`

复制到你项目的目录：
```bash
cp /boot/vmlinux-$(uname -r) ./vmlinux
```

---

## 完整流程示例

```bash
# 1. 准备 vmlinux
cp /boot/vmlinux-$(uname -r) ./vmlinux

# 2. 一键配置
./scripts/setup.sh ./vmlinux

# 3. 检查配置
cat config/kcov_config.yaml

# 4. 运行覆盖率采集
sudo python3 main.py run

# 5. 查看报告
python3 main.py analyze --report
```

---

## 脚本说明

### scripts/setup.sh
快速配置脚本，一键完成所有准备工作。

**参数：**
- `vmlinux_path` (可选): vmlinux 文件路径，默认 `./vmlinux`

**示例：**
```bash
./scripts/setup.sh ./vmlinux
```

### scripts/auto_config.py
自动提取符号地址并更新配置文件。

**参数：**
- `vmlinux_path` (可选): vmlinux 文件路径，默认 `./vmlinux`
- `config_path` (可选): 配置文件路径，默认 `./config/kcov_config.yaml`

**示例：**
```bash
python3 scripts/auto_config.py ./vmlinux ./config/kcov_config.yaml
```

---

## 下一步

配置完成后，参考 [README.md](README.md) 运行覆盖率采集和分析。

```bash
# 运行采集
sudo python3 main.py run

# 查看报告
python3 main.py analyze --report
```
