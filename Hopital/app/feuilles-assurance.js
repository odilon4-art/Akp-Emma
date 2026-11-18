/* Ordonnance Mobile — module de feuilles d'assurance. Utilise la session Supabase de l'application. */
(() => {
  const SUPABASE_URL = location.origin;
  const SUPABASE_ANON_KEY = 'sb_publishable_wsJjuTPF3o_efMRFONly_w_YsgVFgZ8';
  const insurers = ['Aucune assurance','AMU','Allianz','Ascoma','GTA','Gras Savoye','INAM','INAMI','NSIA','Saham/Sanlam','Sunu','TransVie'];
  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const msg = (s, bad=false) => { $('message').textContent=s; $('message').className=bad?'error':'notice'; };
  const sb = window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY);
  let user=null, profile=null, patient=null, selectedPhoto=null;
  const role=()=>profile?.role||'';
  const isAdmin=()=>['admin','superadmin'].includes(role());
  const isHospital=()=>['medecin','infirmier'].includes(role());
  const isPharmacy=()=>role()==='pharmacien';
  async function init(){
    const {data:{session}}=await sb.auth.getSession();
    if(!session){ $('loginWarning').hidden=false; $('workspace').hidden=true; return; }
    user=session.user;
    const {data:p,error}=await sb.from('profiles').select('id,role,hopital_id,pharmacie_id,nom,prenom').eq('id',user.id).maybeSingle();
    if(error||!p){ msg('Impossible de vérifier votre profil. Vérifiez votre compte et les règles Supabase.',true); return; }
    profile=p;
    $('identity').textContent=`Connecté : ${p.prenom||''} ${p.nom||''} (${p.role})`;
    $('loginWarning').hidden=true; $('workspace').hidden=false;
    $('templatePanel').hidden=!(isAdmin()||isHospital());
    $('doctorPanel').hidden=!isHospital();
    $('pharmacyPanel').hidden=!isPharmacy();
    $('adminPanel').hidden=!isAdmin();
    await loadTemplates(); await loadSheets();
  }
  function insurerOptions(sel=''){return insurers.map(x=>`<option ${x===sel?'selected':''} value="${esc(x)}">${esc(x)}</option>`).join('');}
  async function loadTemplates(){
    const {data,error}=await sb.from('modeles_assurance').select('*').order('assurance').order('created_at',{ascending:false});
    if(error){msg('Le module attend la migration SQL fournie dans le ZIP. '+error.message,true);return;}
    const visible=(data||[]).filter(t=>t.statut==='approuve'||isAdmin()||t.cree_par===user.id);
    $('templates').innerHTML=visible.length?visible.map(t=>`<article class="item"><b>${esc(t.assurance)}</b> — ${esc(t.nom)} <span class="pill">${esc(t.statut)}</span><p>${esc(t.description||'')}</p>${t.statut!=='approuve'&&isAdmin()?`<button data-approve="${t.id}">Approuver le modèle</button>`:''}</article>`).join(''):'<p>Aucun modèle enregistré pour le moment.</p>';
    $('templates').querySelectorAll('[data-approve]').forEach(b=>b.onclick=()=>approveTemplate(b.dataset.approve));
  }
  async function approveTemplate(id){
    const {error}=await sb.from('modeles_assurance').update({statut:'approuve',approuve_par:user.id,approuve_le:new Date().toISOString()}).eq('id',id);
    if(error) return msg('Approbation refusée : '+error.message,true);
    await audit('modele_approuve', 'modele_assurance', id, {}); msg('Modèle approuvé.'); await loadTemplates();
  }
  async function uploadPhoto(file){
    if(!file) return null;
    const safe=file.name.replace(/[^a-zA-Z0-9._-]/g,'_'); const path=`${user.id}/${Date.now()}-${safe}`;
    const {error}=await sb.storage.from('insurance-forms').upload(path,file,{upsert:false,contentType:file.type||'image/jpeg'});
    if(error) throw error;
    return path;
  }
  async function createTemplate(e){
    e.preventDefault();
    try {
      let photoPath=null; if(selectedPhoto) photoPath=await uploadPhoto(selectedPhoto);
      const payload={assurance:$('tInsurance').value,nom:$('tName').value.trim(),description:$('tDescription').value.trim(),structure_json:{champs:['nom','prenom','date_naissance','telephone','numero_assure','diagnostic','actes','medicaments','consignes','signature_medecin','cachet'],photo_reference:!!photoPath},photo_path:photoPath,cree_par:user.id,hopital_id:profile.hopital_id,statut:'en_attente'};
      const {data,error}=await sb.from('modeles_assurance').insert(payload).select('id').single();
      if(error) throw error;
      await audit('modele_soumis','modele_assurance',data.id,{assurance:payload.assurance});
      msg('Modèle soumis pour validation. La photo sert de référence ; le formulaire numérique sera rempli dans l’application.'); e.target.reset(); selectedPhoto=null; $('photoPreview').textContent=''; await loadTemplates();
    } catch(err){msg('Échec : '+err.message,true);}
  }
  async function findPatient(){
    const code=$('patientCode').value.trim(); if(!code) return msg('Saisis le code carnet du patient.',true);
    const {data,error}=await sb.from('patients').select('id,nom,prenom,telephone,date_naissance,numero_assure,assurance,assure_principal,assurance2,numero_assure2,assure_principal2,sexe,code_carnet').eq('code_carnet',code).maybeSingle();
    if(error||!data){patient=null;return msg('Patient introuvable ou accès refusé.',true);}
    patient=data;
    $('patientSummary').innerHTML=`<b>${esc(data.prenom)} ${esc(data.nom)}</b><br>Téléphone : ${esc(data.telephone)}<br>Date de naissance : ${esc(data.date_naissance||'—')}<br>Assurance principale : ${esc(data.assurance||data.assure_principal||'Aucune assurance')}<br>N° assuré : ${esc(data.numero_assure||'—')}<br>Assurance secondaire : ${esc(data.assurance2||'Aucune assurance')}<br>N° secondaire : ${esc(data.numero_assure2||'—')}`;
    $('clinicalFields').hidden=false; msg('Patient chargé. Vérifie son identité avant de continuer.');
  }
  async function createSheet(e){
    e.preventDefault(); if(!patient) return msg('Recherche d’abord le patient.',true);
    const insurance=patient.assurance||patient.assure_principal||'Aucune assurance';
    const {data:tm,error:te}=await sb.from('modeles_assurance').select('id,structure_json').eq('assurance',insurance).eq('statut','approuve').order('created_at',{ascending:false}).limit(1).maybeSingle();
    if(te) return msg(te.message,true); if(insurance!=='Aucune assurance'&&!tm) return msg('Aucun modèle approuvé pour '+insurance+'. Demande à l’administrateur de valider le modèle.',true);
    const {data:sheet,error}=await sb.from('feuilles_assurance').insert({patient_id:patient.id,hopital_id:profile.hopital_id,medecin_id:user.id,modele_id:tm?.id||null,assurance,numero_assure:patient.numero_assure,assure_nom:patient.nom,assure_prenom:patient.prenom,assure_naissance:patient.date_naissance,assure_telephone:patient.telephone,donnees_medicales:{diagnostic:$('diagnostic').value.trim(),actes:$('actes').value.trim(),medicaments:$('medicaments').value.trim(),consignes:$('consignes').value.trim()},statut:'brouillon'}).select('id').single();
    if(error) return msg('Création refusée : '+error.message,true);
    await audit('feuille_creee','feuilles_assurance',sheet.id,{patient_id:patient.id,assurance}); msg('Feuille créée en brouillon. Vérifie tout puis valide-la.'); await loadSheets();
  }
  async function signSheet(id){
    const {error}=await sb.from('feuilles_assurance').update({statut:'validee',validee_par:user.id,validee_le:new Date().toISOString()}).eq('id',id).eq('statut','brouillon');
    if(error) return msg('Validation refusée : '+error.message,true);
    await audit('feuille_validee','feuilles_assurance',id,{}); msg('Feuille validée. Le contenu médical est désormais verrouillé dans le flux applicatif.'); await loadSheets();
  }
  async function loadSheets(){
    const {data,error}=await sb.from('feuilles_assurance').select('id,assurance,assure_nom,assure_prenom,statut,created_at,donnees_medicales').order('created_at',{ascending:false}).limit(50);
    if(error){$('sheets').innerHTML='<p>Les feuilles apparaîtront après installation de la migration SQL.</p>';return;}
    $('sheets').innerHTML=(data||[]).length?(data||[]).map(s=>`<article class="item"><b>Feuille #${s.id}</b> — ${esc(s.assurance)} <span class="pill">${esc(s.statut)}</span><p>${esc(s.assure_prenom)} ${esc(s.assure_nom)} · ${new Date(s.created_at).toLocaleString('fr-FR')}</p><p>${esc(s.donnees_medicales?.diagnostic||'')}</p>${isHospital()&&s.statut==='brouillon'?`<button data-sign="${s.id}">Valider la feuille</button>`:''}<button data-view="${s.id}">Consulter / imprimer</button></article>`).join(''):'<p>Aucune feuille pour le moment.</p>';
    $('sheets').querySelectorAll('[data-sign]').forEach(b=>b.onclick=()=>signSheet(b.dataset.sign));
    $('sheets').querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>printSheet((data||[]).find(s=>String(s.id)===b.dataset.view)));
  }
  function printSheet(s){if(!s)return; const d=s.donnees_medicales||{};const w=window.open('','_blank');if(!w)return msg('Autorise les fenêtres pop-up pour imprimer.',true);w.document.write(`<!doctype html><html lang="fr"><meta charset="utf-8"><title>Feuille assurance ${s.id}</title><style>body{font:15px Arial;margin:32px;color:#111}h1{font-size:22px}label{font-weight:bold}section{border:1px solid #bbb;padding:12px;margin:10px 0}</style><h1>ORDONNANCE MOBILE — FEUILLE DE SOINS</h1><p>Référence : ${s.id} · Assurance : ${esc(s.assurance)} · Statut : ${esc(s.statut)}</p><section><label>Patient</label><p>${esc(s.assure_prenom)} ${esc(s.assure_nom)}</p></section><section><label>Diagnostic</label><p>${esc(d.diagnostic||'')}</p><label>Actes</label><p>${esc(d.actes||'')}</p><label>Médicaments prescrits</label><p>${esc(d.medicaments||'')}</p><label>Consignes</label><p>${esc(d.consignes||'')}</p></section><p>Document numérique généré par Ordonnance Mobile. La conformité à un formulaire officiel doit être vérifiée par l’assureur.</p><script>window.print()<\/script></html>`);w.document.close();}
  async function pharmacyLoad(){const code=$('sheetId').value.trim();if(!code)return msg('Saisis la référence de la feuille.',true);const {data,error}=await sb.from('feuilles_assurance').select('id,patient_id,assurance,assure_nom,assure_prenom,statut,donnees_medicales').eq('id',code).maybeSingle();if(error||!data)return msg('Feuille introuvable ou accès refusé.',true);if(data.statut!=='validee')return msg('La feuille doit être validée par le médecin avant la délivrance.',true);$('pharmacySheet').innerHTML=`<b>${esc(data.assure_prenom)} ${esc(data.assure_nom)}</b> — ${esc(data.assurance)}<p>${esc(data.donnees_medicales?.medicaments||'Aucun médicament indiqué')}</p><form id="dispenseForm"><label>Code de délivrance<input id="dispenseCode" required></label><label>Médicaments délivrés<textarea id="dispensed" required></textarea></label><label>Quantités / remarques<textarea id="dispenseNotes"></textarea></label><button>Enregistrer la délivrance</button></form>`;$('dispenseForm').onsubmit=async e=>{e.preventDefault();const {error}=await sb.from('delivrances_pharmacie').insert({feuille_id:data.id,pharmacie_id:profile.pharmacie_id,pharmacien_id:user.id,code_delivrance:$('dispenseCode').value.trim(),medicaments_delivres:$('dispensed').value.trim(),remarques:$('dispenseNotes').value.trim()});if(error)return msg('Délivrance non enregistrée : '+error.message,true);await audit('delivrance_enregistree','feuilles_assurance',data.id,{});msg('Délivrance enregistrée. La prescription médicale n’a pas été modifiée.');};}
  async function audit(action,table,id,details){try{await sb.from('journal_feuilles_assurance').insert({acteur_id:user.id,action,table_cible:table,id_cible:String(id),details});}catch(_){/* journal optionnel si les règles ne le permettent pas */}}
  $('templateForm').addEventListener('submit',createTemplate);$('doctorFind').addEventListener('click',findPatient);$('sheetForm').addEventListener('submit',createSheet);$('photo').addEventListener('change',e=>{selectedPhoto=e.target.files?.[0]||null;$('photoPreview').textContent=selectedPhoto?`Photo choisie : ${selectedPhoto.name} — le texte doit être vérifié et saisi/corrigé avant approbation.`:'';});$('pharmacyFind').addEventListener('click',pharmacyLoad);$('reload').addEventListener('click',async()=>{await loadTemplates();await loadSheets();msg('Liste actualisée.');});
  init();
})();
