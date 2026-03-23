import argparse
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

import sys

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.coverage_db import CoverageDatabase
from utils.config import DEFAULT_CONFIG_PATH, load_project_config


def resolve_source_from_bytecode(bytecode_path: Path, bytecode_dir: Path, source_dir: Path) -> Path | None:
    try:
        relative_path = bytecode_path.resolve().relative_to(bytecode_dir.resolve())
    except Exception:
        return None

    source_path = (source_dir.resolve() / relative_path).with_suffix(".c")
    return source_path


def check_filesystem_mapping(bytecode_dir: Path, source_dir: Path, limit: int) -> int:
    print("[*] 文件系统映射检查")
    print(f"[*] 字节码目录：{bytecode_dir}")
    print(f"[*] 源码目录：{source_dir}")

    if not bytecode_dir.exists():
        print("[!] 字节码目录不存在")
        return 1
    if not source_dir.exists():
        print("[!] 源码目录不存在")
        return 1

    object_files = sorted(bytecode_dir.rglob("*.o"))
    print(f"[*] 发现 .o 文件数量：{len(object_files)}")

    matched = 0
    missing: list[tuple[Path, Path]] = []
    for obj_path in object_files:
        source_path = resolve_source_from_bytecode(obj_path, bytecode_dir, source_dir)
        if source_path is not None and source_path.exists():
            matched += 1
        else:
            missing.append((obj_path, source_path if source_path is not None else Path("<无法映射>")))

    print(f"[*] 可反查成功：{matched}")
    print(f"[*] 反查失败：{len(missing)}")
    if object_files:
        print(f"[*] 映射成功率：{matched / len(object_files) * 100:.2f}%")

    if missing:
        print("[*] 失败样例：")
        for obj_path, source_path in missing[:limit]:
            print(f"    字节码：{obj_path}")
            print(f"    期望源码：{source_path}")

    return 0


def check_database_mapping(db_path: Path, bytecode_dir: Path, source_dir: Path, limit: int) -> int:
    print("\n[*] 数据库 testcase 路径映射检查")
    print(f"[*] 数据库：{db_path}")

    if not db_path.exists():
        print("[!] 数据库文件不存在，跳过数据库检查")
        return 0

    with CoverageDatabase(str(db_path)) as db:
        cursor = db.conn.cursor()
        cursor.execute("SELECT name, path, path_hash FROM test_cases ORDER BY id")
        rows = cursor.fetchall()

    print(f"[*] 数据库 testcase 数量：{len(rows)}")

    under_bytecode_dir = 0
    matched = 0
    outside: list[tuple[str, str]] = []
    missing: list[tuple[str, Path, Path]] = []

    for row in rows:
        tc_name = row["name"]
        tc_path = Path(row["path"]).resolve()

        try:
            tc_path.relative_to(bytecode_dir.resolve())
            under_bytecode_dir += 1
        except Exception:
            outside.append((tc_name, str(tc_path)))
            continue

        source_path = resolve_source_from_bytecode(tc_path, bytecode_dir, source_dir)
        if source_path is not None and source_path.exists():
            matched += 1
        else:
            missing.append((tc_name, tc_path, source_path if source_path is not None else Path("<无法映射>")))

    print(f"[*] 落在字节码目录下的 testcase：{under_bytecode_dir}")
    print(f"[*] 可反查成功：{matched}")
    print(f"[*] 目录外 testcase：{len(outside)}")
    print(f"[*] 目录内但反查失败：{len(missing)}")

    if outside:
        print("[*] 不在字节码目录下的样例：")
        for tc_name, tc_path in outside[:limit]:
            print(f"    {tc_name} -> {tc_path}")

    if missing:
        print("[*] 目录内但源码缺失的样例：")
        for tc_name, tc_path, source_path in missing[:limit]:
            print(f"    {tc_name}")
            print(f"      字节码：{tc_path}")
            print(f"      期望源码：{source_path}")

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="检查字节码目录到源码目录的 seed 反查映射情况")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="项目配置 YAML 路径")
    parser.add_argument("--bytecode-dir", default=None, help="可选，覆盖 config.agent_bytecode_dir")
    parser.add_argument("--source-dir", default=None, help="可选，覆盖 config.agent_source_code_dir")
    parser.add_argument("--skip-db", action="store_true", help="只检查目录映射，不检查数据库 testcase")
    parser.add_argument("--limit", type=int, default=20, help="最多打印多少条失败样例")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    config = load_project_config(args.config)
    bytecode_dir = Path(args.bytecode_dir or config.agent_bytecode_dir).resolve()
    source_dir = Path(args.source_dir or config.agent_source_code_dir).resolve()

    fs_rc = check_filesystem_mapping(bytecode_dir, source_dir, args.limit)
    if fs_rc != 0:
        return fs_rc

    if not args.skip_db:
        check_database_mapping(Path(config.db_path).resolve(), bytecode_dir, source_dir, args.limit)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
