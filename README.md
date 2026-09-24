# XRM-1 Installer

Install **XRM-1** automatically using Linode User Data/StackScript, an OVHcloud Post-Installation Script, Cloud-Init, or manually through the terminal.

The installer configures persistent fallback DNS resolvers before accessing GitHub. This prevents DNS failures when cloud-init or package upgrades restart `systemd-resolved`.

The Node.js/PM2 file-upload receiver (`server.js`, port `3000`) is no longer installed. XRM-1 does not install its npm dependencies or start the upload server.

This change applies to new installer runs. It does not stop or uninstall Node.js, PM2, or the upload server on previously configured servers.

## Linode / Akamai Cloud (recommended for Linode)

Use the complete contents of [linode.sh](linode.sh), starting with
`#!/bin/bash`. It is a standalone bootstrap; it installs its own dependencies
before downloading the shared XRM installer.

### Option A: User Data / cloud-init

1. Create a **new** Linode using an Ubuntu or Debian image with the **cloud-init
   support icon** in Cloud Manager. Not every image accepts User Data.
2. Open **Add User Data** and paste the **entire plain-text contents of
   `linode.sh`**. Do not paste just its URL, a Markdown code fence, or wrap it
   in `#cloud-config`. Linode accepts a Bash user-data script with a shebang.
3. Create the instance and allow the first-boot installer to finish.
   User Data runs on initial provisioning, not on every reboot.
4. If using the Linode API/CLI instead of Cloud Manager, Base64-encode the
   contents into `metadata.user_data`; Cloud Manager expects plain text.

### Option B: StackScript (when User Data is unavailable)

Create a private **StackScript**, select an Ubuntu or Debian target image,
paste all of `linode.sh` into its script field, save, then use
**Deploy New Linode** from that StackScript. StackScripts execute shell code:
do **not** paste the YAML Cloud-Init example below into that field.

Choose **one** of these methods per instance, not both. A lock also prevents
two copies of this Linode bootstrap from running concurrently.

### Logs and troubleshooting

From SSH or the Linode Lish console, run:

```bash
sudo tail -n 100 /var/log/xrm-linode.log
sudo cat /var/lib/xrm-1/linode/status
sudo systemctl status x-ui --no-pager
sudo journalctl -u x-ui -n 100 --no-pager
# User Data path only (run after boot, not from inside user-data):
sudo cloud-init status --long
sudo tail -n 100 /var/log/cloud-init-output.log
```

If `xrm-linode.log` does not exist, the bootstrap likely has not started:
check the chosen image's cloud-init support, the first `#!/bin/bash` line,
and whether the script was actually attached when the instance was created.
If the log exists, the recorded stage (`network`, `packages`, `download`,
`install`, or `service`) identifies where it stopped. Logs can contain panel
credentials printed by the upstream installer; redact them before sharing.

The bootstrap retries early DNS/download failures, waits/retries around APT
locks without deleting them, works without `systemd-resolved` during bootstrap,
and does not depend on curl's newer `--retry-all-errors` option. It reports
success only after the shared installer succeeds, the database exists, and
`x-ui` is active. This is a service check, not an external proxy connectivity test.

> [!WARNING]
> New servers only: XRM installs this repository's `x-ui.db`.
> The shared installer changes DNS, disables UFW, and configures swap/network
> settings. Restrict panel access with the Linode Cloud Firewall and change
> imported credentials. The bootstrap does not configure that cloud firewall.
> It refuses to overwrite an existing or partially installed X-UI deployment.
> Re-running after a successful installation is a no-op while X-UI remains active.
> If installation partly failed, inspect and back up the existing database;
> do not delete it merely to bypass this guard.

Akamai documentation:
[User Data](https://techdocs.akamai.com/cloud-computing/docs/add-user-data-when-deploying-a-compute-instance),
[Metadata / supported images](https://techdocs.akamai.com/cloud-computing/docs/overview-of-the-metadata-service),
[StackScripts](https://techdocs.akamai.com/cloud-computing/docs/getting-started-with-stackscripts).

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

# Bypass a stale 127.0.0.53 stub that can remain after network service restarts.
if [[ -e /etc/resolv.conf && ! -e /etc/resolv.conf.exir-backup ]]; then
    cp -L /etc/resolv.conf /etc/resolv.conf.exir-backup || true
fi
RESOLV_TMP="$(mktemp)"
printf '%s\n' \
    'nameserver 1.1.1.1' \
    'nameserver 8.8.8.8' \
    'nameserver 9.9.9.9' \
    'options timeout:2 attempts:3' > "$RESOLV_TMP"
chmod 644 "$RESOLV_TMP"
cp --remove-destination "$RESOLV_TMP" /etc/resolv.conf
rm -f "$RESOLV_TMP"

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
  - cp -L /etc/resolv.conf /etc/resolv.conf.exir-backup
  - printf 'nameserver 1.1.1.1\nnameserver 8.8.8.8\nnameserver 9.9.9.9\noptions timeout:2 attempts:3\n' > /run/xrm-resolv.conf
  - cp --remove-destination /run/xrm-resolv.conf /etc/resolv.conf
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

cp -L /etc/resolv.conf /etc/resolv.conf.exir-backup 2>/dev/null || true
printf '%s\n' \
  'nameserver 1.1.1.1' \
  'nameserver 8.8.8.8' \
  'nameserver 9.9.9.9' \
  'options timeout:2 attempts:3' > /run/xrm-resolv.conf
cp --remove-destination /run/xrm-resolv.conf /etc/resolv.conf

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

## Automatic shared website

The normal installer now installs the Nava website and nginx gateway automatically
on HTTP 80 and HTTPS 443/2083. No separate website command or certificate arguments
are needed with the shipped XRM database. Only the 2083 XHTTP path changes to
`/api/v1/sync`; the old route remains an alias and 80/443 paths stay unchanged.
Existing per-port TLS identities are reused, with automatic reload for renewed
file-based certificates. See [details and rollback](docs/site-gateway.md).

Failures are logged to `/var/log/xrm-site-install.log` and cause installation to
fail instead of silently claiming success. This does not provision new DNS or
trusted certificates for unrelated domains. Existing servers are not changed
until the new bootstrap is run; do not rerun install.sh on an existing server.
