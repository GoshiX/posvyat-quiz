import http from 'node:http';
import {readFile,writeFile,stat} from 'node:fs/promises';
import {resolve,join,extname,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {db,dataDir,event,saveEvent,mutate,settings,saveSettings,encrypt} from './store.mjs';
import {AppError,assert,text,integer,environment,imageUrl,saveGroup,saveParticipant,regenerateCode,saveQuestion,reorderQuestions,reorderGroups,deleteRecord,saveBase,acceptAnswer,acceptVote,action,rehearsal} from './domain.mjs';
import {adminView,participantView,displayView,quizResultsView,publicYandex} from './views.mjs';
import {session,createSession,logout,validAdmin,loginLimit,failedLogin,loginByCode,oauthStart,oauthCallback} from './auth.mjs';
import {prepareImage} from './media.mjs';
import {storageSettings,saveStorageSettings,uploadToStorage,testStorageConnection} from './storage.mjs';

const root=resolve(dirname(fileURLToPath(import.meta.url)),'../public');
const mime={'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'text/javascript; charset=utf-8','.png':'image/png','.jpg':'image/jpeg','.gif':'image/gif','.webp':'image/webp','.svg':'image/svg+xml','.ico':'image/x-icon'};
const security={
  'X-Content-Type-Options':'nosniff','Referrer-Policy':'same-origin',
  'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https://storage.yandexcloud.net; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'self'; form-action 'self' https://oauth.yandex.ru",
  'Cache-Control':'no-store'
};
function json(res,body,status=200,headers={}){res.writeHead(status,{...security,'Content-Type':'application/json; charset=utf-8',...headers});res.end(JSON.stringify(body));}
async function body(req){
  assert((req.headers['content-type']||'').split(';')[0]==='application/json','Ожидается JSON',415);
  let size=0;const chunks=[];for await(const chunk of req){size+=chunk.length;assert(size<=15*1024*1024,'Файл слишком большой',413);chunks.push(chunk);}
  try{const b=JSON.parse(Buffer.concat(chunks).toString()||'{}');assert(b&&typeof b==='object'&&!Array.isArray(b),'Некорректный JSON');return b;}catch(e){if(e instanceof AppError)throw e;throw new AppError('Некорректный JSON');}
}
function checkOrigin(req){
  const origin=req.headers.origin;if(!origin)return;let allowed=false;try{allowed=new URL(origin).host===req.headers.host||origin===settings().publicUrl;}catch{}
  assert(allowed,'Запрос с другого сайта запрещён',403);
}
function updateSettings(env,b){return mutate(env,()=>{
  const s=event(env);
  if(b.trimCount!==undefined){assert(!s.started||b.trimCount===s.settings.trimCount,'Правила уже зафиксированы',409);s.settings.trimCount=integer(b.trimCount,0,1000);}
  if(b.background!==undefined){assert(b.background==='transparent'||/^#[\da-f]{6}$/i.test(b.background),'Выберите цвет фона или прозрачность');s.settings.background=b.background;}
  if(b.logoUrl!==undefined)s.settings.logoUrl=imageUrl(b.logoUrl);
  saveEvent(env,s);
  const y=settings();let changed=false;
  if(b.yandexEnabled!==undefined){assert(typeof b.yandexEnabled==='boolean','Некорректное значение');y.enabled=b.yandexEnabled;changed=true;}
  if(b.yandexClientId!==undefined){y.clientId=text(b.yandexClientId,300);changed=true;}
  if(b.yandexClientSecret){y.secret=encrypt(text(b.yandexClientSecret,1000));changed=true;}
  if(b.publicUrl!==undefined){const v=text(b.publicUrl,500);if(v){let u;try{u=new URL(v);}catch{throw new AppError('Некорректный адрес сайта');}assert(['http:','https:'].includes(u.protocol)&&!u.username&&!u.password&&!u.search&&!u.hash&&(u.pathname==='/'||u.pathname===''),'Введите адрес сайта без пути');y.publicUrl=u.origin;}else y.publicUrl='';changed=true;}
  if(y.enabled)assert(y.clientId&&y.secret&&y.publicUrl,'Укажите Client ID, Client Secret и адрес сайта');
  if(changed){y.lastTest=null;saveSettings(y);}return {settings:s.settings,yandex:publicYandex()};
});}
async function upload(b){
  assert(typeof b.data==='string'&&b.data.length<=14*1024*1024,'Некорректный файл');const bytes=Buffer.from(b.data,'base64');assert(bytes.length&&bytes.length<=10*1024*1024,'Размер файла должен быть до 10 МБ');
  const image=await prepareImage(bytes,{purpose:b.purpose??'card'});
  const name=`${randomUUID()}.${image.extension}`,storage=storageSettings();
  if(storage.enabled)return await uploadToStorage(image.bytes,{name,contentType:image.contentType},storage);
  await writeFile(join(dataDir,'uploads',name),image.bytes,{flag:'wx',mode:0o600});return {url:`/uploads/${name}`};
}
async function serveFile(res,path,immutable=false){try{const info=await stat(path);if(!info.isFile())throw new Error();const data=await readFile(path);res.writeHead(200,{...security,'Content-Type':mime[extname(path)]||'application/octet-stream','Content-Length':data.length,...(immutable?{'Cache-Control':'public, max-age=31536000, immutable'}:{})});res.end(data);}catch{json(res,{error:'Не найдено'},404);}}
const server=http.createServer(async(req,res)=>{
  try{
    const url=new URL(req.url,'http://localhost'),path=url.pathname,method=req.method;
    if(!['GET','HEAD','POST','DELETE'].includes(method))return json(res,{error:'Метод не поддерживается'},405);
    if(method==='POST'||method==='DELETE')checkOrigin(req);
    if(path==='/api/health')return json(res,{ok:true});
    if(path==='/auth/yandex/start'&&method==='GET'){const r=oauthStart(req,url);res.writeHead(302,{...security,Location:r.location,'Set-Cookie':r.cookie});return res.end();}
    if(path==='/auth/yandex/callback'&&method==='GET'){const r=await oauthCallback(req,url);res.writeHead(302,{...security,Location:r.location,'Set-Cookie':r.cookies});return res.end();}
    if(path==='/api/admin/login'&&method==='POST'){loginLimit(req);const b=await body(req);if(!validAdmin(b.username,b.password)){failedLogin(req);throw new AppError('Неверный логин или пароль',401);}return json(res,{ok:true},200,{'Set-Cookie':createSession(req,'admin')});}
    if(path==='/api/admin/logout'&&method==='POST'){await body(req);return json(res,{ok:true},200,{'Set-Cookie':logout(req,'admin')});}
    if(path==='/api/login'&&method==='POST')return json(res,{ok:true},200,{'Set-Cookie':loginByCode(req,await body(req))});
    if(path==='/api/logout'&&method==='POST'){await body(req);return json(res,{ok:true},200,{'Set-Cookie':logout(req,'participant')});}
    if(path==='/api/participant'&&method==='GET')return json(res,participantView(session(req,'participant'),environment(url.searchParams.get('env')||'production')));
    if(path==='/api/display'&&method==='GET')return json(res,displayView(environment(url.searchParams.get('env')||'production')));
    if(path==='/api/quiz/results'){assert(method==='GET','Метод не поддерживается',405);return json(res,quizResultsView(environment(url.searchParams.get('env')||'production')));}
    if(path==='/api/answer'||path==='/api/vote'){
      assert(method==='POST','Метод не поддерживается',405);const p=session(req,'participant');assert(p,'Войдите в систему',401);const b=await body(req);return json(res,path==='/api/answer'?acceptAnswer(p.environment,p.participant_id,b.questionId,b.optionIndex,b.runId):acceptVote(p.environment,p.participant_id,b.ratings,b.runId));
    }
    if(path.startsWith('/api/admin/')){
      assert(session(req,'admin'),'Войдите в админку',401);const env=environment(url.searchParams.get('env')||'production');
      if(path==='/api/admin/state'&&method==='GET')return json(res,adminView(env));
      if(method==='DELETE'){const m=path.match(/^\/api\/admin\/(groups|participants|questions)\/([^/]+)$/);assert(m,'Не найдено',404);deleteRecord(env,{groups:'group',participants:'participant',questions:'question'}[m[1]],m[2]);return json(res,{ok:true});}
      assert(method==='POST','Метод не поддерживается',405);const b=await body(req);
      const routes={
        '/api/admin/groups':()=>saveGroup(env,b),'/api/admin/groups/order':()=>reorderGroups(env,b.ids),'/api/admin/participants':()=>saveParticipant(env,b),'/api/admin/questions':()=>saveQuestion(env,b),'/api/admin/questions/order':()=>reorderQuestions(env,b.ids),'/api/admin/scores':()=>saveBase(env,b),'/api/admin/settings':()=>updateSettings(env,b),'/api/admin/upload':()=>upload(b),'/api/admin/storage':()=>saveStorageSettings(b),'/api/admin/storage/test':()=>testStorageConnection(),'/api/admin/action':()=>action(env,b),'/api/admin/rehearsal':()=>rehearsal(b)
      };
      if(routes[path])return json(res,(await routes[path]())??{ok:true});
      const m=path.match(/^\/api\/admin\/participants\/([^/]+)\/code$/);if(m)return json(res,regenerateCode(env,m[1]));throw new AppError('Не найдено',404);
    }
    if(path.startsWith('/api/'))throw new AppError('Не найдено',404);
    assert(method==='GET'||method==='HEAD','Метод не поддерживается',405);
    if(/^\/uploads\/[a-f0-9-]+\.(png|jpg|gif|webp)$/.test(path))return await serveFile(res,join(dataDir,path),true);
    const page=['/','/admin','/admin/','/screen','/screen/'].includes(path)?'index.html':decodeURIComponent(path).replace(/^\//,'');
    const file=resolve(root,page);assert(file.startsWith(root+'/'),'Не найдено',404);return await serveFile(res,file);
  }catch(e){if(!res.headersSent)json(res,{error:e instanceof AppError?e.message:'Ошибка сервера'},e.status||500);else res.end();if(!(e instanceof AppError))console.error(e);}
});
const port=Number(process.env.PORT||3000);if(!Number.isInteger(port)||port<1||port>65535)throw new Error('PORT must be 1..65535');
server.listen(port,process.env.HOST||'0.0.0.0',()=>console.log(`Quiz ready: http://localhost:${port}`));
server.requestTimeout=30000;server.headersTimeout=15000;
function shutdown(){server.close(()=>{db.close();process.exit(0);});server.closeIdleConnections();setTimeout(()=>process.exit(0),5000).unref();}
process.once('SIGTERM',shutdown);process.once('SIGINT',shutdown);
