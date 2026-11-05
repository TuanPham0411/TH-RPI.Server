import importlib.util
import pathlib
import tempfile
import unittest
from unittest.mock import patch


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, pathlib.Path(__file__).parents[1]/'deploy'/file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


deployment = load('esp_deployment', 'esp32-deploy.py')
nginx = load('esp_nginx', 'esp32-nginx.py')


class ESPDeploymentTests(unittest.TestCase):
    def test_invalid_image_is_rejected_before_host_changes(self):
        with patch.object(deployment.subprocess, 'run') as command:
            for image in ('latest', '../main', 'a'*39, 'a'*40+';id'):
                with self.assertRaisesRegex(RuntimeError, 'full published image SHA'):
                    deployment.deploy(image)
            command.assert_not_called()

    def test_failed_nginx_validation_preserves_other_sites_and_old_route(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            site = root/'site'
            site.write_text('server_name esp.th.io.vn;\nproxy_pass http://127.0.0.1:5081;')
            link = root/'esp'
            link.symlink_to('/previous/esp')
            portfolio = root/'portfolio'
            portfolio.symlink_to('/etc/nginx/sites-available/th-portfolio')
            with patch.object(nginx, 'AVAILABLE', site), patch.object(nginx, 'ENABLED', link), patch.object(nginx, 'BACKUP', root/'backup.json'), patch.object(nginx.sys, 'argv', ['routing']), patch.object(nginx, 'host', side_effect=[RuntimeError('bad config'), None, None]):
                with self.assertRaises(RuntimeError):
                    nginx.main()
            self.assertEqual(str(link.readlink()), '/previous/esp')
            self.assertEqual(str(portfolio.readlink()), '/etc/nginx/sites-available/th-portfolio')


if __name__ == '__main__':
    unittest.main()
