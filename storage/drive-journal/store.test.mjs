import test from 'node:test';
import assert from 'node:assert/strict';
import {Journal} from './store.mjs';

class Memory {
  data = new Map();
  async get(key) { return this.data.has(key) ? Buffer.from(this.data.get(key)) : null; }
  async put(key, bytes) {
    if (this.data.has(key) && !this.data.get(key).equals(bytes)) throw Error('Immutable conflict');
    this.data.set(key, Buffer.from(bytes));
  }
  async list(prefix) { return [...this.data.keys()].filter(key => key.startsWith(prefix)); }
}
const operation = (extra = {}) => ({id: 'one', recordId: 'ticket', action: 'put', expectedVersion: null, value: {text: 'Xin chào'}, files: [], ...extra});

test('restore without server cache verifies JSON and binary file', async () => {
  const remote = new Memory();
  await new Journal(remote, 'mes-ticket').commit(operation({files: [{id: 'photo', name: 'a.jpg', bytes: Buffer.from([0, 1, 255])}]}));
  const result = await new Journal(remote, 'mes-ticket').restore('ticket');
  assert.deepEqual(result.value, {text: 'Xin chào'});
  assert.equal(result.files[0].size, 3);
});
test('partial operation is invisible and retry completes it', async () => {
  const remote = new Memory(); const put = remote.put.bind(remote); let fail = true;
  remote.put = async (key, bytes) => { if (fail && key.includes('/commits/')) throw Error('Offline'); return put(key, bytes); };
  const store = new Journal(remote, 'mes-ticket');
  await assert.rejects(store.commit(operation()), /Offline/);
  assert.equal(await store.restore('ticket'), null);
  fail = false;
  await store.commit(operation());
  assert.equal((await store.restore('ticket')).id, 'one');
});
test('lost acknowledgement can be retried without duplicate commit', async () => {
  const remote = new Memory(); const put = remote.put.bind(remote); let fail = true;
  remote.put = async (key, bytes) => { await put(key, bytes); if (fail && key.includes('/commits/')) { fail = false; throw Error('Lost acknowledgement'); } };
  const store = new Journal(remote, 'mes-ticket');
  await assert.rejects(store.commit(operation()));
  await store.commit(operation());
  assert.equal((await remote.list('mes-ticket/records/ticket/commits/')).length, 1);
  await assert.rejects(store.commit(operation({value: {text: 'changed'}})), /reused/);
});
test('stale version cannot overwrite committed data', async () => {
  const store = new Journal(new Memory(), 'mes-ticket');
  await store.commit(operation());
  await assert.rejects(store.commit(operation({id: 'two'})), /Version conflict/);
  await store.commit(operation({id: 'two', expectedVersion: 'one', value: {text: 'new'}}));
  assert.equal((await store.restore('ticket')).value.text, 'new');
});
test('deletion keeps versions and writes a tombstone', async () => {
  const remote = new Memory(); const store = new Journal(remote, 'mes-ticket');
  await store.commit(operation());
  await store.commit(operation({id: 'two', expectedVersion: 'one', action: 'delete'}));
  assert.equal((await store.restore('ticket')).action, 'delete');
  assert.ok(await remote.get('mes-ticket/records/ticket/versions/one.json'));
});
test('corruption prevents successful restore', async () => {
  const remote = new Memory(); const store = new Journal(remote, 'mes-ticket');
  await store.commit(operation());
  remote.data.set('mes-ticket/records/ticket/versions/one.json', Buffer.from('{}'));
  await assert.rejects(store.restore('ticket'), /Corrupt/);
});
test('queued calls cannot race or mutate captured payloads', async () => {
  const store = new Journal(new Memory(), 'mes-ticket'); const input = operation();
  const first = store.commit(input); input.value.text = 'mutated';
  const second = store.commit(operation({id: 'two'}));
  await first; await assert.rejects(second, /Version conflict/);
  assert.equal((await store.restore('ticket')).value.text, 'Xin chào');
});
test('restore rejects incomplete history', async () => {
  const remote = new Memory(); const store = new Journal(remote, 'mes-ticket');
  await store.commit(operation());
  await store.commit(operation({id: 'two', expectedVersion: 'one'}));
  remote.data.delete('mes-ticket/records/ticket/commits/one.json');
  await assert.rejects(store.restore('ticket'), /Incomplete/);
});
test('paths and duplicate attachments are rejected', async () => {
  const store = new Journal(new Memory(), 'mes-ticket');
  await assert.rejects(store.commit(operation({recordId: '../secret'})));
  await assert.rejects(store.commit(operation({files: [{id: 'a', name: 'a', bytes: ''}, {id: 'a', name: 'b', bytes: ''}]})));
});
