#!/usr/bin/env python3
"""
对比当前 kcov_runner 和原始版 kcov_runner_legacy 在 guest 中的加载能力。
"""
import argparse
import shlex
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.vm_utils import ssh_base_command, vm_config_from_project_config
from utils.config import DEFAULT_CONFIG_PATH, load_project_config


REPRESENTATIVE_MID_SEEDS = [
    4, 119, 124, 171, 176, 220, 269, 283, 301,
    321, 339, 340, 343, 345, 418, 431, 466, 499,
]


def guest_compare(vm, objects: list[str]) -> str:
    quoted_objects = " ".join(shlex.quote(obj) for obj in objects)
    guest_script = f"""
cd /mnt/root || exit 1
export LD_LIBRARY_PATH=/mnt/root
compare_one() {{
  bin="$1"
  obj="$2"
  out="/tmp/$(basename "$bin")_$(basename "$obj").txt"
  rm -f "$out"
  "$bin" "$obj" -o "$out" >/tmp/compare_stdout.txt 2>/tmp/compare_stderr.txt
  rc=$?
  if [ -f "$out" ]; then
    lines=$(wc -l < "$out")
  else
    lines=0
  fi
  printf '%s' "$lines:$rc"
}}
for obj in {quoted_objects}; do
  old=$(compare_one ./kcov_runner_legacy "$obj")
  new=$(compare_one ./kcov_runner "$obj")
  printf '%s %s %s\\n' "$(basename "$obj")" "$old" "$new"
done
"""
    result = subprocess.run(
        ssh_base_command(vm) + [guest_script],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "guest compare failed")
    return result.stdout


def parse_output(output: str) -> list[dict]:
    rows = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        name, old_text, new_text = line.split()
        old_lines, old_rc = [int(x) for x in old_text.split(":")]
        new_lines, new_rc = [int(x) for x in new_text.split(":")]
        rows.append({
            "name": name,
            "old_lines": old_lines,
            "old_rc": old_rc,
            "new_lines": new_lines,
            "new_rc": new_rc,
            "old_hit": old_lines > 0,
            "new_hit": new_lines > 0,
        })
    return rows


def print_summary(title: str, rows: list[dict]) -> None:
    improved = [r for r in rows if not r["old_hit"] and r["new_hit"]]
    regressed = [r for r in rows if r["old_hit"] and not r["new_hit"]]
    same_hit = [r for r in rows if r["old_hit"] and r["new_hit"]]
    same_miss = [r for r in rows if not r["old_hit"] and not r["new_hit"]]

    print(f"\n[{title}]")
    print(
        f"legacy_hit={sum(r['old_hit'] for r in rows)}/{len(rows)}  "
        f"new_hit={sum(r['new_hit'] for r in rows)}/{len(rows)}  "
        f"improved={len(improved)}  regressed={len(regressed)}"
    )

    for row in rows:
        marker = "="
        if not row["old_hit"] and row["new_hit"]:
            marker = "+"
        elif row["old_hit"] and not row["new_hit"]:
            marker = "-"
        print(
            f"{marker} {row['name']:>8}  "
            f"legacy={row['old_lines']:>7} (rc={row['old_rc']})  "
            f"new={row['new_lines']:>7} (rc={row['new_rc']})"
        )

    if improved:
        print("improved:", ", ".join(r["name"] for r in improved))
    if regressed:
        print("regressed:", ", ".join(r["name"] for r in regressed))
    if same_miss:
        print("still_miss:", ", ".join(r["name"] for r in same_miss))
    if same_hit:
        print("same_hit:", ", ".join(r["name"] for r in same_hit))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--mini-only", action="store_true")
    parser.add_argument("--all-mid", action="store_true")
    args = parser.parse_args()

    config = load_project_config(args.config)
    vm = vm_config_from_project_config(config, guest_workdir="/mnt/root")

    mini_objects = [str(p) for p in sorted(Path("mini-seeds").glob("*.o"))]
    mini_rows = parse_output(guest_compare(vm, mini_objects))
    print_summary("mini-seeds", mini_rows)

    if not args.mini_only:
        if args.all_mid:
            mid_objects = [str(p) for p in sorted(Path("mid-seeds").glob("*.o"))]
            title = "all-mid-seeds"
        else:
            mid_objects = [
                f"mid-seeds/{seed}.o"
                for seed in REPRESENTATIVE_MID_SEEDS
                if Path(f"mid-seeds/{seed}.o").exists()
            ]
            title = "representative-mid-seeds"
        mid_rows = parse_output(guest_compare(vm, mid_objects))
        print_summary(title, mid_rows)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
