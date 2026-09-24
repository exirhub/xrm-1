#!/usr/bin/env python3
"""Opt-in XRM website gateway. Plan first; apply with stopped-DB backup/rollback."""
import argparse
import copy
import http.client
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import ssl
import subprocess
import time

PORTS = (80, 443, 2083)
ROOT = Path('/etc/xrm-site')
STATE = Path('/var/lib/xrm-1/site-gateway')
UNIT = Path('/etc/systemd/system/xrm-site.service')
DB = Path('/etc/x-ui/x-ui.db')
NEW_PATH = '/api/v1/sync'


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True)


def discover_tls(db):
    """Reuse each public listener's own identity, including embedded PEM arrays."""
    found = {}
    for port, raw in db.execute('SELECT port,stream_settings FROM inbounds WHERE enable=1 AND port IN (443,2083)'):
        tls = json.loads(raw).get('tlsSettings', {})
        certs = tls.get('certificates', [])
        if len(certs) != 1:
            raise ValueError(f'Port {port}: expected one TLS certificate; refusing to guess')
        item = certs[0]
        def pem(field, file_field):
            if item.get(file_field):
                path = Path(item[file_field]).resolve(strict=True)
                return dict(file=str(path))
            lines = item.get(field)
            if isinstance(lines, list) and lines:
                return dict(pem='\n'.join(lines) + '\n')
            raise ValueError(f'Port {port}: missing {field}')
        found[port] = dict(cert=pem('certificate', 'certificateFile'),
                           key=pem('key', 'keyFile'))
    if set(found) != {443,2083}:
        raise ValueError('Automatic setup needs TLS identities on both 443 and 2083')
    return found


def install_tls(identities, root):
    for port, pair in identities.items():
        for kind, source in pair.items():
            path = root / f'{port}-{kind}.pem'
            if 'file' in source:
                path.symlink_to(source['file'])
            else:
                path.write_text(source['pem'])
                path.chmod(0o600)
        run('openssl', 'x509', '-in', str(root / f'{port}-cert.pem'), '-noout', '-checkend', '0')


def refresh_tls():
    if not (STATE / 'active').exists():
        return
    paths = sorted(ROOT.glob('*-cert.pem')) + sorted(ROOT.glob('*-key.pem'))
    if not paths:
        paths = [ROOT / 'fullchain.pem', ROOT / 'privkey.pem']
    digest = hashlib.sha256(b''.join(path.read_bytes() for path in paths)).hexdigest()
    marker = STATE / 'tls-digest'
    if marker.exists() and marker.read_text() == digest:
        return
    run('nginx', '-t', '-c', str(ROOT / 'nginx.conf'))
    run('systemctl', 'reload', 'xrm-site')
    marker.write_text(digest)


def safe_path(value):
    if not re.fullmatch(r'/[A-Za-z0-9_./~-]*', value) or '..' in value.split('/'):
        raise ValueError('Unsupported route path: ' + value)
    if value == '/':
        raise ValueError('A root proxy path conflicts with the website')
    return value.rstrip('/')


