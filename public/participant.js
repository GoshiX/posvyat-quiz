import {app, api, esc, icon, poll, toast} from './shared.js';

let data = null;
let signature = '';
let answerPending = false;
let votePending = false;
let loginPending = false;
let ratings = {};
let ratingKey = '';
let waitingKey = '';
let waitingEmoji = '';
const environment = new URLSearchParams(location.search).get('env') === 'rehearsal' ? 'rehearsal' : 'production';
const letters = ['А','Б','В','Г'];
const waitingEmojis = ['🙂','😊','😌','🙌','✨','🌿','☀️','💫'];
const waitingPhases = ['setup','waiting','quiz_finished'];
app.className = 'participant-app';

function branding() {
  return data?.branding?.logoUrl ? `<img class="event-logo" src="${esc(data.branding.logoUrl)}" alt="Логотип мероприятия">` : '<div class="event-wordmark">ПОСВЯТ ФПМИ<span>2026</span></div>';
}
function identity() {
  const p = data.participant;
  return `<header class="participant-header"><div class="person-meta" data-testid="participant-identity"><span class="person-name">${esc(p.firstName)} ${esc(p.lastName)}</span><span class="person-group">Группа ${esc(p.groupNumber)}</span></div><button class="icon-button" id="logout" aria-label="Выйти" title="Выйти">${icon('logout')}</button></header><hr class="participant-divider" aria-hidden="true">`;
}
function statusPanel(symbol,title,description='') {
  return `<section class="phone-state" data-testid="participant-${esc(data?.state?.phase || 'loading')}"><span class="state-symbol">${icon(symbol,30)}</span><h1>${title}</h1>${description?`<p>${description}</p>`:''}</section>`;
}
function waitingPanel() {
  const key=JSON.stringify([data.environment,data.participant.id,data.state.runId??0,data.state.phase,data.state.currentQuestionId]);
  if(waitingKey!==key) {
    waitingKey=key;
    let previous=waitingEmoji;
    try { previous=sessionStorage.getItem('quiz-waiting:last-emoji')||previous; } catch {}
    const choices=waitingEmojis.filter(emoji=>emoji!==previous);
    waitingEmoji=choices[Math.floor(Math.random()*choices.length)];
    try { sessionStorage.setItem('quiz-waiting:last-emoji',waitingEmoji); } catch {}
  }
  return `<section class="phone-state waiting-state" data-testid="participant-waiting" data-phase="${esc(data.state.phase)}"><span class="waiting-emoji" data-testid="waiting-emoji" aria-hidden="true">${waitingEmoji}</span><h1>Ждём…</h1></section>`;
}
function yandexEntry() {
  return data?.yandexEnabled?`<a class="button primary full yandex-login" data-testid="participant-yandex" href="/auth/yandex/start?environment=${environment}"><img class="yandex-logo" src="/assets/yandex-id-logo.svg" width="24" height="24" alt="" aria-hidden="true"><span>Войти через Яндекс</span></a><div class="or-divider"><span>или</span></div>`:'';
}
function loginPage() {
  app.innerHTML = `<main class="phone-shell login-shell"><div class="login-brand" data-logo="${esc(data.branding?.logoUrl||'')}">${branding()}</div><section class="login-card"><h1>Начнём?</h1><p class="form-error" id="login-error" role="alert" hidden></p><div id="yandex-entry" data-enabled="${Boolean(data?.yandexEnabled)}">${yandexEntry()}</div><form id="participant-login" class="login-form"><input id="code" data-testid="participant-code" name="code" aria-label="Персональный код" aria-describedby="login-error" autocomplete="one-time-code" autocapitalize="none" autocorrect="off" spellcheck="false" placeholder="Введите код" required><button class="button secondary full code-login" data-testid="participant-login" type="submit">Войти ${icon('arrow')}</button></form></section><p class="login-note">Если не получается войти через Яндекс - попроси код у куратора.</p></main>`;
  const params = new URLSearchParams(location.search);
  const oauthError = params.get('authError') || params.get('error') || (params.get('yandex') === 'error' ? 'Не удалось войти через Яндекс. Попробуйте персональный код.' : '');
  if (oauthError) { document.querySelector('#login-error').textContent = oauthError; document.querySelector('#login-error').hidden=false; }
  document.querySelector('#participant-login').addEventListener('submit', async event => {
    event.preventDefault(); if(loginPending)return; loginPending=true;
    const button=event.target.querySelector('button'); button.disabled=true; button.textContent='Входим…';
    const errorElement = document.querySelector('#login-error'); errorElement.hidden=true;
    try { await api('/api/login',{method:'POST',body:{code:new FormData(event.target).get('code').trim(),environment}}); signature=''; await refresh(); }
    catch(error) { errorElement.textContent=error.message; errorElement.hidden=false; }
    finally { loginPending=false; if(button.isConnected){button.disabled=false;button.innerHTML=`Войти ${icon('arrow')}`;} }
  });
}
function wireLogout() {
  document.querySelector('#logout')?.addEventListener('click',async () => {
    try { await api('/api/logout',{method:'POST',body:{}}); signature=''; await refresh(); } catch(error){toast(error.message,true);}
  });
}
function showQuestion() {
  const {question:q, state, answer, groupResult} = data;
  if(!q) return statusPanel('clock','Ждём следующий вопрос');
  const reveal=state.phase==='question_reveal';
  const open=state.phase==='question_open';
  const realAnswer=answer && !answer.automatic;
  const answered=Boolean(realAnswer) && !answerPending;
  const won=answered && answer.optionIndex===q.correctIndex;
  let badge = open ? answered?'Ответ принят':'Выберите один ответ' : reveal ? answered ? won?'Верно!':'В этот раз не угадали':'Вы не успели ответить' : 'Приём ответов завершён';
  return `<section class="question-view" data-testid="participant-${state.phase}"><div class="question-intro"><span class="eyebrow">${reveal?'РЕЗУЛЬТАТ ВОПРОСА':'КВИЗ'}</span><h1>${esc(q.text)}</h1><p data-testid="answer-status" class="answer-status ${reveal?(won?'success-text':answered?'error-text':''):answered?'success-text':''}" role="status">${answerPending?'Отправляем ответ…':answered?icon(reveal&&!won?'close':'check',16):''} ${esc(badge)}</p></div><div class="answer-options">${q.options.map((text,index)=>{
    const selected=answered && answer.optionIndex===index;
    const correct=reveal && q.correctIndex===index;
    return `<button class="answer-option ${selected?'selected':''} ${correct?'correct':''} ${reveal&&selected&&!correct?'incorrect':''}" data-testid="answer-${index}" data-answer="${index}" ${!open||answer||answerPending?'disabled':''} aria-pressed="${selected}"><span class="option-letter">${letters[index]}</span><span>${esc(text)}</span>${correct?`<span class="option-sign" aria-label="Правильный ответ">${icon('check')}</span>`:selected?`<span class="option-sign">${icon(reveal?'close':'check')}</span>`:''}</button>`;
  }).join('')}</div>${reveal&&groupResult?`<section class="group-result"><div class="section-line"><h2>Ваша группа</h2><span class="tag ${groupResult.won?'success':''}">${groupResult.won?'✓ Победа в вопросе':'Без баллов'}</span></div><p>Ответ группы — ${letters[groupResult.choice]}. ${groupResult.won?'Правильный вариант набрал больше всего голосов.':'Правильный вариант должен опередить каждый из остальных без ничьей.'}</p><div class="answer-bars">${q.options.map((_,i)=>{const total=groupResult.counts.reduce((a,b)=>a+b,0)||1;return `<div class="answer-bar ${i===q.correctIndex?'is-correct':''}"><span>${letters[i]}</span><div><i style="width:${groupResult.counts[i]/total*100}%"></i></div><strong>${groupResult.counts[i]}</strong></div>`;}).join('')}</div></section>`:''}</section>`;
}
function votingPage() {
  const groups = data.voting?.groups || [];
  const complete=groups.filter(group=>ratings[group.id]>=1&&ratings[group.id]<=10).length;
  return `<main class="phone-shell voting-shell">${identity()}<section class="voting-intro"><span class="eyebrow">ЗРИТЕЛЬСКИЕ СИМПАТИИ</span><h1>Ваши фавориты</h1><p>Оцените выступление каждой группы<br>от 1 до 10 сердечек.</p><div class="rating-progress"><span id="rating-count">Оценено ${complete} из ${groups.length}</span><span>Кроме своей группы</span></div></section><div class="voting-cards">${groups.map(group=>`<article class="voting-card" data-testid="vote-card-${esc(group.id)}" data-group="${esc(group.id)}"><div class="voting-media">${group.mediaUrl?`<img src="${esc(group.mediaUrl)}" loading="lazy" decoding="async" alt="${esc(group.title || `Выступление группы ${group.number}`)}">`:''}<span class="media-fallback" aria-hidden="true">${esc(group.number)}</span><span class="media-group-tag">Группа ${esc(group.number)}</span></div><div class="voting-card-content"><h2>${esc(group.title || `Группа ${group.number}`)}</h2>${group.description?`<p>${esc(group.description)}</p>`:''}<div class="rating-label"><span>Ваша оценка</span><strong id="rating-value-${esc(group.id)}">${ratings[group.id]?`${ratings[group.id]} / 10`:'— / 10'}</strong></div><div class="hearts" role="group" aria-label="Оценка группы ${esc(group.number)}">${Array.from({length:10},(_,i)=>`<button type="button" class="heart ${i<Number(ratings[group.id]||0)?'filled':''}" data-testid="rating-${esc(group.id)}-${i+1}" data-rate="${esc(group.id)}" data-value="${i+1}" aria-label="${i+1} из 10 для группы ${esc(group.number)}" aria-pressed="${Number(ratings[group.id])===i+1}">${icon('heart',25)}<span>${i+1}</span></button>`).join('')}</div></div></article>`).join('')}</div><footer class="voting-footer"><p id="vote-help">${complete===groups.length?'Все группы оценены. Можно отправлять.':'Оцените все группы, чтобы сохранить ответ.'}</p><button class="button primary full" id="submit-vote" data-testid="vote-submit" ${complete!==groups.length?'disabled':''}>Сохранить ответ ${icon('check')}</button><small>После отправки изменить оценки нельзя</small></footer></main>`;
}
function saveDraft() { try { localStorage.setItem(ratingKey,JSON.stringify(ratings)); } catch {} }
function wireRatings() {
  document.querySelectorAll('[data-rate]').forEach(button=>button.addEventListener('click',()=>{
    if(votePending)return;
    const id=button.dataset.rate; ratings[id]=Number(button.dataset.value); saveDraft();
    const card=button.closest('.voting-card');
    card.querySelectorAll('[data-value]').forEach(heart=>{heart.classList.toggle('filled',Number(heart.dataset.value)<=ratings[id]);heart.setAttribute('aria-pressed',String(Number(heart.dataset.value)===ratings[id]));});
    card.querySelector('.rating-label strong').textContent=`${ratings[id]} / 10`;
    const groups=data.voting.groups; const complete=groups.filter(group=>ratings[group.id]>=1&&ratings[group.id]<=10).length;
    document.querySelector('#rating-count').textContent=`Оценено ${complete} из ${groups.length}`;
    document.querySelector('#submit-vote').disabled=complete!==groups.length;
    document.querySelector('#vote-help').textContent=complete===groups.length?'Все группы оценены. Можно отправлять.':'Оцените все группы, чтобы сохранить ответ.';
  }));
  document.querySelector('#submit-vote').addEventListener('click',async()=>{
    if(votePending)return; votePending=true;
    const button=document.querySelector('#submit-vote');button.disabled=true;button.textContent='Сохраняем…';
    document.querySelectorAll('[data-rate]').forEach(item=>item.disabled=true);
    const payload=Object.fromEntries(data.voting.groups.map(group=>[group.id,ratings[group.id]]));
    try { await api('/api/vote',{method:'POST',body:{ratings:payload,runId:data.state.runId??0}}); try{localStorage.removeItem(ratingKey);}catch{} votePending=false;data.voting.submitted=true;signature='';render();await refresh(); }
    catch(error){votePending=false;toast(error.message,true);signature='';await refresh();}
    finally { votePending=false;if(button.isConnected){button.disabled=false;button.innerHTML=`Сохранить ответ ${icon('check')}`;document.querySelectorAll('[data-rate]').forEach(item=>item.disabled=false);}}
  });
}
function render() {
  if(!data.authenticated||!waitingPhases.includes(data.state.phase)||data.voting?.submitted)waitingKey='';
  if(!data.authenticated) {
    if(!document.querySelector('#participant-login'))loginPage();
    else {
      const entry=document.querySelector('#yandex-entry');
      if(entry.dataset.enabled!==String(Boolean(data.yandexEnabled))){entry.innerHTML=yandexEntry();entry.dataset.enabled=String(Boolean(data.yandexEnabled));}
      const brand=document.querySelector('.login-brand');
      if(brand.dataset.logo!==(data.branding?.logoUrl||'')){brand.innerHTML=branding();brand.dataset.logo=data.branding?.logoUrl||'';}
    }
    return;
  }
  const newKey=`quiz-ratings:${data.environment}:${data.participant.id}:run:${data.state.runId??0}`;
  if(ratingKey!==newKey){
    ratingKey=newKey;ratings={};
    try{const draft=JSON.parse(localStorage.getItem(ratingKey)||'{}');if(draft&&typeof draft==='object'&&!Array.isArray(draft))ratings=draft;}catch{}
  }
  const phase=data.state.phase;
  if(phase==='voting_closed'||data.voting?.submitted) {
    app.innerHTML=`<main class="phone-shell thanks-shell" data-testid="participant-thanks"><span class="thanks-symbol">${icon('heart',38)}</span><h1>Спасибо<br>за голосование</h1></main>`;return;
  }
  if(phase==='voting_open') {app.innerHTML=votingPage();wireLogout();wireRatings();return;}
  let content='';
  if(['question_open','question_closed','question_reveal'].includes(phase))content=showQuestion();
  else if(waitingPhases.includes(phase))content=waitingPanel();
  app.innerHTML=`<main class="phone-shell">${identity()}${content}</main>`;
  wireLogout();
  document.querySelectorAll('[data-answer]').forEach(button=>button.addEventListener('click',async()=>{
    if(answerPending||data.answer)return;
    answerPending=true;
    document.querySelectorAll('[data-answer]').forEach(item=>item.disabled=true);
    const status=document.querySelector('.answer-status');status.textContent='Отправляем ответ…';status.classList.remove('success-text');
    try { await api('/api/answer',{method:'POST',body:{questionId:data.question.id,optionIndex:Number(button.dataset.answer),runId:data.state.runId??0}}); answerPending=false;data.answer={optionIndex:Number(button.dataset.answer),automatic:false};signature='';render();await refresh(); }
    catch(error){ answerPending=false;toast(error.message,true);signature='';await refresh(); }
  }));
}
async function refresh() {
  try {
    const next=await api(`/api/participant?env=${environment}`);
    app.classList.remove('connection-lost');
    const {revision,...visibleState}=next.state || {};
    const nextSignature=JSON.stringify({...next,state:visibleState});
    if(answerPending||votePending){return;}
    data=next;
    if(nextSignature!==signature){signature=nextSignature;render();}
  } catch(error) {
    app.classList.add('connection-lost');
    if(!data)app.innerHTML=`<main class="phone-shell"><section class="phone-state"><span class="state-symbol">${icon('clock',30)}</span><h1>Нет соединения</h1><p>Пробуем подключиться снова…</p></section></main>`;
  }
}
poll(refresh);
