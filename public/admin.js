import {app, api, esc, icon, avatar, number, decimal, field, poll, toast, copyText, readFile, phaseLabels} from './shared.js';

const tabs=[['control','Управление','play'],['questions','Вопросы','question'],['groups','Группы','groups'],['participants','Участники','people'],['scores','Баллы','chart'],['settings','Настройки','settings']];
let environment=localStorage.getItem('quiz-admin-environment')==='rehearsal'?'rehearsal':'production';
let activeTab='control';
let state=null;
let busy=false;
let panelSignature='';
let settingsLoaded=false;
let storageTestError='';
let scoreGroupSignature='';
let ready=false;
const letters=['А','Б','В','Г'];
const envPath=path=>`${path}${path.includes('?')?'&':'?'}env=${environment}`;
app.className='admin-app';

function login() {
  if(document.querySelector('#admin-login'))return;
  ready=false;
  app.innerHTML=`<main class="admin-login"><div class="admin-login-brand">${icon('spark',30)}<span>ПОСВЯЩЕНИЕ</span></div><section class="login-card"><span class="eyebrow">ДЛЯ ОРГАНИЗАТОРОВ</span><h1>Управление вечером</h1><p class="lead">Войдите в панель администратора</p><form id="admin-login">${field('Логин','admin-username','',{required:true,autocomplete:'username'})}${field('Пароль','admin-password','',{type:'password',required:true,autocomplete:'current-password'})}<p id="admin-login-error" class="form-error" role="alert" hidden></p><button class="button primary full" data-testid="admin-login" type="submit">Войти ${icon('arrow')}</button></form></section><a href="/" class="text-link">Вход участника</a></main>`;
  document.querySelector('#admin-username').dataset.testid='admin-username';
  document.querySelector('#admin-password').dataset.testid='admin-password';
  document.querySelector('#admin-login').addEventListener('submit',async event=>{
    event.preventDefault();const button=event.target.querySelector('button');button.disabled=true;
    document.querySelector('#admin-login-error').hidden=true;
    try { await api('/api/admin/login',{method:'POST',body:{username:event.target.elements['admin-username'].value,password:event.target.elements['admin-password'].value}});await refresh(); }
    catch(error){const el=document.querySelector('#admin-login-error');if(el){el.textContent=error.message;el.hidden=false;}}
    finally{if(button.isConnected)button.disabled=false;}
  });
}
function shell() {
  ready=true;
  app.innerHTML=`<div class="admin-layout"><aside class="sidebar"><a class="sidebar-brand" href="/admin">${icon('spark',24)}<span>Посвящение<small>ПАНЕЛЬ УПРАВЛЕНИЯ</small></span></a><div class="environment-picker"><label for="environment">Мероприятие</label><select id="environment" aria-label="Режим мероприятия"><option value="production">Рабочий режим</option><option value="rehearsal">Репетиция</option></select></div><nav aria-label="Разделы управления">${tabs.map(([key,label,symbol])=>`<button class="nav-item" data-tab="${key}" data-testid="admin-nav-${key}">${icon(symbol)}<span>${label}</span><i></i></button>`).join('')}</nav><div class="sidebar-bottom"><a id="projector-link" class="nav-item" target="_blank" href="/screen?env=${environment}">${icon('screen')}<span>Открыть экран</span>${icon('arrow',16)}</a><button id="admin-logout" class="nav-item">${icon('logout')}<span>Выйти</span></button><div class="connection-status" id="connection-status"><i></i> Соединение установлено</div></div></aside><main class="admin-main"><header class="admin-topbar"><div><div class="topline"><span class="eyebrow">ПОСВЯЩЕНИЕ В ПЕРВОКУРСНИКИ</span><span id="environment-badge" class="tag rehearsal" ${environment==='production'?'hidden':''}>Репетиция</span></div><h1 id="panel-title">Управление</h1></div><div class="live-tag"><i></i><span id="phase-top">Подготовка</span></div></header><div id="admin-panel"></div></main></div>`;
  document.querySelector('#environment').value=environment;
  document.querySelector('#environment').addEventListener('change',async event=>{
    if(busy){event.target.value=environment;return;}
    const dirty=[...document.querySelectorAll('form[data-dirty="true"]')].length>0||[...document.querySelectorAll('.score-input[data-dirty="true"]')].length>0;
    if(dirty&&!confirm('Несохранённые изменения будут потеряны. Переключить режим?')){event.target.value=environment;return;}
    environment=event.target.value;localStorage.setItem('quiz-admin-environment',environment);state=null;panelSignature='';settingsLoaded=false;scoreGroupSignature='';
    document.querySelector('#environment-badge').hidden=environment==='production';document.querySelector('#projector-link').href=`/screen?env=${environment}`;
    document.querySelector('#admin-panel').innerHTML='<div class="empty-state">Загружаем данные…</div>';
    await refresh();renderPanel();
  });
  document.querySelectorAll('[data-tab]').forEach(button=>button.addEventListener('click',()=>navigate(button.dataset.tab)));
  document.querySelector('#admin-logout').addEventListener('click',async()=>{
    try{await api('/api/admin/logout',{method:'POST',body:{}});state=null;login();}catch(error){toast(error.message,true);}
  });
  renderPanel();
}
function navigate(tab) {
  if(activeTab===tab)return;
  const dirty=[...document.querySelectorAll('form[data-dirty="true"]')].length>0||[...document.querySelectorAll('.score-input[data-dirty="true"]')].length>0;
  if(dirty&&!confirm('Несохранённые изменения будут потеряны. Перейти в другой раздел?'))return;
  activeTab=tab;renderPanel();
}
function sectionHead(title,description,action='') {
  return `<div class="section-heading"><div><h2>${title}</h2>${description?`<p>${description}</p>`:''}</div>${action}</div>`;
}
function empty(text,sub='') {return `<div class="empty-state"><span>${icon('spark',28)}</span><h3>${text}</h3>${sub?`<p>${sub}</p>`:''}</div>`;}
function lockNotice(text='После начала квиза состав и вопросы зафиксированы.') {return `<div class="notice" id="roster-lock" ${state.state.started?'':'hidden'}>${icon('lock',17)}<span>${text}</span></div>`;}
function editorHeader(title,id) {return `<div class="editor-heading"><h2 id="${id}">${title}</h2><span class="tag" data-form-mode>Новая запись</span></div>`;}
function formActions(label) {return `<div class="form-actions"><button class="button primary" type="submit">${label}</button><button class="button secondary" type="button" data-reset-form>Отменить</button><span class="save-state" aria-live="polite"></span></div>`;}
function renderPanel() {
  if(!ready||!state)return;
  panelSignature='';scoreGroupSignature='';settingsLoaded=false;
  document.querySelector('#panel-title').textContent=tabs.find(tab=>tab[0]===activeTab)[1];
  document.querySelectorAll('[data-tab]').forEach(button=>{button.classList.toggle('active',button.dataset.tab===activeTab);button.setAttribute('aria-current',button.dataset.tab===activeTab?'page':'false');});
  const panel=document.querySelector('#admin-panel');
  if(activeTab==='control')panel.innerHTML='<div id="control-live"></div>';
  if(activeTab==='groups')panel.innerHTML=`${lockNotice()}<div class="editor-layout"><section class="panel list-panel">${sectionHead('Группы','Карточки голосования идут в этом порядке. Изменить его можно стрелками.')}<div id="groups-list"></div></section><section class="panel editor-panel">${editorHeader('Добавить группу','group-editor-title')}<form id="group-form"><input type="hidden" name="id"><div class="form-grid">${field('Номер группы','group-number','',{required:true})}${field('Название карточки','group-title','',{required:true})}${field('Описание','group-description','',{type:'textarea',wide:true})}${field('Базовые баллы','group-baseScore',0,{type:'number',required:true,help:'Готовая сумма задавальника и редактуры.',wide:true})}</div>${uploadField('Иконка для таблицы','group-iconUrl')}${uploadField('Фото или GIF для голосования','group-mediaUrl')}${formActions('Сохранить группу')}</form></section></div>`;
  if(activeTab==='participants')panel.innerHTML=`${lockNotice('После старта можно исправить почту или обновить код выбранного участника.')}<div class="editor-layout participants-layout"><section class="panel list-panel">${sectionHead('Список участников','Один человек — одна запись и один персональный код.')}<div class="table-toolbar"><label class="search-field">${icon('people',18)}<input id="participant-search" type="search" placeholder="Найти по имени, группе или почте" aria-label="Поиск участников"></label><span class="subtle" id="participant-count"></span></div><div id="participants-list"></div></section><section class="panel editor-panel">${editorHeader('Добавить участника','participant-editor-title')}<form id="participant-form"><input type="hidden" name="id"><div class="form-grid">${field('Имя','participant-firstName','',{required:true})}${field('Фамилия','participant-lastName','',{required:true})}<div class="field field-wide"><label for="participant-groupId">Группа</label><select id="participant-groupId" data-testid="participant-groupId" name="participant-groupId" required></select></div>${field('Разрешённая почта Яндекса','participant-email','',{type:'email',wide:true,placeholder:'name@yandex.ru',help:'Адрес должен совпадать с почтой в Яндекс ID.'})}${field('Персональный код','participant-code','',{wide:true,help:'Оставьте пустым — код будет создан автоматически.'})}</div>${formActions('Сохранить участника')}<div class="credential-result" id="credential-result" role="status" hidden><span>Персональный код</span><strong id="credential-code"></strong><button class="button secondary small" type="button" id="copy-credential">${icon('copy',16)} Копировать код</button></div><button class="text-button" type="button" id="regenerate-code" hidden>Сгенерировать новый код</button></form></section></div>`;
  if(activeTab==='questions')panel.innerHTML=`${lockNotice()}<div class="editor-layout"><section class="panel list-panel">${sectionHead('Порядок вопросов','Каждый вопрос открывается оператором вручную.')}<div id="questions-list"></div></section><section class="panel editor-panel">${editorHeader('Добавить вопрос','question-editor-title')}<form id="question-form"><input type="hidden" name="id">${field('Текст вопроса','question-text','',{type:'textarea',required:true})}<div class="question-fields">${letters.map((letter,index)=>field(`Вариант ${letter}`,`question-option-${index}`,'',{required:true})).join('')}</div><div class="field"><label for="question-correctIndex">Правильный вариант</label><select id="question-correctIndex" data-testid="question-correctIndex" name="question-correctIndex">${letters.map((letter,index)=>`<option value="${index}">${letter}</option>`).join('')}</select></div>${formActions('Сохранить вопрос')}</form></section></div>`;
  if(activeTab==='scores')panel.innerHTML=`<section class="panel scores-panel">${sectionHead('Баллы групп','База — сумма задавальника и редактуры. Зрительские баллы считаются отдельно.')}<div id="base-lock-notice" class="notice" hidden>${icon('lock',17)} Базовые баллы зафиксированы для показа итогов.</div><div class="table-scroll"><table class="data-table scores-table"><thead><tr><th>Группа</th><th>База</th><th>Квиз</th><th>Итого</th><th>Зрители</th><th>Бюллетени</th><th>Показ на экране</th></tr></thead><tbody id="scores-list"></tbody></table></div><p class="table-note">За выигранный вопрос: 1,5 балла. Вниз округляется сумма за весь квиз. Зрительская оценка не прибавляется к итогу.</p></section>`;
  if(activeTab==='settings')panel.innerHTML=settingsPage();
  wirePanel();syncPanel();
}
function uploadField(label,name,help='PNG, JPG, GIF или WebP. Изображения подготавливаются перед сохранением. При включённом хранилище загружаются в Yandex Object Storage.') {
  return `<div class="field upload-field"><label for="${name}">${label}</label><div class="upload-row"><input id="${name}" data-testid="${name}" name="${name}" type="text" placeholder="Загрузите изображение"><label class="button secondary upload-button" for="${name}-file">${icon('upload',16)}<span>Загрузить</span><input type="file" id="${name}-file" data-testid="${name}-file" accept="image/png,image/jpeg,image/gif,image/webp" data-upload="${name}" aria-label="Загрузить: ${label}"></label></div><small>${help}</small><div class="upload-preview" data-preview="${name}" hidden><img alt="Предпросмотр"><button class="text-button" type="button" data-remove-image="${name}">Убрать изображение</button></div></div>`;
}
function yandexGuide() {
  return `<details class="yandex-guide" data-testid="yandex-guide"><summary>Как получить ключи</summary><ol><li>Укажите публичный адрес размещённого квиза в этой форме и скопируйте <strong>Redirect URI</strong>.</li><li>Откройте <a href="https://oauth.yandex.ru/client/new/id/" target="_blank" rel="noopener noreferrer">создание приложения Яндекс ID</a> под аккаунтом организатора. Укажите название, иконку и контактную почту. Выберите платформу <strong>«Веб-сервисы»</strong> и вставьте скопированный Redirect URI.</li><li>В доступах выберите <strong>«Адрес электронной почты»</strong> и завершите регистрацию.</li><li>Откройте свойства созданного приложения в <a href="https://oauth.yandex.ru/" target="_blank" rel="noopener noreferrer">кабинете Яндекс OAuth</a>. Скопируйте <strong>Client ID</strong> и <strong>Client Secret</strong> в соответствующие поля этой формы.</li><li>Включите вход через Яндекс, нажмите <strong>«Сохранить Яндекс ID»</strong>, затем <strong>«Проверить вход»</strong>. Войдите аккаунтом, чья почта заранее добавлена в список участников. Для проверки сайт должен быть доступен по указанному публичному адресу.</li></ol></details>`;
}
function storagePanel() {
  return `<section class="panel settings-rehearsal settings-storage" data-testid="storage-panel"><div class="section-heading"><div><h2>Хранилище изображений</h2><p>Yandex Object Storage для новых загрузок</p></div>${icon('upload')}</div><p class="notice">Настройки хранилища общие для рабочего режима и репетиции.</p><form id="storage-form"><label class="switch-row" for="storage-enabled"><span><strong>Загружать в Object Storage</strong><small>Включите после сохранения ключей и успешной проверки</small></span><input id="storage-enabled" data-testid="storage-enabled" name="storage-enabled" type="checkbox" role="switch"></label>${field('Бакет','storage-bucket','posvyat2026',{required:true,placeholder:'posvyat2026'})}<div class="form-grid">${field('Access Key ID','storage-accessKeyId','',{type:'password',help:'Пустое поле сохраняет ранее заданный ключ.',autocomplete:'off'})}${field('Secret Access Key','storage-secretAccessKey','',{type:'password',help:'Пустое поле сохраняет ранее заданный секрет.',autocomplete:'off'})}</div><div class="field"><p class="subtle" id="storage-key-state"></p></div><div class="field"><label for="storage-publicBaseUrl">Публичный адрес изображений</label><input id="storage-publicBaseUrl" data-testid="storage-publicBaseUrl" type="text" readonly><small>Объекты должны быть доступны для публичного чтения.</small></div><details class="yandex-guide" data-testid="storage-guide"><summary>Как получить ключи хранилища</summary><ol><li>В <a href="https://yandex.cloud/ru/docs/iam/operations/sa/create" target="_blank" rel="noopener noreferrer">Identity and Access Management</a> создайте сервисный аккаунт <strong>posvyat-upload</strong>.</li><li>Откройте бакет <strong>posvyat2026</strong>: <strong>«Безопасность → Права доступа → Назначить роли»</strong>. Назначьте аккаунту <strong>posvyat-upload</strong> роль <strong>storage.editor</strong> на этот бакет. <a href="https://yandex.cloud/ru/docs/storage/operations/buckets/iam-access" target="_blank" rel="noopener noreferrer">Инструкция по доступу</a>.</li><li>В сервисном аккаунте нажмите <strong>«Создать новый ключ → Создать статический ключ доступа»</strong>. Скопируйте <strong>Access Key ID</strong> и <strong>Secret Access Key</strong> из окна создания: секрет показывается один раз. <a href="https://yandex.cloud/ru/docs/iam/operations/authentication/manage-access-keys" target="_blank" rel="noopener noreferrer">Инструкция по ключам</a>.</li><li>Вставьте оба значения, сохраните хранилище и нажмите <strong>«Проверить хранилище»</strong>. После успешной проверки включите загрузку в Object Storage и сохраните ещё раз.</li><li>Разрешите публичное чтение объектов в бакете. Запись оставьте доступной только сервисному аккаунту.</li></ol></details>${formActions('Сохранить хранилище')}<div class="oauth-test"><div><h3>Проверка подключения</h3><p>Проверяются сохранённые ключи и доступ к бакету. Сначала сохраните изменения.</p></div><button class="button secondary" type="button" id="test-storage" data-testid="test-storage">Проверить хранилище</button></div><div id="storage-test-result" data-testid="storage-test-result" class="test-result" role="status"></div></form></section>`;
}
function settingsPage() {
  return `<div class="settings-grid"><section class="panel"><div class="section-heading"><div><h2>Правила голосования</h2><p>Настройте до начала квиза</p></div>${icon('heart')}</div><form id="rules-form">${field('Отсечение с каждой стороны','settings-trimCount',0,{type:'number',min:0,required:true,help:'Удаляем столько минимальных и максимальных средних оценок. Должна остаться хотя бы одна.'})}<p class="rule-preview" id="rule-preview"></p>${formActions('Сохранить правила')}</form></section><section class="panel"><div class="section-heading"><div><h2>Оформление экрана</h2><p>Применяется сразу после сохранения</p></div>${icon('screen')}</div><form id="branding-form"><div class="field"><label for="background-mode">Фон страницы проектора</label><select id="background-mode"><option value="color">Однотонный</option><option value="transparent">Прозрачный</option></select></div><div class="field color-field"><label for="settings-background">Цвет фона</label><input id="settings-background" name="settings-background" type="color" value="#101113"><code id="background-value">#101113</code></div>${uploadField('Логотип мероприятия','settings-logoUrl','Логотип показывается на странице входа участников. Изображение подготавливается перед сохранением и при включённом хранилище загружается в Yandex Object Storage.')}${formActions('Сохранить оформление')}</form></section><section class="panel settings-yandex"><div class="section-heading"><div><h2>Яндекс ID</h2><p>Вход по разрешённым адресам участников</p></div><span class="yandex-mark">Я</span></div><form id="yandex-form"><label class="switch-row" for="yandex-enabled"><span><strong>Вход через Яндекс</strong><small>Вход по персональному коду доступен всегда</small></span><input id="yandex-enabled" name="yandex-enabled" type="checkbox" role="switch"></label><div class="form-grid">${field('Client ID','yandex-clientId','',{wide:true})}${field('Client Secret','yandex-clientSecret','',{type:'password',wide:true,help:'Пустое поле сохраняет ранее заданный секрет.',autocomplete:'new-password'})}${field('Публичный адрес сайта','yandex-publicUrl','',{type:'url',wide:true,placeholder:'https://quiz.example.ru'})}</div><div class="field"><label for="yandex-redirect">Redirect URI</label><div class="copy-field"><input id="yandex-redirect" readonly><button type="button" class="icon-button" id="copy-redirect" aria-label="Копировать Redirect URI">${icon('copy',18)}</button></div><small>Добавьте этот адрес в настройках приложения Яндекса.</small></div><p class="subtle" id="yandex-secret-state"></p>${yandexGuide()}${formActions('Сохранить Яндекс ID')}<div class="oauth-test"><div><h3>Настоящая проверка входа</h3><p>Проверка откроет авторизацию Яндекса. Сохранение полей не подтверждает работу входа.</p></div><a href="/auth/yandex/start?test=1" id="test-yandex" class="button secondary">Проверить вход ${icon('arrow',16)}</a></div><div id="yandex-test-result" class="test-result" role="status"></div></form></section>${storagePanel()}<section class="panel settings-rehearsal"><div class="section-heading"><div><h2>Репетиция</h2><p>Отдельное мероприятие с теми же правилами</p></div><span class="tag rehearsal">Тестовые данные</span></div><p class="subtle">Рабочие участники и ответы сохраняются. Сначала выберите «Репетиция» в меню слева.</p><div class="rehearsal-options"><div class="form-grid">${field('Количество групп','rehearsal-groups',27,{type:'number',min:2,max:100})}${field('Участников в группе','rehearsal-participants',2,{type:'number',min:1,max:100})}</div><button type="button" class="button secondary" id="rehearsal-seed">Создать тестовые данные</button><button type="button" class="button secondary" id="rehearsal-copy">Скопировать рабочую конфигурацию</button><button type="button" class="button secondary" id="rehearsal-simulate">Симулировать ответы и оценки</button><p class="subtle" id="rehearsal-hint"></p><a class="text-link" href="/?env=rehearsal" target="_blank">Открыть вход участника репетиции ${icon('arrow',16)}</a></div></section></div>`;
}
async function mutate(path,body,method='POST',message) {
  if(busy)return null;busy=true;setBusy(true);
  try {const result=await api(envPath(path),{method,body});if(message)toast(message);await refresh();return result;}
  catch(error){toast(error.message,true);return null;}
  finally{busy=false;setBusy(false);syncLocks();}
}
function setBusy(value) {
  const picker=document.querySelector('#environment');if(picker)picker.disabled=value;
  document.querySelectorAll('[data-action]').forEach(button=>{
    const invalidStart=button.dataset.action==='start_quiz'&&(!state.questions.length||state.groups.length<2||state.groups.length-1-2*state.settings.trimCount<1);
    const revealed=button.dataset.action==='reveal_group'&&state.scores.find(score=>score.groupId===button.dataset.groupId)?.revealed;
    button.disabled=value||invalidStart||Boolean(revealed);
  });
  document.querySelectorAll('form button[type="submit"]').forEach(button=>button.disabled=value);
  syncStorageControls();
}
function getValue(form,key) {return form.elements[key]?.value.trim() ?? '';}
function fill(form,values) {
  for(const [key,value] of Object.entries(values)){const input=form.elements[key];if(input)input.value=value??'';}
  form.dataset.dirty='false';
  const edit=Boolean(form.elements.id?.value);
  form.closest('.panel')?.querySelector('[data-form-mode]')?.replaceChildren(document.createTextNode(edit?'Редактирование':'Новая запись'));
  form.querySelector('.save-state')?.replaceChildren();
  form.querySelectorAll('[data-preview]').forEach(preview=>updatePreview(preview.dataset.preview));
  syncLocks();
}
function resetEditor(form) {
  form.reset();if(form.elements.id)form.elements.id.value='';form.dataset.dirty='false';
  form.closest('.panel')?.querySelector('[data-form-mode]')?.replaceChildren(document.createTextNode('Новая запись'));
  form.querySelectorAll('[data-preview]').forEach(preview=>updatePreview(preview.dataset.preview));
  const credential=document.querySelector('#credential-result');if(credential)credential.hidden=true;
  const regenerate=document.querySelector('#regenerate-code');if(regenerate)regenerate.hidden=true;
  syncLocks();
}
function showCode(code) {
  if(!code)return;
  document.querySelector('#credential-result').hidden=false;document.querySelector('#credential-code').textContent=code;
}
function updatePreview(name) {
  const input=document.getElementById(name);const preview=document.querySelector(`[data-preview="${name}"]`);
  if(!input||!preview)return;preview.hidden=!input.value;if(input.value){const img=preview.querySelector('img');if(img.getAttribute('src')!==input.value){img.hidden=false;img.src=input.value;}}
}
function wirePanel() {
  const panel=document.querySelector('#admin-panel');
  panel.querySelectorAll('form').forEach(form=>{
    form.addEventListener('input',()=>{form.dataset.dirty='true';const status=form.querySelector('.save-state');if(status)status.textContent='Не сохранено';if(form.id==='storage-form'){storageTestError='';syncStorageControls();}});
    form.querySelector('[data-reset-form]')?.addEventListener('click',()=>{
      if(form.id==='storage-form'){form.dataset.dirty='false';storageTestError='';document.querySelector('#storage-accessKeyId').value='';document.querySelector('#storage-secretAccessKey').value='';form.querySelector('.save-state').textContent='';loadSettings();return;}
      if(form.id==='rules-form'||form.id==='branding-form'||form.id==='yandex-form'){form.dataset.dirty='false';loadSettings(true);}
      else resetEditor(form);
    });
  });
  panel.querySelectorAll('[data-upload]').forEach(input=>input.addEventListener('change',async()=>{
    const file=input.files[0];if(!file)return;
    if(!['image/png','image/jpeg','image/gif','image/webp'].includes(file.type)){toast('Выберите PNG, JPG, GIF или WebP.',true);input.value='';return;}
    const label=input.closest('label');const caption=label.querySelector('span');label.classList.add('uploading');input.disabled=true;caption.textContent='Подготавливаем…';
    try{const data=await readFile(file);caption.textContent='Обрабатываем и загружаем…';const purpose={'group-iconUrl':'icon','settings-logoUrl':'logo','group-mediaUrl':'card'}[input.dataset.upload];const result=await api(envPath('/api/admin/upload'),{method:'POST',body:{data,mime:file.type,name:file.name,purpose}});const target=document.getElementById(input.dataset.upload);target.value=result.url;target.dispatchEvent(new Event('input',{bubbles:true}));updatePreview(target.id);toast('Изображение загружено. Сохраните форму.');}
    catch(error){toast(error.message,true);}
    finally{label.classList.remove('uploading');caption.textContent='Загрузить';input.disabled=false;input.value='';syncLocks();}
  }));
  panel.querySelectorAll('[data-remove-image]').forEach(button=>button.addEventListener('click',()=>{const input=document.getElementById(button.dataset.removeImage);input.value='';input.dispatchEvent(new Event('input',{bubbles:true}));updatePreview(input.id);}));
  panel.querySelectorAll('[data-preview]').forEach(preview=>document.getElementById(preview.dataset.preview).addEventListener('change',()=>updatePreview(preview.dataset.preview)));
  document.querySelector('#group-form')?.addEventListener('submit',async event=>{
    event.preventDefault();const form=event.target;
    const body={id:getValue(form,'id')||undefined,number:getValue(form,'group-number'),title:getValue(form,'group-title'),description:getValue(form,'group-description'),baseScore:Number(getValue(form,'group-baseScore')),iconUrl:getValue(form,'group-iconUrl'),mediaUrl:getValue(form,'group-mediaUrl')};
    if(await mutate('/api/admin/groups',body,'POST','Группа сохранена'))resetEditor(form);
  });
  document.querySelector('#participant-form')?.addEventListener('submit',async event=>{
    event.preventDefault();const form=event.target;
    const body={id:getValue(form,'id')||undefined,firstName:getValue(form,'participant-firstName'),lastName:getValue(form,'participant-lastName'),groupId:getValue(form,'participant-groupId'),email:getValue(form,'participant-email')};
    const code=getValue(form,'participant-code');if(code)body.code=code;
    const result=await mutate('/api/admin/participants',body,'POST','Участник сохранён');
    if(result){form.dataset.dirty='false';form.querySelector('.save-state').textContent='Сохранено';const participant=result.participant;if(participant?.id){form.elements.id.value=participant.id;document.querySelector('#regenerate-code').hidden=false;}showCode(result.code);syncLocks();}
  });
  document.querySelector('#regenerate-code')?.addEventListener('click',async()=>{
    const id=document.querySelector('#participant-form').elements.id.value;if(!id)return;
    if(!confirm('Создать новый персональный код? Старый код перестанет работать.'))return;
    const result=await mutate(`/api/admin/participants/${encodeURIComponent(id)}/code`,{},'POST','Новый код создан');if(result)showCode(result.code);
  });
  document.querySelector('#copy-credential')?.addEventListener('click',()=>copyText(document.querySelector('#credential-code').textContent));
  document.querySelector('#participant-search')?.addEventListener('input',()=>{panelSignature='';updateParticipants();});
  document.querySelector('#question-form')?.addEventListener('submit',async event=>{
    event.preventDefault();const form=event.target;const body={id:getValue(form,'id')||undefined,text:getValue(form,'question-text'),options:letters.map((_,i)=>getValue(form,`question-option-${i}`)),correctIndex:Number(getValue(form,'question-correctIndex'))};
    if(await mutate('/api/admin/questions',body,'POST','Вопрос сохранён'))resetEditor(form);
  });
  document.querySelector('#rules-form')?.addEventListener('submit',async event=>{
    event.preventDefault();if(await mutate('/api/admin/settings',{trimCount:Number(document.querySelector('#settings-trimCount').value)},'POST','Правила сохранены')){event.target.dataset.dirty='false';event.target.querySelector('.save-state').textContent='Сохранено';}
  });
  document.querySelector('#settings-trimCount')?.addEventListener('input',updateRulePreview);
  document.querySelector('#branding-form')?.addEventListener('submit',async event=>{
    event.preventDefault();const body={background:document.querySelector('#background-mode').value==='transparent'?'transparent':document.querySelector('#settings-background').value,logoUrl:document.querySelector('#settings-logoUrl').value.trim()};
    if(await mutate('/api/admin/settings',body,'POST','Оформление сохранено')){event.target.dataset.dirty='false';event.target.querySelector('.save-state').textContent='Сохранено';}
  });
  document.querySelector('#background-mode')?.addEventListener('change',()=>{document.querySelector('#settings-background').disabled=document.querySelector('#background-mode').value==='transparent';document.querySelector('#background-value').textContent=document.querySelector('#background-mode').value==='transparent'?'Прозрачный':document.querySelector('#settings-background').value;});
  document.querySelector('#settings-background')?.addEventListener('input',event=>document.querySelector('#background-value').textContent=event.target.value);
  document.querySelector('#yandex-form')?.addEventListener('submit',async event=>{
    event.preventDefault();const body={yandexEnabled:document.querySelector('#yandex-enabled').checked,yandexClientId:document.querySelector('#yandex-clientId').value.trim(),yandexClientSecret:document.querySelector('#yandex-clientSecret').value,publicUrl:document.querySelector('#yandex-publicUrl').value.trim()};
    if(await mutate('/api/admin/settings',body,'POST','Настройки Яндекс ID сохранены')){event.target.dataset.dirty='false';document.querySelector('#yandex-clientSecret').value='';event.target.querySelector('.save-state').textContent='Сохранено';loadSettings();}
  });
  document.querySelector('#yandex-publicUrl')?.addEventListener('input',()=>document.querySelector('#yandex-redirect').value=`${document.querySelector('#yandex-publicUrl').value.trim().replace(/\/$/,'')}/auth/yandex/callback`);
  document.querySelector('#copy-redirect')?.addEventListener('click',()=>copyText(document.querySelector('#yandex-redirect').value));
  document.querySelector('#test-yandex')?.addEventListener('click',event=>{if(document.querySelector('#yandex-form').dataset.dirty==='true'){event.preventDefault();toast('Сначала сохраните настройки Яндекс ID.',true);}});
  document.querySelector('#storage-form')?.addEventListener('submit',async event=>{
    event.preventDefault();const form=event.target;const body={enabled:document.querySelector('#storage-enabled').checked,bucket:document.querySelector('#storage-bucket').value.trim(),accessKeyId:document.querySelector('#storage-accessKeyId').value,secretAccessKey:document.querySelector('#storage-secretAccessKey').value};
    if(await mutate('/api/admin/storage',body,'POST','Настройки хранилища сохранены')){form.dataset.dirty='false';storageTestError='';document.querySelector('#storage-accessKeyId').value='';document.querySelector('#storage-secretAccessKey').value='';form.querySelector('.save-state').textContent='Сохранено';loadSettings();}
  });
  document.querySelector('#test-storage')?.addEventListener('click',async()=>{
    if(busy||document.querySelector('#storage-form').dataset.dirty==='true')return;
    const button=document.querySelector('#test-storage');button.textContent='Проверяем…';storageTestError='';
    const result=await mutate('/api/admin/storage/test',{});
    if(!result){storageTestError='Не удалось проверить хранилище. Проверьте ключи и доступ к бакету, затем повторите.';await refresh();}
    if(button.isConnected){button.textContent='Проверить хранилище';loadSettings();}
  });
  document.querySelector('#rehearsal-seed')?.addEventListener('click',async()=>{
    if(environment!=='rehearsal')return;
    if(!confirm('Создать новые тестовые данные? Текущая репетиция будет сброшена.'))return;
    const groupCount=Number(document.querySelector('#rehearsal-groups').value);const participantsPerGroup=Number(document.querySelector('#rehearsal-participants').value);
    if(await mutate('/api/admin/rehearsal',{action:'seed',groupCount,participantsPerGroup},'POST','Тестовые данные созданы')){loadSettings(true);}
  });
  document.querySelector('#rehearsal-copy')?.addEventListener('click',async()=>{
    if(environment!=='rehearsal')return;
    if(!confirm('Заменить репетицию чистой копией рабочей конфигурации?'))return;
    if(await mutate('/api/admin/rehearsal',{action:'copy'},'POST','Рабочая конфигурация скопирована в репетицию'))loadSettings(true);
  });
  document.querySelector('#rehearsal-simulate')?.addEventListener('click',()=>mutate('/api/admin/rehearsal',{action:'simulate'},'POST','Тестовые ответы отправлены'));
  panel.onclick=async event=>{
    const action=event.target.closest('[data-action]');if(action){
      if(busy||action.disabled)return;
      const restart=action.dataset.action==='restart_event';
      const mode=environment==='rehearsal'?'Репетиция':'Рабочий режим';
      if(restart&&!confirm(`Начать заново: «${mode}»?\n\nБудут удалены ответы и результаты квиза, зрительские оценки и раскрытые итоги. Мероприятие вернётся к подготовке, экран очистится.\n\nГруппы, вопросы, участники и их коды, базовые баллы, настройки и вход на устройствах сохранятся. Редактирование снова станет доступно.`))return;
      await mutate('/api/admin/action',{action:action.dataset.action,runId:state.state.runId??0,...(restart?{confirm:true}:{}),...(action.dataset.groupId?{groupId:action.dataset.groupId}:{})},'POST',restart?'Мероприятие готово к новому запуску':undefined);return;
    }
    const edit=event.target.closest('[data-edit]');if(edit){editEntity(edit.dataset.edit,edit.dataset.id);return;}
    const remove=event.target.closest('[data-delete]');if(remove){
      if(!confirm(`Удалить ${remove.dataset.delete==='groups'?'группу':remove.dataset.delete==='questions'?'вопрос':'участника'}? Это действие нельзя отменить.`))return;
      const result=await mutate(`/api/admin/${remove.dataset.delete}/${encodeURIComponent(remove.dataset.id)}`,undefined,'DELETE','Запись удалена');
      if(result){const form=panel.querySelector('form');if(form?.elements.id?.value===remove.dataset.id)resetEditor(form);}return;
    }
    const moveGroup=event.target.closest('[data-move-group]');if(moveGroup){
      if(busy||moveGroup.disabled)return;
      const ids=state.groups.map(g=>g.id),index=ids.indexOf(moveGroup.dataset.moveGroup),to=index+Number(moveGroup.dataset.direction);
      if(index<0||to<0||to>=ids.length)return;
      [ids[index],ids[to]]=[ids[to],ids[index]];await mutate('/api/admin/groups/order',{ids},'POST','Порядок голосования сохранён');return;
    }
    const move=event.target.closest('[data-move-question]');if(move){
      const ids=state.questions.map(q=>q.id);const index=ids.indexOf(move.dataset.moveQuestion);const to=index+Number(move.dataset.direction);if(to<0||to>=ids.length)return;[ids[index],ids[to]]=[ids[to],ids[index]];await mutate('/api/admin/questions/order',{ids},'POST','Порядок сохранён');return;
    }
    const score=event.target.closest('[data-save-score]');if(score){
      const row=score.closest('tr');const input=row.querySelector('input');if(!input.checkValidity()){input.reportValidity();return;}
      const result=await mutate('/api/admin/scores',{groupId:score.dataset.saveScore,baseScore:Number(input.value)},'POST','Базовые баллы сохранены');if(result){input.dataset.dirty='false';score.disabled=true;}return;
    }
  };
}
function editEntity(type,id) {
  if(type==='groups') {
    const group=state.groups.find(g=>g.id===id);if(!group)return;
    fill(document.querySelector('#group-form'),{id:group.id,'group-number':group.number,'group-title':group.title,'group-description':group.description,'group-baseScore':group.baseScore,'group-iconUrl':group.iconUrl,'group-mediaUrl':group.mediaUrl});
  }
  if(type==='participants') {
    const p=state.participants.find(p=>p.id===id);if(!p)return;
    fill(document.querySelector('#participant-form'),{id:p.id,'participant-firstName':p.firstName,'participant-lastName':p.lastName,'participant-groupId':p.groupId,'participant-email':p.email,'participant-code':''});
    document.querySelector('#credential-result').hidden=true;document.querySelector('#regenerate-code').hidden=false;
  }
  if(type==='questions') {
    const q=state.questions.find(q=>q.id===id);if(!q)return;
    fill(document.querySelector('#question-form'),{id:q.id,'question-text':q.text,'question-correctIndex':q.correctIndex,...Object.fromEntries(q.options.map((option,i)=>[`question-option-${i}`,option]))});
  }
  const form=document.querySelector('.editor-panel form');
  if(innerWidth<1000)form?.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'start'});
}
function rowActions(type,id,disabled=false) {return `<div class="row-actions"><button class="text-button" type="button" data-edit="${type}" data-id="${esc(id)}">Изменить</button><button class="icon-button danger-button" type="button" data-delete="${type}" data-id="${esc(id)}" aria-label="Удалить ${type==='groups'?'группу':type==='participants'?'участника':'вопрос'}" ${disabled?'disabled':''}>${icon('close',16)}</button></div>`;}
function updateGroups() {
  const signature=JSON.stringify(state.groups)+state.state.started;if(signature===panelSignature)return;panelSignature=signature;
  const el=document.querySelector('#groups-list');if(!el)return;
  el.innerHTML=state.groups.length?`<div class="group-list">${state.groups.map((group,index)=>`<article class="group-list-row">${avatar(group.iconUrl,group.number)}<div class="group-list-info"><strong>Группа ${esc(group.number)}</strong><span>${esc(group.title)}</span><small>База: ${number(group.baseScore)} · ${state.participants.filter(p=>p.groupId===group.id).length} участников</small></div>${rowActions('groups',group.id,state.state.started)}<div class="order-buttons"><button class="icon-button" type="button" data-move-group="${esc(group.id)}" data-direction="-1" aria-label="Переместить группу ${esc(group.number)} выше" ${index===0||state.state.started?'disabled':''}>↑</button><button class="icon-button" type="button" data-move-group="${esc(group.id)}" data-direction="1" aria-label="Переместить группу ${esc(group.number)} ниже" ${index===state.groups.length-1||state.state.started?'disabled':''}>↓</button></div></article>`).join('')}</div>`:empty('Добавьте первую группу','После этого можно будет создавать участников.');
}
function updateParticipants() {
  const search=document.querySelector('#participant-search')?.value.toLowerCase().trim()||'';
  const signature=JSON.stringify([state.participants,state.groups,search,state.state.started]);if(signature===panelSignature)return;panelSignature=signature;
  const groupMap=Object.fromEntries(state.groups.map(g=>[g.id,g]));
  const list=state.participants.filter(p=>`${p.firstName} ${p.lastName} ${p.email} ${groupMap[p.groupId]?.number||''}`.toLowerCase().includes(search));
  document.querySelector('#participant-count').textContent=`${list.length} из ${state.participants.length}`;
  document.querySelector('#participants-list').innerHTML=list.length?`<div class="table-scroll"><table class="data-table"><thead><tr><th>Участник</th><th>Группа / почта</th><th></th></tr></thead><tbody>${list.map(p=>`<tr><td><div class="table-name">${esc(p.firstName)} ${esc(p.lastName)}</div><span class="person-status ${p.joined?'is-online':''}">${p.joined?'Вошёл':'Ещё не входил'}</span></td><td><strong>${esc(groupMap[p.groupId]?.number||'—')}</strong><small class="cell-email">${esc(p.email)}</small></td><td>${rowActions('participants',p.id,state.state.started)}</td></tr>`).join('')}</tbody></table></div>`:empty(search?'Никого не нашли':'Пока нет участников',search?'Попробуйте изменить запрос.':'Добавьте участника в форме справа.');
  const select=document.querySelector('#participant-groupId');const selected=select.value;select.innerHTML=`<option value="">Выберите группу</option>${state.groups.map(group=>`<option value="${esc(group.id)}">Группа ${esc(group.number)}</option>`).join('')}`;select.value=selected;
}
function updateQuestions() {
  const signature=JSON.stringify([state.questions,state.state.started]);if(signature===panelSignature)return;panelSignature=signature;
  document.querySelector('#questions-list').innerHTML=state.questions.length?`<div class="question-list">${state.questions.map((question,index)=>`<article class="question-list-row"><span class="question-number">${index+1}</span><div class="question-list-info"><h3>${esc(question.text)}</h3><p>${letters[question.correctIndex]}. ${esc(question.options[question.correctIndex])}</p><div class="question-row-actions"><button class="text-button" type="button" data-edit="questions" data-id="${esc(question.id)}">Изменить</button><button class="text-button danger-button" type="button" data-delete="questions" data-id="${esc(question.id)}" ${state.state.started?'disabled':''}>Удалить</button></div></div><div class="order-buttons"><button class="icon-button" data-move-question="${esc(question.id)}" data-direction="-1" aria-label="Переместить вопрос ${index+1} выше" ${index===0||state.state.started?'disabled':''}>↑</button><button class="icon-button" data-move-question="${esc(question.id)}" data-direction="1" aria-label="Переместить вопрос ${index+1} ниже" ${index===state.questions.length-1||state.state.started?'disabled':''}>↓</button></div></article>`).join('')}</div>`:empty('Подготовьте вопросы','Четыре варианта и один правильный ответ.');
}
function currentAction() {
  const s=state.state;const index=state.questions.findIndex(q=>q.id===s.currentQuestionId);const last=index===state.questions.length-1;
  if(s.phase==='setup')return ['start_quiz','Начать квиз','Откроется первый вопрос. Состав участников и правила будут зафиксированы.'];
  if(s.phase==='question_open')return ['close_question','Закрыть вопрос','После закрытия новые ответы не принимаются. Случайный неверный вариант получат только неответившие, которые хотя бы раз входили в систему.'];
  if(s.phase==='question_closed')return ['reveal_question','Показать ответы','Участники увидят правильный вариант и результат своей группы.'];
  if(s.phase==='question_reveal')return last?['finish_quiz','Завершить квиз','Все вопросы пройдены. Далее — зрительское голосование.']:['wait','Перейти к ожиданию','Следующий вопрос откроется по вашей команде.'];
  if(s.phase==='waiting')return last?['finish_quiz','Завершить квиз','Все вопросы пройдены. Далее — зрительское голосование.']:['next_question','Открыть следующий вопрос','Участники смогут выбрать один из четырёх вариантов.'];
  if(s.phase==='quiz_finished')return ['open_voting','Открыть голосование','Участники оценят каждую другую группу от 1 до 10.'];
  if(s.phase==='voting_open')return ['close_voting','Закрыть голосование','Будут учтены только полностью отправленные наборы оценок.'];
  if(s.phase==='voting_closed'&&s.displayStage==='blank')return ['show_audience','Показать зрительские симпатии','На экране появится готовая таблица зрительских оценок.'];
  if(s.phase==='voting_closed'&&s.displayStage==='audience')return ['show_main','Показать базовые баллы','База зафиксируется. Затем можно отдельно раскрыть квиз каждой группы.'];
  return null;
}
function updateControl() {
  const {revision,...visibleState}=state.state;
  const signature=JSON.stringify([visibleState,state.questions,state.groups,state.state.displayStage==='main'?state.scores:[]]);if(signature===panelSignature){patchControlStats();return;}panelSignature=signature;
  const s=state.state;const q=state.questions.find(q=>q.id===s.currentQuestionId);const qi=state.questions.findIndex(q=>q.id===s.currentQuestionId);
  const action=currentAction();const stats=state.stats;
  const main=s.displayStage==='main';
  const retained=state.groups.length-1-2*state.settings.trimCount;
  const setupProblems=[];
  if(!state.questions.length)setupProblems.push('Добавьте хотя бы один вопрос.');
  if(state.groups.length<2)setupProblems.push('Добавьте хотя бы две группы.');
  if(retained<1)setupProblems.push('Уменьшите отсечение в настройках голосования.');
  document.querySelector('#control-live').innerHTML=`<div class="metric-grid"><article class="metric"><span>Вошли</span><strong>${stats.joinedCount}<small>/ ${stats.participantCount}</small></strong><i>участников</i></article><article class="metric"><span>${s.phase==='voting_open'||s.phase==='voting_closed'?'Сохранили оценки':'Ответили'}</span><strong>${s.phase==='voting_open'||s.phase==='voting_closed'?stats.submittedCount:stats.answeredCount}<small>/ ${stats.participantCount}</small></strong><i>${s.phase==='voting_open'||s.phase==='voting_closed'?'готовых наборов':'на текущий вопрос'}</i></article><article class="metric"><span>Группы</span><strong>${state.groups.length}</strong><i>в мероприятии</i></article><article class="metric"><span>Вопрос</span><strong>${qi>=0?qi+1:'—'}<small>/ ${state.questions.length}</small></strong><i>${phaseLabels[s.phase]}</i></article></div><section class="panel operator-panel"><div class="operator-context"><span class="eyebrow">${main?'РАСКРЫТИЕ ОСНОВНЫХ ИТОГОВ':q?`ВОПРОС ${qi+1} ИЗ ${state.questions.length}`:'ТЕКУЩИЙ ЭТАП'}</span><span class="tag ${s.phase==='question_open'||s.phase==='voting_open'?'success':''}">${phaseLabels[s.phase]}</span></div><h2>${main?'Добавляем баллы квиза':s.phase==='setup'?'Всё начинается с первого вопроса':q&&['question_open','question_closed','question_reveal','waiting'].includes(s.phase)?esc(q.text):s.phase==='quiz_finished'?'Время поддержать другие группы':s.phase==='voting_open'?'Участники выбирают фаворитов':s.displayStage==='audience'?'Зрительские симпатии на экране':'Голосование завершено'}</h2>${q&&['question_open','question_closed','question_reveal'].includes(s.phase)?`<div class="operator-options">${q.options.map((option,i)=>`<div class="${s.phase==='question_reveal'&&i===q.correctIndex?'is-correct':''}"><span>${letters[i]}</span>${esc(option)}${s.phase==='question_reveal'&&i===q.correctIndex?icon('check',17):''}</div>`).join('')}</div>`:''}${action?`<div class="operator-action"><p>${esc(action[2])}</p>${s.phase==='setup'&&setupProblems.length?`<div class="validation-list" role="status">${setupProblems.map(p=>`<p>${icon('close',15)} ${p}</p>`).join('')}</div>`:''}<button class="button primary operator-primary" data-action="${action[0]}" data-testid="action-${action[0]}" ${busy||(s.phase==='setup'&&setupProblems.length)?'disabled':''}>${icon(action[0]==='start_quiz'?'play':'arrow',20)} ${action[1]}</button></div>`:''}${main?`<p class="subtle">Нажмите на группу, чтобы один раз добавить её баллы квиза на общем экране.</p><div class="reveal-grid">${state.groups.map(group=>{const score=state.scores.find(score=>score.groupId===group.id)||{};return `<button class="reveal-button ${score.revealed?'revealed':''}" data-action="reveal_group" data-testid="action-reveal_group-${esc(group.id)}" data-group-id="${esc(group.id)}" ${score.revealed||busy?'disabled':''}>${avatar(group.iconUrl,group.number)}<span><strong>Группа ${esc(group.number)}</strong><small>${score.revealed?'Добавлено':'Добавить квиз'}</small></span><b>${score.revealed?icon('check'): '+'+number(score.quizScore)}</b></button>`;}).join('')}</div>`:''}${s.started?`<div class="form-actions"><button class="button secondary" data-action="restart_event" data-testid="action-restart_event" ${busy?'disabled':''}>Начать заново</button></div>`:''}</section><section class="panel group-progress-panel">${sectionHead('Участие групп','Счётчики обновляются автоматически. Назначенные ответы не входят в число ответивших.')}<div class="table-scroll"><table class="data-table"><thead><tr><th>Группа</th><th>Вошли</th><th>Ответили</th><th>Прогресс ответов</th></tr></thead><tbody>${state.groups.map(group=>{const row=stats.byGroup?.find(row=>row.groupId===group.id)||{answeredCount:0,participantCount:0,joinedCount:0};return `<tr><td><div class="group-cell">${avatar(group.iconUrl,group.number)}<strong>${esc(group.number)}</strong></div></td><td>${row.joinedCount} <span class="subtle">/ ${row.participantCount}</span></td><td>${row.answeredCount} <span class="subtle">/ ${row.participantCount}</span></td><td><div class="progress-track"><i style="width:${row.participantCount?row.answeredCount/row.participantCount*100:0}%"></i></div></td></tr>`;}).join('')}</tbody></table></div>${!state.groups.length?empty('Группы ещё не добавлены'):''}</section>`;
}
function patchControlStats() {
  const metrics=document.querySelectorAll('.metric>strong');if(metrics.length<2)return;
  const stats=state.stats;const voting=['voting_open','voting_closed'].includes(state.state.phase);
  const joined=`${stats.joinedCount}<small>/ ${stats.participantCount}</small>`;
  const answered=`${voting?stats.submittedCount:stats.answeredCount}<small>/ ${stats.participantCount}</small>`;
  if(metrics[0].innerHTML!==joined)metrics[0].innerHTML=joined;
  if(metrics[1].innerHTML!==answered)metrics[1].innerHTML=answered;
  const tableRows=document.querySelectorAll('.group-progress-panel tbody tr');
  state.groups.forEach((group,index)=>{
    const row=tableRows[index];if(!row)return;
    const statsRow=stats.byGroup?.find(item=>item.groupId===group.id)||{answeredCount:0,participantCount:0,joinedCount:0};
    const entered=`${statsRow.joinedCount} <span class="subtle">/ ${statsRow.participantCount}</span>`;
    const answered=`${statsRow.answeredCount} <span class="subtle">/ ${statsRow.participantCount}</span>`;
    if(row.cells[1].innerHTML!==entered)row.cells[1].innerHTML=entered;
    if(row.cells[2].innerHTML!==answered)row.cells[2].innerHTML=answered;
    row.querySelector('.progress-track>i').style.width=`${statsRow.participantCount?statsRow.answeredCount/statsRow.participantCount*100:0}%`;
  });
}
function updateScores() {
  const groupsSignature=JSON.stringify(state.groups.map(g=>[g.id,g.number,g.iconUrl]));
  const list=document.querySelector('#scores-list');
  if(groupsSignature!==scoreGroupSignature){
    scoreGroupSignature=groupsSignature;
    list.innerHTML=state.groups.map(group=>`<tr data-score-group="${esc(group.id)}"><td><div class="group-cell">${avatar(group.iconUrl,group.number)}<strong>${esc(group.number)}</strong></div></td><td><div class="score-edit"><input type="number" step="1" value="${Number(group.baseScore)}" class="score-input" aria-label="База группы ${esc(group.number)}"><button class="icon-button" data-save-score="${esc(group.id)}" aria-label="Сохранить базу группы ${esc(group.number)}" disabled>${icon('check',17)}</button></div></td><td data-score="quizScore"></td><td data-score="totalScore" class="total-score"></td><td data-score="audienceScore"></td><td data-score="voterCount"></td><td data-score="revealed"></td></tr>`).join('')||'<tr><td colspan="7" class="empty-table">Добавьте группы для расчёта баллов</td></tr>';
    list.querySelectorAll('input').forEach(input=>input.addEventListener('input',()=>{input.dataset.dirty='true';input.nextElementSibling.disabled=state.state.baseLocked||busy;}));
  }
  state.groups.forEach(group=>{
    const row=[...list.children].find(row=>row.dataset.scoreGroup===group.id);if(!row)return;
    const score=state.scores.find(score=>score.groupId===group.id)||{baseScore:group.baseScore,quizScore:0,totalScore:group.baseScore,audienceScore:0,voterCount:0,revealed:false};
    const input=row.querySelector('input');if(document.activeElement!==input&&input.dataset.dirty!=='true')input.value=score.baseScore;
    input.disabled=state.state.baseLocked;row.querySelector('[data-save-score]').disabled=state.state.baseLocked||busy||input.dataset.dirty!=='true';
    row.querySelector('[data-score="quizScore"]').textContent=number(score.quizScore);
    row.querySelector('[data-score="totalScore"]').textContent=number(score.totalScore);
    row.querySelector('[data-score="audienceScore"]').textContent=decimal(score.audienceScore);
    row.querySelector('[data-score="voterCount"]').textContent=number(score.voterCount);
    row.querySelector('[data-score="revealed"]').innerHTML=score.revealed?'<span class="tag success">✓ Квиз добавлен</span>':'<span class="subtle">Квиз не раскрыт</span>';
  });
  document.querySelector('#base-lock-notice').hidden=!state.state.baseLocked;
}
function syncStorageControls() {
  const form=document.querySelector('#storage-form');const button=document.querySelector('#test-storage');if(!form||!button)return;
  button.disabled=busy||form.dataset.dirty==='true';
  button.title=form.dataset.dirty==='true'?'Сначала сохраните настройки хранилища.':'';
}
function loadSettings(force=false) {
  if(activeTab!=='settings')return;
  const settings=state.settings;const yandex=state.yandex||{};const storage=state.storage||{};
  const rules=document.querySelector('#rules-form');const branding=document.querySelector('#branding-form');const ya=document.querySelector('#yandex-form');const storageForm=document.querySelector('#storage-form');
  if(force||rules.dataset.dirty!=='true')document.querySelector('#settings-trimCount').value=settings.trimCount;
  if(force||branding.dataset.dirty!=='true'){
    const transparent=settings.background==='transparent';document.querySelector('#background-mode').value=transparent?'transparent':'color';document.querySelector('#settings-background').value=transparent?'#101113':settings.background;document.querySelector('#settings-background').disabled=transparent;document.querySelector('#background-value').textContent=transparent?'Прозрачный':settings.background;document.querySelector('#settings-logoUrl').value=settings.logoUrl||'';updatePreview('settings-logoUrl');
  }
  if(force||ya.dataset.dirty!=='true'){
    document.querySelector('#yandex-enabled').checked=Boolean(yandex.enabled);document.querySelector('#yandex-clientId').value=yandex.clientId||'';document.querySelector('#yandex-publicUrl').value=yandex.publicUrl||'';document.querySelector('#yandex-redirect').value=yandex.redirectUri||`${yandex.publicUrl||''}/auth/yandex/callback`;
  }
  document.querySelector('#yandex-secret-state').textContent=yandex.secretSet?'✓ Client Secret сохранён на сервере':'Client Secret пока не задан';
  const last=yandex.lastTest;const test=document.querySelector('#yandex-test-result');test.classList.toggle('success-text',Boolean(last?.ok));test.classList.toggle('error-text',Boolean(last&&!last.ok));
  test.textContent=last?`${last.ok?'✓ Вход успешно проверен':'× Проверка входа не пройдена'}${last.email?` · ${last.email}`:''}${last.message?` · ${last.message}`:''}`:'Вход ещё не проверен';
  if(force||storageForm.dataset.dirty!=='true'){
    document.querySelector('#storage-enabled').checked=Boolean(storage.enabled);document.querySelector('#storage-bucket').value=storage.bucket||'posvyat2026';
  }
  document.querySelector('#storage-publicBaseUrl').value=storage.publicBaseUrl||`https://storage.yandexcloud.net/${storage.bucket||'posvyat2026'}`;
  document.querySelector('#storage-key-state').textContent=`${storage.accessKeyIdSet?'✓ Access Key ID сохранён':'Access Key ID пока не задан'} · ${storage.secretSet?'✓ Secret Access Key сохранён':'Secret Access Key пока не задан'}`;
  const storageLast=storage.lastTest;const storageResult=document.querySelector('#storage-test-result');storageResult.classList.toggle('success-text',Boolean(storageLast?.ok&&!storageTestError));storageResult.classList.toggle('error-text',Boolean(storageTestError||storageLast&&!storageLast.ok));
  storageResult.textContent=storageTestError||(storageLast?`${storageLast.ok?'✓ Хранилище успешно проверено':'× Проверка хранилища не пройдена'}${storageLast.message?` · ${storageLast.message}`:''}`:'Хранилище ещё не проверено');
  const rehearsal=environment==='rehearsal';document.querySelector('#rehearsal-seed').disabled=!rehearsal||busy;document.querySelector('#rehearsal-copy').disabled=!rehearsal||busy;document.querySelector('#rehearsal-simulate').disabled=!rehearsal||busy||!['question_open','voting_open'].includes(state.state.phase);
  document.querySelector('#rehearsal-hint').textContent=!rehearsal?'Для работы с тестовыми данными переключите режим слева.':['question_open','voting_open'].includes(state.state.phase)?'Симуляция отправит ответы на открытый вопрос или оценки открытого голосования.':'Для симуляции откройте вопрос или зрительское голосование.';
  if(force){[rules,branding,ya,storageForm].forEach(form=>{form.dataset.dirty='false';form.querySelector('.save-state').textContent='';});document.querySelector('#yandex-clientSecret').value='';document.querySelector('#storage-accessKeyId').value='';document.querySelector('#storage-secretAccessKey').value='';}
  syncStorageControls();
  updateRulePreview();settingsLoaded=true;
}
function updateRulePreview() {
  if(activeTab!=='settings')return;
  const retained=state.groups.length-1-2*Number(document.querySelector('#settings-trimCount').value);
  const el=document.querySelector('#rule-preview');el.classList.toggle('error-text',retained<1);el.textContent=retained<1?'С таким отсечением не остаётся оценок. Начать квиз нельзя.':`Групп: ${state.groups.length}. После отсечения у каждой останется оценок: ${retained}.`;
}
function syncLocks() {
  if(!state||!ready)return;
  const started=state.state.started;
  const notice=document.querySelector('#roster-lock');if(notice)notice.hidden=!started;
  for(const selector of ['#group-form','#question-form']){
    const form=document.querySelector(selector);if(!form)continue;
    form.querySelectorAll('input,textarea,select,button[type="submit"],button[data-remove-image]').forEach(input=>input.disabled=started||busy);
    form.querySelectorAll('.upload-button').forEach(label=>label.classList.toggle('is-disabled',started||busy));
  }
  const participant=document.querySelector('#participant-form');if(participant){
    const editing=Boolean(participant.elements.id.value);
    for(const key of ['participant-firstName','participant-lastName','participant-groupId'])participant.elements[key].disabled=started||busy;
    for(const key of ['participant-email','participant-code'])participant.elements[key].disabled=busy||(started&&!editing);
    participant.querySelector('button[type="submit"]').disabled=busy||(started&&!editing);document.querySelector('#regenerate-code').disabled=busy;
  }
  const rules=document.querySelector('#rules-form');if(rules){rules.querySelectorAll('input,button[type="submit"]').forEach(input=>input.disabled=started||busy);}
}
function syncPanel() {
  if(!state||!ready)return;
  document.querySelector('#phase-top').textContent=phaseLabels[state.state.phase]||'Подготовка';
  if(activeTab==='control')updateControl();
  if(activeTab==='groups')updateGroups();
  if(activeTab==='participants')updateParticipants();
  if(activeTab==='questions')updateQuestions();
  if(activeTab==='scores')updateScores();
  if(activeTab==='settings')loadSettings();
  syncLocks();
}
async function refresh() {
  try {
    const requestEnvironment=environment;
    const next=await api(envPath('/api/admin/state'));
    if(requestEnvironment!==environment)return;
    state=next;
    if(!ready)shell();else syncPanel();
    const status=document.querySelector('#connection-status');if(status){status.classList.remove('offline');status.innerHTML='<i></i> Соединение установлено';}
  } catch(error) {
    if(error.status===401){state=null;login();return;}
    if(!ready)app.innerHTML=`<main class="admin-login">${empty('Не удалось подключиться','Пробуем восстановить соединение…')}</main>`;
    const status=document.querySelector('#connection-status');if(status){status.classList.add('offline');status.innerHTML='<i></i> Соединение потеряно';}
  }
}
poll(refresh);
