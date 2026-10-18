export const app = document.querySelector('#app');
export const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
export const number = value => new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 1}).format(Number(value) || 0);
export const decimal = value => new Intl.NumberFormat('ru-RU', {minimumFractionDigits:1, maximumFractionDigits:1}).format(Number(value) || 0);
export function icon(name, size=20) {
  const paths = {
    arrow:'<path d="M5 12h14M13 6l6 6-6 6"/>',
    chevron:'<path d="m9 5 7 7-7 7"/>',
    check:'<path d="m5 12 4 4L19 6"/>',
    close:'<path d="m6 6 12 12M6 18 18 6"/>',
    plus:'<path d="M12 5v14M5 12h14"/>',
    heart:'<path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8l1.1 1.1L12 21l7.8-7.5 1.1-1.1a5.5 5.5 0 0 0-.1-7.8Z"/>',
    play:'<path d="m8 4 12 8-12 8Z"/>',
    groups:'<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/>',
    people:'<circle cx="9" cy="8" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3M17 5a3 3 0 0 1 0 6M21 21v-3a6 6 0 0 0-4-5.6"/>',
    question:'<circle cx="12" cy="12" r="9"/><path d="M9 9a3 3 0 0 1 6 0c0 2-3 2-3 4M12 17h.01"/>',
    chart:'<path d="M4 20V10M12 20V4M20 20v-7"/>',
    settings:'<path d="M4 7h16M4 17h16"/><circle cx="8" cy="7" r="3"/><circle cx="16" cy="17" r="3"/>',
    screen:'<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M12 17v4M8 21h8"/>',
    logout:'<path d="M9 4H4v16h5M9 12h12m-5-5 5 5-5 5"/>',
    copy:'<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>',
    upload:'<path d="M12 16V3m-5 5 5-5 5 5M3 16v5h18v-5"/>',
    clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    spark:'<path d="m12 2 2.6 7.4L22 12l-7.4 2.6L12 22l-2.6-7.4L2 12l7.4-2.6Z"/>',
    lock:'<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V6a4 4 0 0 1 8 0v4"/>',
  };
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.spark}</svg>`;
}
export function avatar(url, label, className='') {
  return `<span class="avatar ${className}"><span>${esc(label)}</span>${url ? `<img src="${esc(url)}" alt="" loading="lazy">` : ''}</span>`;
}
document.addEventListener('error', event => {
  if (event.target instanceof HTMLImageElement) event.target.hidden = true;
}, true);
export async function api(path, options={}) {
  const response = await fetch(path, {credentials:'same-origin', cache:'no-store', ...options, headers: {'Content-Type':'application/json', ...options.headers}, body:options.body === undefined ? undefined : JSON.stringify(options.body)});
  let result;
  try { result = await response.json(); } catch { result = {}; }
  if (!response.ok) {
    const error = new Error(result.error || 'Не удалось выполнить запрос. Попробуйте ещё раз.');
    error.status = response.status;
    throw error;
  }
  return result;
}
let toastTimer;
export function toast(message, error=false) {
  const element = document.querySelector('#toast');
  element.textContent = message;
  element.classList.toggle('is-error', error);
  element.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { element.hidden = true; }, error ? 6500 : 3500);
}
export function poll(callback) {
  let pending = false;
  const tick = async () => {
    if (pending) return;
    pending = true;
    try { await callback(); } finally { pending = false; }
  };
  tick();
  const interval = setInterval(tick, 1000);
  window.addEventListener('online', tick);
  return () => { clearInterval(interval); window.removeEventListener('online', tick); };
}
export function field(label, name, value='', options={}) {
  const {type='text',required=false,help='',disabled=false,min,max,placeholder='',autocomplete='off',wide=false} = options;
  const attrs = `id="${esc(name)}" data-testid="${esc(name)}" name="${esc(name)}" ${required?'required':''} ${disabled?'disabled':''} ${min!==undefined?`min="${min}"`:''} ${max!==undefined?`max="${max}"`:''} placeholder="${esc(placeholder)}" autocomplete="${autocomplete}"`;
  return `<div class="field ${wide?'field-wide':''}"><label for="${esc(name)}">${esc(label)}</label>${type==='textarea'?`<textarea ${attrs} rows="3">${esc(value)}</textarea>`:`<input ${attrs} type="${type}" value="${esc(value)}" ${type==='number'?'step="1"':''}>`}${help?`<small>${esc(help)}</small>`:''}</div>`;
}
export async function copyText(value) {
  try { await navigator.clipboard.writeText(value); }
  catch {
    const input = document.createElement('textarea'); input.value = value; input.style.position='fixed'; input.style.opacity='0'; document.body.append(input); input.select(); document.execCommand('copy'); input.remove();
  }
  toast('Скопировано');
}
export function readFile(file) {
  return new Promise((resolve,reject) => {
    const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(',')[1]); reader.onerror = () => reject(new Error('Не удалось прочитать файл')); reader.readAsDataURL(file);
  });
}
export const phaseLabels = {setup:'Подготовка',waiting:'Ожидание',question_open:'Приём ответов',question_closed:'Вопрос закрыт',question_reveal:'Показ ответов',quiz_finished:'Квиз завершён',voting_open:'Голосование открыто',voting_closed:'Голосование закрыто'};
