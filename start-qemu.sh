#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="${WORKSPACE:-$HOME/workspace}"
KERNEL="${KERNEL:-$WORKSPACE/linux/arch/x86/boot/bzImage}"
IMAGE="${IMAGE:-$WORKSPACE/image/bookworm.img}"
SSH_KEY="${SSH_KEY:-$WORKSPACE/image/bookworm.id_rsa}"
SHARE_PATH="${SHARE_PATH:-$WORKSPACE/../ver_kcov}"
SSH_PORT="${SSH_PORT:-10086}"
QEMU_PIDFILE="${QEMU_PIDFILE:-$WORKSPACE/vm.pid}"
SHARE_TAG="${SHARE_TAG:-hostshare}"
MEMORY="${MEMORY:-16G}"
SMP="${SMP:-8}"
VIRTIOFS_SOCKET="${VIRTIOFS_SOCKET:-/tmp/qemu-opt-virtiofs.sock}"
VIRTIOFSD_SANDBOX="${VIRTIOFSD_SANDBOX:-chroot}"
VIRTIOFSD_CACHE="${VIRTIOFSD_CACHE:-always}"
VIRTIOFSD_USE_SUDO="${VIRTIOFSD_USE_SUDO:-1}"

find_virtiofsd() {
    local candidate
    for candidate in \
        "${VIRTIOFSD:-}" \
        "$(command -v virtiofsd 2>/dev/null || true)" \
        /usr/libexec/virtiofsd \
        /usr/lib/qemu/virtiofsd \
        /usr/lib/virtiofsd
    do
        if [[ -n "${candidate}" && -x "${candidate}" ]]; then
            printf '%s\n' "${candidate}"
            return 0
        fi
    done

    return 1
}

VIRTIOFSD_BIN="$(find_virtiofsd)" || {
    echo "virtiofsd not found. Please install it or set VIRTIOFSD=/path/to/virtiofsd" >&2
    exit 1
}

if [[ ! -f "${KERNEL}" ]]; then
    echo "Kernel image not found: ${KERNEL}" >&2
    exit 1
fi

if [[ ! -f "${IMAGE}" ]]; then
    echo "Disk image not found: ${IMAGE}" >&2
    exit 1
fi

if [[ ! -d "${SHARE_PATH}" ]]; then
    echo "Shared directory not found: ${SHARE_PATH}" >&2
    exit 1
fi

run_virtiofsd() {
    if [[ "${VIRTIOFSD_USE_SUDO}" == "1" ]]; then
        sudo "${VIRTIOFSD_BIN}" "$@"
    else
        "${VIRTIOFSD_BIN}" "$@"
    fi
}

cleanup() {
    if [[ -n "${VIRTIOFSD_PID:-}" ]] && kill -0 "${VIRTIOFSD_PID}" 2>/dev/null; then
        if [[ "${VIRTIOFSD_USE_SUDO}" == "1" ]]; then
            sudo kill "${VIRTIOFSD_PID}" 2>/dev/null || true
        else
            kill "${VIRTIOFSD_PID}" 2>/dev/null || true
        fi
        wait "${VIRTIOFSD_PID}" 2>/dev/null || true
    fi
    if [[ -S "${VIRTIOFS_SOCKET}" ]]; then
        if [[ "${VIRTIOFSD_USE_SUDO}" == "1" ]]; then
            sudo rm -f "${VIRTIOFS_SOCKET}" 2>/dev/null || true
        else
            rm -f "${VIRTIOFS_SOCKET}" 2>/dev/null || true
        fi
    fi
}

trap cleanup EXIT INT TERM

if [[ -S "${VIRTIOFS_SOCKET}" ]]; then
    if [[ "${VIRTIOFSD_USE_SUDO}" == "1" ]]; then
        sudo rm -f "${VIRTIOFS_SOCKET}"
    else
        rm -f "${VIRTIOFS_SOCKET}"
    fi
fi

run_virtiofsd \
    --socket-path="${VIRTIOFS_SOCKET}" \
    -o "source=${SHARE_PATH}" \
    -o "sandbox=${VIRTIOFSD_SANDBOX}" \
    -o "cache=${VIRTIOFSD_CACHE}" \
    &
VIRTIOFSD_PID=$!

until [[ -S "${VIRTIOFS_SOCKET}" ]]; do
    sleep 0.1
done

if [[ "${VIRTIOFSD_USE_SUDO}" == "1" ]]; then
    sudo chmod 666 "${VIRTIOFS_SOCKET}"
fi

cd "${WORKSPACE}"

if [[ -f "${SSH_KEY}" ]]; then
    cat <<EOF
Starting QEMU...
After the guest boots, connect with:
ssh -q -i "${SSH_KEY}" -p "${SSH_PORT}" -o 'StrictHostKeyChecking no' root@127.0.0.1

Inside the guest, mount the shared directory with:
mount -t virtiofs "${SHARE_TAG}" /mnt/hostshare

EOF
fi

sudo qemu-system-x86_64 \
    -enable-kvm \
    -machine q35,accel=kvm \
    -cpu host,migratable=off \
    -m "${MEMORY}" \
    -smp "${SMP}",sockets=1,cores="${SMP}",threads=1 \
    -object memory-backend-memfd,id=mem,size="${MEMORY}",share=on \
    -numa node,memdev=mem \
    -kernel "${KERNEL}" \
    -append "console=ttyS0 root=/dev/vda rootfstype=ext4 earlyprintk=serial net.ifnames=0" \
    -device virtio-rng-pci \
    -object iothread,id=iothread0 \
    -blockdev driver=file,filename="${IMAGE}",node-name=file0,aio=io_uring,cache.direct=on,cache.no-flush=off \
    -blockdev driver=raw,file=file0,node-name=drive0 \
    -device virtio-blk-pci,drive=drive0,iothread=iothread0,queue-size=1024 \
    -chardev socket,id=char0,path="${VIRTIOFS_SOCKET}" \
    -device vhost-user-fs-pci,chardev=char0,tag="${SHARE_TAG}" \
    -netdev user,id=net0,hostfwd=tcp:127.0.0.1:${SSH_PORT}-:22 \
    -device virtio-net-pci,netdev=net0,mq=on,vectors=10 \
    -nographic \
    -pidfile "${QEMU_PIDFILE}"
