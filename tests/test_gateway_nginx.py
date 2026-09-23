"""Validate the generated real nginx config when nginx is installed (CI)."""
import importlib.util
from pathlib import Path
import shutil
import os
import subprocess
import tempfile
import unittest
from test_site_gateway import database, g


@unittest.skipUnless(shutil.which('nginx') and os.geteuid() == 0, 'requires nginx and root; dedicated gateway CI runs this check')
class NginxTests(unittest.TestCase):
    def test_generated_configuration(self):
        with tempfile.TemporaryDirectory(prefix='xrm-gateway-') as directory:
            root=Path(directory)
            subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=example.com','-keyout',str(root/'privkey.pem'),'-out',str(root/'fullchain.pem')],check=True,capture_output=True)
            conf=g.render(g.plan(database()),'example.com',root)
            for port in g.PORTS:
                conf=conf.replace(f'listen {port}',f'listen {port + 20000}').replace(f'listen [::]:{port}',f'listen [::]:{port + 20000}')
            conf=conf.replace('/run/xrm-site.pid',str(root/'nginx.pid')).replace('/var/log/xrm-site-error.log',str(root/'error.log'))
            # Isolated syntax check only: no worker is started. Avoid UID switching in containers.
            (root/'nginx.conf').write_text('user root;\n' + conf)
            result = subprocess.run(['nginx','-t','-c',str(root/'nginx.conf')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
