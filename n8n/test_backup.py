import importlib.util,json,os,pathlib,unittest
spec=importlib.util.spec_from_file_location('backup',pathlib.Path(__file__).with_name('backup.py'));b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
@unittest.skipUnless(os.environ.get('N8N_TEST_POSTGRES'),'Disposable CI PostgreSQL only')
class DatabaseBackupTest(unittest.TestCase):
 def test_snapshot_counts_and_workflow_export_are_valid(self):
  b.DB=os.environ['N8N_TEST_POSTGRES']
  b.sql('CREATE DATABASE th_n8n;','postgres')
  try:
   b.sql('CREATE TABLE workflow_entity(id text primary key,nodes jsonb); INSERT INTO workflow_entity VALUES (\'one\',\'[]\'),(\'two\',\'[]\'); CREATE TABLE empty_table(value text);')
   self.assertEqual(json.loads(b.sql(b.COUNTS)),{'workflow_entity':2,'empty_table':0})
  finally:b.sql('DROP DATABASE th_n8n WITH (FORCE);','postgres')
if __name__=='__main__':unittest.main()
