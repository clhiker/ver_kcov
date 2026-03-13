/*
 * KCOV Runner - eBPF Verifier 覆盖率采集程序
 * 
 * 功能：
 * 1. 加载 eBPF 程序
 * 2. 启用 KCOV 收集执行路径
 * 3. 输出 Verifier 相关的 PC 序列
 * 
 * 使用方法：
 *   ./kcov_runner test_program.o
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <linux/bpf.h>
#include <bpf/bpf.h>
#include <bpf/libbpf.h>
#include <errno.h>
#include <stdint.h>

/* KCOV 相关定义 */
#include <linux/kcov.h>

/* 默认 KCOV 缓冲区大小 - 2MB (能容纳 ~260000 个 PC) */
#define KCOV_BUFFER_SIZE (2 << 20)

/* 输出文件 */
#define OUTPUT_FILE "verifier_pcs.txt"

/* 调试信息级别 */
static int verbose = 0;

/* 打印调试信息 */
#define DEBUG_PRINT(level, fmt, ...) \
    do { \
        if (verbose >= level) { \
            fprintf(stderr, "[DEBUG] " fmt "\n", ##__VA_ARGS__); \
        } \
    } while (0)

/* 错误处理 */
#define CHECK_ERROR(condition, msg) \
    do { \
        if (condition) { \
            fprintf(stderr, "[ERROR] %s: %s\n", msg, strerror(errno)); \
            return -1; \
        } \
    } while (0)

/*
 * 结构体：KCOV 上下文
 */
struct kcov_context {
    int fd;                    /* KCOV 设备文件描述符 */
    uint64_t *cover_data;      /* KCOV 数据缓冲区 */
    uint64_t buffer_size;      /* 缓冲区大小 */
    uint64_t *pcs;             /* 收集到的 PC 序列 */
    size_t pc_count;           /* PC 数量 */
};

/*
 * 初始化 KCOV
 */
static int kcov_init(struct kcov_context *ctx, uint64_t buffer_size)
{
    int fd;
    uint64_t *cover_area;
    
    /* 打开 KCOV 设备 */
    fd = open("/sys/kernel/debug/kcov", O_RDWR);
    if (fd < 0) {
        perror("[ERROR] 打开 /sys/kernel/debug/kcov 失败");
        fprintf(stderr, "提示：确保已挂载 debugfs: mount -t debugfs none /sys/kernel/debug\n");
        return -1;
    }
    
    /* 初始化 KCOV 为 TRACE 模式 */
    if (ioctl(fd, KCOV_INIT_TRACE, buffer_size)) {
        perror("[ERROR] KCOV_INIT_TRACE 失败");
        close(fd);
        return -1;
    }
    
    /* 映射 KCOV 缓冲区 */
    cover_area = mmap(NULL, buffer_size * sizeof(uint64_t),
                     PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (cover_area == MAP_FAILED) {
        perror("[ERROR] mmap KCOV 缓冲区失败");
        close(fd);
        return -1;
    }
    
    ctx->fd = fd;
    ctx->cover_data = cover_area;
    ctx->buffer_size = buffer_size;
    ctx->pcs = NULL;
    ctx->pc_count = 0;
    
    DEBUG_PRINT(1, "KCOV 初始化成功：缓冲区大小 = %lu KB", 
                (buffer_size * sizeof(uint64_t)) / 1024);
    
    return 0;
}

/*
 * 启用 KCOV 收集
 */
static int kcov_enable(struct kcov_context *ctx)
{
    /* 重置缓冲区计数 */
    ctx->cover_data[0] = 0;
    
    /* 启用 KCOV - 新内核需要传递 mode 参数 */
    if (ioctl(ctx->fd, KCOV_ENABLE, KCOV_TRACE_PC)) {
        perror("[ERROR] KCOV_ENABLE 失败");
        return -1;
    }
    
    DEBUG_PRINT(2, "KCOV 已启用");
    return 0;
}

/*
 * 禁用 KCOV 收集
 */
static int kcov_disable(struct kcov_context *ctx)
{
    if (ioctl(ctx->fd, KCOV_DISABLE)) {
        perror("[ERROR] KCOV_DISABLE 失败");
        return -1;
    }
    
    DEBUG_PRINT(2, "KCOV 已禁用");
    return 0;
}

/*
 * 收集 KCOV 数据
 */
static int kcov_collect(struct kcov_context *ctx)
{
    uint64_t count;
    uint64_t *data;
    
    /* 读取收集到的 PC 数量 */
    count = ctx->cover_data[0];
    
    if (count == 0) {
        DEBUG_PRINT(1, "未收集到任何 PC");
        return 0;
    }
    
    if (count >= ctx->buffer_size) {
        fprintf(stderr, "[WARNING] KCOV 缓冲区可能溢出 (count=%lu, size=%lu)\n",
                count, ctx->buffer_size);
        count = ctx->buffer_size - 1;
    }
    
    /* 分配内存存储 PC */
    ctx->pcs = malloc(count * sizeof(uint64_t));
    if (!ctx->pcs) {
        perror("[ERROR] 分配 PC 内存失败");
        return -1;
    }
    
    /* 复制 PC 数据 */
    data = &ctx->cover_data[1];
    memcpy(ctx->pcs, data, count * sizeof(uint64_t));
    ctx->pc_count = count;
    
    DEBUG_PRINT(1, "收集到 %lu 个 PC", count);
    return 0;
}

/*
 * 清理 KCOV 资源
 */
static void kcov_cleanup(struct kcov_context *ctx)
{
    if (ctx->pcs) {
        free(ctx->pcs);
        ctx->pcs = NULL;
    }
    
    if (ctx->cover_data && ctx->cover_data != MAP_FAILED) {
        munmap(ctx->cover_data, ctx->buffer_size * sizeof(uint64_t));
        ctx->cover_data = NULL;
    }
    
    if (ctx->fd >= 0) {
        close(ctx->fd);
        ctx->fd = -1;
    }
    
    DEBUG_PRINT(1, "KCOV 资源已清理");
}

/*
 * 保存 PC 到文件
 */
static int save_pcs_to_file(struct kcov_context *ctx, const char *filename)
{
    FILE *fp;
    size_t i;
    
    fp = fopen(filename, "w");
    if (!fp) {
        perror("[ERROR] 打开输出文件失败");
        return -1;
    }
    
    for (i = 0; i < ctx->pc_count; i++) {
        fprintf(fp, "0x%lx\n", ctx->pcs[i]);
    }
    
    fclose(fp);
    printf("[INFO] 已保存 %lu 个 PC 到 %s\n", ctx->pc_count, filename);
    return 0;
}

/*
 * 打印 PC 序列
 */
static void print_pcs(struct kcov_context *ctx)
{
    size_t i;
    
    printf("\n=== 收集到的 PC 序列 (共 %lu 个) ===\n", ctx->pc_count);
    for (i = 0; i < ctx->pc_count && i < 20; i++) {
        printf("  [%04lu] 0x%lx\n", i, ctx->pcs[i]);
    }
    
    if (ctx->pc_count > 20) {
        printf("  ... 还有 %lu 个 PC\n", ctx->pc_count - 20);
    }
    printf("=====================================\n\n");
}

/*
 * 加载并验证 eBPF 程序
 * 这会触发内核中的 verifier 执行
 */
static int load_and_verify_bpf(const char *bpf_file)
{
    struct bpf_object *obj = NULL;
    int err;
    
    DEBUG_PRINT(1, "加载 eBPF 程序：%s", bpf_file);
    
    /* 打开 BPF 对象文件 */
    obj = bpf_object__open_file(bpf_file, NULL);
    if (libbpf_get_error(obj)) {
        fprintf(stderr, "[ERROR] 打开 BPF 文件失败：%s\n", bpf_file);
        return -1;
    }
    
    /* 加载 BPF 对象 - 这会触发 verifier */
    err = bpf_object__load(obj);
    if (err) {
        fprintf(stderr, "[ERROR] 加载 BPF 对象失败 (errno=%d)\n", errno);
        fprintf(stderr, "提示：这可能是 verifier 拒绝了该程序\n");
        bpf_object__close(obj);
        return -1;
    }
    
    DEBUG_PRINT(1, "BPF 程序加载成功");
    
    /* 保持程序加载状态一段时间，确保 KCOV 能收集到数据 */
    usleep(10000);  /* 10ms */
    
    /* 关闭对象 */
    bpf_object__close(obj);
    
    return 0;
}

/*
 * 主函数
 */
int main(int argc, char *argv[])
{
    struct kcov_context ctx = {0};
    const char *bpf_file;
    int ret = 0;
    
    /* 解析命令行参数 */
    if (argc < 2) {
        fprintf(stderr, "用法：%s <bpf_program.o> [选项]\n", argv[0]);
        fprintf(stderr, "选项:\n");
        fprintf(stderr, "  -v, --verbose    显示调试信息\n");
        fprintf(stderr, "  -o, --output     指定输出文件 (默认：%s)\n", OUTPUT_FILE);
        fprintf(stderr, "  -h, --help       显示帮助\n");
        return 1;
    }
    
    bpf_file = argv[1];
    
    /* 解析选项 */
    for (int i = 2; i < argc; i++) {
        if (strcmp(argv[i], "-v") == 0 || strcmp(argv[i], "--verbose") == 0) {
            verbose = 1;
        } else if (strcmp(argv[i], "-h") == 0 || strcmp(argv[i], "--help") == 0) {
            return 0;
        }
    }
    
    /* 检查文件是否存在 */
    if (access(bpf_file, F_OK) != 0) {
        fprintf(stderr, "[ERROR] 文件不存在：%s\n", bpf_file);
        return 1;
    }
    
    printf("[INFO] 开始加载 eBPF 程序：%s\n", bpf_file);
    
    /* 步骤 1: 初始化 KCOV */
    if (kcov_init(&ctx, KCOV_BUFFER_SIZE) < 0) {
        fprintf(stderr, "[ERROR] KCOV 初始化失败\n");
        return 1;
    }
    
    /* 步骤 2: 启用 KCOV - 必须在加载 BPF 之前！*/
    if (kcov_enable(&ctx) < 0) {
        fprintf(stderr, "[ERROR] 启用 KCOV 失败\n");
        kcov_cleanup(&ctx);
        return 1;
    }
    
    /* 步骤 3: 加载并验证 BPF 程序 */
    /* 这会触发 verifier，KCOV 会收集执行路径 */
    int verify_result = load_and_verify_bpf(bpf_file);
    
    /* 步骤 4: 禁用 KCOV - 无论验证成功还是失败，都要禁用并收集数据 */
    if (kcov_disable(&ctx) < 0) {
        fprintf(stderr, "[WARNING] 禁用 KCOV 失败\n");
    }
    
    /* 步骤 5: 收集数据 - 即使 verifier 失败也要收集 */
    if (kcov_collect(&ctx) < 0) {
        fprintf(stderr, "[ERROR] 收集 KCOV 数据失败\n");
        kcov_cleanup(&ctx);
        return 1;
    }
    
    /* 检查 verifier 结果 */
    if (verify_result < 0) {
        fprintf(stderr, "[INFO] BPF 程序验证失败，但已收集 KCOV 数据\n");
        fprintf(stderr, "[INFO] Verifier 执行路径已被记录，即使程序被拒绝\n");
    }
    
    /* 步骤 6: 输出结果 */
    if (verbose) {
        print_pcs(&ctx);
    }
    
    /* 保存到文件 */
    if (save_pcs_to_file(&ctx, OUTPUT_FILE) < 0) {
        ret = 1;
    }
    
    /* 步骤 7: 清理资源 */
    kcov_cleanup(&ctx);
    
    if (ret == 0) {
        if (verify_result >= 0) {
            printf("[INFO] 完成！Verifier 验证通过。\n");
        } else {
            printf("[INFO] 完成！Verifier 验证失败，但覆盖率数据已保存。\n");
            /* 返回 0 表示数据采集成功，即使 verifier 失败 */
            ret = 0;
        }
    }
    
    return ret;
}
