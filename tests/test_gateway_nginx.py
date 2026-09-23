"""Validate the generated real nginx config when nginx is installed (CI)."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from test_site_gateway import database, g


@unittest.skipUnless(shutil.which('nginx'), 'nginx not installed')
class NginxTests(unittest.TestCase):
    def test_generated_configuration(self):
        with tempfile.TemporaryDirectory(prefix='xrm-gateway-') as directory:
            root=Path(directory)
            subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=example.com','-keyout',str(root/'privkey.pem'),'-out',str(root/'fullchain.pem')],check=True,capture_output=True)
            conf=g.render(g.plan(database()),'example.com',root)
            conf=conf.replace('/run/xrm-site.pid',str(root/'nginx.pid')).replace('/var/log/xrm-site-error.log',str(root/'error.log'))
            (root/'nginx.conf').write_text(conf)
            subprocess.run(['nginx','-t','-c',str(root/'nginx.conf')],check=True,capture_output=True)
