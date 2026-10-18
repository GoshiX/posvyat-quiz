const route = location.pathname.replace(/\/$/, '') || '/';
if (route === '/screen') {
  document.title = 'Экран · Посвящение';
  await import('./screen.js');
} else if (route === '/admin') {
  document.title = 'Управление · Посвящение';
  await import('./admin.js');
} else {
  await import('./participant.js');
}
