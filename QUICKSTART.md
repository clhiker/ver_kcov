# 快速开始指南

## 5 分钟快速上手

### 步骤 1: 安装依赖

```bash
cd /home/clhiker/ver_kcov
pip install pyyaml
```

### 步骤 2: 准备环境

```bash
# 准备 vmlinux（带调试符号的内核镜像）
cp /path/to/vmlinux ./vmlinux

# 编译 KCOV 采集程序
make
```

### 步骤 3: 获取 Verifier 地址范围

```bash
# 提取符号地址
./scripts/extract_symbols.sh ./vmlinux

# 输出示例：
# bpf_check: 0xffffffff81dcd390
# do_check:  0xffffffff81e08050
# 记下起始和结束地址
```

### 步骤 4: 配置

编辑 `config/kcov_config.yaml`：

```yaml
vmlinux_path: "./vmlinux"
kcov_runner_path: "./kcov_runner"
verifier_start_addr: "0xffffffff81dcd390"  # 替换为你的地址
verifier_end_addr: "0xffffffff81e17e30"
testcase_dir: "./testcases"
```

### 步骤 5: 运行

```bash
# 运行覆盖率采集
python3 main.py run

# 查看报告
python3 main.py analyze --report
```

## 常见问题

### Q: 找不到 vmlinux？

**A:** vmlinux 是内核镜像，通常位于：
- `/boot/vmlinux-$(uname -r)`
- 或从内核源码编译：`make vmlinux`

确保包含调试信息：
```bash
file vmlinux
# 应显示 "with debug_info"
```

### Q: KCOV 设备无法访问？

**A:** 需要内核支持和正确权限：

```bash
# 检查内核配置
zgrep KCOV /proc/config.gz
# 应看到：CONFIG_KCOV=y

# 挂载 debugfs
sudo mount -t debugfs none /sys/kernel/debug

# 设置权限
sudo chmod 666 /sys/kernel/debug/kcov
```

### Q: 缓冲区溢出？

**A:** 如果 PC 数量超过缓冲区，修改 `kcov_runner.c`：

```c
#define KCOV_BUFFER_SIZE (2 << 20)  // 增大到 2MB
```

然后重新编译：
```bash
make clean && make
```

### Q: 覆盖率为 0？

**A:** 检查：
1. Verifier 地址范围是否正确
2. KCOV 是否正常工作
3. 测试用例是否有效

## 检查清单

运行前确认：
- [ ] vmlinux 存在且包含调试信息
- [ ] kcov_runner 已编译
- [ ] testcases 目录有 .o 文件
- [ ] config/kcov_config.yaml 配置正确
- [ ] KCOV 设备可访问

## 下一步

- 查看 [README.md](README.md) 了解完整文档
- 运行 `python3 main.py --help` 查看所有命令
- 尝试分析多个测试用例
