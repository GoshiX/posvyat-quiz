import { randomUUID,randomInt,randomBytes } from 'node:crypto';
import { db,event,saveEvent,list,get,put,remove,mutate,digest,resetEnvironment } from './store.mjs';
import { questionResult,quizTotal,audienceTotals } from './scoring.mjs';

export class AppError extends Error { constructor(message,status=400){super(message);this.status=status;} }
export const assert=(condition,message,status=400)=>{if(!condition)throw new AppError(message,status);};
export const text=(value,max=300)=>{assert(typeof value==='string','Ожидается текст');const v=value.trim();assert(v.length<=max,'Слишком длинный текст');return v;};
export const integer=(value,min=Number.MIN_SAFE_INTEGER,max=Number.MAX_SAFE_INTEGER)=>{assert(Number.isSafeInteger(value)&&value>=min&&value<=max,'Некорректное число');return value;};
export function environment(value='production'){assert(['production','rehearsal'].includes(value),'Неизвестный режим');return value;}
export const questions=env=>list(env,'question').sort((a,b)=>a.position-b.position);
export function groupsInVotingOrder(env){
  const order=event(env).settings.votingOrder??[];
  const ranks=new Map(order.map((id,index)=>[id,index]));
  return list(env,'group').sort((a,b)=>(ranks.get(a.id)??order.length)-(ranks.get(b.id)??order.length));
}
export const safeParticipant=p=>{const {codeHash,...rest}=p;return rest;};
export const wrong=correct=>{const choices=[0,1,2,3].filter(i=>i!==correct);return choices[randomInt(3)];};
export function imageUrl(value=''){
  const v=text(value,300);
  const local=/^\/uploads\/[a-f0-9-]+\.(png|jpg|gif|webp)$/.test(v);
  const stored=/^https:\/\/storage\.yandexcloud\.net\/[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]\/quiz\/[a-f0-9-]+\.(png|jpg|gif|webp)$/.test(v);
  assert(!v||local||stored,'Выберите загруженное изображение');return v;
}
function editable(env){assert(!event(env).started,'Настройки зафиксированы после начала квиза',409);}
export function saveGroup(env,b){return mutate(env,()=>{
  editable(env);const old=b.id?get(env,'group',b.id):null;assert(!b.id||old,'Группа не найдена',404);
  const number=text(b.number,30);assert(number,'Введите номер группы');assert(!list(env,'group').some(g=>g.number===number&&g.id!==b.id),'Номер группы уже используется');
  return put(env,'group',{id:old?.id||randomUUID(),number,title:text(b.title??'',160),description:text(b.description??'',1000),iconUrl:imageUrl(b.iconUrl??''),mediaUrl:imageUrl(b.mediaUrl??''),baseScore:integer(b.baseScore??0)});
});}
export function deleteRecord(env,kind,id){return mutate(env,()=>{editable(env);assert(get(env,kind,id),'Запись не найдена',404);if(kind==='group')assert(!list(env,'participant').some(p=>p.groupId===id),'Сначала удалите или перенесите участников группы');remove(env,kind,id);});}
export function saveParticipant(env,b){return mutate(env,()=>{
  const old=b.id?get(env,'participant',b.id):null;assert(!b.id||old,'Участник не найден',404);
  if(event(env).started)assert(old&&old.groupId===b.groupId&&old.firstName===b.firstName&&old.lastName===b.lastName,'Состав участников зафиксирован',409);
  const firstName=text(b.firstName,100),lastName=text(b.lastName,100);assert(firstName&&lastName,'Введите имя и фамилию');assert(get(env,'group',b.groupId),'Выберите группу');
  const email=text(b.email??'',254).toLowerCase();assert(!email||/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email),'Некорректная почта');assert(!email||!list(env,'participant').some(p=>p.email===email&&p.id!==b.id),'Эта почта уже используется');
  let code; if(!old||b.code){code=b.code?text(b.code,128):randomBytes(9).toString('base64url');assert(code.length>=6,'Код должен содержать минимум 6 символов');assert(!list(env,'participant').some(p=>p.codeHash===digest(code)&&p.id!==b.id),'Код уже используется');}
  const participant=put(env,'participant',{id:old?.id||randomUUID(),firstName,lastName,groupId:b.groupId,email,codeHash:code?digest(code):old.codeHash,joined:old?.joined||false});
  return {participant:safeParticipant(participant),...(code?{code}:{})};
});}
export function regenerateCode(env,id){return mutate(env,()=>{const p=get(env,'participant',id);assert(p,'Участник не найден',404);const code=randomBytes(9).toString('base64url');p.codeHash=digest(code);put(env,'participant',p);return {participant:safeParticipant(p),code};});}
export function saveQuestion(env,b){return mutate(env,()=>{
  editable(env);const old=b.id?get(env,'question',b.id):null;assert(!b.id||old,'Вопрос не найден',404);const qtext=text(b.text,2000);assert(qtext,'Введите вопрос');assert(Array.isArray(b.options)&&b.options.length===4,'Нужно четыре варианта');const options=b.options.map(x=>text(x,500));assert(options.every(Boolean),'Заполните все варианты');
  return put(env,'question',{id:old?.id||randomUUID(),text:qtext,options,correctIndex:integer(b.correctIndex,0,3),position:old?.position??(Math.max(-1,...questions(env).map(q=>q.position))+1)});
});}
export function reorderQuestions(env,ids){return mutate(env,()=>{editable(env);const all=questions(env);assert(Array.isArray(ids)&&ids.length===all.length&&new Set(ids).size===ids.length&&ids.every(id=>all.some(q=>q.id===id)),'Некорректный порядок');ids.forEach((id,position)=>put(env,'question',{...get(env,'question',id),position}));});}
export function reorderGroups(env,ids){return mutate(env,()=>{
  editable(env);const all=list(env,'group');
  assert(Array.isArray(ids)&&ids.length===all.length&&new Set(ids).size===ids.length&&ids.every(id=>all.some(g=>g.id===id)),'Некорректный порядок');
  const s=event(env);s.settings.votingOrder=[...ids];saveEvent(env,s);
});}
export function saveBase(env,b){return mutate(env,()=>{assert(!event(env).baseLocked,'Базовые баллы уже показаны и зафиксированы',409);const g=get(env,'group',b.groupId);assert(g,'Группа не найдена',404);g.baseScore=integer(b.baseScore);put(env,'group',g);return g;});}
function currentRun(s,runId=0){assert(runId===s.runId,'Мероприятие началось заново. Обновите страницу',409);}
export function acceptAnswer(env,participantId,questionId,optionIndex,runId){return mutate(env,()=>{
  const s=event(env);currentRun(s,runId);assert(s.phase==='question_open'&&s.currentQuestionId===questionId,'Приём ответов закрыт',409);assert(get(env,'participant',participantId),'Участник не найден',401);integer(optionIndex,0,3);
  assert(!db.prepare('SELECT 1 FROM answers WHERE environment=? AND question_id=? AND participant_id=?').get(env,questionId,participantId),'Ответ уже принят',409);
  db.prepare('INSERT INTO answers VALUES (?,?,?,?,0)').run(env,questionId,participantId,optionIndex);return {ok:true};
});}
export function acceptVote(env,participantId,ratings,runId){return mutate(env,()=>{
  const s=event(env);currentRun(s,runId);assert(s.phase==='voting_open','Голосование закрыто',409);const p=get(env,'participant',participantId);assert(p,'Участник не найден',401);
  assert(!db.prepare('SELECT 1 FROM ballots WHERE environment=? AND participant_id=?').get(env,participantId),'Оценки уже сохранены',409);
  const ids=list(env,'group').filter(g=>g.id!==p.groupId).map(g=>g.id);assert(ratings&&typeof ratings==='object'&&!Array.isArray(ratings),'Оцените все группы');const keys=Object.keys(ratings);assert(keys.length===ids.length&&keys.every(id=>ids.includes(id)),'Оцените каждую другую группу');for(const id of ids)integer(ratings[id],1,10);
  db.prepare('INSERT INTO ballots VALUES (?,?,?)').run(env,participantId,JSON.stringify(ratings));return {ok:true};
});}
export function ballots(env){return db.prepare('SELECT participant_id,ratings FROM ballots WHERE environment=?').all(env).map(b=>({participantId:b.participant_id,ratings:JSON.parse(b.ratings)}));}
export function scoring(env){
  const groups=list(env,'group'),participants=list(env,'participant');const audiences=audienceTotals(groups,participants,ballots(env),event(env).settings.trimCount);
  const results=db.prepare('SELECT group_id,data FROM results WHERE environment=?').all(env);const revealed=new Set(db.prepare('SELECT group_id FROM reveals WHERE environment=?').all(env).map(r=>r.group_id));
  return groups.map(g=>{const wins=results.filter(r=>r.group_id===g.id&&JSON.parse(r.data).won).length;const audience=audiences.find(a=>a.groupId===g.id);return {groupId:g.id,baseScore:g.baseScore,quizRaw:wins*1.5,quizScore:quizTotal(wins),totalScore:g.baseScore+quizTotal(wins),audienceScore:audience?.score??0,revealed:revealed.has(g.id),voterCount:audience?.voterCount??0,contributions:audience?.contributions??[],retained:audience?.retained??[]};});
}
export function action(env,b){return mutate(env,()=>{
  const s=event(env),qs=questions(env),groups=list(env,'group'),members=list(env,'participant');const current=qs.find(q=>q.id===s.currentQuestionId);const currentIndex=qs.findIndex(q=>q.id===s.currentQuestionId);
  if(b.runId!==undefined)currentRun(s,b.runId);
  switch(b.action){
    case 'restart_event':
      assert(b.confirm===true,'Подтвердите сброс результатов');assert(b.runId===s.runId,'Мероприятие изменилось. Обновите страницу',409);assert(s.started,'Мероприятие ещё не началось',409);
      for(const table of ['answers','results','ballots','reveals'])db.prepare(`DELETE FROM ${table} WHERE environment=?`).run(env);
      Object.assign(s,{runId:s.runId+1,phase:'setup',currentQuestionId:null,started:false,displayStage:'blank',baseLocked:false});break;
    case 'start_quiz':
      assert(s.phase==='setup','Квиз уже начат',409);assert(groups.length>=2,'Добавьте минимум две группы');assert(qs.length,'Добавьте вопросы');assert(members.length,'Добавьте участников');assert(groups.length-1-2*s.settings.trimCount>=1,'Отсечение удаляет все оценки: уменьшите его в настройках');
      s.started=true;s.phase='question_open';s.currentQuestionId=qs[0].id;break;
    case 'close_question': {
      assert(s.phase==='question_open','Вопрос не открыт',409);
      const answerRows=db.prepare('SELECT * FROM answers WHERE environment=? AND question_id=?').all(env,current.id);const answered=new Set(answerRows.map(a=>a.participant_id));
      for(const p of members)if(p.joined&&!answered.has(p.id)){const option=wrong(current.correctIndex);db.prepare('INSERT INTO answers VALUES (?,?,?,?,1)').run(env,current.id,p.id,option);answerRows.push({participant_id:p.id,option_index:option,automatic:1});}
      for(const g of groups){const ids=new Set(members.filter(p=>p.groupId===g.id).map(p=>p.id));const counts=[0,0,0,0];for(const a of answerRows)if(ids.has(a.participant_id))counts[a.option_index]++;const result=questionResult(counts,current.correctIndex,wrong);db.prepare('INSERT INTO results VALUES (?,?,?,?)').run(env,current.id,g.id,JSON.stringify(result));}
      s.phase='question_closed';break;
    }
    case 'reveal_question':assert(s.phase==='question_closed','Сначала закройте вопрос',409);s.phase='question_reveal';break;
    case 'wait':assert(s.phase==='question_reveal','Сначала покажите ответ',409);s.phase='waiting';break;
    case 'next_question':assert(s.phase==='waiting','Сначала перейдите в ожидание',409);assert(currentIndex+1<qs.length,'Вопросы закончились');s.currentQuestionId=qs[currentIndex+1].id;s.phase='question_open';break;
    case 'finish_quiz':assert(['question_reveal','waiting'].includes(s.phase)&&currentIndex===qs.length-1,'Завершите все вопросы',409);s.phase='quiz_finished';break;
    case 'open_voting':assert(s.phase==='quiz_finished','Сначала завершите квиз',409);s.phase='voting_open';break;
    case 'close_voting':assert(s.phase==='voting_open','Голосование не открыто',409);s.phase='voting_closed';break;
    case 'show_audience':assert(s.phase==='voting_closed'&&s.displayStage==='blank','Завершите голосование',409);s.displayStage='audience';break;
    case 'show_main':assert(s.displayStage==='audience','Сначала покажите зрительские симпатии',409);s.displayStage='main';s.baseLocked=true;break;
    case 'reveal_group':assert(s.displayStage==='main','Сначала покажите основную таблицу',409);assert(get(env,'group',b.groupId),'Группа не найдена',404);db.prepare('INSERT OR IGNORE INTO reveals VALUES (?,?)').run(env,b.groupId);break;
    default:throw new AppError('Неизвестное действие');
  }
  saveEvent(env,s);return {state:s};
});}
export function rehearsal(b){
  const env='rehearsal';
  if(b.action==='simulate'){
    const s=event(env);for(const p of list(env,'participant')){
      if(s.phase==='question_open'&&!db.prepare('SELECT 1 FROM answers WHERE environment=? AND question_id=? AND participant_id=?').get(env,s.currentQuestionId,p.id))acceptAnswer(env,p.id,s.currentQuestionId,randomInt(4),s.runId);
      if(s.phase==='voting_open'&&!db.prepare('SELECT 1 FROM ballots WHERE environment=? AND participant_id=?').get(env,p.id))acceptVote(env,p.id,Object.fromEntries(list(env,'group').filter(g=>g.id!==p.groupId).map(g=>[g.id,randomInt(1,11)])),s.runId);
    }return {ok:true};
  }
  assert(['seed','copy'].includes(b.action),'Неизвестное действие репетиции');
  return mutate(env,()=>{
    const previous=event(env);resetEnvironment(env);const s=event(env);s.revision=previous.revision;s.runId=previous.runId+1;
    const codes=[];
    if(b.action==='copy'){
      s.settings={...event('production').settings};
      for(const kind of ['group','question','participant'])for(const record of list('production',kind)){
        if(kind==='participant'){const code=randomBytes(9).toString('base64url');put(env,kind,{...record,joined:false,codeHash:digest(code)});codes.push({participantId:record.id,code});}
        else put(env,kind,record);
      }
    }else{
      const count=integer(b.groupCount??27,2,150),perGroup=integer(b.participantsPerGroup??2,1,50);s.settings.trimCount=count>=6?2:0;
      for(let i=0;i<count;i++){const id=randomUUID();put(env,'group',{id,number:String(601+i),title:['После звонка','Первый день','Большая перемена','Новый маршрут'][i%4],description:'Короткая история нашей группы',iconUrl:'',mediaUrl:'',baseScore:100-i*2});for(let j=0;j<perGroup;j++){const pid=randomUUID(),code=`demo-${601+i}-${j+1}`;put(env,'participant',{id:pid,firstName:['Алексей','Мария','Иван','Анна'][j%4],lastName:`Участник ${i+1}`,groupId:id,email:'',codeHash:digest(code),joined:false});codes.push({participantId:pid,code});}}
      const samples=[['Сколько граней у куба?',['4','6','8','12'],1],['Сколько сторон у шестиугольника?',['3','5','6','8'],2],['Какой цвет получится из синего и жёлтого?',['Зелёный','Красный','Белый','Фиолетовый'],0]];
      samples.forEach(([text,options,correctIndex],position)=>put(env,'question',{id:randomUUID(),text,options,correctIndex,position}));
    }
    saveEvent(env,s);return {ok:true,codes};
  });
}
