import {app, api, esc, avatar, decimal, number, poll} from './shared.js';

document.documentElement.classList.add('projector-document');
document.body.classList.add('projector-body');
app.className='projector';
app.setAttribute('aria-label','Таблица результатов');
app.innerHTML='<div class="projector-grid" id="projector-grid"></div>';
document.querySelector('#toast').remove();
const grid=document.querySelector('#projector-grid');
const environment=new URLSearchParams(location.search).get('env')==='rehearsal'?'rehearsal':'production';
const reduced=matchMedia('(prefers-reduced-motion: reduce)');
const rows=new Map();
const motions=new Set();
const queue=[];
const transferUnit=1000;
const timing={highlight:350,expression:7000,zero:300,total:180,reorder:650,settle:700};
const gentleTiming={highlight:150,expression:7000,count:150,zero:180,total:0,reorder:120,settle:200};
// The observed table may be several reveals ahead of the table on the projector.
// Keep their scores separate until each group's own count has finished.
let observed=null;
let displayed={groups:[],stage:'blank',background:'#101113'};
let snapshot='';
let generation=0;
let running=false;
let active=null;
let controller=null;
let layoutFrame;

function layout() {
  const width=app.clientWidth||innerWidth;
  const height=app.clientHeight||innerHeight;
  const count=displayed.groups.length;
  let columns=count?Math.max(1,Math.min(count,Math.round(Math.sqrt(count*(width/height)/5.4)))):1;
  if(width<620)columns=Math.min(columns,2);
  const rowCount=Math.max(1,Math.ceil(count/columns));
  columns=Math.ceil(count/rowCount)||1;
  const spacing=Math.max(6,Math.min(22,height*.018));
  const rowHeight=(height-spacing*2-spacing*(rowCount-1))/rowCount;
  const columnWidth=(width-spacing*2-spacing*(columns-1))/columns;
  grid.style.setProperty('--columns',columns);
  grid.style.setProperty('--rows',rowCount);
  grid.style.setProperty('--gap',`${spacing}px`);
  grid.style.setProperty('--score-font',`${Math.max(12,Math.min(rowHeight*.38,columnWidth*.12,64))}px`);
  grid.style.setProperty('--number-font',`${Math.max(12,Math.min(rowHeight*.29,columnWidth*.09,48))}px`);
  grid.style.setProperty('--avatar-size',`${Math.max(18,Math.min(rowHeight*.68,columnWidth*.2,92))}px`);
}

function background(next) {
  document.documentElement.style.background=next.background==='transparent'?'transparent':next.background;
  document.body.style.background='transparent';
  app.style.background='transparent';
  app.dataset.displayStage=next.stage;
}

function scoreText(score) {
  return displayed.stage==='audience'?decimal(score):number(score);
}

function setScore(row,value) {
  const score=row.querySelector('.projector-score');
  score.dataset.value=String(value);
  score.textContent=scoreText(value);
}

function expression(row,value,total) {
  const score=row.querySelector('.projector-score');
  if(!score.querySelector('.projector-score-addition')){
    score.innerHTML='<span class="projector-score-base"></span><span class="projector-score-addition"></span>';
  }
  const remaining=total-value;
  score.dataset.value=String(value);
  score.querySelector('.projector-score-base').textContent=number(value);
  score.querySelector('.projector-score-addition').textContent=`${remaining<0?'−':'+'}${number(Math.abs(remaining))}`;
}

function status(row,group) {
  row.dataset.revealed=String(Boolean(group.revealed));
  row.classList.toggle('is-revealed',displayed.stage==='main'&&Boolean(group.revealed));
  row.classList.toggle('is-base-score',displayed.stage==='main'&&!group.revealed);
}

function syncRows() {
  const ids=new Set(displayed.groups.map(group=>group.id));
  for(const [id,row] of rows)if(!ids.has(id)){row.remove();rows.delete(id);}
  for(const group of displayed.groups){
    let row=rows.get(group.id);
    if(!row){
      row=document.createElement('div');
      row.className='projector-row';
      row.dataset.testid='screen-row';
      row.dataset.groupId=group.id;
      row.dataset.revealStage='idle';
      row.innerHTML=`${avatar(group.iconUrl,group.number)}<span class="projector-number">${esc(group.number)}</span><strong class="projector-score" data-testid="screen-score"></strong>`;
      rows.set(group.id,row);
    }
    if(row.dataset.icon!==(group.iconUrl||'')||row.dataset.number!==String(group.number)){
      row.querySelector('.avatar').outerHTML=avatar(group.iconUrl,group.number);
      row.querySelector('.projector-number').textContent=group.number;
      row.dataset.icon=group.iconUrl||'';
      row.dataset.number=String(group.number);
    }
    // Polls can refresh icons/backgrounds without interrupting the current expression.
    if(active?.id!==group.id){status(row,group);setScore(row,group.score);}
  }
}

