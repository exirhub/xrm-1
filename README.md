# XRM-1 Installer

Install **XRM-1** automatically using an OVHcloud Post-Installation Script, Cloud-Init, or manually through the terminal.

## OVHcloud Post-Installation Script (P-I-S)

Paste the complete Bash script below into the **Post-Installation Script (P-I-S)** section when creating an Ubuntu or Debian server on OVHcloud.

> [!IMPORTANT]
> This is an executable Bash script. Do not add `#cloud-config` or a `runcmd` section.

```bash
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

export DEBIAN_FRONTEND=noninteractive

INSTALL_URL="https://raw.githubusercontent.com/exirhub/xrm-1/main/install.sh"
INSTALL_FILE="/root/install.sh"
LOG_FILE="/var/log/xrm-post-install.log"

exec > >(tee -a "$LOG_FILE") 2>&1

trap 'echo "[ERROR] Installation failed at line ${LINENO}, exit code: $?"' ERR

echo "[$(date -Is)] Starting XRM post-installation..."

apt-get -o Acquire::Retries=5 update
apt-get -o Acquire::Retries=5 install -y \
    ca-certificates \
    curl \
    wget

for attempt in {1..10}; do
    echo "Downloading installer - attempt ${attempt}/10"

    if curl \
        --fail \
        --silent \
        --show-error \
        --location \
        --connect-timeout 20 \
        "$INSTALL_URL" \
        --output "$INSTALL_FILE"; then
        break
    fi

    if [[ "$attempt" -eq 10 ]]; then
        echo "Unable to download installer."
        exit 1
    fi

    sleep 5
done

if [[ ! -s "$INSTALL_FILE" ]]; then
    echo "Downloaded installer is empty."
    exit 1
fi

# Fix the invalid //EDIT BY line in the current installer
sed -i 's|^//EDIT BY:|# EDIT BY:|' "$INSTALL_FILE"

chmod 700 "$INSTALL_FILE"

cd /root
/bin/bash "$INSTALL_FILE"

if systemctl is-active --quiet x-ui; then
    echo "[$(date -Is)] XRM installation completed successfully."
else
    echo "Installation completed, but the x-ui service is not active."
    systemctl status x-ui --no-pager || true
    exit 1
fi
```

After the server has started, inspect the installation log and service status with:

```bash
tail -f /var/log/xrm-post-install.log
systemctl status x-ui --no-pager
```

## Automatic Installation with Cloud-Init

Paste the following configuration into the **Cloud-Init / User Data** section when creating your server:

```yaml
#cloud-config

runcmd:
  - cd /root
  - wget -q https://raw.githubusercontent.com/exirhub/xrm-1/main/install.sh -O install.sh
  - chmod +x install.sh
  - bash install.sh
```

## Manual Installation

Run the following command as the `root` user:

```bash
cd /root && \
wget -q https://raw.githubusercontent.com/exirhub/xrm-1/main/install.sh -O install.sh && \
chmod +x install.sh && \
bash install.sh
```

## One-Line Installation

```bash
wget -qO- https://raw.githubusercontent.com/exirhub/xrm-1/main/install.sh | bash
```

> [!IMPORTANT]
> Run the installer with `root` privileges on a newly created server.

## Requirements

* Ubuntu or Debian-based server
* Root access
* Active internet connection

## Repository

```text
https://github.com/exirhub/xrm-1
```