def plan(db, port_free=lambda p: True):
    settings = dict(db.execute('SELECT key,value FROM settings'))
    rows = db.execute('SELECT id,port,protocol,listen,stream_settings FROM inbounds WHERE enable=1').fetchall()
    used = {int(r[1]) for r in db.execute('SELECT id,port FROM inbounds')}
    used.add(int(settings.get('webPort', '2053')))
    def allocate():
        for p in range(32080, 33080):
            if p not in used and port_free(p):
                used.add(p)
                return p
        raise ValueError('No free loopback port')
    routes = []
    for ident, port, protocol, listen, raw in rows:
        if port not in PORTS:
            continue
        stream = json.loads(raw)
        network = stream.get('network', 'tcp')
        if network not in ('ws', 'xhttp') or stream.get('security', 'none') not in ('none', 'tls'):
            raise ValueError(f'Inbound {ident}: {network} / {stream.get("security")} needs a separate migration')
        if port == 80 and stream.get('security', 'none') != 'none':
            raise ValueError('TLS on port 80 needs a separate migration')
        if port != 80 and stream.get('security') != 'tls':
            raise ValueError(f'Plain HTTP on {port} cannot be changed to HTTPS without changing clients')
        if any(r['public'] == port for r in routes):
            raise ValueError(f'Multiple listeners on {port} need explicit address mapping')
        key = network + 'Settings'
        cfg = stream.get(key, {})
        route = '/' if network == 'ws' and cfg.get('path', '/') == '/' else safe_path(cfg.get('path', '/'))
        if network == 'ws' and route == NEW_PATH:
            raise ValueError('WebSocket path conflicts with XHTTP alias')
        new = copy.deepcopy(stream)
        new['security'] = 'none'
        new.pop('tlsSettings', None)
        new.pop('finalmask', None)  # TLS terminates at nginx, not at this loopback listener.
        if port == 2083 and network == 'xhttp':
            new[key]['path'] = NEW_PATH
        new['sockopt'] = {}  # Do not inherit listener proxy-protocol/bind options.
        routes.append(dict(id=ident, public=port, backend=allocate(), network=network,
                           path=route, stream=new))
    panel = None
    if int(settings.get('webPort', '2053')) in PORTS:
        path = safe_path(settings.get('webBasePath', '/'))
        for r in routes:
            if r['public'] != int(settings['webPort']) or (r['network'] == 'ws' and r['path'] == '/'):
                continue
            if path.startswith(r['path']) or r['path'].startswith(path):
                raise ValueError('Panel and proxy route paths overlap')
        if path.startswith(NEW_PATH) or NEW_PATH.startswith(path):
            raise ValueError('Panel path conflicts with XHTTP alias')
        panel = dict(public=int(settings['webPort']), backend=allocate(), path=path)
    if not any(r['network'] == 'xhttp' and r['public'] == 2083 for r in routes):
        raise ValueError('No XHTTP inbound on port 2083 to expose as /api/v1/sync')
    return dict(routes=routes, panel=panel)


def location(route, source, index):
    target = route['stream'].get(route['network'] + 'Settings', {}).get('path', route['path']).rstrip('/') or '/'
    # A prefix location includes XHTTP session/sequence suffixes; exact handles bare path.
    rewrite = '' if source == target else f'rewrite ^{re.escape(source)}(.*)$ {target}$1 break;'
    base = f'''{rewrite}
        client_max_body_size 0;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_request_buffering off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
'''
    backend = f'127.0.0.1:{route["backend"]}'
    if route['network'] == 'ws':
        body = base + f'''proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_pass http://{backend};'''
        extra = ''
    else:
        # stream-up/one use full duplex h2c. HTTP/1 packet-up uses unbuffered proxy_pass.
        body = f'''error_page 418 = @xhttp_{index};
        if ($server_protocol = "HTTP/2.0") {{ return 418; }}
        ''' + base + f'proxy_pass http://{backend};'
        extra = f'''location @xhttp_{index} {{
        {rewrite}
        client_max_body_size 0;
        grpc_set_header Host $host;
        grpc_read_timeout 3600s;
        grpc_send_timeout 3600s;
        grpc_pass grpc://{backend};
    }}'''
    if route['network'] == 'ws' and source == '/':
        return f'''location / {{
        error_page 418 = @root_ws_{index};
        if ($http_upgrade ~* ^websocket$) {{ return 418; }}
        try_files $uri $uri/ =404;
    }}
    location @root_ws_{index} {{
        {body}
    }}'''
    return f'location = {source} {{\n{body}\n    }}\n    location ^~ {source}/ {{\n{body}\n    }}\n    {extra}'


