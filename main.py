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
        
        if args.equivalent:
            # 显示等价测试用例
            equiv = db.get_equivalent_test_cases()
            print(f"\n等价测试用例分组：{len(equiv)} 组")
            for path_hash, test_cases in list(equiv.items())[:5]:
                print(f"  {path_hash}: {len(test_cases)} 个用例")
                print(f"    示例：{test_cases[0]}")


def cmd_query(args):
    """查询覆盖率信息"""
    config = load_config(args.config)
    
    with CoverageDatabase(config.db_path) as db:
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
    print(f"覆盖行数：{report.covered_lines}")
    
    if report.total_lines > 0:
        print(f"覆盖率：{report.coverage_percentage:.2f}%")
    
    if report.equivalent_groups:
        print(f"\n等价测试用例组数：{len(report.equivalent_groups)}")
        total_redundant = sum(len(cases) - 1 for cases in report.equivalent_groups.values())
        print(f"可精简测试用例数：{total_redundant}")
    
    print("="*60)


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
    analyze_parser.add_argument('--equivalent', action='store_true',
                               help='显示等价测试用例')
    analyze_parser.set_defaults(func=cmd_analyze)
    
    # query 命令
    query_parser = subparsers.add_parser('query', help='查询覆盖率信息')
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
