import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('recovery',pathlib.Path(__file__).parents[1]/'deploy/portfolio-recovery.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
spec2=importlib.util.spec_from_file_location('routing',pathlib.Path(__file__).parents[1]/'deploy/portfolio-nginx.py')
n=importlib.util.module_from_spec(spec2);spec2.loader.exec_module(n)


class RecoveryTests(unittest.TestCase):
    def test_existing_database_is_rejected_before_any_restore(self):
        with self.assertRaisesRegex(RuntimeError,'not empty'):
            r.assert_empty_database(lambda sql:b'7\n')
        r.assert_empty_database(lambda sql:b'0\n')

    def test_empty_or_ownerless_content_is_not_publishable(self):
        for counts in [{'users':0,'admins':0,'content':0,'media':0},{'users':1,'admins':0,'content':1,'media':1},{'users':1,'admins':1,'content':0,'media':1}]:
            with self.assertRaisesRegex(RuntimeError,'site remains unpublished'):
                r.validate_restored_counts(lambda sql:json.dumps(counts).encode())
        counts={'users':3,'admins':1,'content':1,'media':12}
        self.assertEqual(r.validate_restored_counts(lambda sql:json.dumps(counts).encode()),counts)

    def test_retained_environment_is_read_without_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            path=pathlib.Path(directory)/'.env'
            original='# Comment\nOWNER_EMAIL="owner@example.com"\nSMTP_URL=smtp://a:b@example.com\n'
            path.write_text(original)
            self.assertEqual(r.read_environment(path)['OWNER_EMAIL'],'owner@example.com')
            self.assertEqual(path.read_text(),original)

    def test_nginx_validation_failure_restores_previously_disabled_site(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory)
            available=root/'available';available.write_text('server_name th.io.vn;\nproxy_pass http://127.0.0.1:8080;')
            enabled=root/'enabled';backup=root/'backup.json'
            with patch.object(n,'AVAILABLE',available),patch.object(n,'ENABLED',enabled),patch.object(n,'BACKUP',backup),patch.object(n.sys,'argv',['routing']),patch.object(n,'host',side_effect=[RuntimeError('invalid configuration'),None,None]):
                with self.assertRaises(RuntimeError):n.main()
            self.assertFalse(enabled.is_symlink())
            self.assertEqual(available.read_text(),'server_name th.io.vn;\nproxy_pass http://127.0.0.1:8080;')

    def test_nginx_existing_route_is_restored_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory)
            available=root/'available';available.write_text('server_name th.io.vn;\nproxy_pass http://127.0.0.1:8080;')
            enabled=root/'enabled';enabled.symlink_to('/old/portfolio')
            backup=root/'backup.json'
            with patch.object(n,'AVAILABLE',available),patch.object(n,'ENABLED',enabled),patch.object(n,'BACKUP',backup),patch.object(n.sys,'argv',['routing']),patch.object(n,'host',side_effect=[RuntimeError('invalid configuration'),None,None]):
                with self.assertRaises(RuntimeError):n.main()
            self.assertEqual(str(enabled.readlink()),'/old/portfolio')


if __name__=='__main__':unittest.main()
