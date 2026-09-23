#!/bin/bash
# Paste this entire file into Linode StackScript OR a supported image's User Data.
# New Ubuntu/Debian servers only. Never wait for cloud-init from inside this script.

linode_retry() {
    local attempt
    for attempt in 1 2 3 4 5 6; do
        if "$@"; then
            return 0
        fi
        echo "Attempt $attempt/6 failed: $1" >&2
        if [[ "$attempt" -lt 6 ]]; then
            sleep 10
        fi
    done
    return 1
}

linode_download() {
    local url="$1" destination="$2"
    # Retry every curl failure, including DNS, on older curl versions too.
    # Always truncate between attempts; never execute a partial download.
    : > "$destination"
    curl --fail --show-error --location --connect-timeout 20 --max-time 300 \
        --output "$destination" "$url" && [[ -s "$destination" ]]
}

linode_check_target() {
    local state_dir="$1" db_file="$2" binary="$3"
    if [[ -f "$state_dir/complete" ]]; then
        if systemctl is-active --quiet x-ui; then
            echo "XRM-1 is already installed; leaving the database unchanged."
            return 10
        fi
        echo "ERROR: Installation was completed before, but x-ui is now inactive. Inspect the service; refusing to overwrite its database." >&2
        return 1
    fi
    if [[ -e "$db_file" || -e "$binary" ]]; then
        echo "ERROR: An existing or partial x-ui installation was found. This bootstrap is for new servers only; back up and diagnose the existing installation first." >&2
        return 1
    fi
}

linode_execute() {
    local install_file="$1"
    if [[ "$(head -c 2 "$install_file")" != '#!' ]]; then
        echo "ERROR: Download is not an executable script." >&2
        return 1
    fi
    bash -n "$install_file" || return 1
    # No pipe-to-shell, interactive prompts, or swallowed installer errors.
    bash -e "$install_file" </dev/null
}

linode_main() {
    set -Eeuo pipefail
    umask 077
    export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
    if [[ "$EUID" -ne 0 ]]; then
        echo "ERROR: Run as root." >&2
        return 1
    fi
    # shellcheck source=/dev/null
    . /etc/os-release
    case "$ID" in
        ubuntu|debian) ;;
        *) echo "ERROR: Select an Ubuntu or Debian Linode image." >&2; return 1 ;;
    esac
    if [[ ! -d /run/systemd/system ]] || ! command -v systemctl >/dev/null; then
        echo "ERROR: A booted systemd-based Linode image is required." >&2
        return 1
    fi

    local state_dir=/var/lib/xrm-1/linode
    local log_file=/var/log/xrm-linode.log
    local install_file="" stage=preflight target_status
    install -d -m 700 "$state_dir"
    touch "$log_file"
    chmod 600 "$log_file"
    exec > >(tee -a "$log_file") 2>&1
    trap 'rc=$?; echo "ERROR: Stage=$stage, line=$LINENO, exit=$rc. See /var/log/xrm-linode.log"; printf "failed: %s (exit %s)\n" "$stage" "$rc" > "$state_dir/status"; exit "$rc"' ERR
    # Variables remain alive while EXIT runs: linode_main calls exit below.
    trap '[[ -z "$install_file" ]] || rm -f -- "$install_file"' EXIT
    echo "[$(date -Is)] Starting XRM-1 Linode bootstrap."
    command -v flock >/dev/null || { echo "ERROR: util-linux (flock) is required."; exit 1; }
    exec 9>/run/lock/xrm-linode.lock
    if ! flock -n 9; then
        echo "ERROR: Another Linode bootstrap is already running."
        exit 1
    fi
    if linode_check_target "$state_dir" /etc/x-ui/x-ui.db /usr/local/x-ui/x-ui; then
        :
    else
        target_status=$?
        if [[ "$target_status" -eq 10 ]]; then exit 0; fi
        exit "$target_status"
    fi

    export DEBIAN_FRONTEND=noninteractive
    export NEEDRESTART_MODE=a
    export XUI_NONINTERACTIVE=1
    stage=network
    printf 'running: %s\n' "$stage" > "$state_dir/status"
    # Keep Linode's network/DNS configuration during bootstrap. No dependency
    # on systemd-resolved, which is absent on some Debian images.
    linode_retry getent ahosts raw.githubusercontent.com >/dev/null

    stage=packages
    printf 'running: %s\n' "$stage" > "$state_dir/status"
    # First boot may overlap apt-daily. Wait/retry; never remove package locks.
    linode_retry apt-get -o DPkg::Lock::Timeout=120 -o Acquire::Retries=5 update
    linode_retry apt-get -o DPkg::Lock::Timeout=120 -o Acquire::Retries=5 \
        install -y ca-certificates curl iproute2 procps util-linux cron tar tzdata socat openssl

    stage=download
    printf 'running: %s\n' "$stage" > "$state_dir/status"
    install_file="$(mktemp "$state_dir/installer.XXXXXX")"
    linode_retry linode_download \
        https://raw.githubusercontent.com/exirhub/xrm-1/main/install.sh "$install_file"
    stage=install
    printf 'running: %s\n' "$stage" > "$state_dir/status"
    # Do not pipe curl to bash or background the installer. Fail cloud-init /
    # StackScript when a required installer command fails.
    linode_execute "$install_file"

    stage=service
    printf 'running: %s\n' "$stage" > "$state_dir/status"
    linode_retry systemctl is-active --quiet x-ui
    [[ -s /etc/x-ui/x-ui.db ]]
    date -Is > "$state_dir/complete"
    printf 'complete\n' > "$state_dir/status"
    echo "[$(date -Is)] XRM-1 installed successfully. Log: $log_file"
    exit 0
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    linode_main "$@"
fi
