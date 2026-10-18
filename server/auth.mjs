import {randomBytes,timingSafeEqual,createHash} from 'node:crypto';
import {db,digest,event,get,put,list,mutate,settings,saveSettings,decrypt} from './store.mjs';
import {AppError,assert,environment} from './domain.mjs';
import {clientAddress} from './client-address.mjs';
export const cookies=req=>Object.fromEntries((req.headers.cookie||'').split(';').flatMap(pair=>{const i=pair.indexOf('=');if(i<0)return [];try{return [[pair.slice(0,i).trim(),decodeURIComponent(pair.slice(i+1))]];}catch{return [];}}));
export const secureCookie=req=>new URL(settings().publicUrl||'http://localhost').protocol==='https:'||!!req.socket.encrypted;
export function cookie(req,name,value,maxAge=172800){return `${name}=${encodeURIComponent(value)}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${maxAge}${secureCookie(req)?'; Secure':''}`;}
export function session(req,role){const token=cookies(req)[role==='admin'?'quiz_admin':'quiz_participant'];if(!token)return null;return db.prepare('SELECT * FROM sessions WHERE token=? AND role=? AND expires>?').get(digest(token),role,Date.now())||null;}
export function createSession(req,role,env='production',participantId=null){
  const token=randomBytes(32).toString('base64url');db.prepare('DELETE FROM sessions WHERE expires<?').run(Date.now());db.prepare('INSERT INTO sessions VALUES (?,?,?,?,?)').run(digest(token),role,env,participantId,Date.now()+172800000);
  if(participantId)mutate(env,()=>{const p=get(env,'participant',participantId);p.joined=true;put(env,'participant',p);});
  return cookie(req,role==='admin'?'quiz_admin':'quiz_participant',token);
}
export function logout(req,role){const token=cookies(req)[role==='admin'?'quiz_admin':'quiz_participant'];if(token)db.prepare('DELETE FROM sessions WHERE token=?').run(digest(token));return cookie(req,role==='admin'?'quiz_admin':'quiz_participant','',0);}
export function validAdmin(username,password){const expectedUser=process.env.ADMIN_USER||'admin',expectedPassword=process.env.ADMIN_PASSWORD||'quiz-admin-2026';return typeof username==='string'&&typeof password==='string'&&timingSafeEqual(Buffer.from(digest(`${username}\0${password}`)),Buffer.from(digest(`${expectedUser}\0${expectedPassword}`)));}
const failures=new Map();
export function loginLimit(req){const key=clientAddress(req),v=failures.get(key);if(v&&v.expires>Date.now())assert(v.count<40,'Слишком много попыток. Попробуйте через минуту',429);else failures.delete(key);}
export function failedLogin(req){const key=clientAddress(req),v=failures.get(key);failures.set(key,{count:(v?.count||0)+1,expires:Date.now()+60000});if(failures.size>10000)for(const [k,x] of failures)if(x.expires<Date.now())failures.delete(k);}
export function loginByCode(req,b){loginLimit(req);const env=environment(b.environment||'production');assert(typeof b.code==='string'&&b.code.length<=128,'Введите персональный код');const p=list(env,'participant').find(p=>p.codeHash===digest(b.code.trim()));if(!p){failedLogin(req);throw new AppError('Неверный код',401);}return createSession(req,'participant',env,p.id);}

