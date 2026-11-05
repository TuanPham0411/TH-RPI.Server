"""Opt-in local Docker lifecycle checks; never connects to remote RPI hosts."""
import os
import time
import unittest
import uuid

import inventory


@unittest.skipUnless(os.environ.get('TH_CONSOLE_DOCKER_TEST') == '1', 'Opt-in local Docker integration test')
class DockerIntegrationTests(unittest.TestCase):
    def test_new_unhealthy_stopped_removed_container_and_image_tag(self):
        suffix = uuid.uuid4().hex[:12]
        name = 'th-console-test-' + suffix
        tag = 'th-console-fixture:' + suffix
        image = os.environ.get('TH_CONSOLE_TEST_IMAGE', 'th-console:test')
        before = inventory.collect()
        self.assertNotIn('containers', before['errors'])
        inventory.run(['docker', 'tag', image, tag])
        identifier = None
        try:
            identifier = inventory.run(['docker', 'run', '-d', '--name', name, '--network', 'none',
                                        '--health-cmd', 'exit 1', '--health-interval', '1s',
                                        '--health-timeout', '1s', '--health-retries', '1',
                                        '--label', 'com.docker.compose.project=discovery-fixture',
                                        '--label', 'com.docker.compose.service=worker',
                                        '--entrypoint', '/bin/sleep', tag, '90'])
            for _ in range(30):
                current = inventory.collect()
                row = next((r for r in current['containers'] if r['id'] == identifier), None)
                if row and row['health'] == 'unhealthy':
                    break
                time.sleep(1)
            self.assertIsNotNone(row)
            self.assertEqual(row['health'], 'unhealthy')
            self.assertEqual(row['project'], 'discovery-fixture')
            events = inventory.changes(before, current)
            self.assertTrue(any(e['source'] == 'containers' and e['kind'] == 'added' and e['name'] == name for e in events))
            self.assertTrue(any(e['source'] == 'images' and e['kind'] == 'added' and e['name'] == 'th-console-fixture' for e in events))
            inventory.run(['docker', 'stop', '--time', '1', identifier])
            stopped = inventory.collect()
            row = next(r for r in stopped['containers'] if r['id'] == identifier)
            self.assertEqual(row['state'], 'exited')
            self.assertTrue(any(e['name'] == name and 'state' in e['fields'] for e in inventory.changes(current, stopped)))
            inventory.run(['docker', 'rm', '-v', identifier])
            identifier = None
            removed = inventory.collect()
            self.assertTrue(any(e['name'] == name and e['kind'] == 'removed' for e in inventory.changes(stopped, removed)))
        finally:
            if identifier:
                inventory.run(['docker', 'rm', '-fv', identifier])
            inventory.run(['docker', 'image', 'rm', tag])


if __name__ == '__main__':
    unittest.main()
