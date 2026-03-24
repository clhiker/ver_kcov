"""
主程序入口
"""
import os
import shlex
import sys
import argparse
from pathlib import Path

from utils.config import Config, DEFAULT_CONFIG_PATH, load_project_config
from utils.terminal_format import format_table_row
from pipeline.runner import CoveragePipeline
from analysis.coverage_analyzer import CoverageAnalyzer
from core.coverage_db import CoverageDatabase
from scripts.vm_utils import detect_guest_workdir, run_guest_command_streaming, to_repo_relative, vm_config_from_project_config


def cmd_run(args):
    """运行覆盖率采集"""
    config = load_config(args.config)
    path_type = args.path_type

    if os.environ.get("KCOV_RUN_IN_GUEST") != "1":
        vm_config = vm_config_from_project_config(config)
        print("[*] 正在准备虚拟机挂载点和 guest 工作目录...")
        guest_workdir = detect_guest_workdir(vm_config)
        print(f"[*] Guest 工作目录：{guest_workdir}")

        guest_cmd = ["python3", "-u", "main.py"]
        config_path = Path(args.config)
        if config_path.exists():
            guest_cmd.extend(["--config", to_repo_relative(config_path.resolve())])
        else:
            guest_cmd.extend(["--config", args.config])
        guest_cmd.append("run")

        if args.testcases:
            testcase_path = Path(args.testcases)
            testcase_arg = to_repo_relative(testcase_path.resolve()) if testcase_path.exists() else args.testcases
            guest_cmd.extend(["--testcases", testcase_arg])
        if args.parallel:
            guest_cmd.append("--parallel")
        if path_type:
            guest_cmd.extend(["--path-type", path_type])

        quoted_cmd = "KCOV_RUN_IN_GUEST=1 " + " ".join(shlex.quote(part) for part in guest_cmd)
        result = run_guest_command_streaming(vm_config, quoted_cmd, workdir=guest_workdir)
        if result.returncode != 0:
            sys.exit(result.returncode)
        return
    
    if args.parallel:
        from pipeline.parallel_runner import ParallelCoveragePipeline
        pipeline_cls = ParallelCoveragePipeline
    else:
        from pipeline.runner import CoveragePipeline
        pipeline_cls = CoveragePipeline
        
    with pipeline_cls(config) as pipeline:
        stats = pipeline.run(
            testcase_dir=args.testcases,
            parallel=args.parallel,
            path_type=path_type
        )
        
        if stats['failed'] > 0:
            sys.exit(1)


def cmd_clear(args):
    """清空覆盖率数据"""
    config = load_config(args.config)
    print("\n[*] 正在清空数据库中的旧数据...")
    with CoverageDatabase(config.db_path) as db:
        db.clear_all_data()
    print("[*] 清理完成。\n")


def cmd_analyze(args):
    """分析覆盖率数据"""
    config = load_config(args.config)
    
    with CoverageDatabase(config.db_path) as db:
        analyzer = CoverageAnalyzer(db)
        
        if args.report:
            # 生成报告
            report = analyzer.generate_report()
            print_report(report)
        
        if args.stats or args.detail:
            # --stats 打印总览；--detail 额外打印测试用例级明细
            print_detailed_stats(db, show_testcase_details=args.detail)


