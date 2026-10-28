import {createHash} from 'node:crypto';

export const digest = bytes => createHash('sha256').update(bytes).digest('hex');
const encode = value => Buffer.from(JSON.stringify(value));
const valid = value => typeof value === 'string' && /^[a-zA-Z0-9_-]{1,128}$/.test(value);

// Exactly one writer per application. The transport must implement immutable
// put(key, bytes, mime) and get(key). put must reject differing existing bytes.
// A failed acknowledgement is retried with the SAME operation id and payload.
export class Journal {
  constructor(transport, application) {
    if (!valid(application)) throw Error('Invalid application');
    this.transport = transport;
    this.application = application;
    this.queue = Promise.resolve();
  }

  commit(input) {
    // Capture bytes before queueing; callers cannot mutate an in-flight operation.
    let operation;
    try {
      if (!valid(input.id) || !valid(input.recordId)) throw Error('Invalid identifier');
      if (!['put', 'delete'].includes(input.action)) throw Error('Invalid action');
      if (input.expectedVersion !== null && !valid(input.expectedVersion)) throw Error('Expected version required');
      if (input.action === 'put' && input.value === undefined) throw Error('Value required');
      const files = (input.files || []).map(file => {
        if (!valid(file.id) || typeof file.name !== 'string' || !file.name || file.name.length > 255) throw Error('Invalid file');
        const bytes = Buffer.from(file.bytes);
        return {id: file.id, name: file.name, type: file.type || 'application/octet-stream', bytes};
      });
      if (new Set(files.map(f => f.id)).size !== files.length) throw Error('Duplicate file id');
      if (input.action === 'delete' && files.length) throw Error('Delete cannot upload files');
      operation = {id: input.id, recordId: input.recordId, action: input.action,
        expectedVersion: input.expectedVersion, value: input.action === 'put' ? JSON.parse(JSON.stringify(input.value)) : null, files};
    } catch (error) { return Promise.reject(error); }
    const work = this.queue.then(() => this.write(operation));
    this.queue = work.catch(() => {});
    return work;
  }

  async write(operation) {
    const prefix = `${this.application}/records/${operation.recordId}`;
    const files = operation.files.map(file => ({id: file.id, name: file.name, type: file.type,
      size: file.bytes.length, sha256: digest(file.bytes), key: `${this.application}/files/${operation.id}/${file.id}`}));
    const record = {schemaVersion: 1, application: this.application, id: operation.id,
      recordId: operation.recordId, action: operation.action, parent: operation.expectedVersion,
      value: operation.value, files};
    const recordBytes = encode(record);
    const receiptKey = `${prefix}/commits/${operation.id}.json`;
    const receipt = {schemaVersion: 1, recordKey: `${prefix}/versions/${operation.id}.json`, sha256: digest(recordBytes)};
    const previousReceipt = await this.transport.get(receiptKey);
    if (previousReceipt) {
      if (!previousReceipt.equals(encode(receipt))) throw Error('Operation id reused with different data');
      await this.readVersion(prefix, operation.id);
      return {version: operation.id, committed: true};
    }
    const head = await this.restore(operation.recordId);
    if ((head?.id ?? null) !== operation.expectedVersion) throw Error('Version conflict');
    for (let i = 0; i < files.length; i++) {
      await this.transport.put(files[i].key, operation.files[i].bytes, files[i].type);
      const remote = await this.transport.get(files[i].key);
      if (!remote || digest(remote) !== files[i].sha256) throw Error('File verification failed');
    }
    await this.transport.put(receipt.recordKey, recordBytes, 'application/json');
    const remote = await this.transport.get(receipt.recordKey);
    if (!remote || digest(remote) !== receipt.sha256) throw Error('Record verification failed');
    // Commit marker is LAST. Partial uploads are never visible to readers.
    await this.transport.put(receiptKey, encode(receipt), 'application/json');
    const confirmation = await this.transport.get(receiptKey);
    if (!confirmation?.equals(encode(receipt))) throw Error('Commit acknowledgement unavailable; retry same id');
    return {version: operation.id, committed: true};
  }

  async readVersion(prefix, id) {
    const marker = await this.transport.get(`${prefix}/commits/${id}.json`);
    if (!marker) return null;
    const receipt = JSON.parse(marker);
    if (receipt.schemaVersion !== 1 || receipt.recordKey !== `${prefix}/versions/${id}.json`) throw Error('Invalid receipt');
    const bytes = await this.transport.get(receipt.recordKey);
    if (!bytes || digest(bytes) !== receipt.sha256) throw Error('Corrupt or missing record');
    const record = JSON.parse(bytes);
    if (record.schemaVersion !== 1 || record.application !== this.application || record.id !== id ||
        `${this.application}/records/${record.recordId}` !== prefix ||
        !['put', 'delete'].includes(record.action) || (record.parent !== null && !valid(record.parent))) throw Error('Invalid record');
    for (const file of record.files) {
      if (!valid(file.id) || file.key !== `${this.application}/files/${id}/${file.id}`) throw Error('Invalid file reference');
      const content = await this.transport.get(file.key);
      if (!content || content.length !== file.size || digest(content) !== file.sha256) throw Error('Corrupt or missing file');
    }
    return record;
  }

  async restore(recordId) {
    if (!valid(recordId)) throw Error('Invalid record id');
    const prefix = `${this.application}/records/${recordId}`;
    const keys = await this.transport.list(`${prefix}/commits/`);
    const records = [];
    for (const key of keys) {
      const id = key.slice(`${prefix}/commits/`.length).replace(/\.json$/, '');
      if (!valid(id) || key !== `${prefix}/commits/${id}.json`) throw Error('Invalid commit key');
      records.push(await this.readVersion(prefix, id));
    }
    let parent = null, head = null;
    const visited = new Set();
    while (true) {
      const children = records.filter(record => record.parent === parent);
      if (children.length > 1) throw Error('Conflicting writers detected; manual reconciliation required');
      if (!children.length) break;
      head = children[0];
      if (visited.has(head.id)) throw Error('Cycle detected');
      visited.add(head.id);
      parent = head.id;
    }
    if (visited.size !== records.length) throw Error('Incomplete commit history');
    return head;
  }
}