def render(p, domain, root=ROOT, auto_tls=False):
    if domain != '_' and not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?', domain):
        raise ValueError('Use a DNS hostname without scheme, path, or port')
    if not re.fullmatch(r'/[A-Za-z0-9_/-]+', str(root)):
        raise ValueError('Invalid gateway directory')
    preferred = next((r for r in p['routes'] if r['public'] == 2083 and r['network'] == 'xhttp'),
                     next(r for r in p['routes'] if r['network'] == 'xhttp'))
    servers = []
    for port in PORTS:
        own = [r for r in p['routes'] if r['public'] == port]
        alias = next((r for r in own if r['network'] == 'xhttp'), preferred)
        mappings = [(r, r['path']) for r in own]
        if port == 2083 and not any(path == NEW_PATH for _, path in mappings):
            mappings.append((alias, NEW_PATH))
        blocks = [location(r, path, f'{port}_{i}') for i, (r, path) in enumerate(mappings)]
        panel = p['panel']
        if panel and panel['public'] == port:
            path = panel['path']
            blocks.append(f'''location = {path} {{ return 308 {path}/; }}
    location ^~ {path}/ {{
        proxy_pass http://127.0.0.1:{panel['backend']};
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
    }}''')
        default_location = '' if any(r['network'] == 'ws' and path == '/' for r, path in mappings) else 'location / { try_files $uri $uri/ =404; }'
        cert_name = f'{port}-cert.pem' if auto_tls else 'fullchain.pem'
        key_name = f'{port}-key.pem' if auto_tls else 'privkey.pem'
        tls = '' if port == 80 else f'ssl_certificate {root}/{cert_name};\n    ssl_certificate_key {root}/{key_name};\n    ssl_protocols TLSv1.2 TLSv1.3;'
        suffix = '' if port == 80 else ' ssl http2'
        servers.append(f'''server {{
    listen {port}{suffix};
    listen [::]:{port}{suffix};
    server_name {domain};
    {tls}
    root {root}/website;
    index index.html;
    {default_location}
    {chr(10).join(blocks)}
}}''')
    return f'''worker_processes auto;
pid /run/xrm-site.pid;
error_log /var/log/xrm-site-error.log warn;
events {{ worker_connections 4096; }}
http {{
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    access_log off;
    sendfile on;
    map $http_upgrade $connection_upgrade {{ default upgrade; '' close; }}
    {chr(10).join(servers)}
}}
'''


def update_db(db, p):
    with db:
        for r in p['routes']:
            db.execute('UPDATE inbounds SET listen=?,port=?,stream_settings=? WHERE id=?',
                       ('127.0.0.1', r['backend'], json.dumps(r['stream']), r['id']))
        if p['panel']:
            for key, value in dict(webPort=str(p['panel']['backend']), webListen='127.0.0.1', webCertFile='', webKeyFile='').items():
                if db.execute('SELECT 1 FROM settings WHERE key=?', (key,)).fetchone():
                    db.execute('UPDATE settings SET value=? WHERE key=?', (value, key))
                else:
                    db.execute('INSERT INTO settings(key,value) VALUES (?,?)', (key, value))


def free(port):
    try:
        with socket.socket() as s:
            s.bind(('0.0.0.0', port))
        return True
    except OSError:
        return False


