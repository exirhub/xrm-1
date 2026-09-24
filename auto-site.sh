#!/usr/bin/env bash
# Called automatically by install.sh; can also upgrade an existing installation.
set -Eeuo pipefail
umask 077
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
[[ "$EUID" == 0 ]] || { echo 'Run as root.' >&2; exit 1; }
command -v flock >/dev/null || { echo 'util-linux is required.' >&2; exit 1; }
exec 8>/run/lock/xrm-auto-site.lock
flock -n 8 || { echo 'Website setup is already running.' >&2; exit 1; }
touch /var/log/xrm-site-install.log
chmod 600 /var/log/xrm-site-install.log
exec > >(tee -a /var/log/xrm-site-install.log) 2>&1
created_policy=0
stage=dependencies
cleanup() {
    if [[ "$created_policy" == 1 ]]; then rm -f /usr/sbin/policy-rc.d; fi
}
trap cleanup EXIT
trap 'rc=$?; echo "ERROR: automatic website setup failed at $stage (exit $rc). See /var/log/xrm-site-install.log"; exit "$rc"' ERR
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l
nginx_present=0
command -v nginx >/dev/null && nginx_present=1
need_packages=0
for cmd in nginx python3 openssl ss curl; do
    command -v "$cmd" >/dev/null || need_packages=1
done
if [[ "$need_packages" == 1 ]]; then
    # Prevent the package's default web server from racing Xray for port 80.
    # Never replace an administrator's existing package-service policy.
    if [[ ! -e /usr/sbin/policy-rc.d && ! -L /usr/sbin/policy-rc.d ]]; then
        printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d
        chmod 755 /usr/sbin/policy-rc.d
        created_policy=1
    fi
    apt-get -o DPkg::Lock::Timeout=120 -o Acquire::Retries=5 update
    apt-get -o DPkg::Lock::Timeout=120 -o Acquire::Retries=5 install -y nginx python3 openssl iproute2 ca-certificates curl
    if [[ "$nginx_present" == 0 ]]; then systemctl disable nginx.service; fi
    cleanup
    created_policy=0
fi
for cmd in python3 nginx openssl ss curl; do command -v "$cmd" >/dev/null; done
stage=download
bundle=/usr/local/lib/xrm-site
install -d -m 755 "$bundle" "$bundle/website"
fetch() {
    local url="$1" destination="$2" attempt
    for attempt in 1 2 3 4 5 6; do
        if curl -fSL --connect-timeout 20 --max-time 180 "$url" -o "$destination.part" && [[ -s "$destination.part" ]]; then
            mv "$destination.part" "$destination"
            return 0
        fi
        rm -f "$destination.part"
        [[ "$attempt" == 6 ]] || sleep 3
    done
    return 1
}
# Resolve once so all downloaded components belong to the same revision.
fetch https://api.github.com/repos/exirhub/xrm-1/commits/main "$bundle/revision.json"
revision="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["sha"])' "$bundle/revision.json")"
[[ "$revision" =~ ^[a-f0-9]{40}$ ]]
for file in site_gateway.py website/index.html website/styles.css website/favicon.svg; do
    fetch "https://raw.githubusercontent.com/exirhub/xrm-1/$revision/$file" "$bundle/$file"
done
stage=migration
python3 "$bundle/site_gateway.py" --auto --apply
stage=renewal-watch
cat > /etc/systemd/system/xrm-site-tls.service <<'EOF'
[Unit]
Description=Reload XRM gateway when existing certificate files renew
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /usr/local/lib/xrm-site/site_gateway.py --refresh-tls
EOF
cat > /etc/systemd/system/xrm-site-tls.timer <<'EOF'
[Unit]
Description=Check existing XRM TLS certificates hourly
[Timer]
OnBootSec=5min
OnUnitActiveSec=1h
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now xrm-site-tls.timer
python3 "$bundle/site_gateway.py" --refresh-tls
systemctl is-active --quiet x-ui xrm-site
echo 'XRM website installation complete: HTTP 80, HTTPS 443/2083. No separate website setup required.'