def cmd_query(args):
    """查询覆盖率信息"""
    config = load_config(args.config)
    
    with CoverageDatabase(config.db_path) as db:
        if args.execution_paths:
            paths = db.get_execution_paths_summary()
            print("="*80)
            print("执行路径摘要")
            print("="*80)

            if not paths:
                print("当前没有执行路径数据")
                print("="*80)
                return

            print(format_table_row([
                ("path_hash", 18, "left"),
                ("PC 数", 10, "right"),
                ("覆盖行数", 12, "right"),
                ("用例数", 8, "right"),
                ("测试用例集合", 30, "left"),
            ]))
            print("-"*80)

            for path in paths:
                print(format_table_row([
                    (path['path_hash'], 18, "left"),
                    (path['pc_count'], 10, "right"),
                    (path['covered_lines'], 12, "right"),
                    (len(path['testcases']), 8, "right"),
                    (', '.join(path['testcases']), 30, "left"),
                ]))
                if args.verbose:
                    sequences = db.get_execution_path_sequence(path['path_hash'])
                    if sequences:
                        for file_path, lines in sorted(sequences.items()):
                            print(f"  {file_path}: {' -> '.join(str(line) for line in lines)}")
                    else:
                        print("  [!] 未找到已持久化的执行轨迹，请重新运行 run 以生成轨迹数据")

            print("-"*80)
            print(f"总计 {len(paths)} 条执行路径")
            print("="*80)
            return

        if args.stable_paths:
            paths = db.get_stable_paths_summary()
            print("="*80)
            print("稳定路径摘要")
            print("="*80)

            if not paths:
                print("当前没有稳定路径数据")
                print("="*80)
                return

            print(format_table_row([
                ("stable_hash", 18, "left"),
                ("锚点数", 10, "right"),
                ("覆盖行数", 12, "right"),
                ("用例数", 8, "right"),
                ("测试用例集合", 30, "left"),
            ]))
            print("-"*80)

            for path in paths:
                print(format_table_row([
                    (path['stable_path_hash'], 18, "left"),
                    (path['anchor_count'], 10, "right"),
                    (path['covered_lines'], 12, "right"),
                    (len(path['testcases']), 8, "right"),
                    (', '.join(path['testcases']), 30, "left"),
                ]))
                if args.verbose:
                    print(f"  对应执行路径哈希: {', '.join(path['raw_paths'])}")
                    stable_sequence = db.get_stable_path_sequence(path['stable_path_hash'])
                    if stable_sequence:
                        print(f"  锚点序列: {' -> '.join(stable_sequence)}")

            print("-"*80)
            print(f"总计 {len(paths)} 条稳定路径")
            print("="*80)
            return

        if args.testcase:
            # 查询指定测试用例的覆盖详情
            detail = db.get_testcase_detailed_coverage(args.testcase)
            
            if 'error' in detail:
                print(f"[!] 错误：{detail['error']}")
                return
            
            print("="*60)
            print(f"测试用例：{detail['name']} 覆盖详情")
            print("="*60)
            print(f"唯一路径哈希：{detail['path_hash']}")
            print(f"覆盖的唯一行数：{detail['total_unique_lines']}")
            print(f"涉及文件数：{len(detail['files'])}")
            print("\n覆盖的文件及行号:")
            print("-"*60)
            
            for file_path, lines in sorted(detail['files'].items()):
                # 简化文件路径显示
                if len(file_path) > 50:
                    display_path = "..." + file_path[-47:]
                else:
                    display_path = file_path
                
                print(f"\n{display_path}")
                print(f"  覆盖行数：{len(lines)}")
                
                # 显示行号（如果行数不多，全部显示；否则显示前 20 行）
                if len(lines) <= 20:
                    print(f"  行号：{lines}")
                else:
                    print(f"  行号：{lines[:20]}... (共 {len(lines)} 行)")
            
            print("\n" + "="*60)
        
        if args.file:
            # 查询文件覆盖情况
            covered = db.get_covered_lines_by_file(args.file)
            print(f"文件 {args.file} 被覆盖的行数：{len(covered)}")
            if args.verbose:
                print(f"覆盖的行：{sorted(list(covered))[:50]}...")
        
        if args.line:
            # 查询覆盖指定行的测试用例
            file_path, line_num = args.line.rsplit(':', 1)
            test_cases = db.find_test_cases_for_line(file_path, int(line_num))
            print(f"覆盖 {file_path}:{line_num} 的测试用例:")
            for tc in test_cases:
                print(f"  - {tc}")


def cmd_export(args):
    """导出覆盖率数据"""
    config = load_config(args.config)
    
    with CoverageDatabase(config.db_path) as db:
        analyzer = CoverageAnalyzer(db)
        analyzer.export_report(args.output, format=args.format)
        print(f"报告已导出到：{args.output}")


def load_config(config_path: str) -> Config:
    """加载配置"""
    return load_project_config(config_path)


