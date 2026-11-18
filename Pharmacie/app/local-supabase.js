/* Ordonnance Mobile (version PC) : remplace supabase-js par un client qui parle au serveur local Python.
   Même interface que supabase-js pour la partie utilisée par l'application. Aucun Internet requis. */
(function () {
  const KEY = 'omh_token';
  const tok = () => { try { return localStorage.getItem(KEY) || ''; } catch (e) { return ''; } };
  const setTok = t => { try { t ? localStorage.setItem(KEY, t) : localStorage.removeItem(KEY); } catch (e) {} };

  async function post(path, body, raw) {
    const h = { 'X-OM': '1', 'Authorization': 'Bearer ' + tok() };
    h['Content-Type'] = raw ? 'application/octet-stream' : 'application/json';
    let r;
    try { r = await fetch(path, { method: 'POST', headers: h, body: raw ? body : JSON.stringify(body || {}) }); }
    catch (e) { return { status: 0, j: { error: 'Serveur local injoignable' } }; }
    let j = {};
    try { j = await r.json(); } catch (e) {}
    return { status: r.status, j };
  }
  const err = (r) => ({ message: r.j.error || r.j.message || 'Erreur', status: r.status, code: r.j.code || null });

  class Query {
    constructor(table) { this.q = { table, op: 'select', filters: [], order: [], limit: null, single: null, select: '*', returning: false }; }
    select(c) { if (this.q.op !== 'select') this.q.returning = true; this.q.select = c || '*'; return this; }
    insert(v) { this.q.op = 'insert'; this.q.values = v; return this; }
    update(v) { this.q.op = 'update'; this.q.values = v; return this; }
    delete() { this.q.op = 'delete'; return this; }
    _f(c, o, v) { this.q.filters.push([c, o, v]); return this; }
    eq(c, v) { return this._f(c, 'eq', v); }
    neq(c, v) { return this._f(c, 'neq', v); }
    gt(c, v) { return this._f(c, 'gt', v); }
    gte(c, v) { return this._f(c, 'gte', v); }
    lt(c, v) { return this._f(c, 'lt', v); }
    lte(c, v) { return this._f(c, 'lte', v); }
    in(c, v) { return this._f(c, 'in', v); }
    is(c, v) { return this._f(c, 'is', v); }
    order(c, o) { this.q.order.push([c, !(o && o.ascending === false)]); return this; }
    limit(n) { this.q.limit = n; return this; }
    single() { this.q.single = 'single'; return this; }
    maybeSingle() { this.q.single = 'maybe'; return this; }
    then(res, rej) { return this._run().then(res, rej); }
    async _run() {
      const r = await post('/api/rest', this.q);
      if (r.status >= 400 || r.j.error) return { data: null, error: err(r) };
      return { data: r.j.data === undefined ? null : r.j.data, error: null };
    }
  }

  function createClient() {
    const auth = {
      async signInWithPassword({ email, password }) {
        const r = await post('/api/auth/signin', { email, password });
        if (r.status >= 400 || r.j.error) return { data: { user: null, session: null }, error: err(r) };
        setTok(r.j.token);
        const session = { access_token: r.j.token, user: r.j.user };
        return { data: { user: r.j.user, session }, error: null };
      },
      async getSession() {
        const r = await post('/api/auth/session', {});
        return { data: { session: r.j.user ? { access_token: tok(), user: r.j.user } : null }, error: null };
      },
      async signOut() { await post('/api/auth/signout', {}); setTok(''); return { error: null }; },
      async updateUser(p) {
        const r = await post('/api/auth/update', p);
        if (r.status >= 400 || r.j.error) return { data: null, error: err(r) };
        if (r.j.token) setTok(r.j.token);
        return { data: { user: {} }, error: null };
      },
      async resetPasswordForEmail() {
        return { data: null, error: { message: "indisponible en mode PC (pas d'e-mail). Demandez à l'administrateur de votre établissement : Personnel > Changer le mot de passe." } };
      },
      onAuthStateChange() { return { data: { subscription: { unsubscribe() {} } } }; },
    };
    return {
      auth,
      from: t => new Query(t),
      rpc: (name, args) => ({
        then(res, rej) {
          return post('/api/rpc/' + name, args || {}).then(r =>
            (r.status >= 400 || r.j.error) ? { data: null, error: err(r) } : { data: r.j.data === undefined ? null : r.j.data, error: null }).then(res, rej);
        },
      }),
      functions: {
        async invoke(name, opts) {
          const r = await post('/functions/v1/' + name, (opts && opts.body) || {});
          if (r.status >= 400 || r.j.error) {
            return { data: null, error: { message: r.j.error || 'Erreur', context: { json: async () => r.j } } };
          }
          return { data: r.j, error: null };
        },
      },
      channel() {
        const ch = {
          cbs: [], timer: null,
          on(_t, _f, cb) { ch.cbs.push(cb); return ch; },
          subscribe() {
            let last = null;
            ch.timer = setInterval(async () => {
              const r = await post('/api/rev', {});
              if (last !== null && r.j.rev !== last) ch.cbs.forEach(cb => cb({}));
              if (r.j.rev !== undefined) last = r.j.rev;
            }, 2000);
            return ch;
          },
        };
        return ch;
      },
      removeChannel(ch) { if (ch && ch.timer) clearInterval(ch.timer); },
      storage: {
        from: bucket => ({
          async upload(path, file) {
            const r = await post('/api/storage/' + bucket + '/' + path.split('/').map(encodeURIComponent).join('/'), file, true);
            return (r.status >= 400 || r.j.error) ? { data: null, error: err(r) } : { data: { path: r.j.path }, error: null };
          },
        }),
      },
    };
  }
  window.supabase = { createClient };

  /* Bandeau « mode PC / démo » + lien vers le simulateur de patients */
  addEventListener('DOMContentLoaded', async () => {
    if (/simulateur-patient/.test(location.pathname) || /recovery/.test(location.pathname)) return;
    const r = await post('/api/mode', {});
    const m = r.j || {};
    const box = document.createElement('div');
    box.style.cssText = 'position:fixed;right:10px;bottom:'+(m.kind==='patient'?'84px':'10px')+';z-index:9999;font:13px system-ui,sans-serif;max-width:300px';
    const btn = document.createElement('button');
    btn.textContent = m.demo ? '🧪 Mode démo' : '🧪 Outils';
    btn.style.cssText = 'background:#6d28d9;color:#fff;border:0;border-radius:20px;padding:8px 14px;cursor:pointer;float:right';
    const panel = document.createElement('div');
    panel.hidden = true;
    panel.style.cssText = 'clear:both;background:#fff;color:#111;border:1px solid #8886;border-radius:12px;padding:10px;margin-bottom:6px;box-shadow:0 4px 18px #0004';
    let h = '<b>' + (m.demo ? 'Mode démo (données fictives, effacées à chaque lancement)' : 'Version PC (données sur cet ordinateur)') + '</b>';
    if (m.demo && m.comptes && m.comptes.length) {
      h += '<br><small>Cliquez pour remplir la connexion (mot de passe : ' + (m.comptes.find(c => (c.app || 'hopital') === (m.kind || 'hopital')) || m.comptes[0]).password + ')</small><br>';
      h += m.comptes.filter(c => (c.app || 'hopital') === (m.kind || 'hopital')).map(c => '<button data-e="' + c.email + '" data-p="' + c.password + '" style="margin:3px 3px 0 0;padding:5px 8px;border-radius:8px;border:1px solid #8886;background:#f3f3f3;color:#111;cursor:pointer">' + (c.label || c.role) + '</button>').join('');
    }
    if (m.kind === 'patient') h += '<br><small>Code secret 123456 pour tous. Mawuli Dossou (90444444) a une ordonnance à chercher en pharmacie. Lancez aussi Hôpital et Pharmacie en démo : les trois partagent les mêmes données.</small>'; else if (m.notes) h += '<br><small>' + m.notes + '</small>';
    h += '<br><a href="simulateur-patient.html" style="display:inline-block;margin-top:8px;color:#6d28d9;font-weight:700">➜ Simulateur patient ' + (m.kind === 'pharmacie' ? '(réserver des produits)' : '(créer des patients et rendez-vous)') + '</a>';
    panel.innerHTML = h;
    panel.querySelectorAll('button[data-e]').forEach(b => b.onclick = () => {
      const tl = document.getElementById('tl'), cs = document.getElementById('cs');
      if (tl && cs) { tl.value = b.dataset.e.replace(/^p(\d+)@.*$/, '$1'); cs.value = b.dataset.p; cs.focus(); return; }
      const e = document.getElementById('em'), p = document.getElementById('pw');
      if (e && p) { e.value = b.dataset.e; p.value = b.dataset.p; p.focus(); } else alert('Allez d\'abord sur l\'écran « J\'ai déjà un compte ».');
    });
    btn.onclick = () => { panel.hidden = !panel.hidden; };
    box.append(panel, btn);
    document.body.appendChild(box);
  });
})();
