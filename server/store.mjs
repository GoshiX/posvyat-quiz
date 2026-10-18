import { DatabaseSync } from 'node:sqlite';
import { mkdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs';
import { resolve, join } from 'node:path';
import { randomBytes, createHmac, createCipheriv, createDecipheriv } from 'node:crypto';

export const dataDir = resolve(process.env.DATA_DIR || 'data');
mkdirSync(dataDir, { recursive: true });
mkdirSync(join(dataDir, 'uploads'), { recursive: true });
const keyPath = join(dataDir, 'secret.key');
if (!existsSync(keyPath)) writeFileSync(keyPath, randomBytes(32), { mode: 0o600, flag: 'wx' });
const key = readFileSync(keyPath);
export const digest = value => createHmac('sha256', key).update(String(value)).digest('hex');
export function encrypt(value) {
  const iv = randomBytes(12), cipher = createCipheriv('aes-256-gcm', key, iv);
  const data = Buffer.concat([cipher.update(value, 'utf8'), cipher.final()]);
  return Buffer.concat([iv, cipher.getAuthTag(), data]).toString('base64');
}
export function decrypt(value) {
  if (!value) return '';
  const b = Buffer.from(value, 'base64'), cipher = createDecipheriv('aes-256-gcm', key, b.subarray(0, 12));
  cipher.setAuthTag(b.subarray(12, 28));
  return Buffer.concat([cipher.update(b.subarray(28)), cipher.final()]).toString('utf8');
}
export const db = new DatabaseSync(join(dataDir, 'quiz.sqlite'));
db.exec(`PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA busy_timeout=5000;
CREATE TABLE IF NOT EXISTS events (environment TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS records (environment TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(environment,kind,id));
CREATE TABLE IF NOT EXISTS answers (environment TEXT NOT NULL, question_id TEXT NOT NULL, participant_id TEXT NOT NULL, option_index INTEGER NOT NULL, automatic INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(environment,question_id,participant_id));
CREATE TABLE IF NOT EXISTS results (environment TEXT NOT NULL, question_id TEXT NOT NULL, group_id TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(environment,question_id,group_id));
CREATE TABLE IF NOT EXISTS ballots (environment TEXT NOT NULL, participant_id TEXT NOT NULL, ratings TEXT NOT NULL, PRIMARY KEY(environment,participant_id));
CREATE TABLE IF NOT EXISTS reveals (environment TEXT NOT NULL, group_id TEXT NOT NULL, PRIMARY KEY(environment,group_id));
CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, role TEXT NOT NULL, environment TEXT NOT NULL, participant_id TEXT, expires INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS global_settings (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS oauth_states (state TEXT PRIMARY KEY, data TEXT NOT NULL, expires INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires);
`);
const freshEvent = () => ({runId:0,phase:'setup',currentQuestionId:null,started:false,displayStage:'blank',baseLocked:false,revision:0,settings:{trimCount:0,background:'#101114',logoUrl:''}});
for (const env of ['production','rehearsal']) db.prepare('INSERT OR IGNORE INTO events VALUES (?,?)').run(env,JSON.stringify(freshEvent()));
db.prepare('INSERT OR IGNORE INTO global_settings VALUES (1,?)').run(JSON.stringify({enabled:false,clientId:'',secret:'',publicUrl:'',lastTest:null}));
export function event(env) { return {runId:0,...JSON.parse(db.prepare('SELECT data FROM events WHERE environment=?').get(env).data)}; }
export function saveEvent(env, data) { db.prepare('UPDATE events SET data=? WHERE environment=?').run(JSON.stringify(data),env); }
export function list(env, kind) { return db.prepare('SELECT data FROM records WHERE environment=? AND kind=? ORDER BY rowid').all(env,kind).map(r=>JSON.parse(r.data)); }
export function get(env,kind,id) { const r=db.prepare('SELECT data FROM records WHERE environment=? AND kind=? AND id=?').get(env,kind,id); return r?JSON.parse(r.data):null; }
export function put(env,kind,record) { db.prepare('INSERT INTO records VALUES (?,?,?,?) ON CONFLICT(environment,kind,id) DO UPDATE SET data=excluded.data').run(env,kind,record.id,JSON.stringify(record)); return record; }
export function remove(env,kind,id) { db.prepare('DELETE FROM records WHERE environment=? AND kind=? AND id=?').run(env,kind,id); }
export function settings() { return JSON.parse(db.prepare('SELECT data FROM global_settings WHERE id=1').get().data); }
export function saveSettings(value) { db.prepare('UPDATE global_settings SET data=? WHERE id=1').run(JSON.stringify(value)); }
export function transaction(fn) { db.exec('BEGIN IMMEDIATE'); try { const result=fn(); db.exec('COMMIT'); return result; } catch(e) { db.exec('ROLLBACK'); throw e; } }
export function mutate(env,fn) { return transaction(()=>{const value=fn();const s=event(env);s.revision++;saveEvent(env,s);return value;}); }
export function resetEnvironment(env) {
  if(env!=='rehearsal') throw new Error('Only rehearsal can be reset');
  for(const table of ['records','answers','results','ballots','reveals']) db.prepare(`DELETE FROM ${table} WHERE environment=?`).run(env);
  db.prepare("DELETE FROM sessions WHERE environment=? AND role='participant'").run(env);
  saveEvent(env,freshEvent());
}
