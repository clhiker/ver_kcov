import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

import sys

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.coverage_db import CoverageDatabase
from utils.config import DEFAULT_CONFIG_PATH, load_project_config


FUNC_HEADER_RE = re.compile(
    r"^(?:static\s+)?"
    r"(?:__always_inline\s+|noinline\s+|inline\s+|__printf\([^)]*\)\s+|"
    r"__maybe_unused\s+|__cold\s+|__weak\s+|notrace\s+|__no_kcsan\s+|"
    r"__nocfi\s+|__attribute__\([^)]*\)\s+)*"
    r"([_A-Za-z][\w\s\*]+?)\s+([A-Za-z_][\w]*)\s*\("
)

CONTROL_KEYWORDS = {
    "if", "for", "while", "switch", "return", "sizeof", "typeof",
    "case", "do", "goto",
}


@dataclass
class VerifierFunction:
    name: str
    return_type: str
    start_line: int
    end_line: int
    body_start_line: int
    call_names: list[str]
    covered_lines: int
    coverage_percent: float


def load_verifier_lines(verifier_path: Path) -> list[str]:
    return verifier_path.read_text(encoding="utf-8", errors="ignore").splitlines()


def find_function_ranges(lines: list[str]) -> list[tuple[str, str, int, int, int]]:
    functions: list[tuple[str, str, int, int, int]] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line or line.startswith("#") or line.endswith(";"):
            i += 1
            continue

        match = FUNC_HEADER_RE.match(line)
        if not match:
            i += 1
            continue

        header_start = i
        header_parts = [lines[i]]
        paren_balance = lines[i].count("(") - lines[i].count(")")
        j = i
        while paren_balance > 0 and j + 1 < len(lines):
            j += 1
            header_parts.append(lines[j])
            paren_balance += lines[j].count("(") - lines[j].count(")")

        brace_found = False
        body_start = j
        while body_start < len(lines):
            if "{" in lines[body_start]:
                brace_found = True
                break
            if lines[body_start].strip().endswith(";"):
                break
            body_start += 1

        if not brace_found:
            i = j + 1
            continue

        brace_depth = 0
        end_line = body_start
        entered = False
        for k in range(body_start, len(lines)):
            brace_depth += lines[k].count("{")
            if "{" in lines[k]:
                entered = True
            brace_depth -= lines[k].count("}")
            if entered and brace_depth == 0:
                end_line = k
                break

        name = match.group(2)
        if name in CONTROL_KEYWORDS:
            i = end_line + 1
            continue

        functions.append((
            name,
            match.group(1).strip(),
            header_start + 1,
            body_start + 1,
            end_line + 1,
        ))
        i = end_line + 1

    return functions


def build_call_graph(lines: list[str], functions: list[tuple[str, str, int, int, int]]) -> list[VerifierFunction]:
    function_names = {name for name, _, _, _, _ in functions}
    results: list[VerifierFunction] = []
    empty_covered: set[int] = set()

    for name, return_type, start_line, body_start_line, end_line in functions:
        body = "\n".join(lines[body_start_line - 1:end_line])
        calls = sorted({
            callee
            for callee in function_names
            if callee != name and re.search(rf"\b{re.escape(callee)}\s*\(", body)
        })
        results.append(VerifierFunction(
            name=name,
            return_type=return_type,
            start_line=start_line,
            end_line=end_line,
            body_start_line=body_start_line,
            call_names=calls,
            covered_lines=len(empty_covered),
            coverage_percent=0.0,
        ))

    return results


def apply_coverage(functions: list[VerifierFunction], covered_lines: set[int]) -> list[VerifierFunction]:
    updated: list[VerifierFunction] = []
    for func in functions:
        line_span = max(1, func.end_line - func.body_start_line + 1)
        covered = sum(1 for line in covered_lines if func.body_start_line <= line <= func.end_line)
        updated.append(VerifierFunction(
            name=func.name,
            return_type=func.return_type,
            start_line=func.start_line,
            end_line=func.end_line,
            body_start_line=func.body_start_line,
            call_names=func.call_names,
            covered_lines=covered,
            coverage_percent=(covered * 100.0 / line_span),
        ))
    return updated


def build_report(verifier_path: Path, db_path: Path) -> dict:
    lines = load_verifier_lines(verifier_path)
    function_ranges = find_function_ranges(lines)
    functions = build_call_graph(lines, function_ranges)

    with CoverageDatabase(str(db_path)) as db:
        covered_lines = db.get_covered_lines_by_file("kernel/bpf/verifier.c")

    functions = apply_coverage(functions, covered_lines)
    covered_functions = [func for func in functions if func.covered_lines > 0]

    return {
        "verifier_path": str(verifier_path.resolve()),
        "db_path": str(db_path.resolve()),
        "total_lines": len(lines),
        "covered_lines": len(covered_lines),
        "coverage_percent": (len(covered_lines) * 100.0 / len(lines)) if lines else 0.0,
        "total_functions": len(functions),
        "covered_functions": len(covered_functions),
        "function_coverage_percent": (len(covered_functions) * 100.0 / len(functions)) if functions else 0.0,
        "functions": [
            {
                "name": func.name,
                "return_type": func.return_type,
                "start_line": func.start_line,
                "end_line": func.end_line,
                "body_start_line": func.body_start_line,
                "covered_lines": func.covered_lines,
                "coverage_percent": func.coverage_percent,
                "callees": func.call_names,
            }
            for func in functions
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="分析 verifier.c 图谱及其覆盖情况")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="项目配置 YAML 路径")
    parser.add_argument("--verifier", default="verifier.c", help="verifier.c 源码路径")
    parser.add_argument("--output", default="cache/verifier_map_report.json", help="输出 JSON 路径")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    config = load_project_config(args.config)
    report = build_report(Path(args.verifier), Path(config.db_path))
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[*] verifier 总行数：{report['total_lines']}")
    print(f"[*] 已覆盖 verifier 行数：{report['covered_lines']}")
    print(f"[*] verifier 行覆盖率：{report['coverage_percent']:.4f}%")
    print(f"[*] verifier 函数总数：{report['total_functions']}")
    print(f"[*] 已覆盖函数数：{report['covered_functions']}")
    print(f"[*] verifier 函数覆盖率：{report['function_coverage_percent']:.4f}%")
    print(f"[*] 报告已写入：{output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