def rollback():
    backup = STATE / 'before.db'
    if not backup.exists():
        raise ValueError('No gateway database backup exists')
    subprocess.run(['systemctl', 'disable', '--now', 'xrm-site-tls.timer'], capture_output=True)
    subprocess.run(['systemctl', 'disable', '--now', 'xrm-site'], capture_output=True)
    run('systemctl', 'stop', 'x-ui')
    with sqlite3.connect(backup) as src, sqlite3.connect(DB) as dst:
        src.backup(dst)
    run('systemctl', 'start', 'x-ui')
    (STATE / 'active').unlink(missing_ok=True)
    print('Restored pre-gateway database. x-ui restarted. Backup retained.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--domain')
    parser.add_argument('--cert', type=Path)
    parser.add_argument('--key', type=Path)
    parser.add_argument('--refresh-tls', action='store_true')
    parser.add_argument('--auto', action='store_true', help='Reuse existing per-port TLS identities without prompts')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--rollback', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Run as root')
    os.umask(0o077)
    # One installer/rollback at a time.
    import fcntl
    lock = open('/run/lock/xrm-site.lock', 'w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if args.refresh_tls:
        refresh_tls()
        return
    if args.rollback:
        rollback()
        return
    if args.auto and (STATE / 'active').exists():
        run('systemctl', 'is-active', '--quiet', 'x-ui', 'xrm-site')
        print('Website gateway already active; no database changes.')
        return
    if not args.auto and (not args.domain or not args.cert or not args.key):
        parser.error('--domain, --cert and --key are required (existing certificate files)')
    if not DB.is_file():
        raise ValueError('Install x-ui first; no database found')
    if ROOT.exists() or (STATE / 'before.db').exists() or UNIT.exists():
        raise ValueError('Gateway state already exists; inspect it instead of overwriting the backup')
    for binary in ('nginx', 'ss', 'systemctl', 'openssl'):
        if not shutil.which(binary):
            raise ValueError(f'Missing {binary}; install nginx, iproute2 and openssl first')
    run('systemctl', 'is-active', '--quiet', 'x-ui')
    for port in PORTS:
        listeners = run('ss', '-H', '-ltnp', f'sport = :{port}').stdout
        for line in listeners.splitlines():
            if not re.search(r'users:\(\("(?:xray[^" ]*|x-ui)"', line):
                raise ValueError(f'Port {port} belongs to another service. No changes made.')
    with sqlite3.connect(f'file:{DB}?mode=ro', uri=True) as db:
        p = plan(db, free)
        identities = discover_tls(db) if args.auto else None
    if args.auto:
        args.domain = args.domain or '_'
    config = render(p, args.domain, auto_tls=args.auto)
    if not args.auto:
        cert = args.cert.read_bytes()
        key = args.key.read_bytes()
        run('openssl', 'x509', '-in', str(args.cert), '-noout', '-checkend', '0')
    print(json.dumps({'domain': args.domain, 'routes': [{k:v for k,v in r.items() if k != 'stream'} for r in p['routes']], 'panel': p['panel']}, indent=2))
    if not args.apply:
        print('Plan only. Re-run with --apply to migrate; existing client paths remain available.')
        return
    STATE.mkdir(parents=True, mode=0o700)
    ROOT.mkdir(mode=0o755)
    ROOT.chmod(0o755)
    shutil.copytree(Path(__file__).resolve().parent / 'website', ROOT / 'website')
    for path in (ROOT / 'website').rglob('*'):
        path.chmod(0o755 if path.is_dir() else 0o644)
    (ROOT / 'website').chmod(0o755)
    try:
        if args.auto:
            install_tls(identities, ROOT)
        else:
            (ROOT / 'fullchain.pem').write_bytes(cert)
            (ROOT / 'privkey.pem').write_bytes(key)
        (ROOT / 'nginx.conf').write_text(config)
        run('nginx', '-t', '-c', str(ROOT / 'nginx.conf'))
    except Exception:
        shutil.rmtree(ROOT)
        raise
    UNIT.write_text(f'''[Unit]
Description=XRM shared website and XHTTP gateway
After=network.target x-ui.service
[Service]
Type=simple
ExecStart={shutil.which('nginx')} -c {ROOT}/nginx.conf -g "daemon off;"
ExecReload={shutil.which('nginx')} -c {ROOT}/nginx.conf -s reload
Restart=on-failure
[Install]
WantedBy=multi-user.target
''')
    stopped = False
    backed_up = False
    try:
        run('systemctl', 'stop', 'x-ui')
        stopped = True
        # Recompute after stopping panel to include the latest saved changes.
        with sqlite3.connect(DB) as db:
            latest = plan(db, free)
            if latest != p:
                raise ValueError('Panel configuration changed during planning; retry after inspection')
            with sqlite3.connect(STATE / 'before.db') as backup:
                db.backup(backup)
            backed_up = True
            update_db(db, p)
        run('systemctl', 'start', 'x-ui')
        run('systemctl', 'daemon-reload')
        run('systemctl', 'enable', '--now', 'xrm-site')
        for attempt in range(20):
            try:
                run('systemctl', 'is-active', '--quiet', 'x-ui', 'xrm-site')
                for r in p['routes'] + ([p['panel']] if p['panel'] else []):
                    with socket.create_connection(('127.0.0.1', r['backend']), timeout=1):
                        pass
                for port in PORTS:
                    conn = (http.client.HTTPConnection('127.0.0.1', port, timeout=2) if port == 80 else
                            http.client.HTTPSConnection('127.0.0.1', port, timeout=2, context=ssl._create_unverified_context()))
                    try:
                        conn.request('GET', '/', headers={'Host': args.domain})
                        res = conn.getresponse()
                        if res.status != 200 or b'Nava' not in res.read():
                            raise ValueError('Website check failed')
                    finally:
                        conn.close()
                break
            except Exception:
                if attempt == 19:
                    raise
                time.sleep(1)
        (STATE / 'plan.json').write_text(json.dumps(p, indent=2))
        (STATE / 'active').write_text(args.domain + '\n')
    except BaseException:
        if backed_up:
            rollback()
        elif stopped:
            run('systemctl', 'start', 'x-ui')
        raise
    print('Gateway ready on 80/443/2083. Test real proxy traffic from your client before fleet rollout.')
    print('Existing TLS identities preserved. File-based identities follow their original certificate paths.')
    print('Rollback: python3 site_gateway.py --rollback (restores entire saved database).')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        raise SystemExit('ERROR: ' + str(error))
