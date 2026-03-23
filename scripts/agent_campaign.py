import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.coverage_db import CoverageDatabase
from scripts.vm_utils import VMConfig, detect_guest_workdir, ensure_guest_mount, run_guest_command, to_repo_relative
from utils.config import DEFAULT_CONFIG_PATH, load_project_config


def log(message: str) -> None:
    print(message, flush=True)


def get_db_snapshot(db_path: str) -> dict:
    with CoverageDatabase(db_path) as db:
        execution_paths = db.get_execution_paths_summary()
        stats = db.get_coverage_statistics()
        sparse_count = sum(1 for item in execution_paths if len(item["testcases"]) == 1)
        dense_count = sum(1 for item in execution_paths if len(item["testcases"]) >= 2)
        return {
            "test_cases": stats["total_test_cases"],
            "execution_paths": len(execution_paths),
            "sparse_paths": sparse_count,
            "dense_paths": dense_count,
            "covered_lines": stats["covered_lines"],
        }


def diff_snapshot(before: dict, after: dict) -> dict:
    return {key: after[key] - before.get(key, 0) for key in after}


def print_snapshot(label: str, snapshot: dict) -> None:
    log(
        f"[*] {label}：测试用例={snapshot['test_cases']} "
        f"执行路径={snapshot['execution_paths']} "
        f"稀疏路径={snapshot['sparse_paths']} 稠密路径={snapshot['dense_paths']} 已覆盖行={snapshot['covered_lines']}"
    )


def gather_status(db_path: str, campaign_root: Path) -> dict:
    with CoverageDatabase(db_path) as db:
        execution_paths = db.get_execution_paths_summary()
        stats = db.get_coverage_statistics()
        sparse_paths = [item for item in execution_paths if len(item["testcases"]) == 1]
        dense_paths = [item for item in execution_paths if len(item["testcases"]) >= 2]

    recent_runs = []
    if campaign_root.exists():
        for summary_path in sorted(campaign_root.rglob("summary.json"), reverse=True)[:10]:
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            recent_runs.append({
                "path": str(summary_path.parent),
                "objective": summary.get("objective"),
                "best_path": summary.get("best_result", {}).get("path_hash", ""),
                "new_pool": len(summary.get("new_path_pool", [])),
                "sparse_pool": len(summary.get("sparse_path_pool", [])),
                "matched_sparse_pool": len(summary.get("matched_sparse_pool", [])),
            })

    return {
        "test_cases": stats["total_test_cases"],
        "execution_paths": len(execution_paths),
        "covered_lines": stats["covered_lines"],
        "sparse_path_count": len(sparse_paths),
        "dense_path_count": len(dense_paths),
        "top_sparse": [
            {"path_hash": item["path_hash"], "testcase": item["testcases"][0]}
            for item in sparse_paths[:10]
        ],
        "top_dense": [
            {"path_hash": item["path_hash"], "count": len(item["testcases"]), "sample": item["testcases"][:3]}
            for item in dense_paths[:10]
        ],
        "recent_runs": recent_runs,
    }


def select_ingest_candidates(run_dir: Path) -> list[Path]:
    summary_path = run_dir / "summary.json"
    if not summary_path.exists():
        return []

    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return []

    candidate_attempts: set[int] = set()
    for key in ("matched_sparse_pool", "new_path_pool", "sparse_path_pool"):
        for item in summary.get(key, []):
            attempt = item.get("attempt")
            if isinstance(attempt, int):
                candidate_attempts.add(attempt)

    best_result = summary.get("best_result", {})
    best_attempt = best_result.get("attempt")
    if isinstance(best_attempt, int) and best_result.get("path_status") in {"new", "sparse"}:
        candidate_attempts.add(best_attempt)

    selected: list[Path] = []
    for attempt in sorted(candidate_attempts):
        preferred = run_dir / f"{run_dir.name}_attempt_{attempt:02d}.o"
        legacy = run_dir / f"attempt_{attempt:02d}.o"
        if preferred.exists():
            selected.append(preferred)
        elif legacy.exists():
            selected.append(legacy)

    return selected


