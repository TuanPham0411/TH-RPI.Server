import importlib.util,pathlib,unittest
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('console',pathlib.Path(__file__).with_name('console.py'));c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
class ConsoleTests(unittest.TestCase):
 def test_only_existing_usb_partitions_are_exposed(self):
  tree={'blockdevices':[{'path':'/dev/mmcblk0','tran':None,'children':[{'path':'/dev/mmcblk0p2','type':'part','fstype':'ext4'}]},{'path':'/dev/sda','tran':'usb','children':[{'path':'/dev/sda1','type':'part','fstype':'exfat'}]}]}
  with patch.object(c,'json_command',return_value=tree):self.assertEqual([p['path'] for p in c.block_devices()],['/dev/sda1'])
 def test_system_and_unknown_devices_cannot_be_mounted(self):
  with patch.object(c,'block_devices',return_value=[]),patch.object(c.subprocess,'run') as run:
   for path in ['/dev/mmcblk0p2','/dev/sda1;reboot','/dev/missing']:
    with self.assertRaises(ValueError):c.usb_action(path)
   run.assert_not_called()
 def test_mount_in_use_by_docker_is_protected(self):
  with patch.object(c,'command',side_effect=['26.1','container']),patch.object(c,'json_command',return_value=[{'Source':'/mnt/th-usb-disk/app-data'}]):self.assertTrue(c.protected_mount('/mnt/th-usb-disk'))
 def test_unmount_fails_closed_if_docker_status_unknown(self):
  with patch.object(c,'command',return_value=''):self.assertTrue(c.protected_mount('/mnt/th-usb-disk'))
 def test_non_console_mount_cannot_be_removed(self):
  with patch.object(c,'block_devices',return_value=[{'path':'/dev/sda1','mountpoints':['/media/data']}]),patch.object(c.subprocess,'run') as run:
   with self.assertRaises(ValueError):c.usb_action('/dev/sda1',True)
   run.assert_not_called()
 def test_existing_mounted_partition_is_not_overmounted(self):
  with patch.object(c,'block_devices',return_value=[{'path':'/dev/sda1','mountpoints':['/mnt/th-usb-data']}]),patch.object(c.subprocess,'run') as run:
   with self.assertRaises(ValueError):c.usb_action('/dev/sda1')
   run.assert_not_called()
 def test_health_requires_application_ready_payload(self):
  class Response:
   status=200
   def __enter__(self):return self
   def __exit__(self,*a):pass
   def read(self):return b'{"ok":true}'
  with patch.object(c.urllib.request,'urlopen',return_value=Response()):self.assertEqual(c.health(c.APPS[0])[1],'HEALTHY')
  with patch.object(c.urllib.request,'urlopen',side_effect=OSError()):self.assertEqual(c.health(c.APPS[0])[1],'UNREACHABLE')
if __name__=='__main__':unittest.main()
