try {
  document.documentElement.classList.toggle('dark', localStorage.getItem('bypay-theme') === 'dark');
  document.documentElement.lang = localStorage.getItem('bypay-locale') === 'en' ? 'en' : 'zh-CN';
} catch { /* Dark Chinese defaults also work without storage. */ }
