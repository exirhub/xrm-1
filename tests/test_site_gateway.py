import copy
import importlib.util
import json
from pathlib import Path
import sqlite3
import unittest

spec = importlib.util.spec_from_file_location('gateway', Path(__file__).parents[1] / 'site_gateway.py')
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)


def database():
    db = sqlite3.connect(':memory:')
    db.executescript('CREATE TABLE settings(key TEXT,value TEXT); CREATE TABLE inbounds(id INTEGER,port INTEGER,protocol TEXT,listen TEXT,stream_settings TEXT,enable INTEGER,settings TEXT);')
    db.executemany('INSERT INTO settings VALUES (?,?)', [('webPort','8144'),('webBasePath','/manager/')])
    for ident, port, net, path in [(1,443,'ws','/socket'), (2,80,'xhttp','/api/v1/video'), (3,2083,'xhttp','/eersare'), (4,8080,'xhttp','/untouched')]:
        stream = dict(network=net, security='none' if port in (80,8080) else 'tls')
        stream[net+'Settings'] = dict(path=path, mode='auto')
        db.execute('INSERT INTO inbounds VALUES (?,?,?,?,?,?,?)', (ident,port,'vless','',json.dumps(stream),1,'{"clients":[{"id":"retain-me"}]}'))
    db.commit()
    return db


class GatewayTests(unittest.TestCase):
    def test_only_2083_path_changes_and_credentials_survive(self):
        db=database(); p=g.plan(db); g.update_db(db,p)
        rows=db.execute('SELECT id,port,listen,stream_settings,settings FROM inbounds ORDER BY id').fetchall()
        self.assertEqual([json.loads(r[3]).get('xhttpSettings',{}).get('path') for r in rows], [None,'/api/v1/video','/api/v1/sync','/untouched'])
        self.assertEqual(rows[3][1:3], (8080,''))
        self.assertTrue(all('retain-me' in r[4] for r in rows))
        self.assertTrue(all(r[2]=='127.0.0.1' for r in rows[:3]))
        self.assertEqual(dict(db.execute('SELECT key,value FROM settings'))['webPort'],'8144')

    def test_alias_only_on_2083_and_legacy_rewrite(self):
        conf=g.render(g.plan(database()),'example.com')
        self.assertEqual(conf.count('location = /api/v1/sync'),1)
        self.assertIn('rewrite ^/eersare(.*)$ /api/v1/sync$1 break;',conf)
        self.assertIn('location ^~ /api/v1/video/',conf)
        self.assertIn('grpc_pass grpc://',conf)
        self.assertIn('proxy_request_buffering off',conf)
        self.assertIn('proxy_set_header Upgrade $http_upgrade',conf)

    def test_raw_reality_rejected_before_mutation(self):
        db=database(); db.execute('UPDATE inbounds SET stream_settings=? WHERE id=1', (json.dumps(dict(network='tcp',security='reality')),))
        with self.assertRaises(ValueError): g.plan(db)
        self.assertEqual(db.execute('SELECT port FROM inbounds WHERE id=1').fetchone()[0],443)

    def test_invalid_paths_rejected(self):
        for path in ['/','/foo;bar','/a/../b','/a?b=1']:
            with self.assertRaises(ValueError):g.safe_path(path)

    def test_occupied_backend_skipped(self):
        p=g.plan(database(),lambda port:port!=32080)
        self.assertEqual(p['routes'][0]['backend'],32081)

    def test_panel_on_target_moved_preserving_path(self):
        db=database();db.execute("UPDATE settings SET value='2083' WHERE key='webPort'")
        p=g.plan(db);g.update_db(db,p)
        settings=dict(db.execute('SELECT key,value FROM settings'))
        self.assertEqual(settings['webListen'],'127.0.0.1')
        self.assertEqual(settings['webBasePath'],'/manager/')
        self.assertIn('location ^~ /manager/',g.render(p,'example.com'))

    def test_domain_injection_rejected(self):
        with self.assertRaises(ValueError):g.render(g.plan(database()),'example.com; include /tmp/*')

    def test_stopped_database_backup_restores(self):
        db=database();before=list(db.iterdump());backup=sqlite3.connect(':memory:');db.backup(backup)
        g.update_db(db,g.plan(db));backup.backup(db)
        self.assertEqual(before,list(db.iterdump()))

if __name__=='__main__':unittest.main()