// Overrides are for offline integration tests, never browser/admin-configurable endpoints.
const oauthEndpoint=()=>process.env.YANDEX_AUTHORIZE_URL||'https://oauth.yandex.ru/authorize';
const tokenEndpoint=()=>process.env.YANDEX_TOKEN_URL||'https://oauth.yandex.ru/token';
const userEndpoint=()=>process.env.YANDEX_USER_URL||'https://login.yandex.ru/info';
export function oauthStart(req,url){
  const y=settings(),test=url.searchParams.get('test')==='1',admin=session(req,'admin');
  if(test)assert(admin,'Войдите в админку',401);else assert(y.enabled,'Вход через Яндекс выключен',400);
  assert(y.clientId&&y.secret&&y.publicUrl,'Яндекс ещё не настроен');
  const env=environment(url.searchParams.get('environment')||'production');const state=randomBytes(32).toString('base64url'),binding=randomBytes(32).toString('base64url'),verifier=randomBytes(32).toString('base64url');
  const data={env,test,binding:digest(binding),verifier,adminToken:admin?.token??null,clientId:y.clientId,redirectUri:`${y.publicUrl}/auth/yandex/callback`};
  db.prepare('DELETE FROM oauth_states WHERE expires<?').run(Date.now());db.prepare('INSERT INTO oauth_states VALUES (?,?,?)').run(digest(state),JSON.stringify(data),Date.now()+600000);
  const target=new URL(oauthEndpoint());target.search=new URLSearchParams({response_type:'code',client_id:y.clientId,redirect_uri:data.redirectUri,scope:'login:email',state,code_challenge:createHash('sha256').update(verifier).digest('base64url'),code_challenge_method:'S256',force_confirm:'yes'}).toString();
  return {location:target.href,cookie:cookie(req,'quiz_oauth',binding,600)};
}
export async function oauthCallback(req,url){
  const state=url.searchParams.get('state'),binding=cookies(req).quiz_oauth;
  const record=state?db.prepare('SELECT * FROM oauth_states WHERE state=? AND expires>?').get(digest(state),Date.now()):null;
  assert(record&&binding,'Сессия входа истекла. Попробуйте снова');const data=JSON.parse(record.data);assert(data.binding===digest(binding),'Сессия входа не совпадает');
  db.prepare('DELETE FROM oauth_states WHERE state=?').run(digest(state));
  const clear=cookie(req,'quiz_oauth','',0);
  const fail=message=>{
    if(data.test){const y=settings();y.lastTest={ok:false,email:'',message,at:new Date().toISOString()};saveSettings(y);return {location:'/admin?yandex=error',cookies:[clear]};}
    return {location:`/?authError=${encodeURIComponent(message)}${data.env==='rehearsal'?'&env=rehearsal':''}`,cookies:[clear]};
  };
  if(url.searchParams.has('error'))return fail('Вход через Яндекс отменён');
  const code=url.searchParams.get('code');if(!code)return fail('Яндекс не передал код авторизации');
  const y=settings();if(y.clientId!==data.clientId||`${y.publicUrl}/auth/yandex/callback`!==data.redirectUri)return fail('Настройки Яндекса изменились. Повторите вход');
  if(data.test&&session(req,'admin')?.token!==data.adminToken)return fail('Сессия администратора истекла');
  if(!data.test&&!y.enabled)return fail('Вход через Яндекс выключен');
  try{
    const tokenResponse=await fetch(tokenEndpoint(),{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({grant_type:'authorization_code',code,client_id:y.clientId,client_secret:decrypt(y.secret),code_verifier:data.verifier}),signal:AbortSignal.timeout(12000)});
    if(!tokenResponse.ok)return fail('Не удалось получить токен Яндекса. Проверьте настройки приложения');
    const token=await tokenResponse.json();if(!token.access_token)return fail('Яндекс не передал токен');
    const userResponse=await fetch(`${userEndpoint()}?format=json`,{headers:{Authorization:`OAuth ${token.access_token}`},signal:AbortSignal.timeout(12000)});if(!userResponse.ok)return fail('Не удалось получить почту Яндекса');
    const info=await userResponse.json();const email=typeof info.default_email==='string'?info.default_email.trim().toLowerCase():'';if(!email)return fail('Разрешите доступ к электронной почте');
    if(data.test){const fresh=settings();fresh.lastTest={ok:true,email,message:'Авторизация и получение почты проверены',at:new Date().toISOString()};saveSettings(fresh);return {location:'/admin?yandex=success',cookies:[clear]};}
    const p=list(data.env,'participant').find(p=>p.email===email);if(!p)return fail('Этой почты нет в списке участников');
    return {location:data.env==='rehearsal'?'/?env=rehearsal':'/',cookies:[clear,createSession(req,'participant',data.env,p.id)]};
  }catch{return fail('Яндекс временно недоступен. Используйте персональный код');}
}