def prepare_ingest_directory(run_dir: Path, object_paths: list[Path]) -> Path:
    ingest_dir = run_dir / "_ingest_candidates"
    ingest_dir.mkdir(parents=True, exist_ok=True)

    for obj_path in object_paths:
        target_path = ingest_dir / obj_path.name
        if target_path.exists():
            target_path.unlink()
        shutil.copy2(obj_path, target_path)

    return ingest_dir


def print_status(status: dict) -> None:
    log(
        f"[*] 测试用例={status['test_cases']} 执行路径={status['execution_paths']} "
        f"已覆盖行={status['covered_lines']} 稀疏路径={status['sparse_path_count']} "
        f"稠密路径={status['dense_path_count']}"
    )
    log("[*] 稀疏路径 Top 列表：")
    for item in status["top_sparse"][:5]:
        log(f"    {item['path_hash']}  测试用例={item['testcase']}")
    log("[*] 稠密路径 Top 列表：")
    for item in status["top_dense"][:5]:
        log(f"    {item['path_hash']}  数量={item['count']}  样例={','.join(item['sample'])}")
    log("[*] 最近的 campaign 运行：")
    for item in status["recent_runs"][:5]:
        log(
            f"    {item['path']}  目标={item['objective']} 最佳路径={item['best_path']} "
            f"新路径池={item['new_pool']} 稀疏路径池={item['sparse_pool']} 命中目标稀疏路径池={item['matched_sparse_pool']}"
        )