def print_report(report):
    """打印报告"""
    print("="*60)
    print("Verifier 覆盖率分析报告")
    print("="*60)
    print(f"测试用例总数：{report.total_test_cases}")
    print(f"唯一执行路径数：{report.unique_execution_paths}")
    print(f"唯一稳定路径数：{report.unique_stable_paths}")
    print(f"唯一覆盖行集合数：{report.unique_coverage_groups}")
    print(f"覆盖文件数：{report.covered_files}")
    print(f"覆盖行数（去重后）：{report.covered_lines}")
    
    if report.total_lines > 0:
        print(f"覆盖率：{report.coverage_percentage:.2f}%")
    
    if report.coverage_groups:
        print("\n" + "="*82)
        print("覆盖行集合摘要")
        print("="*82)
        print(format_table_row([
            ("覆盖签名", 18, "left"),
            ("覆盖PC数", 12, "right"),
            ("唯一行数", 12, "right"),
            ("用例数", 8, "right"),
            ("测试用例集合", 34, "left"),
        ]))
        print("-"*82)

        for path in report.coverage_groups:
            print(format_table_row([
                (path['coverage_signature'], 18, "left"),
                (path.get('covered_pcs', 0), 12, "right"),
                (path.get('unique_lines', path['covered_lines']), 12, "right"),
                (len(path['testcases']), 8, "right"),
                (', '.join(path['testcases']), 34, "left"),
            ]))

        print("-"*82)
        print(f"总计 {len(report.coverage_groups)} 个覆盖行集合")
    
    print("="*60)


def print_detailed_stats(db, show_testcase_details=False):
    """打印详细统计信息"""
    cursor = db.conn.cursor()
    execution_paths = db.get_execution_paths_summary()
    stable_paths = db.get_stable_paths_summary()
    coverage_groups = db.get_coverage_groups_summary()
    
    # Verifier 总代码行数
    verifier_path = Path(__file__).resolve().parent / "verifier.c"
    verifier_total_lines = 0
    if verifier_path.exists():
        with open(verifier_path, 'r', encoding='utf-8', errors='ignore') as f:
            verifier_total_lines = sum(1 for _ in f)
    verifier_address_space = 303104
    estimated_total_pcs = verifier_address_space // 4
    
    print("\n" + "="*70)
    print("Verifier 覆盖率详细统计报告")
    print("="*70)
    
    print("\n【代码规模】")
    print(f"  Verifier 总代码行数：{verifier_total_lines:,} 行")
    print(f"  Verifier 地址空间：{verifier_address_space:,} 字节")
    print(f"  预估总 PC 数量：{estimated_total_pcs:,} 个")
    
    # 获取覆盖统计
    cursor.execute(
        "SELECT COUNT(DISTINCT pc_address) FROM source_coverage "
        "WHERE pc_address IS NOT NULL AND pc_address != '' AND file_path LIKE ?",
        (f"%{CoverageDatabase.VERIFIER_FILE_SUFFIX}",)
    )
    collected_unique_pcs = cursor.fetchone()[0]
    
    cursor.execute(
        "SELECT COUNT(DISTINCT file_path || ':' || line_number) FROM source_coverage WHERE file_path LIKE ?",
        (f"%{CoverageDatabase.VERIFIER_FILE_SUFFIX}",)
    )
    covered_source_lines = cursor.fetchone()[0]
    
    cursor.execute(
        "SELECT COUNT(DISTINCT file_path) FROM source_coverage WHERE file_path LIKE ?",
        (f"%{CoverageDatabase.VERIFIER_FILE_SUFFIX}",)
    )
    covered_files = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM test_cases")
    total_test_cases = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM test_cases WHERE pc_count > 0")
    successful_test_cases = cursor.fetchone()[0]
    failed_test_cases = total_test_cases - successful_test_cases
    
    print("\n【覆盖情况】")
    print(f"  已采集唯一 PC 数：{collected_unique_pcs:,} 个")
    print(f"  已覆盖源码行数：{covered_source_lines:,} 行")
    print(f"  已覆盖文件数：{covered_files} 个")
    
    print("\n【覆盖率】")
    line_coverage = (covered_source_lines / verifier_total_lines) * 100 if verifier_total_lines > 0 else 0.0
    pc_coverage = (collected_unique_pcs / estimated_total_pcs) * 100 if estimated_total_pcs > 0 else 0.0
    print(f"  代码行覆盖率：{line_coverage:.4f}%")
    print(f"  PC 覆盖率：{pc_coverage:.4f}%")
    
    print("\n【路径统计】")
    print(f"  唯一执行路径数：{len(execution_paths)} 条")
    print(f"  唯一稳定路径数：{len(stable_paths)} 条")
    print(f"  唯一覆盖行集合数：{len(coverage_groups)} 个")
    print(f"  测试用例总数：{total_test_cases} 个")
    print(f"  成功用例数（pc_count > 0）：{successful_test_cases} 个")
    print(f"  失败用例数（pc_count = 0）：{failed_test_cases} 个")
    
    if show_testcase_details:
        print("\n【测试用例覆盖率详情】")
        cursor.execute("SELECT id, name, pc_count FROM test_cases ORDER BY name")
        testcases = cursor.fetchall()
        
        print("\n  " + format_table_row([
            ("测试用例", 20, "left"),
            ("路径 PC 数", 10, "right"),
            ("唯一 PC 数", 12, "right"),
            ("覆盖行数", 10, "right"),
            ("行覆盖率", 12, "right"),
        ]))
        print("  " + "-"*66)
        
        for tc in testcases:
            testcase_id = tc['id']
            name = tc['name']
            pc_count = tc['pc_count']
            
            cursor.execute("""
                SELECT COUNT(DISTINCT file_path || ':' || line_number)
                FROM source_coverage
                WHERE testcase_id = ?
                  AND file_path LIKE ?
            """, (testcase_id, f"%{CoverageDatabase.VERIFIER_FILE_SUFFIX}"))
            covered_lines = cursor.fetchone()[0]
            
            cursor.execute("""
                SELECT COUNT(DISTINCT pc_address)
                FROM source_coverage
                WHERE testcase_id = ? AND pc_address IS NOT NULL AND pc_address != ''
                  AND file_path LIKE ?
            """, (testcase_id, f"%{CoverageDatabase.VERIFIER_FILE_SUFFIX}"))
            unique_pcs = cursor.fetchone()[0]
            
            tc_line_coverage = (covered_lines / verifier_total_lines) * 100 if verifier_total_lines > 0 else 0.0
            
            print("  " + format_table_row([
                (name, 20, "left"),
                (pc_count, 10, "right"),
                (unique_pcs, 12, "right"),
                (covered_lines, 10, "right"),
                (f"{tc_line_coverage:>11.4f}%", 12, "right"),
            ]))
    
    print("\n" + "="*70)