function positions() {
  return new Map([...rows].map(([id,row])=>[id,row.getBoundingClientRect()]));
}

function place(animate=false) {
  const before=animate?positions():null;
  displayed.groups.forEach(group=>grid.append(rows.get(group.id)));
  layout();
  if(!animate||reduced.matches)return;
  for(const [id,row] of rows){
    const old=before.get(id);
    if(!old)continue;
    const next=row.getBoundingClientRect();
    const x=old.left-next.left,y=old.top-next.top;
    if(Math.abs(x)<.5&&Math.abs(y)<.5)continue;
    const motion=row.animate([
      {transform:`translate(${x}px,${y}px)`},
      {transform:'translate(0,0)'},
    ],{duration:timing.reorder,easing:'cubic-bezier(.16,1,.3,1)'});
    motions.add(motion);
    motion.finished.catch(()=>{}).finally(()=>motions.delete(motion));
  }
}

function queueState() {
  grid.dataset.revealQueue=String(queue.length);
}

function stage(row,value) {
  row.dataset.revealStage=value;
  grid.dataset.revealStage=value;
  grid.dataset.activeGroup=row.dataset.groupId;
  row.classList.add('is-revealing');
}

function pause(duration,signal) {
  return new Promise(resolve=>{
    if(signal.aborted){resolve(false);return;}
    const finish=value=>{clearTimeout(timer);signal.removeEventListener('abort',abort);resolve(value);};
    const abort=()=>finish(false);
    const timer=setTimeout(()=>finish(true),duration);
    signal.addEventListener('abort',abort,{once:true});
  });
}

function count(row,from,to,duration,signal) {
  if(reduced.matches||from===to){expression(row,to,to);return pause(duration,signal);}
  return new Promise(resolve=>{
    const started=performance.now();
    const steps=Math.abs(to-from);
    const direction=Math.sign(to-from);
    let previous=-1;
    let frame;
    const finish=value=>{cancelAnimationFrame(frame);signal.removeEventListener('abort',abort);resolve(value);};
    const abort=()=>finish(false);
    const tick=now=>{
      if(signal.aborted){finish(false);return;}
      const elapsed=Math.max(0,now-started);
      const progress=Math.min(1,elapsed/duration);
      // Reach 1, 2, 4, 8 transferred points at 1, 2, 3, 4 units of time.
      // Derive both sides from the same integer so the total stays constant.
      const step=progress===1?steps:Math.min(steps,Math.floor(2**(elapsed/transferUnit-1)));
      if(step!==previous){expression(row,from+direction*step,to);previous=step;}
      if(progress<1)frame=requestAnimationFrame(tick);else finish(true);
    };
    if(signal.aborted){resolve(false);return;}
    expression(row,from,to);
    signal.addEventListener('abort',abort,{once:true});
    frame=requestAnimationFrame(tick);
  });
}

async function reveal(job,signal) {
  const row=rows.get(job.id);
  const group=displayed.groups.find(group=>group.id===job.id);
  if(!row||!group)return;
  const duration=reduced.matches?gentleTiming:timing;
  const from=Number(group.score)||0;
  const to=Number(job.score)||0;
  stage(row,'highlight');
  if(!await pause(duration.highlight,signal))return;

  stage(row,'expression');
  const score=row.querySelector('.projector-score');
  expression(row,from,to);
  if(!await pause(duration.expression,signal))return;

  stage(row,'count');
  group.revealed=true;
  status(row,group);
  const steps=Math.abs(to-from);
  const countDuration=reduced.matches?duration.count:steps?transferUnit*(1+Math.log2(steps)):0;
  if(!await count(row,from,to,countDuration,signal))return;

  stage(row,'zero');
  if(!await pause(duration.zero,signal))return;
  score.style.setProperty('--addition-width',`${score.querySelector('.projector-score-addition').getBoundingClientRect().width}px`);
  stage(row,'total');
  if(!await pause(duration.total,signal))return;
  setScore(row,to);

  group.score=to;
  stage(row,'reorder');
  // Sort the scores actually shown, never the future scores already in the queue.
  // The server's order resolves equal scores once both groups have reached them.
  const order=new Map(observed.groups.map((group,index)=>[group.id,index]));
  displayed.groups.sort((a,b)=>b.score-a.score||order.get(a.id)-order.get(b.id));
  place(true);
  if(!await pause(duration.reorder,signal))return;

  stage(row,'settle');
  if(!await pause(duration.settle,signal))return;
  row.classList.remove('is-revealing');
  row.dataset.revealStage='complete';
}

