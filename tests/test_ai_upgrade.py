import importlib.util
import pathlib
import unittest

path = pathlib.Path(__file__).resolve().parents[1] / 'deploy/upgrade-ai.py'
spec = importlib.util.spec_from_file_location('ai_upgrade', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class UpgradePlan(unittest.TestCase):
    def original(self):
        return {'services': {'api': {'image': 'old-api', 'environment': {'PUBLIC_ORIGIN': 'https://thienha.io.vn', 'DATABASE_URL': 'retained-private-db'}, 'volumes': ['retained-files']}, 'migrate': {'image': 'old-api', 'environment': {'DATABASE_URL': 'retained-operator'}}, 'web': {'image': 'old-web', 'volumes': ['retained-nginx']}, 'db': {'image': 'postgres:17', 'environment': {'POSTGRES_PASSWORD': 'private'}}, 'storage': {'image': 'retained-storage', 'volumes': ['private-config']}}, 'volumes': {'ai-db': {'external': True, 'name': 'ai-agent_ai-db'}, 'ai-files': {'external': True, 'name': 'ai-agent_ai-files'}}}

    def test_upgrade_preserves_database_storage_private_connections_and_mounts(self):
        original = self.original()
        updated = module.updated_compose(original, 'a' * 40)
        for name in ('db', 'storage'):
            self.assertEqual(original['services'][name], updated['services'][name])
        self.assertEqual(original['volumes'], updated['volumes'])
        self.assertEqual(original['services']['api']['volumes'], updated['services']['api']['volumes'])
        self.assertEqual(updated['services']['api']['environment']['DATABASE_URL'], 'retained-private-db')
        self.assertEqual(original['services']['api']['image'], 'old-api')
        self.assertTrue(updated['services']['web']['image'].endswith('a' * 40))

    def test_bad_release_and_unexpected_volume_fail_closed(self):
        with self.assertRaises(ValueError):
            module.updated_compose(self.original(), 'main')
        original = self.original()
        original['volumes']['ai-db']['name'] = 'another-application'
        with self.assertRaises(ValueError):
            module.updated_compose(original, 'a' * 40)


if __name__ == '__main__':
    unittest.main()
