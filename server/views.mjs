import {db,event,list,get,settings} from './store.mjs';
import {questions,groupsInVotingOrder,scoring,safeParticipant} from './domain.mjs';
import {publicStorage} from './storage.mjs';
export function publicYandex(){const y=settings();return {enabled:y.enabled,clientId:y.clientId,secretSet:!!y.secret,publicUrl:y.publicUrl,redirectUri:y.publicUrl?`${y.publicUrl}/auth/yandex/callback`:'',lastTest:y.lastTest};}
export function adminView(env){
  const s=event(env),groups=groupsInVotingOrder(env),participants=list(env,'participant'),qs=questions(env);
  const answers=s.currentQuestionId?db.prepare('SELECT participant_id FROM answers WHERE environment=? AND question_id=? AND automatic=0').all(env,s.currentQuestionId):[];
  const answered=new Set(answers.map(x=>x.participant_id));
  return {environment:env,state:s,settings:s.settings,groups,participants:participants.map(safeParticipant),questions:qs,scores:scoring(env),stats:{answeredCount:answers.length,joinedCount:participants.filter(p=>p.joined).length,participantCount:participants.length,submittedCount:db.prepare('SELECT count(*) n FROM ballots WHERE environment=?').get(env).n,byGroup:groups.map(g=>{const ps=participants.filter(p=>p.groupId===g.id);return {groupId:g.id,answeredCount:ps.filter(p=>answered.has(p.id)).length,participantCount:ps.length,joinedCount:ps.filter(p=>p.joined).length};})},yandex:publicYandex(),storage:publicStorage()};
}
export function participantView(session,anonymousEnv='production'){
  const env=session?.environment||anonymousEnv,s=event(env),y=settings();
  const base={authenticated:false,environment:env,state:{phase:s.phase,currentQuestionId:s.currentQuestionId,runId:s.runId,revision:s.revision},branding:{logoUrl:s.settings.logoUrl},yandexEnabled:!!(y.enabled&&y.clientId&&y.secret&&y.publicUrl)};
  const p=session?get(env,'participant',session.participant_id):null;if(!p)return base;
  const group=get(env,'group',p.groupId);base.authenticated=true;base.participant={id:p.id,firstName:p.firstName,lastName:p.lastName,groupId:p.groupId,groupNumber:group?.number??''};
  if(['question_open','question_closed','question_reveal'].includes(s.phase)){
    const q=get(env,'question',s.currentQuestionId);base.question={id:q.id,text:q.text,options:q.options};
    const a=db.prepare('SELECT option_index,automatic FROM answers WHERE environment=? AND question_id=? AND participant_id=?').get(env,q.id,p.id);
    base.answer=a&&(!a.automatic||s.phase==='question_reveal')?{optionIndex:a.option_index,automatic:!!a.automatic}:null;
    if(s.phase==='question_reveal'){base.question.correctIndex=q.correctIndex;const r=db.prepare('SELECT data FROM results WHERE environment=? AND question_id=? AND group_id=?').get(env,q.id,p.groupId);base.groupResult=r?JSON.parse(r.data):null;}
  }
  if(['voting_open','voting_closed'].includes(s.phase)){
    const submitted=!!db.prepare('SELECT 1 FROM ballots WHERE environment=? AND participant_id=?').get(env,p.id);
    base.voting={submitted,groups:!submitted&&s.phase==='voting_open'?groupsInVotingOrder(env).filter(g=>g.id!==p.groupId).map(({id,number,title,description,mediaUrl})=>({id,number,title,description,mediaUrl})):[]};
  }
  return base;
}
export function displayView(env){
  const s=event(env);const scores=scoring(env),groups=list(env,'group');
  const revealOrder=s.displayStage==='main'?db.prepare('SELECT group_id FROM reveals WHERE environment=? ORDER BY rowid').all(env).map(r=>r.group_id):[];
  return {stage:s.displayStage,background:s.settings.background,revision:s.revision,runId:s.runId,revealOrder,groups:s.displayStage==='blank'?[]:groups.map(g=>{const a=scores.find(a=>a.groupId===g.id);return {id:g.id,number:g.number,iconUrl:g.iconUrl,score:s.displayStage==='audience'?a.audienceScore:g.baseScore+(a.revealed?a.quizScore:0),revealed:a.revealed,...(s.displayStage==='main'?{baseScore:g.baseScore}:{})};}).sort((a,b)=>b.score-a.score)};
}

export function quizResultsView(env){
  const s=event(env),qs=questions(env);
  const question=qs.findIndex(q=>q.id===s.currentQuestionId)+1;
  const hidden={question,ready:false,groups:{}};
  // Keep published results available while the operator waits or moves on to voting.
  // Closing a question calculates results but does not publish them to the console.
  if(!question||!['question_reveal','waiting','quiz_finished','voting_open','voting_closed'].includes(s.phase))return hidden;
  const groups=list(env,'group');
  const results=new Map(db.prepare('SELECT group_id,data FROM results WHERE environment=? AND question_id=?').all(env,s.currentQuestionId).map(row=>[row.group_id,JSON.parse(row.data)]));
  if(!groups.length||groups.some(group=>!results.has(group.id)))return hidden;
  return {question,ready:true,groups:Object.fromEntries(groups.map(group=>[group.number,results.get(group.id).won?'g':'r']))};
}
