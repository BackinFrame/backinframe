// Show progress while photos upload; without JavaScript the form still works.
(function () {
  var form = document.getElementById('upload');
  if (!form || !window.FormData || !window.XMLHttpRequest) return;
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var bar = document.getElementById('progress'), busy = document.getElementById('busy');
    var button = form.querySelector('button');
    button.disabled = true; bar.hidden = false; busy.hidden = false;
    var xhr = new XMLHttpRequest();
    xhr.open('POST', form.action);
    xhr.upload.onprogress = function (ev) { if (ev.lengthComputable) bar.value = Math.round(ev.loaded * 100 / ev.total); };
    xhr.onload = function () { document.open(); document.write(xhr.responseText); document.close(); };
    xhr.onerror = function () { button.disabled = false; bar.hidden = true; busy.hidden = true; form.submit(); };
    xhr.send(new FormData(form));
  });
})();
