import {createHash} from 'node:crypto';

// Uses the existing application's OAuth token supplier, never a token from chat.
// The root MUST have been created/selected with that application's drive.file grant.
export class GoogleDriveTransport {
  constructor({rootId, accessToken, fetchImpl = fetch}) {
    if (!/^[\w-]+$/.test(rootId) || typeof accessToken !== 'function') throw Error('Drive connection required');
    this.rootId = rootId; this.accessToken = accessToken; this.fetch = fetchImpl;
  }
  async request(path, init = {}, upload = false) {
    const token = await this.accessToken();
    const response = await this.fetch(`https://www.googleapis.com/${upload ? 'upload/' : ''}drive/v3/${path}`, {
      ...init, headers: {...init.headers, Authorization: `Bearer ${token}`},
      signal: AbortSignal.timeout(60000)
    });
    if (!response.ok) throw Error(`Drive request failed (${response.status}); operation not acknowledged`);
    return response;
  }
  parts(key) {
    const parts = key.split('/');
    if (!parts.length || parts.some(part => !/^[a-zA-Z0-9_.-]{1,160}$/.test(part) || part === '.' || part === '..')) throw Error('Invalid Drive key');
    return parts;
  }
  async children(parent, name) {
    const query = new URLSearchParams({q: `'${parent}' in parents and trashed = false${name ? ` and name = '${name}'` : ''}`,
      fields: 'nextPageToken,files(id,name,mimeType,size,md5Checksum)', pageSize: '1000'});
    const files = []; let page;
    do {
      if (page) query.set('pageToken', page);
      const body = await (await this.request(`files?${query}`)).json();
      files.push(...body.files); page = body.nextPageToken;
    } while (page);
    return files;
  }
  async resolve(parts, create = false) {
    let parent = this.rootId;
    for (const name of parts) {
      const found = await this.children(parent, name);
      if (found.length > 1) throw Error('Duplicate Drive folder; reconcile before continuing');
      if (found.length) {
        if (found[0].mimeType !== 'application/vnd.google-apps.folder') throw Error('Drive path is not a folder');
        parent = found[0].id;
      } else if (!create) return null;
      else {
        const response = await this.request('files?fields=id', {method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({name, mimeType: 'application/vnd.google-apps.folder', parents: [parent]})});
        parent = (await response.json()).id;
      }
    }
    return parent;
  }
  async metadata(key, create = false) {
    const parts = this.parts(key); const name = parts.pop(); const parent = await this.resolve(parts, create);
    if (!parent) return {parent: null, name, file: null};
    const matches = await this.children(parent, name);
    if (matches.length > 1) throw Error('Duplicate Drive object; reconcile before continuing');
    return {parent, name, file: matches[0] || null};
  }
  async get(key) {
    const {file} = await this.metadata(key);
    if (!file) return null;
    if (file.mimeType === 'application/vnd.google-apps.folder') throw Error('Expected file');
    return Buffer.from(await (await this.request(`files/${file.id}?alt=media`)).arrayBuffer());
  }
  async put(key, bytes, type) {
    const {parent, name, file} = await this.metadata(key, true);
    if (file) {
      // Remote comparison makes retry safe after server/cache loss.
      const existing = await this.get(key);
      if (!existing.equals(bytes)) throw Error('Immutable Drive object conflict');
      return;
    }
    // Generate an ID first: final upload replay can use this ID after a timeout.
    const ids = await (await this.request('files/generateIds?count=1&space=drive&type=files')).json();
    const id = ids.ids[0];
    const session = await this.request('files?uploadType=resumable&fields=id,size,md5Checksum', {
      method: 'POST', headers: {'Content-Type': 'application/json', 'X-Upload-Content-Type': type,
        'X-Upload-Content-Length': String(bytes.length)},
      body: JSON.stringify({id, name, mimeType: type, parents: [parent]})
    }, true);
    const location = session.headers.get('location');
    if (!location || new URL(location).origin !== 'https://www.googleapis.com') throw Error('Invalid upload session');
    const result = await this.fetch(location, {method: 'PUT', body: bytes,
      headers: {'Content-Type': type, 'Content-Length': String(bytes.length)}, signal: AbortSignal.timeout(180000)});
    if (!result.ok) throw Error(`Drive upload failed (${result.status}); retry same operation`);
    const stored = await result.json();
    if (stored.id !== id || Number(stored.size) !== bytes.length || stored.md5Checksum !== createHash('md5').update(bytes).digest('hex')) throw Error('Drive upload verification failed');
  }
  async list(prefix) {
    if (!prefix.endsWith('/')) throw Error('Folder prefix required');
    const parent = await this.resolve(this.parts(prefix.slice(0, -1)));
    if (!parent) return [];
    const children = await this.children(parent);
    return children.map(file => {
      if (file.mimeType === 'application/vnd.google-apps.folder') throw Error('Unexpected directory in commit log');
      this.parts(file.name);
      return prefix + file.name;
    });
  }
}
