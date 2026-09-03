# XRM-1 Installer

Install **XRM-1** automatically using an OVHcloud Post-Installation Script, Cloud-Init, or manually through the terminal.

The installer configures persistent fallback DNS resolvers before accessing GitHub. This prevents DNS failures when cloud-init or package upgrades restart `systemd-resolved`.

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
DNS_FILE="/etc/systemd/resolved.conf.d/99-exir-dns.conf"

exec > >(tee -a "$LOG_FILE") 2>&1
trap 'echo "[ERROR] Installation failed at line ${LINENO}, exit code: $?"' ERR

echo "[$(date -Is)] Starting XRM post-installation..."

install -d -m 755 /etc/systemd/resolved.conf.d
cat > "$DNS_FILE" <<'EOF'
[Resolve]
DNS=1.1.1.1 8.8.8.8
FallbackDNS=9.9.9.9 8.8.4.4
EOF

systemctl restart systemd-resolved || true
resolvectl flush-caches 2>/dev/null || true

for attempt in {1..10}; do
    if getent ahostsv4 raw.githubusercontent.com >/dev/null 2>&1; then
        break
    fi

    if [[ "$attempt" -eq 10 ]]; then
        echo "Unable to resolve raw.githubusercontent.com."
        exit 1
    fi

    echo "Waiting for DNS - attempt ${attempt}/10"
    sleep 5
done

apt-get -o Acquire::Retries=5 update
apt-get -o Acquire::Retries=5 install -y ca-certificates curl

curl --fail --location \
    --retry 10 --retry-all-errors --retry-delay 3 \
    --connect-timeout 20 --max-time 300 \
    "$INSTALL_URL" --output "$INSTALL_FILE"

if [[ ! -s "$INSTALL_FILE" ]]; then
    echo "Downloaded installer is empty."
    exit 1
fi

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

write_files:
  - path: /etc/systemd/resolved.conf.d/99-exir-dns.conf
    permissions: "0644"
    content: |
      [Resolve]
      DNS=1.1.1.1 8.8.8.8
      FallbackDNS=9.9.9.9 8.8.4.4

runcmd:
  - systemctl restart systemd-resolved
  - apt-get -o Acquire::Retries=5 update
  - apt-get -o Acquire::Retries=5 install -y ca-certificates curl
  - curl -fL --retry 10 --retry-all-errors --retry-delay 3 https://raw.githubusercontent.com/exirhub/xrm-1/main/install.sh -o /root/install.sh
  - chmod 700 /root/install.sh
  - bash /root/install.sh
```

## Manual Installation

Run the following block as the `root` user. It configures DNS before downloading the installer:

```bash
install -d -m 755 /etc/systemd/resolved.conf.d

printf '%s\n' \
  '[Resolve]' \
  'DNS=1.1.1.1 8.8.8.8' \
  'FallbackDNS=9.9.9.9 8.8.4.4' \
  > /etc/systemd/resolved.conf.d/99-exir-dns.conf

systemctl restart systemd-resolved

cd /root
curl -fL --retry 10 --retry-all-errors --retry-delay 3 \
  https://raw.githubusercontent.com/exirhub/xrm-1/main/install.sh \
  -o install.sh
chmod 700 install.sh
bash install.sh
```

## One-Line Installation

Use this command on a server where DNS already resolves GitHub:

```bash
curl -fL --retry 10 --retry-all-errors https://raw.githubusercontent.com/exirhub/xrm-1/main/install.sh | bash
```

> [!IMPORTANT]
> Run the installer with `root` privileges on a newly created server.

## Requirements

- Ubuntu or Debian-based server
- Root access
- Active internet connection
- Access to `github.com`, `api.github.com`, and `raw.githubusercontent.com`

## Repository

```text
https://github.com/exirhub/xrm-1
```
