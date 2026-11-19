// Admin pages: ask before a form with data-confirm is sent (kept out of inline handlers for the CSP).
document.addEventListener('submit', function (event) {
  var text = event.target.getAttribute && event.target.getAttribute('data-confirm');
  if (text && !window.confirm(text)) event.preventDefault();
});