async function drain() {
  if(running||!queue.length)return;
  const version=generation;
  running=true;
  try{
    while(queue.length&&version===generation){
      active=queue.shift();
      queueState();
      controller=new AbortController();
      await reveal(active,controller.signal);
      if(version!==generation)return;
      active=null;
    }
  }finally{
    if(version===generation){
      running=false;
      controller=null;
      grid.dataset.revealStage='idle';
      delete grid.dataset.activeGroup;
    }
  }
}

function cancel() {
  generation++;
  controller?.abort();
  controller=null;
  active=null;
  running=false;
  queue.length=0;
  queueState();
  motions.forEach(motion=>motion.cancel());
  motions.clear();
  rows.forEach(row=>{row.classList.remove('is-revealing');row.dataset.revealStage='idle';});
  grid.dataset.revealStage='idle';
  delete grid.dataset.activeGroup;
}

function install(next) {
  cancel();
  displayed={...next,groups:next.groups.map(group=>({...group}))};
  background(next);
  syncRows();
  place();
}

function receive(next) {
  const old=observed;
  observed=next;
  const previous=new Map(old?.groups.map(group=>[group.id,group])||[]);
  const reset=!old||old.runId!==next.runId||
    next.groups.some(group=>previous.get(group.id)?.revealed&&!group.revealed)||
    (active&&!next.groups.some(group=>group.id===active.id));
  if(reset||next.stage!=='main'){install(next);return;}

  const enteringMain=old.stage!=='main';
  if(enteringMain){
    // show_main and the first reveal may share one poll. Establish the base
    // table first so that this first reveal receives the same full sequence.
    install({...next,groups:next.groups.map(group=>({...group,score:group.baseScore??group.score,revealed:false}))});
  }

  const order=new Map((next.revealOrder||[]).map((id,index)=>[id,index]));
  const revealed=next.groups.filter(group=>group.revealed&&(enteringMain||previous.has(group.id)&&!previous.get(group.id).revealed));
  revealed.sort((a,b)=>(order.get(a.id)??Infinity)-(order.get(b.id)??Infinity));
  revealed.forEach(group=>queue.push({...group}));
  queueState();

  const pending=new Set(queue.map(group=>group.id));
  if(active)pending.add(active.id);
  const shown=new Map(displayed.groups.map(group=>[group.id,group]));
  // Retain the current row order until the active count reaches its total.
  displayed.groups=displayed.groups.filter(group=>next.groups.some(next=>next.id===group.id));
  for(const group of next.groups){
    const current=shown.get(group.id);
    if(current){
      const {score,revealed}=current;
      Object.assign(current,group,pending.has(group.id)?{score,revealed}:{});
    }else displayed.groups.push({...group});
  }
  displayed.background=next.background;
  background(next);
  syncRows();
  if(!active&&!queue.length){
    displayed.groups.sort((a,b)=>next.groups.findIndex(group=>group.id===a.id)-next.groups.findIndex(group=>group.id===b.id));
    place();
  }else if(!active)place();
  void drain();
}

const observer=new ResizeObserver(()=>{
  cancelAnimationFrame(layoutFrame);
  layoutFrame=requestAnimationFrame(()=>{
    motions.forEach(motion=>motion.cancel());
    motions.clear();
    layout();
  });
});
observer.observe(app);
reduced.addEventListener('change',()=>{if(observed)install(observed);});
poll(async()=>{
  try{
    const next=await api(`/api/display?env=${environment}`);
    const sig=JSON.stringify([next.stage,next.background,next.runId,next.groups,next.revealOrder]);
    if(sig!==snapshot){snapshot=sig;receive(next);}
  }catch{/* Keep the last confirmed public table when offline. */}
});
