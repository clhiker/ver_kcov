/*
 * KCOV Runner (Raw Loader) - eBPF verifier coverage collector
 *
 * This runner avoids libbpf object loading path and directly performs
 * BPF_PROG_LOAD via syscall after extracting instruction section from ELF.
 */

#define _GNU_SOURCE
#include <elf.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/bpf.h>
#include <linux/kcov.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>

#define KCOV_BUFFER_SIZE (2 << 20)
#define OUTPUT_FILE "verifier_pcs.txt"
#define LOG_BUF_SIZE (1 << 20)

static int verbose = 0;

#define DEBUG_PRINT(level, fmt, ...) \
    do { \
        if (verbose >= (level)) \
            fprintf(stderr, "[DEBUG] " fmt "\n", ##__VA_ARGS__); \
    } while (0)

struct kcov_context {
    int fd;
    uint64_t *cover_data;
    uint64_t buffer_size;
    uint64_t *pcs;
    size_t pc_count;
};

struct elf_program {
    struct bpf_insn *insns;
    size_t insn_cnt;
    const char *license;
};

static int sys_bpf(enum bpf_cmd cmd, union bpf_attr *attr, unsigned int size)
{
    return syscall(__NR_bpf, cmd, attr, size);
}

static int kcov_init(struct kcov_context *ctx, uint64_t buffer_size)
{
    int fd;
    uint64_t *cover_area;

    fd = open("/sys/kernel/debug/kcov", O_RDWR);
    if (fd < 0) {
        perror("[ERROR] open /sys/kernel/debug/kcov failed");
        fprintf(stderr, "hint: mount -t debugfs none /sys/kernel/debug\n");
        return -1;
    }

    if (ioctl(fd, KCOV_INIT_TRACE, buffer_size)) {
        perror("[ERROR] KCOV_INIT_TRACE failed");
        close(fd);
        return -1;
    }

    cover_area = mmap(NULL,
                      buffer_size * sizeof(uint64_t),
                      PROT_READ | PROT_WRITE,
                      MAP_SHARED,
                      fd,
                      0);
    if (cover_area == MAP_FAILED) {
        perror("[ERROR] mmap KCOV buffer failed");
        close(fd);
        return -1;
    }

    ctx->fd = fd;
    ctx->cover_data = cover_area;
    ctx->buffer_size = buffer_size;
    ctx->pcs = NULL;
    ctx->pc_count = 0;
    return 0;
}

static int kcov_enable(struct kcov_context *ctx)
{
    ctx->cover_data[0] = 0;
    if (ioctl(ctx->fd, KCOV_ENABLE, KCOV_TRACE_PC)) {
        perror("[ERROR] KCOV_ENABLE failed");
        return -1;
    }
    return 0;
}

static int kcov_disable(struct kcov_context *ctx)
{
    if (ioctl(ctx->fd, KCOV_DISABLE)) {
        perror("[ERROR] KCOV_DISABLE failed");
        return -1;
    }
    return 0;
}

static int kcov_collect(struct kcov_context *ctx)
{
    uint64_t count;

    count = ctx->cover_data[0];
    if (count == 0)
        return 0;

    if (count >= ctx->buffer_size) {
        fprintf(stderr,
                "[WARNING] KCOV buffer may overflow (count=%lu, size=%lu)\n",
                count,
                ctx->buffer_size);
        count = ctx->buffer_size - 1;
    }

    ctx->pcs = malloc(count * sizeof(uint64_t));
    if (!ctx->pcs) {
        perror("[ERROR] alloc pcs failed");
        return -1;
    }

    memcpy(ctx->pcs, &ctx->cover_data[1], count * sizeof(uint64_t));
    ctx->pc_count = count;
    return 0;
}

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
}

static int save_pcs_to_file(struct kcov_context *ctx, const char *filename)
{
    FILE *fp;
    size_t i;

    fp = fopen(filename, "w");
    if (!fp) {
        perror("[ERROR] open output file failed");
        return -1;
    }

    for (i = 0; i < ctx->pc_count; i++)
        fprintf(fp, "0x%lx\n", ctx->pcs[i]);

    fclose(fp);
    return 0;
}

