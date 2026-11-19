/* Back in Frame web app: notifications on/off and the rhythm buttons. */
(function () {
  'use strict';
  const root = document.documentElement;
  const base = root.dataset.base;             // e.g. /a/<token>
  const vapidKey = root.dataset.vapid;
  const T = JSON.parse(document.getElementById('texts').textContent);
  const statusEl = document.getElementById('push-status');
  const pushBtn = document.getElementById('push-on');
  const offBtn = document.getElementById('push-off');
  const installHint = document.getElementById('install-hint');

  const ua = navigator.userAgent;
  const isIOS = /iphone|ipad|ipod/i.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const standalone = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
  const pushSupported = 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;

  function setStatus(text) { statusEl.textContent = text; }

  // -- iPhone and iPad: a step-by-step guide to the home screen ------------------------------
  // Which steps fit this browser. iOS 26 Safari reports an older iOS ("OS 18_6") in its user agent,
  // so "Version/26" is the better hint there.
  function iosFlow(agent) {
    const os = agent.match(/OS (\d+)[_.](\d+)/);
    const version = agent.match(/Version\/(\d+)\.(\d+)/);
    let major = os ? +os[1] : 0;
    let minor = os ? +os[2] : 0;
    if (version && +version[1] > major) { major = +version[1]; minor = +version[2]; }
    const otherBrowser = /CriOS|FxiOS|EdgiOS|OPiOS/.test(agent);
    const inApp = /FBAN|FBAV|Instagram|LinkedInApp|Snapchat|Line\/|GSA\/|MicroMessenger/.test(agent) ||
      (!otherBrowser && !/Safari\//.test(agent));
    if (major && (major < 16 || (major === 16 && minor < 4))) return 'old';
    if (inApp) return 'inapp';
    if (otherBrowser) return 'browser';
    return major >= 26 ? 'ios26' : 'ios';
  }

  function startGuide(flow) {
    const box = document.getElementById('install-hint');
    const list = box.querySelector('[data-flow="' + flow + '"]');
    list.hidden = false;
    box.querySelectorAll('[data-flow-note="' + flow + '"]').forEach((n) => { n.hidden = false; });
    if (flow === 'inapp' || flow === 'old') document.getElementById('guide-missing').hidden = true;
    const frames = list.dataset.frames.split(' ').filter(Boolean);
    const steps = list.tagName === 'OL' ? Array.from(list.children) : [];
    const picture = box.querySelector('.guide-picture');
    const prev = document.getElementById('guide-prev');
    const next = document.getElementById('guide-next');
    const play = document.getElementById('guide-play');
    const count = document.getElementById('guide-count');
    if (!frames.length) { picture.hidden = true; return flow; }
    if (frames.length < 2) { prev.hidden = next.hidden = play.hidden = count.hidden = true; }
    const quiet = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    let index = 0;
    let timer = null;

    function show(i) {
      index = (i + frames.length) % frames.length;
      box.querySelectorAll('.frame').forEach((g) => { g.setAttribute('hidden', ''); });
      const frame = box.querySelector('.frame[data-frame="' + frames[index] + '"]');
      if (frame) frame.removeAttribute('hidden');
      steps.forEach((li, n) => {
        li.classList.toggle('current', n === index);
        if (n === index) li.setAttribute('aria-current', 'step'); else li.removeAttribute('aria-current');
      });
      count.textContent = count.dataset.template.replace('{n}', index + 1).replace('{total}', frames.length);
    }
    function setPlaying(on) {
      clearInterval(timer);
      timer = on ? setInterval(() => show(index + 1), 2800) : null;
      play.dataset.playing = on ? '1' : '';
      play.querySelector('[aria-hidden]').textContent = on ? '❚❚' : '▶';
      play.querySelector('.sr').textContent = on ? play.dataset.pause : play.dataset.play;
    }
    prev.addEventListener('click', () => { setPlaying(false); show(index - 1); });
    next.addEventListener('click', () => { setPlaying(false); show(index + 1); });
    play.addEventListener('click', () => setPlaying(!play.dataset.playing));
    show(0);
    setPlaying(!quiet && frames.length > 1);
    return flow;
  }

  document.getElementById('copy-link').addEventListener('click', async () => {
    const done = document.getElementById('copy-done');
    const url = location.href.split('#')[0];
    try {
      await navigator.clipboard.writeText(url);
    } catch (err) {
      const field = document.createElement('textarea');
      field.value = url;
      field.setAttribute('readonly', '');
      field.style.position = 'absolute';
      field.style.left = '-9999px';
      document.body.appendChild(field);
      field.select();
      try { document.execCommand('copy'); } catch (e) { /* the user can still copy the address bar */ }
      field.remove();
    }
    done.textContent = document.getElementById('copied-text').value;
  });

  function keyBytes(b64) {
    const pad = '='.repeat((4 - (b64.length % 4)) % 4);
    const raw = atob((b64 + pad).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from(raw, (c) => c.charCodeAt(0));
  }

  async function post(path, body) {
    const res = await fetch(base + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
      credentials: 'same-origin',
    });
    if (!res.ok) throw new Error(String(res.status));
    return res.json();
  }

  async function registration() {
    return navigator.serviceWorker.register(base + '/sw.js', { scope: base + '/' });
  }

  async function refresh() {
    pushBtn.hidden = offBtn.hidden = true;
    if (isIOS && !standalone) {
      installHint.hidden = false;
      const flow = startGuide(iosFlow(ua));
      setStatus(flow === 'old' ? T.push_unsupported : T.ios_install_first);
      return;
    }
    if (!pushSupported) { setStatus(T.push_unsupported); return; }
    if (Notification.permission === 'denied') { setStatus(T.push_denied); return; }
    const reg = await registration();
    const sub = await reg.pushManager.getSubscription();
    if (sub) { setStatus(T.push_on); offBtn.hidden = false; }
    else { setStatus(T.push_off); pushBtn.hidden = false; }
  }

  pushBtn.addEventListener('click', async () => {
    pushBtn.disabled = true;
    try {
      const permission = await Notification.requestPermission();
      if (permission !== 'granted') { await refresh(); return; }
      const reg = await registration();
      await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(vapidKey) });
      await post('/push', sub.toJSON());
    } catch (err) {
      setStatus(T.push_failed);
      pushBtn.disabled = false;
      return;
    }
    pushBtn.disabled = false;
    await refresh();
  });

  offBtn.addEventListener('click', async () => {
    try {
      const reg = await registration();
      const sub = await reg.pushManager.getSubscription();
      if (sub) {
        await post('/push/remove', { endpoint: sub.endpoint });
        await sub.unsubscribe();
      }
    } catch (err) { /* nothing to undo */ }
    await refresh();
  });

  document.querySelectorAll('[data-command]').forEach((button) => {
    button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        const out = await post('/command/' + button.dataset.command);
        document.getElementById('rhythm').textContent = out.rhythm;
        document.getElementById('reply').textContent = out.reply;
        document.getElementById('pause').hidden = out.paused;
        document.getElementById('resume').hidden = !out.paused;
      } catch (err) {
        document.getElementById('reply').textContent = T.command_failed;
      }
      button.disabled = false;
    });
  });

  // A heart on a photo: tap again to take it back.
  document.querySelectorAll('[data-react]').forEach((button) => {
    button.addEventListener('click', async () => {
      const on = button.getAttribute('aria-pressed') !== 'true';
      button.disabled = true;
      try {
        await post('/react/' + button.dataset.react, { reaction: on ? 'heart' : '' });
        button.setAttribute('aria-pressed', on ? 'true' : 'false');
      } catch (err) {
        document.getElementById('reply').textContent = T.command_failed;
      }
      button.disabled = false;
    });
  });

  if (location.hash) {
    const el = document.getElementById(location.hash.slice(1));
    if (el) el.scrollIntoView();
  }
  refresh().catch(() => setStatus(T.push_unsupported));
})();
