import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from utils.config import Config

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SSH_KEY = Path.home() / "workspace/image/bookworm.id_rsa"


@dataclass
class VMConfig:
    ssh_key: str = str(DEFAULT_SSH_KEY)
    ssh_port: int = 10086
    ssh_host: str = "root@127.0.0.1"
    guest_mount_point: str = "/mnt/root"
    guest_workdir: Optional[str] = None


def vm_config_from_project_config(config: Config, guest_workdir: Optional[str] = None) -> VMConfig:
    return VMConfig(
        ssh_key=config.vm_ssh_key,
        ssh_port=config.vm_ssh_port,
        ssh_host=config.vm_ssh_host,
        guest_mount_point=config.vm_guest_mount_point,
        guest_workdir=guest_workdir,
    )


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _run_local(command: list[str], check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, check=check)


def ssh_base_command(vm: VMConfig) -> list[str]:
    return [
        "ssh",
        "-q",
        "-o",
        "StrictHostKeyChecking=no",
        "-i",
        vm.ssh_key,
        "-p",
        str(vm.ssh_port),
        vm.ssh_host,
    ]


def ensure_guest_mount(vm: VMConfig) -> str:
    mount_point = vm.guest_mount_point
    guest_script = (
        f"mkdir -p {_shell_quote(mount_point)} && "
        f"(mountpoint -q {_shell_quote(mount_point)} || mount -t virtiofs hostshare {_shell_quote(mount_point)})"
    )
    result = _run_local(ssh_base_command(vm) + [guest_script])
    if result.returncode != 0:
        raise RuntimeError(f"Failed to mount host share inside VM: {result.stderr.strip()}")
    return mount_point


def detect_guest_workdir(vm: VMConfig) -> str:
    if vm.guest_workdir:
        return vm.guest_workdir

    mount_point = ensure_guest_mount(vm)
    candidates = [
        mount_point,
        f"{mount_point}/{REPO_ROOT.name}",
        "/mnt/hostshare",
        f"/mnt/hostshare/{REPO_ROOT.name}",
    ]
    for candidate in candidates:
        guest_script = (
            f"if [ -f {_shell_quote(candidate + '/main.py')} ] && "
            f"[ -f {_shell_quote(candidate + '/kcov_runner')} ]; then "
            f"printf '%s' {_shell_quote(candidate)}; fi"
        )
        result = _run_local(ssh_base_command(vm) + [guest_script])
        if result.returncode == 0 and result.stdout.strip():
            vm.guest_workdir = result.stdout.strip()
            return vm.guest_workdir

    raise RuntimeError(
        "Could not detect project directory inside VM. "
        f"Tried: {', '.join(candidates)}"
    )


def run_guest_command(vm: VMConfig, command: str, workdir: Optional[str] = None) -> subprocess.CompletedProcess:
    guest_workdir = workdir or detect_guest_workdir(vm)
    guest_script = f"cd {_shell_quote(guest_workdir)} && {command}"
    return _run_local(ssh_base_command(vm) + [guest_script])


def to_repo_relative(path: Path) -> str:
    return os.path.relpath(path.resolve(), REPO_ROOT)
