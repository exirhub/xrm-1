# Shared website on 80, 443 and 2083

This is an **opt-in migration for an existing XRM deployment**, not a replacement
for install.sh. It never imports the repository's sample database. The initial
XRM installer is unchanged. Keep an SSH session open during migration.

The bundled static Nava website is adapted from exirhub/test. It has no signup
form or artificial visitor counters and needs no Node.js runtime. The same site
is served over HTTP on 80 and HTTPS on 443/2083.

## Routing contract

- Only the enabled XHTTP inbound originally on **2083** gets the new path
  `/api/v1/sync`. Its old path remains an nginx alias, rewriting session suffixes
  to the new path. New clients can use the new path; old clients keep working.
- Existing paths on **80 and 443 are unchanged**. The new alias is not added there.
- Public ports stay unchanged. Supported listeners on these three ports move to
  allocated loopback ports in 32080–33079. TLS terminates at nginx on 443/2083.
  Credentials, client accounting, inbound IDs and inbound tags are preserved.
- The repository database currently uses WS on 443, packet-up XHTTP on 80,
  XHTTP on 2083, and the panel on 8144. An unaffected panel stays on its port.
- If the panel uses a target public port, it moves to loopback and retains its
  existing non-root base path through nginx. A panel rooted at `/` is rejected.
- HTTP/1 XHTTP uses unbuffered proxying; HTTP/2 uses grpc_pass. WebSocket upgrade
  headers are preserved. The Xray stream's TLS/finalmask and listener sockopt
  settings are removed on these internal listeners because nginx owns the edge.
- REALITY, raw TCP and unsupported transports, root/unsafe paths, incompatible
  TLS/port combinations and unrelated port owners cause a preflight failure.
  These require a separate design; the script will not kill their processes.

This does not fix multi-origin XHTTP session affinity. A normal site also does
not guarantee how a CDN classifies or handles proxy traffic.

## Install dependencies and obtain the checkout

Use the reviewed checkout containing `setup-site.sh`, `site_gateway.py`, and
`website/`. Do not run the general XRM installer again on an existing server.

On a server without nginx, install it without automatically starting its default
site on port 80. Respect any existing policy-rc.d instead of overwriting it:

```bash
sudo bash <<'SH'
set -e
created=0
cleanup() { if [ "$created" = 1 ]; then rm -f /usr/sbin/policy-rc.d; fi; }
trap cleanup EXIT
if [ ! -e /usr/sbin/policy-rc.d ]; then
  printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d
  chmod 755 /usr/sbin/policy-rc.d
  created=1
fi
apt-get update
apt-get install -y python3 nginx openssl iproute2
SH
```

An existing nginx/Apache service on the requested ports is **not automatically
stopped or overwritten**. Integrate that installation separately first.

## Plan and apply

Supply the actual public hostname and an existing valid certificate/key. A
Cloudflare Origin CA certificate is suitable only when clients access through
Cloudflare; direct browsers need a publicly trusted certificate. The certificate
must cover the hostname used by clients. This script does not issue certificates.

```bash
sudo bash setup-site.sh \
  --domain shopdelivery.store \
  --cert /path/to/fullchain.pem --key /path/to/privkey.pem

# Same command with --apply executes the reviewed plan:
sudo bash setup-site.sh \
  --domain shopdelivery.store \
  --cert /path/to/fullchain.pem --key /path/to/privkey.pem --apply
```

If your client hostname is www.shopdelivery.store, use that hostname instead.
All public hostnames must be covered by the supplied certificate. Existing
alternative SNI names/certificates are not automatically imported. Verify this
before migrating a server serving several hostnames.

The script validates nginx first, stops x-ui, takes a consistent SQLite backup,
updates the stopped database, starts x-ui and a dedicated **xrm-site.service**,
and checks all loopback listeners and website responses on all three ports.
Runtime failure triggers automatic database rollback. There is a brief service
interruption. The sample x-ui.db and the upstream installer are never modified.

Test WS on 443, the original XHTTP route on 80, and **both old and new paths** on
2083 from a real client, including upload/download, before repeating on other
servers. Local website/listener checks are not proof of successful proxy data
transfer. Full Xray/Cloudflare integration must be tested on the deployment.

The panel will show the internal listener ports after migration. Its generated
share links must be adjusted to the original public port/TLS settings; do not
publish loopback ports to users. Existing manually stored Exir profiles retain
their public connection details. This version does not rewrite generated panel
subscription links or automatically migrate future inbounds.

## Operations and rollback

```bash
sudo systemctl status xrm-site x-ui --no-pager
sudo tail -n 100 /var/log/xrm-site-error.log
sudo journalctl -u xrm-site -n 100 --no-pager
sudo python3 site_gateway.py --rollback
```

Rollback restores the **whole pre-migration database**, including accounting and
panel settings at that time. Use it before making subsequent panel edits; later
edits/accounting would be lost. The backup remains at
`/var/lib/xrm-1/site-gateway/before.db` (root-only directory). Re-running apply
refuses to overwrite existing gateway state or this backup. After a failed run,
inspect the retained state; do not delete the backup to bypass the guard.

Certificates are copied into `/etc/xrm-site/fullchain.pem` and `privkey.pem`.
Include these paths in your renewal deployment hook, then run:

```bash
sudo nginx -t -c /etc/xrm-site/nginx.conf && sudo systemctl reload xrm-site
```

The dedicated config is `/etc/xrm-site/nginx.conf`; unrelated nginx configs are
not loaded by this service. Access logging is disabled. Existing firewall rules
are not changed. Only 80/443/2083 need public access for the site; retain access
rules for the existing management port.
