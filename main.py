"""
主程序入口
"""
import sys
import argparse
from pathlib import Path

from utils.config import Config
from pipeline.runner import CoveragePipeline
from analysis.coverage_analyzer import CoverageAnalyzer
from core.coverage_db import CoverageDatabase


def cmd_run(args):
    """运行覆盖率采集"""
    config = load_config(args.config)
    
    with CoveragePipeline(config) as pipeline:
        stats = pipeline.run(
            testcase_dir=args.testcases,
            parallel=args.parallel
        )
        
        if stats['failed'] > 0:
            sys.exit(1)


def cmd_analyze(args):
    """分析覆盖率数据"""
    config = load_config(args.config)
    
    with CoverageDatabase(config.db_path) as db:
        analyzer = CoverageAnalyzer(db)
        
        if args.report:
            # 生成报告
            report = analyzer.generate_report()
            print_report(report)
        
        if args.stats:
            # 显示详细统计
            print_detailed_stats(db)


def cmd_query(args):
    """查询覆盖率信息"""
    config = load_config(args.config)
    
    with CoverageDatabase(config.db_path) as db:
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
    if Path(config_path).exists():
        return Config.from_yaml(config_path)
    else:
        print(f"[!] 配置文件不存在：{config_path}，使用默认配置")
        return Config()


def print_report(report):
    """打印报告"""
    print("="*60)
    print("Verifier 覆盖率分析报告")
    print("="*60)
    print(f"测试用例总数：{report.total_test_cases}")
    print(f"唯一路径数：{report.unique_paths}")
    print(f"覆盖文件数：{report.covered_files}")
    print(f"覆盖行数（去重后）：{report.covered_lines}")
    
    if report.total_lines > 0:
        print(f"覆盖率：{report.coverage_percentage:.2f}%")
    
    # 显示每个测试用例的覆盖详情
    if report.testcase_coverage:
        print("\n" + "="*60)
        print("各测试用例覆盖详情")
        print("="*60)
        print(f"{'测试用例':<40} {'覆盖行数':<12} {'唯一行数':<12} {'状态':<10}")
        print("-"*70)
        
        for tc in report.testcase_coverage:
            name = tc['name']
            # 截断过长的名字
            if len(name) > 38:
                name = name[:35] + "..."
            # 标识失败的测试用例
            status = "失败" if tc['unique_lines'] == 0 or tc['covered_lines'] == 0 else "成功"
            print(f"{name:<40} {tc['covered_lines']:<12} {tc['unique_lines']:<12} {status:<10}")
        
        print("-"*70)
        print(f"总计 {len(report.testcase_coverage)} 个测试用例")
    
    print("="*60)


def print_detailed_stats(db):
    """打印详细统计信息"""
    cursor = db.conn.cursor()
    
    # Verifier 总代码行数
    verifier_total_lines = 26169
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
    cursor.execute("SELECT COUNT(DISTINCT pc_address) FROM source_coverage WHERE pc_address IS NOT NULL AND pc_address != ''")
    collected_unique_pcs = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(DISTINCT file_path || ':' || line_number) FROM source_coverage")
    covered_source_lines = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(DISTINCT file_path) FROM source_coverage")
    covered_files = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM path_fingerprints")
    total_paths = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM test_cases")
    total_test_cases = cursor.fetchone()[0]
    
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
    print(f"  路径指纹总数：{total_paths} 条")
    print(f"  测试用例总数：{total_test_cases} 个")
    
    # 每个测试用例的覆盖率
    print("\n【测试用例覆盖率详情】")
    cursor.execute("SELECT id, name, path_hash, pc_count FROM test_cases ORDER BY name")
    testcases = cursor.fetchall()
    
    print(f"\n  {'测试用例':<20} {'路径 PC 数':>10} {'唯一 PC 数':>12} {'覆盖行数':>10} {'行覆盖率':>12}")
    print("  " + "-"*66)
    
    for tc in testcases:
        testcase_id = tc['id']
        name = tc['name']
        path_hash = tc['path_hash']
        pc_count = tc['pc_count']
        
        cursor.execute("""
            SELECT COUNT(DISTINCT file_path || ':' || line_number)
            FROM source_coverage
            WHERE testcase_id = ?
        """, (testcase_id,))
        covered_lines = cursor.fetchone()[0]
        
        cursor.execute("""
            SELECT COUNT(DISTINCT pc_address)
            FROM source_coverage
            WHERE testcase_id = ? AND pc_address IS NOT NULL AND pc_address != ''
        """, (testcase_id,))
        unique_pcs = cursor.fetchone()[0]
        
        tc_line_coverage = (covered_lines / verifier_total_lines) * 100 if verifier_total_lines > 0 else 0.0
        
        print(f"  {name:<20} {pc_count:>10} {unique_pcs:>12} {covered_lines:>10} {tc_line_coverage:>11.4f}%")
    
    print("\n" + "="*70)


def main():
    parser = argparse.ArgumentParser(description='Verifer 覆盖率采集框架')
    parser.add_argument('--config', '-c', default='config/kcov_config.yaml',
                       help='配置文件路径')
    
    subparsers = parser.add_subparsers(dest='command', help='命令')
    
    # run 命令
    run_parser = subparsers.add_parser('run', help='运行覆盖率采集')
    run_parser.add_argument('--testcases', '-t', help='测试用例目录')
    run_parser.add_argument('--parallel', '-p', action='store_true', 
                           help='启用并行处理')
    run_parser.set_defaults(func=cmd_run)
    
    # analyze 命令
    analyze_parser = subparsers.add_parser('analyze', help='分析覆盖率数据')
    analyze_parser.add_argument('--report', action='store_true',
                               help='生成覆盖率报告')
    analyze_parser.add_argument('--stats', action='store_true',
                               help='显示详细统计信息（包括总 PC 数、总代码行数、覆盖率）')
    analyze_parser.set_defaults(func=cmd_analyze)
    
    # query 命令
    query_parser = subparsers.add_parser('query', help='查询覆盖率信息')
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