def run_campaign(args) -> int:
    config = load_project_config(args.config)
    vm_config = VMConfig(
        ssh_key=args.ssh_key or config.vm_ssh_key,
        ssh_port=args.ssh_port or config.vm_ssh_port,
        ssh_host=args.ssh_host or config.vm_ssh_host,
        guest_mount_point=args.guest_mount_point or config.vm_guest_mount_point,
        guest_workdir=args.guest_workdir,
    )
    log("[*] 正在准备虚拟机挂载点和 guest 工作目录...")
    ensure_guest_mount(vm_config)
    guest_workdir = detect_guest_workdir(vm_config)
    log(f"[*] Guest 工作目录：{guest_workdir}")

    campaign_root = Path(args.output_root or config.agent_campaign_output_root).resolve()
    campaign_root.mkdir(parents=True, exist_ok=True)

    effective_objective = args.objective or config.agent_objective
    rounds_completed = 0
    while args.rounds == 0 or rounds_completed < args.rounds:
        rounds_completed += 1
        round_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = campaign_root / f"{effective_objective}_{rounds_completed:04d}_{round_tag}"
        run_dir.mkdir(parents=True, exist_ok=True)

        log(f"\n[*] ===== 第 {rounds_completed} 轮 Campaign =====")
        log(f"[*] 运行目录：{run_dir}")

        before = get_db_snapshot(config.db_path)
        print_snapshot("开始前", before)

        mutator_cmd = [
            "python3",
            "-u",
            "scripts/agent_mutator.py",
            "--provider",
            args.provider or config.agent_provider,
            "--model",
            args.model or config.agent_model,
            "--objective",
            effective_objective,
            "--max-iterations",
            str(args.max_iterations or config.agent_max_iterations),
            "--top-k",
            str(args.top_k or config.agent_top_k),
            "--nearby-budget",
            str(args.nearby_budget or config.agent_nearby_budget),
            "--output-dir",
            str(run_dir),
        ]
        log("[*] 正在启动 agent_mutator...")
        mutator_res = subprocess.run(mutator_cmd, text=True, env={**os.environ, "PYTHONUNBUFFERED": "1"})
        if mutator_res.returncode != 0:
            log(f"[!] 第 {rounds_completed} 轮中的 agent_mutator 执行失败，退出码为 {mutator_res.returncode}")
            if args.stop_on_error:
                return mutator_res.returncode
            time.sleep(args.sleep_seconds or config.agent_campaign_sleep_seconds)
            continue

        selected_objects = select_ingest_candidates(run_dir)
        if selected_objects:
            ingest_dir = prepare_ingest_directory(run_dir, selected_objects)
            rel_ingest_dir = to_repo_relative(ingest_dir)
            ingest_cmd = f"python3 main.py run -t {rel_ingest_dir} --path-type full -p"
            log(
                f"[*] 已筛选出 {len(selected_objects)} 个有价值的变异结果，"
                "正在通过 guest 侧流水线回灌到覆盖率数据库..."
            )
            ingest_res = run_guest_command(vm_config, ingest_cmd, workdir=guest_workdir)
            if ingest_res.stdout.strip():
                log(ingest_res.stdout.strip())
            if ingest_res.stderr.strip():
                log(ingest_res.stderr.strip())
        else:
            log("[*] 本轮未发现新的或稀疏的 full 路径结果，跳过数据库回灌。")

        after = get_db_snapshot(config.db_path)
        delta = diff_snapshot(before, after)
        print_snapshot("结束后", after)
        log(
            f"[*] 增量：测试用例={delta['test_cases']:+d} 执行路径={delta['execution_paths']:+d} "
            f"稀疏路径={delta['sparse_paths']:+d} "
            f"稠密路径={delta['dense_paths']:+d} 已覆盖行={delta['covered_lines']:+d}"
        )

        summary_path = run_dir / "summary.json"
        if summary_path.exists():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            log(
                f"[*] 本轮摘要：目标={summary.get('objective')} "
                f"最佳路径={summary.get('best_result', {}).get('path_hash', '')} "
                f"新路径池={len(summary.get('new_path_pool', []))} "
                f"稀疏路径池={len(summary.get('sparse_path_pool', []))} "
                f"命中目标稀疏路径池={len(summary.get('matched_sparse_pool', []))}"
            )

        live_status = gather_status(config.db_path, campaign_root)
        log("[*] 当前 campaign 窗口的实时状态：")
        print_status(live_status)

        if args.rounds != 0 and rounds_completed >= args.rounds:
            break

        time.sleep(args.sleep_seconds or config.agent_campaign_sleep_seconds)

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="持续运行路径引导式 agent，并将结果回灌到数据库")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--provider", default=None, help="可选，覆盖 config.agent_provider")
    parser.add_argument("--model", default=None, help="可选，覆盖 config.agent_model")
    parser.add_argument("--objective", default=None, choices=["enrich_sparse", "generate_new"])
    parser.add_argument("--max-iterations", type=int, default=None, help="可选，覆盖 config.agent_max_iterations")
    parser.add_argument("--top-k", type=int, default=None, help="可选，覆盖 config.agent_top_k")
    parser.add_argument("--nearby-budget", type=int, default=None, help="可选，覆盖 config.agent_nearby_budget")
    parser.add_argument("--rounds", type=int, default=0, help="0 表示持续运行")
    parser.add_argument("--sleep-seconds", type=int, default=None, help="可选，覆盖 config.agent_campaign_sleep_seconds")
    parser.add_argument("--output-root", default=None, help="可选，覆盖 config.agent_campaign_output_root")
    parser.add_argument("--stop-on-error", action="store_true")
    parser.add_argument("--ssh-key", default=None, help="可选，覆盖 config.vm_ssh_key")
    parser.add_argument("--ssh-port", type=int, default=None, help="可选，覆盖 config.vm_ssh_port")
    parser.add_argument("--ssh-host", default=None, help="可选，覆盖 config.vm_ssh_host")
    parser.add_argument("--guest-mount-point", default=None, help="可选，覆盖 config.vm_guest_mount_point")
    parser.add_argument("--guest-workdir", default=None, help="可选，覆盖自动探测到的 guest 工作目录")
    return parser


if __name__ == "__main__":
    parser = build_parser()
    raise SystemExit(run_campaign(parser.parse_args()))