static int read_file_into_memory(const char *path, void **buf_out, size_t *len_out)
{
    int fd;
    struct stat st;
    void *buf;
    ssize_t rd;

    fd = open(path, O_RDONLY);
    if (fd < 0) {
        perror("[ERROR] open ELF failed");
        return -1;
    }

    if (fstat(fd, &st) < 0) {
        perror("[ERROR] stat ELF failed");
        close(fd);
        return -1;
    }

    if (st.st_size <= 0) {
        fprintf(stderr, "[ERROR] empty ELF file\n");
        close(fd);
        return -1;
    }

    buf = malloc((size_t)st.st_size);
    if (!buf) {
        perror("[ERROR] alloc ELF buffer failed");
        close(fd);
        return -1;
    }

    rd = read(fd, buf, (size_t)st.st_size);
    close(fd);
    if (rd != st.st_size) {
        perror("[ERROR] read ELF failed");
        free(buf);
        return -1;
    }

    *buf_out = buf;
    *len_out = (size_t)st.st_size;
    return 0;
}

static int extract_program_from_elf(void *elf_buf, size_t elf_len, struct elf_program *prog)
{
    Elf64_Ehdr *eh;
    Elf64_Shdr *shdrs;
    const char *shstrtab;
    size_t i;
    Elf64_Shdr *text_sec = NULL;
    const char *license = "GPL";

    if (elf_len < sizeof(Elf64_Ehdr)) {
        fprintf(stderr, "[ERROR] ELF too small\n");
        return -1;
    }

    eh = (Elf64_Ehdr *)elf_buf;
    if (memcmp(eh->e_ident, ELFMAG, SELFMAG) != 0 || eh->e_ident[EI_CLASS] != ELFCLASS64) {
        fprintf(stderr, "[ERROR] unsupported ELF format\n");
        return -1;
    }

    if (eh->e_shoff == 0 || eh->e_shentsize != sizeof(Elf64_Shdr)) {
        fprintf(stderr, "[ERROR] invalid section header table\n");
        return -1;
    }

    if ((size_t)eh->e_shoff + (size_t)eh->e_shnum * sizeof(Elf64_Shdr) > elf_len) {
        fprintf(stderr, "[ERROR] section header table out of bounds\n");
        return -1;
    }

    shdrs = (Elf64_Shdr *)((char *)elf_buf + eh->e_shoff);
    if (eh->e_shstrndx == SHN_UNDEF || eh->e_shstrndx >= eh->e_shnum) {
        fprintf(stderr, "[ERROR] invalid shstrndx\n");
        return -1;
    }

    if (shdrs[eh->e_shstrndx].sh_offset + shdrs[eh->e_shstrndx].sh_size > elf_len) {
        fprintf(stderr, "[ERROR] section string table out of bounds\n");
        return -1;
    }
    shstrtab = (const char *)elf_buf + shdrs[eh->e_shstrndx].sh_offset;

    for (i = 0; i < eh->e_shnum; i++) {
        const Elf64_Shdr *s = &shdrs[i];
        const char *name = shstrtab + s->sh_name;

        if (s->sh_offset + s->sh_size > elf_len)
            continue;

        if ((s->sh_flags & SHF_EXECINSTR) && s->sh_type == SHT_PROGBITS && s->sh_size >= sizeof(struct bpf_insn)) {
            if (!text_sec || s->sh_size > text_sec->sh_size)
                text_sec = (Elf64_Shdr *)s;
        }

        if (!strcmp(name, "license") || !strcmp(name, ".license")) {
            const char *sec_data = (const char *)elf_buf + s->sh_offset;
            if (s->sh_size > 1)
                license = sec_data;
        }
    }

    if (!text_sec) {
        fprintf(stderr, "[ERROR] no executable instruction section found\n");
        return -1;
    }

    prog->insns = (struct bpf_insn *)((char *)elf_buf + text_sec->sh_offset);
    prog->insn_cnt = text_sec->sh_size / sizeof(struct bpf_insn);
    prog->license = license;

    DEBUG_PRINT(1, "ELF program extracted: insn_cnt=%lu, license=%s", (unsigned long)prog->insn_cnt, prog->license);
    return 0;
}

static int try_prog_load(const struct elf_program *prog,
                         enum bpf_prog_type prog_type,
                         const char *prog_name,
                         char *log_buf,
                         size_t log_buf_sz)
{
    union bpf_attr attr;
    int fd;

    memset(&attr, 0, sizeof(attr));
    attr.prog_type = prog_type;
    attr.insn_cnt = prog->insn_cnt;
    attr.insns = (uint64_t)(uintptr_t)prog->insns;
    attr.license = (uint64_t)(uintptr_t)prog->license;
    attr.log_buf = (uint64_t)(uintptr_t)log_buf;
    attr.log_size = log_buf_sz;
    attr.log_level = 1;
    attr.kern_version = 0;

#ifdef HAVE_BPF_ATTR_PROG_NAME
    if (prog_name && prog_name[0]) {
        strncpy((char *)attr.prog_name, prog_name, BPF_OBJ_NAME_LEN - 1);
        attr.prog_name[BPF_OBJ_NAME_LEN - 1] = '\0';
    }
#else
    (void)prog_name;
#endif

    fd = sys_bpf(BPF_PROG_LOAD, &attr, sizeof(attr));
    if (fd < 0)
        return -1;

    close(fd);
    return 0;
}