def main():
    parser = argparse.ArgumentParser(description='Verifer 覆盖率采集框架')
    parser.add_argument('--config', '-c', default=str(DEFAULT_CONFIG_PATH),
                       help='配置文件路径')
    
    subparsers = parser.add_subparsers(dest='command', help='命令')
    
    # run 命令
    run_parser = subparsers.add_parser('run', help='运行覆盖率采集')
    run_parser.add_argument('--testcases', '-t', help='测试用例目录')
    run_parser.add_argument('--parallel', '-p', action='store_true', 
                           help='启用并行处理')
    run_parser.add_argument('--path-type', choices=['stable', 'full', 'all'], 
                           default='all', help='选择采集的路径类型：stable (仅稳定路径), full (完整执行路径及源码覆盖), all (两者皆采)')
    run_parser.set_defaults(func=cmd_run)
    
    # clear 命令
    clear_parser = subparsers.add_parser('clear', help='清空数据库中的原有覆盖率数据')
    clear_parser.set_defaults(func=cmd_clear)
    
    # analyze 命令
    analyze_parser = subparsers.add_parser('analyze', help='分析覆盖率数据')
    analyze_parser.add_argument('--report', action='store_true',
                               help='生成覆盖率报告')
    analyze_parser.add_argument('--stats', action='store_true',
                               help='显示汇总统计信息（包括总 PC 数、总代码行数、覆盖率）')
    analyze_parser.add_argument('--detail', action='store_true',
                               help='显示测试用例级覆盖详情')
    analyze_parser.set_defaults(func=cmd_analyze)
    
    # query 命令
    query_parser = subparsers.add_parser('query', help='查询覆盖率信息')
    query_parser.add_argument('--execution-paths', action='store_true', help='查询当前执行路径摘要')
    query_parser.add_argument('--stable-paths', action='store_true', help='查询当前稳定路径摘要')
    query_parser.add_argument('--testcase', '-tc', help='查询指定测试用例的覆盖详情')
    query_parser.add_argument('--file', '-f', help='查询文件覆盖情况')
    query_parser.add_argument('--line', '-l', help='查询覆盖指定行的测试用例 (格式：file:line)')
    query_parser.add_argument('--verbose', '-v', action='store_true',
                             help='显示详细信息')
    query_parser.set_defaults(func=cmd_query)
    
    # export 命令
    export_parser = subparsers.add_parser('export', help='导出数据')
    export_parser.add_argument('--output', '-o', required=True,
                              help='输出文件路径')
    export_parser.add_argument('--format', choices=['json', 'text'], 
                              default='json', help='导出格式')
    export_parser.set_defaults(func=cmd_export)
    
    args = parser.parse_args()
    
    if args.command is None:
        parser.print_help()
        sys.exit(1)
    
    args.func(args)


if __name__ == "__main__":
    main()
