import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import inventory as i
import monitor as m


def sample(containers=None, services=None, images=None, errors=None):
    return {'containers': containers or [], 'services': services or [], 'images': images or [], 'errors': errors or {}}


class InventoryTests(unittest.TestCase):
    def test_container_discovery_includes_stopped_containers_and_only_selected_metadata(self):
        row = {'id': 'abc', 'name': '/brand-new', 'image': 'app:next', 'image_id': 'sha256:123',
               'state': 'exited', 'health': 'not-configured', 'project': 'custom', 'service': 'worker', 'ports': None}
        with patch.object(i, 'run', side_effect=['abc', json.dumps(row)]) as run:
            found = i.containers()
        self.assertEqual(found[0]['name'], 'brand-new')
        self.assertEqual(found[0]['state'], 'exited')
        self.assertEqual(found[0]['ports'], {})
        self.assertIn('-aq', run.call_args_list[0].args[0])
        template = run.call_args_list[1].args[0][4]
        self.assertNotIn('.Config.Env', template)
        self.assertNotIn('.State.Log', template)
        self.assertNotIn('.Config.Cmd', template)

    def test_empty_docker_inventory_does_not_inspect_every_container(self):
        with patch.object(i, 'run', return_value='') as run:
            self.assertEqual(i.containers(), [])
        self.assertEqual(run.call_count, 1)

    def test_images_preserve_multiple_tags_and_deduplicate_identical_rows(self):
        row = {'ID': 'sha256:x', 'Repository': 'custom', 'Tag': 'v1', 'Digest': 'sha256:d', 'Size': '1MB'}
        rows = [row, row, {**row, 'Tag': 'v2'}]
        with patch.object(i, 'run', return_value='\n'.join(map(json.dumps, rows))):
            images = i.images()
        self.assertEqual([r['tag'] for r in images], ['v1', 'v2'])

    def test_service_discovery_merges_installed_and_transient_without_allowlist(self):
        output = 'Id=disabled.service\nActiveState=inactive\nUnitFileState=disabled\nMainPID=0\n\nId=transient.service\nActiveState=active\nSubState=running\nMainPID=42'
        with patch.object(i, 'run', side_effect=['disabled.service disabled enabled', 'transient.service loaded active running Temporary service', output]) as run:
            rows = i.services()
        self.assertEqual([r['id'] for r in rows], ['disabled.service', 'transient.service'])
        self.assertEqual(rows[1]['pid'], 42)
        self.assertEqual(set(run.call_args_list[-1].args[0][-2:]), {'disabled.service', 'transient.service'})

    def test_installed_template_is_listed_without_loading_an_invalid_instance(self):
        with patch.object(i, 'run', side_effect=['getty@.service enabled enabled','']) as run:
            rows = i.services()
        self.assertEqual(run.call_count, 2)
        self.assertEqual(rows[0]['id'], 'getty@.service')
        self.assertEqual(rows[0]['state'], 'not-instantiated')

    def test_permissions_failure_does_not_hide_other_sources(self):
        with patch.object(i, 'containers', side_effect=i.Unavailable('docker exited 1')), patch.object(i, 'images', return_value=[]), patch.object(i, 'services', return_value=[{'id': 'ssh.service'}]):
            value = i.collect()
        self.assertIn('containers', value['errors'])
        self.assertEqual(value['services'][0]['id'], 'ssh.service')

    def test_command_failure_is_explicit_without_leaking_stderr(self):
        with patch.object(i.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'secret=value')):
            with self.assertRaises(i.Unavailable) as caught:
                i.run(['docker', 'ps'])
        self.assertNotIn('secret', str(caught.exception))

    def test_added_removed_and_changed_health_image_service_are_detected(self):
        before = sample(containers=[{'id': 'a', 'name': 'app', 'health': 'healthy', 'image_id': 'old'}, {'id': 'b', 'name': 'gone'}], services=[{'id': 'custom.service', 'state': 'active'}])
        after = sample(containers=[{'id': 'a', 'name': 'app', 'health': 'unhealthy', 'image_id': 'new'}, {'id': 'c', 'name': 'created'}], services=[{'id': 'custom.service', 'state': 'failed'}])
        events = i.changes(before, after)
        self.assertEqual([(e['source'], e['kind']) for e in events], [('containers', 'updated'), ('containers', 'removed'), ('containers', 'added'), ('services', 'updated')])
        self.assertEqual(events[0]['fields'], ['health', 'image_id'])
        self.assertEqual(i.changes(after, after), [])
        self.assertEqual(i.changes(None, after), [])

    def test_outage_does_not_report_mass_removals_and_last_known_rows_are_marked(self):
        before = {'inventory': sample(containers=[{'id': 'a', 'name': 'app'}]), 'changes': []}
        with patch.object(i, 'collect', return_value=sample(errors={'containers': 'docker exited 1'})):
            after = m.sample(before)
        self.assertEqual(after['changes'], [])
        self.assertEqual(after['inventory']['containers'], before['inventory']['containers'])
        self.assertIn('containers', after['inventory']['errors'])

    def test_recovery_compares_against_last_successful_sample(self):
        before = {'inventory': sample(containers=[{'id': 'a', 'name': 'app', 'health': 'healthy'}]), 'changes': []}
        with patch.object(i, 'collect', return_value=sample(errors={'containers': 'offline'})):
            outage = m.sample(before)
        with patch.object(i, 'collect', return_value=sample(containers=[{'id': 'a', 'name': 'app', 'health': 'unhealthy'}])):
            recovered = m.sample(outage)
        self.assertEqual(recovered['changes'][0]['fields'], ['health'])

    def test_atomic_state_round_trip_preserves_history_across_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / 'inventory.json'
            before = {'inventory': sample(), 'changes': [{'kind': 'added'}]}
            m.save(path, before)
            self.assertEqual(m.load(path), before)
            self.assertFalse(path.with_suffix('.new').exists())
            with patch.object(i, 'collect', return_value=sample()):
                after = m.sample(m.load(path))
            self.assertEqual(after['changes'], before['changes'])

    def test_corrupt_state_is_ignored_and_symlink_temporary_is_not_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / 'inventory.json'
            path.write_text('not json')
            self.assertIsNone(m.load(path))
            target = pathlib.Path(directory) / 'target'
            target.write_text('preserved')
            path.with_suffix('.new').symlink_to(target)
            with self.assertRaises(OSError):
                m.save(path, {'inventory': sample(), 'changes': []})
            self.assertEqual(target.read_text(), 'preserved')


if __name__ == '__main__':
    unittest.main()
