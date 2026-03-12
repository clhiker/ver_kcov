# KCOV Runner Makefile
# 用于编译 eBPF Verifier 覆盖率采集程序

CC = gcc
CFLAGS = -Wall -Wextra -g -O2
LDFLAGS = -lbpf

# 目标文件
TARGET = kcov_runner

# 源文件
SRC = kcov_runner.c

# 默认目标
all: $(TARGET)

# 编译 kcov_runner
$(TARGET): $(SRC)
	@echo "编译 $(TARGET)..."
	$(CC) $(CFLAGS) -o $(TARGET) $(SRC) $(LDFLAGS)
	@echo "编译完成！"
	@echo ""
	@echo "使用方法:"
	@echo "  ./$(TARGET) <program.o>"
	@echo ""
	@echo "示例:"
	@echo "  ./$(TARGET) testcases/example.o"

# 清理
clean:
	@echo "清理编译文件..."
	rm -f $(TARGET)
	@echo "清理完成！"

# 安装依赖（可选）
install-deps:
	@echo "检查 libbpf 依赖..."
	@pkg-config --exists libbpf || (echo "错误：未找到 libbpf，请先安装:" && \
		echo "  Ubuntu/Debian: sudo apt-get install libbpf-dev" && \
		echo "  Fedora/RHEL: sudo dnf install libbpf-devel" && \
		echo "  Arch: sudo pacman -S libbpf" && \
		exit 1)
	@echo "libbpf 已安装"

# 帮助
help:
	@echo "KCOV Runner 编译系统"
	@echo ""
	@echo "目标:"
	@echo "  all           - 编译 kcov_runner (默认)"
	@echo "  clean         - 清理编译文件"
	@echo "  install-deps  - 检查并安装依赖"
	@echo "  help          - 显示此帮助信息"
	@echo ""
	@echo "依赖:"
	@echo "  - libbpf"
	@echo "  - gcc"
	@echo "  - 内核支持 KCOV (CONFIG_KCOV=y)"
	@echo ""
	@echo "快速开始:"
	@echo "  make install-deps  # 检查依赖"
	@echo "  make               # 编译"
	@echo "  ./kcov_runner test.o  # 运行"

.PHONY: all clean install-deps help