static int load_and_verify_raw(const char *bpf_file, const char *prog_name)
{
    void *elf_buf = NULL;
    size_t elf_len = 0;
    struct elf_program prog;
    char *log_buf = NULL;
    int rc = -1;
    size_t i;

    const enum bpf_prog_type candidates[] = {
        BPF_PROG_TYPE_SOCKET_FILTER,
        BPF_PROG_TYPE_SCHED_CLS,
        BPF_PROG_TYPE_KPROBE,
        BPF_PROG_TYPE_TRACEPOINT,
        BPF_PROG_TYPE_TRACING,
    };

    if (read_file_into_memory(bpf_file, &elf_buf, &elf_len) < 0)
        goto out;

    if (extract_program_from_elf(elf_buf, elf_len, &prog) < 0)
        goto out;

    log_buf = calloc(1, LOG_BUF_SIZE);
    if (!log_buf) {
        perror("[ERROR] alloc verifier log buffer failed");
        goto out;
    }

    for (i = 0; i < sizeof(candidates) / sizeof(candidates[0]); i++) {
        memset(log_buf, 0, LOG_BUF_SIZE);
        rc = try_prog_load(&prog, candidates[i], prog_name, log_buf, LOG_BUF_SIZE);
        if (rc == 0) {
            DEBUG_PRINT(1, "BPF_PROG_LOAD succeeded with prog_type=%d", candidates[i]);
            break;
        }
        DEBUG_PRINT(1,
                    "BPF_PROG_LOAD failed with prog_type=%d errno=%d",
                    candidates[i],
                    errno);
    }

    if (rc != 0 && verbose >= 1 && log_buf[0] != '\0')
        fprintf(stderr, "%s\n", log_buf);

out:
    if (log_buf)
        free(log_buf);
    if (elf_buf)
        free(elf_buf);
    return rc;
}

int main(int argc, char *argv[])
{
    struct kcov_context ctx = {0};
    const char *bpf_file;
    const char *output_file = OUTPUT_FILE;
    const char *prog_name = NULL;
    int ret = 0;
    int verify_result;
    int i;

    if (argc < 2) {
        fprintf(stderr, "usage: %s <bpf_program.o> [options]\n", argv[0]);
        fprintf(stderr, "options:\n");
        fprintf(stderr, "  -v, --verbose\n");
        fprintf(stderr, "  -o, --output <file>\n");
        fprintf(stderr, "  -n, --name <prog_name>\n");
        fprintf(stderr, "  -h, --help\n");
        return 1;
    }

    bpf_file = argv[1];
    for (i = 2; i < argc; i++) {
        if (!strcmp(argv[i], "-v") || !strcmp(argv[i], "--verbose")) {
            verbose = 1;
        } else if ((!strcmp(argv[i], "-o") || !strcmp(argv[i], "--output")) && i + 1 < argc) {
            output_file = argv[++i];
        } else if ((!strcmp(argv[i], "-n") || !strcmp(argv[i], "--name")) && i + 1 < argc) {
            prog_name = argv[++i];
        } else if (!strcmp(argv[i], "-h") || !strcmp(argv[i], "--help")) {
            return 0;
        }
    }

    if (access(bpf_file, F_OK) != 0) {
        fprintf(stderr, "[ERROR] file not found: %s\n", bpf_file);
        return 1;
    }

    if (kcov_init(&ctx, KCOV_BUFFER_SIZE) < 0)
        return 1;
    if (kcov_enable(&ctx) < 0) {
        kcov_cleanup(&ctx);
        return 1;
    }

    verify_result = load_and_verify_raw(bpf_file, prog_name);

    if (kcov_disable(&ctx) < 0)
        fprintf(stderr, "[WARNING] KCOV_DISABLE failed\n");

    if (kcov_collect(&ctx) < 0) {
        kcov_cleanup(&ctx);
        return 1;
    }

    if (save_pcs_to_file(&ctx, output_file) < 0)
        ret = 1;

    if (verify_result != 0)
        DEBUG_PRINT(1, "BPF load failed, but KCOV data (if any) has been written");

    kcov_cleanup(&ctx);
    return ret;
}
