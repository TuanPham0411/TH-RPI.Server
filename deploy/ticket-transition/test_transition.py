import unittest
from transition import renamer

class NamespaceTest(unittest.TestCase):
 def test_identifiers_and_large_integer_values(self):
  text,change=renamer('mes-ticket')
  row={'id':'mes-owner','counter':9223372036854775807,'data':{'reporterUid':'mes-owner','uri':'https://ticket.th.io.vn/api/mes/media/abc','title':'MES inbox'},'password_hash':'$2b$10$.MES.secret','refreshToken':'MES-private-token'}
  result=change(row)
  self.assertEqual(result['id'],'ticket-owner')
  self.assertEqual(result['counter'],9223372036854775807)
  self.assertEqual(result['password_hash'],row['password_hash'])
  self.assertEqual(result['refreshToken'],row['refreshToken'])
  self.assertEqual(result['data']['uri'],'https://ticket.th.io.vn/api/ticket/media/abc')
  self.assertEqual(text('mes_private.mes_dispatch mes_ticket th_mes mesOnly'),'ticket_private.ticket_dispatch ticket th_ticket ticketOnly')
 def test_ordinary_words_are_preserved(self):
  text,change=renamer('mes-ticket')
  self.assertEqual(text('names times themes messages'), 'names times themes messages')
  self.assertEqual(change({'name':'202610150001_mes.sql'})['name'],'202610150001_ticket.sql')

if __name__=='__main__':unittest.main()
