import unittest,tempfile,pathlib,json,os,time,importlib.util
spec=importlib.util.spec_from_file_location('supervisor',pathlib.Path(__file__).with_name('supervisor.py'));service=importlib.util.module_from_spec(spec);spec.loader.exec_module(service)
class FunctionBoundary(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.folder=tempfile.TemporaryDirectory();service.HOME=pathlib.Path(cls.folder.name);(service.HOME/'state').mkdir()
  (service.HOME/'state/ai-agent-deployment.json').write_text(json.dumps({'imageTag':os.environ['TH_FUNCTION_TEST_TAG']}))
 @classmethod
 def tearDownClass(cls):cls.folder.cleanup()
 def test_json_function_and_async_function(self):
  self.assertEqual(service.execute({'code':'return {total:input.a+input.b};','input':{'a':2,'b':3}})['output'],{'total':5})
  self.assertEqual(service.execute({'code':'return await Promise.resolve({ok:true});','input':{}})['output'],{'ok':True})
 def test_escape_still_has_no_host_secrets_socket_or_writable_filesystem(self):
  result=service.execute({'code':'''const p=input.constructor.constructor('return process')();const fs=input.constructor.constructor('return require')()('node:fs');let readOnly=false;try{fs.writeFileSync('/tmp/escape','x')}catch{readOnly=true}return {uid:p.getuid(),socket:fs.existsSync('/var/run/docker.sock'),host:fs.existsSync('/home/thadmin'),secret:!!p.env.TH_SECRET_TEST_TOKEN,readOnly};''','input':{}})['output']
  self.assertEqual(result,{'uid':65534,'socket':False,'host':False,'secret':False,'readOnly':True})
 def test_network_namespace_has_only_loopback(self):
  result=service.execute({'code':"return Object.keys(input.constructor.constructor('return require')()('node:os').networkInterfaces());",'input':{}})['output'];self.assertEqual(result,['lo'])
 def test_runaway_sync_and_async_functions_are_terminated(self):
  for code in ['while(true){}','while(true){await Promise.resolve()}']:
   start=time.monotonic()
   with self.assertRaises(ValueError):service.execute({'code':code,'input':{}})
   self.assertLess(time.monotonic()-start,20)
 def test_output_flood_is_bounded(self):
  with self.assertRaises(ValueError):service.execute({'code':"input.constructor.constructor('return process')().stdout.write('x'.repeat(250000));return 1;",'input':{}})
if __name__=='__main__':unittest.main()
