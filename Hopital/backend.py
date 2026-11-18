"""Serveur local d'Ordonnance Mobile (remplace Supabase).

- Base SQLite sur le PC (aucun Internet requis)
- Comptes + mots de passe hachés (PBKDF2)
- Reproduit l'API utilisée par l'application : tables, fonctions RPC, fonctions serveur
- Règles d'accès simples (équivalent des règles RLS) : chaque établissement ne voit que ses données
"""
import datetime
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import socket
import sqlite3
import threading
import time
import unicodedata
import urllib.parse
import uuid
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

STAFF_ROLES = ("infirmier", "medecin", "admin")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class ApiError(Exception):
    def __init__(self, message, status=400, code=None):
        super().__init__(message)
        self.message, self.status, self.code = message, status, code


# --------------------------------------------------------------------------- base
class DB:
    UNIQUE = {"ordonnances": [("rendez_vous_id",)],
              "delivrances_pharmacie": [("feuille_id", "pharmacie_id")],
              "produits_pharmacie": [("pharmacie_id", "nom")]}

    def __init__(self, path):
        self.lock = threading.RLock()
        self.con = sqlite3.connect(path, check_same_thread=False, timeout=30)
        try:
            self.con.execute("pragma journal_mode=wal")
        except sqlite3.Error:
            pass
        self.con.executescript("""
        create table if not exists docs(n integer primary key autoincrement, tbl text not null, id text not null, data text not null);
        create index if not exists docs_idx on docs(tbl,id);
        create table if not exists users(id text primary key, email text unique not null, salt text not null, pw text not null, created text);
        create table if not exists sessions(token text primary key, user_id text not null, created text);
        create table if not exists seq(tbl text primary key, n integer not null);
        create table if not exists meta(k text primary key, v integer not null);
        create table if not exists kv(k text primary key, v text);
        """)

    @property
    def rev(self):
        r = self.con.execute("select v from meta where k='rev'").fetchone()
        return r[0] if r else 0

    def _bump(self):
        self.con.execute("insert into meta(k,v) values('rev',1) on conflict(k) do update set v=v+1")

    # utilitaires
    def _next(self, tbl):
        r = self.con.execute("select n from seq where tbl=?", (tbl,)).fetchone()
        n = (r[0] if r else 0) + 1
        self.con.execute("insert or replace into seq values(?,?)", (tbl, n))
        return n

    def all(self, tbl):
        with self.lock:
            return [json.loads(d) for (d,) in self.con.execute("select data from docs where tbl=? order by n", (tbl,))]

    def get(self, tbl, id_):
        with self.lock:
            r = self.con.execute("select data from docs where tbl=? and id=?", (tbl, str(id_))).fetchone()
            return json.loads(r[0]) if r else None

    def _begin(self):
        if not self.con.in_transaction:
            self.con.execute("begin immediate")

    def insert(self, tbl, row):
        with self.lock:
            self._begin()
            try:
                row = dict(row)
                for cols in self.UNIQUE.get(tbl, []):
                    if all(row.get(c) is not None for c in cols):
                        for o in self.all(tbl):
                            if all(str(o.get(c)) == str(row[c]) for c in cols):
                                raise ApiError('duplicate key value violates unique constraint "%s_%s_key"' % (tbl, "_".join(cols)), 409, "23505")
                if row.get("id") in (None, ""):
                    row["id"] = self._next(tbl)
                row.setdefault("created_at", now())
                self.con.execute("insert into docs(tbl,id,data) values(?,?,?)", (tbl, str(row["id"]), json.dumps(row, ensure_ascii=False)))
                self._bump()
                self.con.commit()
                return row
            except BaseException:
                self.con.rollback()
                raise

    def update(self, tbl, id_, changes):
        with self.lock:
            self._begin()
            try:
                row = self.get(tbl, id_)
                if row is None:
                    self.con.rollback()
                    return None
                row.update(changes)
                self.con.execute("update docs set data=? where tbl=? and id=?", (json.dumps(row, ensure_ascii=False), tbl, str(id_)))
                self._bump()
                self.con.commit()
                return row
            except BaseException:
                self.con.rollback()
                raise

    def delete(self, tbl, id_):
        with self.lock:
            self.con.execute("delete from docs where tbl=? and id=?", (tbl, str(id_)))
            self._bump()
            self.con.commit()

    # comptes
    @staticmethod
    def _hash(pw, salt):
        return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000).hex()

    def create_user(self, email, password, min_len=8):
        email = email.strip().lower()
        if not EMAIL_RE.match(email):
            raise ApiError("Adresse e-mail invalide")
        if len(password or "") < min_len:
            raise ApiError("Mot de passe : %d caractères minimum" % min_len)
        with self.lock:
            if self.con.execute("select 1 from users where email=?", (email,)).fetchone():
                raise ApiError("Cette adresse e-mail est déjà utilisée")
            uid, salt = str(uuid.uuid4()), secrets.token_hex(16)
            try:
                self.con.execute("insert into users values(?,?,?,?,?)", (uid, email, salt, self._hash(password, salt), now()))
            except sqlite3.IntegrityError:
                raise ApiError("Cette adresse e-mail est déjà utilisée")
            self.con.commit()
            return uid

    def check_login(self, email, password):
        email = (email or "").strip().lower()
        if email.endswith(PATIENT_DOMAIN):
            email = patient_email(email[1:-len(PATIENT_DOMAIN)])
        with self.lock:
            r = self.con.execute("select id,salt,pw from users where email=?", (email.strip().lower(),)).fetchone()
        if not r or not hmac.compare_digest(self._hash(password, r[1]), r[2]):
            return None
        return r[0]

    def set_password(self, uid, password, min_len=8):
        if len(password or "") < min_len:
            raise ApiError("Mot de passe : %d caractères minimum" % min_len)
        with self.lock:
            salt = secrets.token_hex(16)
            self.con.execute("update users set salt=?,pw=? where id=?", (salt, self._hash(password, salt), uid))
            self.con.execute("delete from sessions where user_id=?", (uid,))
            self.con.commit()

    def same_password(self, uid, password):
        with self.lock:
            r = self.con.execute("select salt,pw from users where id=?", (uid,)).fetchone()
        return bool(r) and hmac.compare_digest(self._hash(password, r[0]), r[1])

    def email_of(self, uid):
        r = self.con.execute("select email from users where id=?", (uid,)).fetchone()
        return r[0] if r else None

    def set_email(self, uid, email):
        email = email.strip().lower()
        if not EMAIL_RE.match(email):
            raise ApiError("Adresse e-mail invalide")
        with self.lock:
            if self.con.execute("select 1 from users where email=? and id<>?", (email, uid)).fetchone():
                raise ApiError("Cette adresse e-mail est déjà utilisée")
            self.con.execute("update users set email=? where id=?", (email, uid))
            self.con.commit()

    def new_session(self, uid):
        t = secrets.token_urlsafe(32)
        with self.lock:
            self.con.execute("insert into sessions values(?,?,?)", (t, uid, now()))
            self.con.commit()
        return t

    def session_user(self, token):
        if not token:
            return None
        with self.lock:
            r = self.con.execute("select user_id from sessions where token=?", (token,)).fetchone()
        return {"id": r[0], "email": self.email_of(r[0])} if r else None

    def drop_session(self, token):
        with self.lock:
            self.con.execute("delete from sessions where token=?", (token,))
            self.con.commit()


# --------------------------------------------------------------------------- requêtes
def split_top(s):
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def norm(s):
    t = unicodedata.normalize("NFD", str(s or "").lower()).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", t).split())


def digits(s):
    """Numéro de téléphone réduit à ses chiffres, sans indicatif Togo (+228 / 00228) : 90 12 34 56 et +228 90 12 34 56 sont le même numéro."""
    d = re.sub(r"\D", "", str(s or ""))
    if d.startswith("00"):
        d = d[2:]
    if d.startswith("228") and len(d) == 11:
        d = d[3:]
    return d


PATIENT_DOMAIN = "@patient.ordonnancemobile.app"


def patient_email(tel):
    return "p%s%s" % (digits(tel), PATIENT_DOMAIN)


# relation : (table cible, colonne locale, colonne distante, "one"|"many")
REL = {
    ("rendez_vous", "patients"): ("patients", "patient_id", "id", "one"),
    ("rendez_vous", "constantes"): ("constantes", "id", "rendez_vous_id", "one"),
    ("rendez_vous", "ordonnances"): ("ordonnances", "id", "rendez_vous_id", "one"),
    ("ordonnances", "ordonnance_lignes"): ("ordonnance_lignes", "id", "ordonnance_id", "many"),
    ("resultats_analyses", "patients"): ("patients", "patient_id", "id", "one"),
    ("feuilles_assurance", "patients"): ("patients", "patient_id", "id", "one"),
    ("rendez_vous", "hopitaux"): ("hopitaux", "hopital_id", "id", "one"),
    ("ordonnances", "hopitaux"): ("hopitaux", "hopital_id", "id", "one"),
    ("resultats_analyses", "hopitaux"): ("hopitaux", "hopital_id", "id", "one"),
    ("commandes_pharmacie", "pharmacies"): ("pharmacies", "pharmacie_id", "id", "one"),
}


def project(db, tbl, row, select):
    if not select or select.strip() == "*":
        return dict(row)
    out = {}
    for item in split_top(select):
        m = re.match(r"^([a-z_]+)\((.*)\)$", item, re.S)
        if m:
            name, inner = m.group(1), m.group(2)
            rel = REL.get((tbl, name))
            if not rel:
                raise ApiError("Relation inconnue : %s" % name)
            target, lc, rc, kind = rel
            matches = [r for r in db.all(target) if row.get(lc) is not None and str(r.get(rc)) == str(row.get(lc))]
            sub = [project(db, target, r, inner) for r in matches]
            out[name] = (sub[-1] if sub else None) if kind == "one" else sub
        elif item == "*":
            out.update(row)
        else:
            out[item] = row.get(item)
    return out


def cmp_ok(val, op, ref):
    if op == "is":
        return (val is None) == (ref is None)
    if op == "in":
        return str(val) in [str(x) for x in ref]
    if op == "eq":
        return str(val) == str(ref) if val is not None and ref is not None else val == ref
    if op == "neq":
        return str(val) != str(ref)
    if val is None:
        return False
    try:
        a, b = (float(val), float(ref)) if not isinstance(val, str) else (val, str(ref))
    except (TypeError, ValueError):
        a, b = str(val), str(ref)
    return {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}.get(op, False)


class Ctx:
    """Utilisateur connecté + profil."""

    def __init__(self, db, user):
        self.db, self.user = db, user
        self.profile = db.get("profiles", user["id"]) if user else None

    @property
    def uid(self):
        return self.user["id"]

    @property
    def role(self):
        return (self.profile or {}).get("role")

    @property
    def hop(self):
        return (self.profile or {}).get("hopital_id")

    def is_admin(self):
        return self.role in ("admin", "superadmin")

    def staff(self):
        return self.role in STAFF_ROLES and self.hop is not None

    def same_hop(self, row):
        return self.hop is not None and str(row.get("hopital_id")) == str(self.hop)

    @property
    def pharm(self):
        return (self.profile or {}).get("pharmacie_id")

    def pharm_staff(self):
        return self.role in ("pharmacien", "admin") and self.pharm is not None

    def same_pharm(self, row):
        return self.pharm is not None and str(row.get("pharmacie_id")) == str(self.pharm)

    @property
    def pat(self):
        return (self.profile or {}).get("patient_id")

    def is_patient(self):
        return self.role == "patient" and self.pat is not None

    def mine(self, row):
        return self.pat is not None and str(row.get("patient_id")) == str(self.pat)


def visible_patient(c, tbl, row):
    """Un patient ne voit que son carnet, ses rendez-vous, ordonnances, résultats, commandes et avis,
    plus les établissements publiés."""
    if tbl == "profiles":
        return row["id"] == c.uid
    if tbl == "patients":
        return str(row.get("id")) == str(c.pat)
    if tbl in ("hopitaux", "pharmacies"):
        return bool(row.get("publie"))
    if tbl in ("rendez_vous", "ordonnances", "resultats_analyses", "commandes_pharmacie", "avis"):
        return c.mine(row)
    if tbl == "ordonnance_lignes":
        o = c.db.get("ordonnances", row.get("ordonnance_id"))
        return bool(o) and c.mine(o)
    if tbl == "constantes":
        r = c.db.get("rendez_vous", row.get("rendez_vous_id"))
        return bool(r) and c.mine(r)
    return False


def visible(c, tbl, row, cache):
    if c.is_patient():
        return visible_patient(c, tbl, row)
    if tbl == "profiles":
        return row["id"] == c.uid
    if tbl in ("hopitaux", "patients", "pharmacies"):
        return True
    if tbl in ("rendez_vous", "resultats_analyses", "ordonnances"):
        return c.same_hop(row)
    if tbl in ("produits_pharmacie", "commandes_pharmacie", "ventes_comptoir"):
        return c.pharm_staff() and c.same_pharm(row)
    if tbl == "constantes":
        r = cache.setdefault(("rdv", row.get("rendez_vous_id")), c.db.get("rendez_vous", row.get("rendez_vous_id")))
        return bool(r) and c.same_hop(r)
    if tbl == "ordonnance_lignes":
        o = c.db.get("ordonnances", row.get("ordonnance_id"))
        return bool(o) and c.same_hop(o)
    if tbl == "modeles_assurance":
        return row.get("statut") == "approuve" or row.get("cree_par") == c.uid or c.is_admin()
    if tbl == "feuilles_assurance":
        return (c.is_admin() or row.get("medecin_id") == c.uid
                or (c.role in ("medecin", "infirmier") and c.same_hop(row))
                or (c.role == "pharmacien" and row.get("statut") == "validee"))
    if tbl == "delivrances_pharmacie":
        return c.is_admin() or row.get("pharmacien_id") == c.uid
    if tbl == "journal_feuilles_assurance":
        return c.is_admin() or row.get("acteur_id") == c.uid
    return False


def check_hopital_publish(h):
    check_fiche_publiee(h, pharmacie=False)


def check_fiche_publiee(h, pharmacie):
    if h.get("publie"):
        ok = h.get("nom") and h.get("localisation") and h.get("lat") is not None and h.get("lng") is not None
        if not pharmacie:
            ok = ok and (h.get("prix_consultation") or 0) > 0 and len(h.get("specialites") or []) > 0
        if not ok:
            raise ApiError("fiche incomplete : informations obligatoires manquantes")


def coverage(x, pat, total):
    """Part payée par l'assurance principale puis complémentaire (même règle que l'application patient)."""
    def one(ass, num, rest):
        t = (x.get("taux_assurances") or {}).get(ass) if ass and num else None
        pris = x.get("assurances_prises") or []
        if t is None or x.get("remboursable") is False or (pris and ass not in pris):
            return None
        return ass, round(rest * t / 100)
    a = one(pat.get("assurance"), pat.get("numero_assure"), total)
    b = one(pat.get("assurance2"), pat.get("numero_assure2"), total - (a[1] if a else 0))
    lst = [z for z in (a, b) if z]
    return (" + ".join(z[0] for z in lst) or None), sum(z[1] for z in lst)


def prepare_insert(c, tbl, row):
    row = dict(row)
    if c.is_patient():
        return prepare_insert_patient(c, tbl, row)
    if tbl == "constantes":
        r = c.db.get("rendez_vous", row.get("rendez_vous_id"))
        if not (c.staff() and r and c.same_hop(r)):
            raise ApiError("permission denied", 403)
        row["saisi_par"] = c.uid
    elif tbl == "ordonnances":
        r = c.db.get("rendez_vous", row.get("rendez_vous_id"))
        if not (c.role in ("medecin", "admin") and r and c.same_hop(r) and str(row.get("hopital_id")) == str(c.hop)):
            raise ApiError("permission denied", 403)
        row["medecin_id"] = c.uid
    elif tbl == "ordonnance_lignes":
        o = c.db.get("ordonnances", row.get("ordonnance_id"))
        if not (o and c.same_hop(o) and o.get("medecin_id") == c.uid):
            raise ApiError("permission denied", 403)
    elif tbl == "modeles_assurance":
        if not (c.role in ("medecin", "infirmier", "admin", "superadmin")):
            raise ApiError("permission denied", 403)
        row.update(cree_par=c.uid, statut="en_attente")  # impossible de s'auto-approuver
        row.pop("approuve_par", None)
        row.pop("approuve_le", None)
    elif tbl == "feuilles_assurance":
        if not (c.role in ("medecin", "infirmier") and c.staff()):
            raise ApiError("permission denied", 403)
        row.update(medecin_id=c.uid, statut="brouillon", hopital_id=c.hop)
        row.setdefault("updated_at", now())
    elif tbl == "delivrances_pharmacie":
        f = c.db.get("feuilles_assurance", row.get("feuille_id"))
        if not (c.role == "pharmacien" and f and f.get("statut") == "validee"):
            raise ApiError("permission denied", 403)
        row.update(pharmacien_id=c.uid, pharmacie_id=(c.profile or {}).get("pharmacie_id"))
    elif tbl == "journal_feuilles_assurance":
        row["acteur_id"] = c.uid
    elif tbl == "produits_pharmacie":
        if not c.pharm_staff():
            raise ApiError("permission denied", 403)
        try:
            q, p = int(row.get("quantite")), int(row.get("prix"))
        except (TypeError, ValueError):
            raise ApiError("Quantité ou prix invalide")
        if q < 0 or p < 0 or not str(row.get("nom") or "").strip():
            raise ApiError("Produit invalide")
        row.update(pharmacie_id=c.pharm, quantite=q, prix=p, nom=str(row["nom"]).strip(),
                   remboursable=bool(row.get("remboursable", True)), assurances_prises=list(row.get("assurances_prises") or []),
                   updated_at=now())
    else:
        raise ApiError("permission denied for table " + tbl, 403)
    return row


def prepare_insert_patient(c, tbl, row):
    db = c.db
    pat = db.get("patients", c.pat)
    if tbl == "rendez_vous":
        h = db.get("hopitaux", row.get("hopital_id"))
        if not (h and h.get("publie")):
            raise ApiError("Hôpital indisponible")
        mode = row.get("mode_paiement") or "Espèces"
        if mode not in (h.get("paiements_acceptes") or PAYS_DEFAUT):
            raise ApiError("Ce mode de paiement n'est pas accepté par cet hôpital")
        spec = str(row.get("specialite") or "").strip()
        if spec not in (h.get("specialites") or []):
            raise ApiError("Spécialité indisponible dans cet hôpital")
        prix = int(h.get("prix_consultation") or 0)
        ass, part = coverage(h, pat, prix)
        today = datetime.date.today().isoformat()
        n = 1 + sum(1 for r in db.all("rendez_vous") if str(r.get("hopital_id")) == str(h["id"]) and str(r.get("created_at", ""))[:10] == today)
        return {"patient_id": c.pat, "hopital_id": h["id"], "specialite": spec, "symptomes": str(row.get("symptomes") or spec)[:500],
                "urgence": (str(row.get("urgence") or "").strip() or None), "statut": "attente", "mode_paiement": mode,
                "montant": prix - part, "part_assurance": part, "assurance": ass, "numero_assure": pat.get("numero_assure") if ass else None,
                "numero_file": n, "paiement_statut": "en_attente"}
    if tbl == "commandes_pharmacie":
        ph = db.get("pharmacies", row.get("pharmacie_id"))
        if not (ph and ph.get("publie")):
            raise ApiError("Pharmacie indisponible")
        mode = row.get("mode_paiement") or "Espèces"
        if mode not in (ph.get("paiements_acceptes") or PAYS_DEFAUT):
            raise ApiError("Ce mode de paiement n'est pas accepté par cette pharmacie")
        prod = next((p for p in db.all("produits_pharmacie") if str(p.get("pharmacie_id")) == str(ph["id"]) and p.get("nom") == row.get("produit")), None)
        try:
            q = int(row.get("quantite") or 1)
        except (TypeError, ValueError):
            q = 0
        if not prod or q < 1:
            raise ApiError("Produit introuvable")
        if prod["quantite"] < q:
            raise ApiError("Stock insuffisant")
        total = q * int(prod["prix"])
        ass, part = coverage({**prod, "taux_assurances": ph.get("taux_assurances") or {}}, pat, total)
        open_codes = {str(x.get("code_retrait")) for x in db.all("commandes_pharmacie")
                      if str(x.get("pharmacie_id")) == str(ph["id"]) and x.get("statut") != "livre"}
        code = next(k for k in ("%06d" % secrets.randbelow(1_000_000) for _ in range(5000)) if k not in open_codes)
        out = {"patient_id": c.pat, "pharmacie_id": ph["id"], "produit": prod["nom"], "quantite": q, "montant": total - part,
               "part_assurance": part, "assurance": ass, "mode_paiement": mode, "code_retrait": code,
               "ligne_id": row.get("ligne_id"), "statut": "en_attente_paiement"}
        if ass:
            for k in ("numero_assure", "assure_nom", "assure_naissance", "assure_sexe", "code_prescripteur", "code_formation"):
                out[k] = str(row.get(k) or "")[:120] or None
        return out
    if tbl == "avis":
        try:
            note = int(row.get("note"))
        except (TypeError, ValueError):
            note = 0
        if not 1 <= note <= 5:
            raise ApiError("Note invalide")
        out = {"patient_id": c.pat, "note": note}
        if row.get("rendez_vous_id") is not None:
            r = db.get("rendez_vous", row.get("rendez_vous_id"))
            if not (r and c.mine(r) and r.get("statut") == "termine"):
                raise ApiError("Avis impossible")
            out["rendez_vous_id"] = r["id"]
        elif row.get("commande_id") is not None:
            r = db.get("commandes_pharmacie", row.get("commande_id"))
            if not (r and c.mine(r) and r.get("statut") == "livre"):
                raise ApiError("Avis impossible")
            out["commande_id"] = r["id"]
        else:
            raise ApiError("Avis impossible")
        for a in db.all("avis"):
            if c.mine(a) and a.get("rendez_vous_id") == out.get("rendez_vous_id") and a.get("commande_id") == out.get("commande_id"):
                raise ApiError("Avis déjà donné", 409, "23505")
        return out
    raise ApiError("permission denied for table " + tbl, 403)


def refresh_rating(db, avis):
    """Recalcule note_moyenne / nb_avis de l'établissement noté."""
    if avis.get("rendez_vous_id") is not None:
        tbl, key, src, srckey = "hopitaux", "hopital_id", "rendez_vous", "rendez_vous_id"
    else:
        tbl, key, src, srckey = "pharmacies", "pharmacie_id", "commandes_pharmacie", "commande_id"
    target = (db.get(src, avis.get(srckey)) or {}).get(key)
    notes = [a["note"] for a in db.all("avis") if a.get(srckey) is not None
             and str((db.get(src, a.get(srckey)) or {}).get(key)) == str(target)]
    if target is not None and notes:
        db.update(tbl, target, {"note_moyenne": round(sum(notes) / len(notes), 1), "nb_avis": len(notes)})


def check_update(c, tbl, row, changes):
    changes = {k: v for k, v in changes.items() if k not in ("id", "created_at")}
    if tbl == "patients":
        if not (c.is_patient() and str(row["id"]) == str(c.pat)):
            raise ApiError("permission denied", 403)
        ok = ("assurance", "numero_assure", "assure_principal", "date_naissance", "assurance2", "numero_assure2", "assure_principal2")
        changes = {k: v for k, v in changes.items() if k in ok}
    elif c.is_patient():
        raise ApiError("permission denied", 403)
    elif tbl == "hopitaux":
        if not (c.role == "admin" and str(row["id"]) == str(c.hop)):
            raise ApiError("permission denied", 403)
        merged = {**row, **changes}
        check_hopital_publish(merged)
    elif tbl == "pharmacies":
        if not (c.pharm_staff() and str(row["id"]) == str(c.pharm)):
            raise ApiError("permission denied", 403)
        check_fiche_publiee({**row, **changes}, pharmacie=True)
    elif tbl == "produits_pharmacie":
        if not (c.pharm_staff() and c.same_pharm(row)):
            raise ApiError("permission denied", 403)
        changes = {k: v for k, v in changes.items() if k in ("quantite", "prix", "remboursable", "assurances_prises", "updated_at")}
        for k in ("quantite", "prix"):
            if k in changes:
                try:
                    changes[k] = int(changes[k])
                except (TypeError, ValueError):
                    raise ApiError("Valeur invalide")
                if changes[k] < 0:
                    raise ApiError("Valeur invalide")
    elif tbl == "rendez_vous":
        if not (c.staff() and c.same_hop(row)):
            raise ApiError("permission denied", 403)
        changes = {k: v for k, v in changes.items() if k in ("statut", "medecin_id")}
    elif tbl == "modeles_assurance":
        if not c.is_admin():
            raise ApiError("permission denied", 403)
    elif tbl == "feuilles_assurance":
        if not (row.get("statut") == "brouillon" and (row.get("medecin_id") == c.uid or c.is_admin())):
            raise ApiError("permission denied", 403)
        changes = {k: v for k, v in changes.items() if k in ("statut", "validee_par", "validee_le", "donnees_medicales")}
        if changes.get("statut") not in (None, "brouillon", "validee", "annulee"):
            raise ApiError("valeur invalide", 400)
        changes["updated_at"] = now()
    else:
        raise ApiError("permission denied for table " + tbl, 403)
    return changes


def rest(db, c, req):
    op, tbl = req.get("op"), req.get("table")
    filters = req.get("filters") or []
    with db.lock:
        rows = db.all(tbl)
        cache = {}
        rows = [r for r in rows if visible(c, tbl, r, cache)]
        for col, o, val in filters:
            rows = [r for r in rows if cmp_ok(r.get(col), o, val)]
        for col, asc in reversed(req.get("order") or []):
            rows.sort(key=lambda r: (r.get(col) is None, r.get(col) if r.get(col) is not None else 0), reverse=not asc)
        if req.get("limit") is not None:
            rows = rows[: int(req["limit"])]
        if op == "insert":
            values = req.get("values")
            lst = values if isinstance(values, list) else [values]
            out = [db.insert(tbl, prepare_insert(c, tbl, v)) for v in lst]
            if tbl == "avis":
                for a in out:
                    refresh_rating(db, a)
            rows = out
        elif op == "update":
            out = []
            for r in rows:
                ch = check_update(c, tbl, r, req.get("values") or {})
                out.append(db.update(tbl, r["id"], ch))
            rows = out
        elif op == "delete":
            if tbl != "produits_pharmacie" or c.is_patient():
                raise ApiError("permission denied", 403)
            for r in rows:
                if not (c.pharm_staff() and c.same_pharm(r)):
                    raise ApiError("permission denied", 403)
            for r in rows:
                db.delete(tbl, r["id"])
            rows = []
        sel = req.get("select")
        if op == "select" or req.get("returning"):
            data = [project(db, tbl, r, sel) for r in rows]
        else:
            data = None
    single = req.get("single")
    if data is not None and single:
        if len(data) == 1:
            data = data[0]
        elif single == "maybe" and len(data) == 0:
            data = None
        else:
            raise ApiError("JSON object requested, multiple (or no) rows returned", 406)
    return data


# --------------------------------------------------------------------------- RPC
def rpc_patient(db, c, name, a):
    if name == "chercher_medicament":
        mots = norm(a.get("p_texte")).split()
        if not mots or sum(len(m) for m in mots) < 2:
            return []
        out = []
        phs = {str(p["id"]): p for p in db.all("pharmacies") if p.get("publie")}
        for prod in db.all("produits_pharmacie"):
            ph = phs.get(str(prod.get("pharmacie_id")))
            if not ph or int(prod.get("quantite") or 0) < 1:
                continue
            n = norm(prod.get("nom"))
            if all(m in n for m in mots):
                out.append({"pharmacie_id": ph["id"], "pharmacie_nom": ph["nom"], "produit": prod["nom"], "prix": prod["prix"],
                            "lat": ph.get("lat"), "lng": ph.get("lng"), "localisation": ph.get("localisation"),
                            "telephone": ph.get("telephone"), "note_moyenne": ph.get("note_moyenne"), "nb_avis": ph.get("nb_avis") or 0,
                            "remboursable": prod.get("remboursable", True), "taux_assurances": ph.get("taux_assurances") or {},
                            "assurances_prises": prod.get("assurances_prises") or [], "verifie": bool(ph.get("verifie")),
                            "paiements_acceptes": ph.get("paiements_acceptes") or PAYS_DEFAUT,
                            "heure_ouverture": ph.get("heure_ouverture"), "heure_fermeture": ph.get("heure_fermeture")})
        return out
    if name == "position_file":
        r = db.get("rendez_vous", a.get("p_rdv"))
        if not (r and c.mine(r)):
            return None
        if not (r.get("paiement_statut") == "paye" and r.get("statut") in ("attente", "constantes")):
            return None
        q = [x for x in db.all("rendez_vous") if str(x.get("hopital_id")) == str(r.get("hopital_id"))
             and x.get("paiement_statut") == "paye" and x.get("statut") in ("attente", "constantes")]
        q.sort(key=lambda x: (0 if x.get("urgence") else 1, str(x.get("paye_le") or x.get("created_at")), x["id"]))
        return 1 + [x["id"] for x in q].index(r["id"])
    if name == "soumettre_resultat":
        r = db.get("resultats_analyses", a.get("p_id"))
        if not (r and c.mine(r)):
            raise ApiError("Résultat introuvable")
        db.update("resultats_analyses", r["id"], {"soumis_le": r.get("soumis_le") or now()})
        return None
    raise ApiError("permission denied", 403)


def rpc(db, c, name, a):
    if c.is_patient():
        return rpc_patient(db, c, name, a)
    if name == "simulation_liste":
        return []
    if name == "simulation_choisir":
        raise ApiError("Le simulateur n'est pas disponible en mode local")
    if name == "delivrer_commande":
        if not (c.pharm_staff() and str(a.get("p_pharmacie")) == str(c.pharm)):
            raise ApiError("permission denied", 403)
        code = str(a.get("p_code") or "").strip()
        cmd = next((x for x in db.all("commandes_pharmacie") if c.same_pharm(x) and str(x.get("code_retrait")) == code
                    and x.get("statut") in ("paye", "en_attente_paiement")), None)
        if not cmd:
            raise ApiError("Code invalide ou deja utilise")
        if cmd["statut"] == "en_attente_paiement":  # espèces : le stock baisse maintenant
            prod = next((p for p in db.all("produits_pharmacie") if c.same_pharm(p) and p.get("nom") == cmd.get("produit")), None)
            if not prod or prod["quantite"] < cmd["quantite"]:
                raise ApiError("stock insuffisant")
            db.update("produits_pharmacie", prod["id"], {"quantite": prod["quantite"] - cmd["quantite"], "updated_at": now()})
        db.update("commandes_pharmacie", cmd["id"], {"statut": "livre", "livre_le": now(), "livre_par": c.uid})
        return None
    if name == "vente_comptoir":
        if not (c.pharm_staff() and str(a.get("p_pharmacie")) == str(c.pharm)):
            raise ApiError("permission denied", 403)
        try:
            q = int(a.get("p_quantite"))
        except (TypeError, ValueError):
            raise ApiError("Quantité invalide")
        prod = next((p for p in db.all("produits_pharmacie") if c.same_pharm(p) and p.get("nom") == a.get("p_produit")), None)
        if q < 1 or not prod:
            raise ApiError("Vente impossible")
        if prod["quantite"] < q:
            raise ApiError("stock insuffisant")
        mode = a.get("p_mode") or "Espèces"
        if mode not in PAYS_DEFAUT:
            raise ApiError("Mode de paiement invalide")
        db.update("produits_pharmacie", prod["id"], {"quantite": prod["quantite"] - q, "updated_at": now()})
        db.insert("ventes_comptoir", {"pharmacie_id": c.pharm, "produit": prod["nom"], "quantite": q,
                                      "montant": q * int(prod["prix"]), "vendeur_id": c.uid, "mode_paiement": mode})
        return None
    if not c.staff():
        raise ApiError("permission denied", 403)
    if name == "encaisser_rdv_especes":
        r = db.get("rendez_vous", a.get("p_rdv"))
        if not (r and c.same_hop(r)):
            raise ApiError("Rendez-vous introuvable")
        if not (r.get("statut") == "attente" and r.get("paiement_statut") == "en_attente" and r.get("mode_paiement") == "Espèces"):
            raise ApiError("Ce rendez-vous n'est pas à encaisser")
        db.update("rendez_vous", r["id"], {"paiement_statut": "paye", "paye_le": now()})
        return None
    if name == "medecin_disponible":
        r = db.get("rendez_vous", a.get("p_rdv"))
        if not (r and c.same_hop(r)):
            raise ApiError("Rendez-vous introuvable")
        for p in db.all("profiles"):
            if p.get("role") == "medecin" and str(p.get("hopital_id")) == str(c.hop):
                sp = p.get("specialites") or []
                if not sp or r.get("specialite") in sp:
                    return True
        return False
    if name == "publier_resultat":
        if c.role not in ("medecin", "admin"):
            raise ApiError("Réservé au médecin")
        code = (a.get("p_code") or "").strip()
        pat = next((p for p in db.all("patients") if str(p.get("code_carnet")) == code), None)
        if not pat:
            raise ApiError("Code de carnet introuvable")
        db.insert("resultats_analyses", {"patient_id": pat["id"], "hopital_id": c.hop, "medecin_id": c.uid,
                                         "titre": a.get("p_titre"), "contenu": a.get("p_contenu")})
        return None
    if name == "repondre_resultat":
        if c.role != "medecin":
            raise ApiError("Réservé au médecin")
        r = db.get("resultats_analyses", a.get("p_id"))
        if not (r and c.same_hop(r)):
            raise ApiError("Résultat introuvable")
        if not r.get("soumis_le"):
            raise ApiError("Le patient n'a pas encore soumis ce résultat")
        db.update("resultats_analyses", r["id"], {"repondu_le": now(), "reponse_note": a.get("p_note"),
                                                    "reponse_lignes": a.get("p_lignes") or [], "reponse_signature": a.get("p_signature"),
                                                    "repondu_par": c.uid})
        return None
    raise ApiError("Fonction inconnue : " + name, 404)


# --------------------------------------------------------------------------- fonctions serveur
PAYS_DEFAUT = ["Espèces", "Carte bancaire", "Moov Money", "Mixx By Yas", "T-Money", "Flooz"]


def new_hopital(nom, responsable, email):
    return {"nom": nom, "responsable": responsable, "email": email, "publie": False, "specialites": [],
            "assurances": [], "taux_assurances": {}, "paiements_acceptes": PAYS_DEFAUT,
            "jours": [0, 1, 2, 3, 4, 5, 6], "heure_ouverture": "08:00", "heure_fermeture": "17:30",
            "type_etablissement": "Clinique privée", "prix_consultation": 0, "urgences_24h": False}


def new_pharmacie(nom, responsable, email):
    return {"nom": nom, "responsable": responsable, "email": email, "publie": False, "assurances": [], "taux_assurances": {},
            "paiements_acceptes": PAYS_DEFAUT, "jours": [0, 1, 2, 3, 4, 5, 6],
            "heure_ouverture": "08:00", "heure_fermeture": "19:00", "garde": False}


def fn_inscription(db, body):
    typ = body.get("type")
    if typ not in ("hopital", "pharmacie"):
        raise ApiError("Type de compte non pris en charge")
    nom, resp = (body.get("nom") or "").strip(), (body.get("responsable") or "").strip()
    if len(nom) < 2 or len(resp) < 2:
        raise ApiError("Remplissez tous les champs")
    email = (body.get("email") or "").strip().lower()
    uid = db.create_user(email, body.get("password") or "")
    if typ == "pharmacie":
        ph = db.insert("pharmacies", new_pharmacie(nom, resp, email))
        db.insert("profiles", {"id": uid, "role": "admin", "hopital_id": None, "pharmacie_id": ph["id"], "nom": resp, "prenom": "", "specialites": []})
    else:
        h = db.insert("hopitaux", new_hopital(nom, resp, email))
        db.insert("profiles", {"id": uid, "role": "admin", "hopital_id": h["id"], "nom": resp, "prenom": "", "specialites": []})
    return {"ok": True}


def fn_patient_inscription(db, body):
    tel = digits(body.get("telephone"))
    code = str(body.get("code") or "")
    nom, prenom = str(body.get("nom") or "").strip(), str(body.get("prenom") or "").strip()
    if len(tel) < 8:
        raise ApiError("Numéro de téléphone invalide")
    if not re.fullmatch(r"\d{6}", code):
        raise ApiError("Le code secret doit avoir 6 chiffres")
    if len(nom) < 2 or len(prenom) < 2:
        raise ApiError("Nom et prénom requis")
    try:
        uid = db.create_user(patient_email(tel), code, min_len=6)
    except ApiError as e:
        if "déjà" in e.message:
            raise ApiError("Ce numéro a déjà un carnet. Connectez-vous.")
        raise
    used = {str(p.get("code_carnet")) for p in db.all("patients")}
    carnet = next(k for k in ("".join(secrets.choice("ABCDEFGHJKMNPQRSTUVWXYZ23456789") for _ in range(6)) for _ in range(5000)) if k not in used)
    try:
        age = int(body.get("age"))
        age = age if 0 <= age <= 130 else None
    except (TypeError, ValueError):
        age = None
    pat = db.insert("patients", {"nom": nom, "prenom": prenom, "age": age, "sexe": body.get("sexe") if body.get("sexe") in ("Femme", "Homme") else None,
                                 "telephone": str(body.get("telephone") or "").strip(), "date_naissance": body.get("date_naissance") or None,
                                 "code_carnet": carnet, "user_id": uid})
    db.insert("profiles", {"id": uid, "role": "patient", "patient_id": pat["id"], "hopital_id": None, "pharmacie_id": None,
                           "nom": nom, "prenom": prenom, "specialites": []})
    return {"ok": True}


def fn_recuperer(db, body, attempts):
    tel = digits(body.get("telephone"))
    t = time.time()
    lst = [x for x in attempts.get(tel, []) if t - x < 3600]
    if len(lst) >= 5:
        raise ApiError("Trop d'essais. Réessayez dans une heure.", 429)
    attempts[tel] = lst + [t]
    pat = next((p for p in db.all("patients") if tel and digits(p.get("telephone")) == tel), None)
    ok = (pat and norm(body.get("nom")) == norm(pat.get("nom")) and norm(body.get("prenom")) == norm(pat.get("prenom"))
          and pat.get("date_naissance") and pat.get("date_naissance") == body.get("date_naissance"))
    if not ok:
        raise ApiError("Informations incorrectes")
    code = str(body.get("nouveau_code") or "")
    if not re.fullmatch(r"\d{6}", code):
        raise ApiError("Le nouveau code doit avoir 6 chiffres")
    db.set_password(pat["user_id"], code, min_len=6)
    return {"ok": True}


def fn_paiement(db, c, name, body):
    """Paiement local : aucun fournisseur réel. « paiement-test » simule un paiement réussi (rien n'est débité)."""
    if not c.is_patient():
        raise ApiError("permission denied", 403)
    if name == "paiement-initier":
        return {"configured": False}
    typ = body.get("type")
    if typ == "rdv":
        r = db.get("rendez_vous", body.get("id"))
        if not (r and c.mine(r)):
            raise ApiError("Rendez-vous introuvable", 404)
        if r.get("mode_paiement") == "Espèces":
            raise ApiError("Le paiement en espèces se fait sur place")
        if r.get("paiement_statut") != "paye":
            db.update("rendez_vous", r["id"], {"paiement_statut": "paye", "paye_le": now()})
        return {"ok": True}
    if typ == "commande":
        r = db.get("commandes_pharmacie", body.get("id"))
        if not (r and c.mine(r)):
            raise ApiError("Commande introuvable", 404)
        if r.get("mode_paiement") == "Espèces":
            raise ApiError("Le paiement en espèces se fait sur place")
        if r.get("statut") == "en_attente_paiement":
            prod = next((p for p in db.all("produits_pharmacie") if str(p.get("pharmacie_id")) == str(r.get("pharmacie_id")) and p.get("nom") == r.get("produit")), None)
            q = int(r.get("quantite") or 1)
            if not prod or prod["quantite"] < q:
                raise ApiError("Stock insuffisant")
            db.update("produits_pharmacie", prod["id"], {"quantite": prod["quantite"] - q, "updated_at": now()})
            db.update("commandes_pharmacie", r["id"], {"statut": "paye", "paye_le": now()})
        return {"ok": True}
    raise ApiError("Type de paiement inconnu")


def fn_gestion(db, c, body):
    if not (c and c.role == "admin" and (c.hop or c.pharm)):
        raise ApiError("Réservé à l'administrateur de l'établissement", 403)
    act = body.get("action")
    pharm = c.pharm is not None
    mine = (lambda p: str(p.get("pharmacie_id")) == str(c.pharm)) if pharm else (lambda p: str(p.get("hopital_id")) == str(c.hop))
    roles = ("pharmacien", "admin") if pharm else STAFF_ROLES
    if act == "list_staff":
        staff = [{**p, "email": db.email_of(p["id"])} for p in db.all("profiles") if mine(p) and p.get("role") in roles]
        return {"staff": staff}
    if act == "create_staff":
        role = body.get("role")
        if role not in roles:
            raise ApiError("Fonction invalide")
        if role == "medecin" and not body.get("specialites"):
            raise ApiError("Choisissez au moins une spécialité pour le médecin")
        uid = db.create_user(body.get("email") or "", body.get("password") or "")
        db.insert("profiles", {"id": uid, "role": role, "hopital_id": None if pharm else c.hop, "pharmacie_id": c.pharm if pharm else None,
                               "nom": (body.get("nom") or "").strip(),
                               "prenom": (body.get("prenom") or "").strip(),
                               "specialites": body.get("specialites") or [] if role == "medecin" else []})
        return {"ok": True, "user_id": uid}
    if act in ("set_password", "set_email"):
        p = db.get("profiles", body.get("user_id"))
        if not (p and mine(p)):
            raise ApiError("Compte introuvable", 404)
        if act == "set_password":
            db.set_password(p["id"], body.get("password") or "")
        else:
            db.set_email(p["id"], body.get("email") or "")
        return {"ok": True}
    raise ApiError("Action inconnue")


# --------------------------------------------------------------------------- aide au test (patients / RDV simulés)
def dev_info(db):
    hs = [{"id": h["id"], "nom": h["nom"], "publie": h.get("publie"), "specialites": h.get("specialites") or [],
           "prix": h.get("prix_consultation"), "paiements": h.get("paiements_acceptes") or [],
           "taux": h.get("taux_assurances") or {}} for h in db.all("hopitaux")]
    ps = db.all("patients")
    rs = []
    for r in db.all("resultats_analyses"):
        p = db.get("patients", r.get("patient_id")) or {}
        rs.append({**r, "patient": "%s %s" % (p.get("prenom", ""), p.get("nom", ""))})
    return {"hopitaux": hs, "patients": ps, "resultats": rs}


def dev_patient(db, b):
    nom, prenom = (b.get("nom") or "").strip(), (b.get("prenom") or "").strip()
    if not nom or not prenom:
        raise ApiError("Nom et prénom requis")
    code = "".join(secrets.choice("ABCDEFGHJKMNPQRSTUVWXYZ23456789") for _ in range(6))
    return db.insert("patients", {"nom": nom, "prenom": prenom, "age": b.get("age") or None, "sexe": b.get("sexe") or None,
                                  "telephone": b.get("telephone") or "", "date_naissance": b.get("date_naissance") or None,
                                  "assurance": b.get("assurance") or None, "numero_assure": b.get("numero_assure") or None,
                                  "code_carnet": code, "user_id": str(uuid.uuid4())})


def dev_rdv(db, b):
    h, p = db.get("hopitaux", b.get("hopital_id")), db.get("patients", b.get("patient_id"))
    if not (h and p):
        raise ApiError("Hôpital ou patient introuvable")
    today = datetime.date.today().isoformat()
    n = 1 + sum(1 for r in db.all("rendez_vous") if str(r.get("hopital_id")) == str(h["id"]) and str(r.get("created_at", ""))[:10] == today)
    prix = int(h.get("prix_consultation") or 0)
    taux = (h.get("taux_assurances") or {}).get(p.get("assurance") or "", 0) if p.get("numero_assure") else 0
    mode = b.get("mode_paiement") or "Espèces"
    row = {"patient_id": p["id"], "hopital_id": h["id"], "specialite": b.get("specialite") or "Généraliste",
           "symptomes": b.get("symptomes") or "", "urgence": (b.get("urgence") or "").strip() or None,
           "statut": "attente", "mode_paiement": mode, "montant": prix - round(prix * taux / 100),
           "part_assurance": round(prix * taux / 100), "assurance": p.get("assurance"),
           "numero_assure": p.get("numero_assure"), "numero_file": n,
           "paiement_statut": "en_attente" if mode == "Espèces" else "paye"}
    if row["paiement_statut"] == "paye":
        row["paye_le"] = now()
    return db.insert("rendez_vous", row)


def dev_soumettre(db, b):
    r = db.get("resultats_analyses", b.get("id"))
    if not r:
        raise ApiError("Résultat introuvable")
    db.update("resultats_analyses", r["id"], {"soumis_le": now()})
    return {"ok": True}


# --------------------------------------------------------------------------- données de démonstration
def seed_demo(db, stub=True):
    pw = "Demo1234!"
    h = new_hopital("Clinique Démo de Lomé", "Dr Démo", "admin@demo.local")
    h.update(publie=True, localisation="Boulevard du 13 Janvier, Lomé", repere="Près du marché de Hanoukopé", lat=6.1725, lng=1.2314,
             telephone="+228 90 00 00 00", email="contact@demo.local", agrement="DEMO-001", urgences_24h=True,
             specialites=["Généraliste", "Pédiatrie", "Cardiologie"], prix_consultation=5000,
             assurances=["AMU", "NSIA"], taux_assurances={"AMU": 70, "NSIA": 80})
    h = db.insert("hopitaux", h)
    comptes = [("admin@demo.local", "admin", "Démo", "Admin", []),
               ("infirmier@demo.local", "infirmier", "Kossi", "Akouété", []),
               ("medecin@demo.local", "medecin", "Mensah", "Ama", ["Généraliste", "Pédiatrie", "Cardiologie"])]
    ids = {}
    for email, role, nom, prenom, sp in comptes:
        uid = db.create_user(email, pw)
        ids[role] = uid
        db.insert("profiles", {"id": uid, "role": role, "hopital_id": h["id"], "nom": nom, "prenom": prenom, "specialites": sp})
    if stub:
        pharm = db.create_user("pharmacien@demo.local", pw)
        db.insert("pharmacies", {"id": 1, "nom": "Pharmacie Démo"})
        db.insert("profiles", {"id": pharm, "role": "pharmacien", "hopital_id": None, "pharmacie_id": 1, "nom": "Pharma", "prenom": "Yao", "specialites": []})
    P = [dev_patient(db, d) for d in (
        {"nom": "Agbodjan", "prenom": "Afi", "age": 34, "sexe": "F", "telephone": "90111111", "date_naissance": "1992-03-14", "assurance": "AMU", "numero_assure": "AMU-0001"},
        {"nom": "Tchalla", "prenom": "Komi", "age": 7, "sexe": "M", "telephone": "90222222", "date_naissance": "2019-08-02"},
        {"nom": "Lawson", "prenom": "Edem", "age": 58, "sexe": "M", "telephone": "90333333", "date_naissance": "1968-11-21", "assurance": "NSIA", "numero_assure": "NSIA-778"},
        {"nom": "Dossou", "prenom": "Mawuli", "age": 41, "sexe": "M", "telephone": "90444444", "date_naissance": "1985-01-09"})]
    mk = lambda i, **k: dev_rdv(db, {"hopital_id": h["id"], "patient_id": P[i]["id"], **k})
    mk(0, specialite="Généraliste", symptomes="Fièvre et maux de tête depuis 2 jours", mode_paiement="Espèces")            # à encaisser
    r2 = mk(1, specialite="Pédiatrie", symptomes="Toux et nez qui coule", mode_paiement="T-Money")                           # infirmerie
    r3 = mk(2, specialite="Cardiologie", symptomes="Douleurs dans la poitrine à l'effort", mode_paiement="Flooz", urgence="Douleur thoracique")  # → médecin
    db.insert("constantes", {"rendez_vous_id": r3["id"], "temperature": 37.2, "tension": "15/9", "poids": 82, "saisi_par": ids["infirmier"]})
    db.update("rendez_vous", r3["id"], {"statut": "constantes"})
    r4 = mk(3, specialite="Généraliste", symptomes="Contrôle après paludisme", mode_paiement="Carte bancaire")
    db.insert("constantes", {"rendez_vous_id": r4["id"], "temperature": 36.8, "tension": "12/8", "poids": 74, "saisi_par": ids["infirmier"]})
    o = db.insert("ordonnances", {"rendez_vous_id": r4["id"], "patient_id": P[3]["id"], "hopital_id": h["id"], "medecin_id": ids["medecin"],
                                  "signature": "Dr Ama Mensah", "cachet": h["nom"], "consignes": "Repos et boire beaucoup d'eau."})
    db.insert("ordonnance_lignes", {"ordonnance_id": o["id"], "medicament": "Paracétamol 500 mg", "posologie": "1 comprimé 3 fois par jour pendant 3 jours"})
    db.update("rendez_vous", r4["id"], {"statut": "termine", "medecin_id": ids["medecin"]})
    res = db.insert("resultats_analyses", {"patient_id": P[0]["id"], "hopital_id": h["id"], "medecin_id": ids["medecin"],
                                           "titre": "Bilan sanguin (NFS)", "contenu": "Hémoglobine : 12,5 g/dL\nGlobules blancs : 6 800 /mm³\nConclusion : normal."})
    db.update("resultats_analyses", res["id"], {"soumis_le": now()})
    seed_demo.patients = P
    return [(c[0], pw, c[1]) for c in comptes] + ([("pharmacien@demo.local", pw, "pharmacien")] if stub else [])


def patient_account(db, pat, code):
    """Donne un vrai compte (téléphone + code secret) à un patient existant."""
    uid = db.create_user(patient_email(pat["telephone"]), code, min_len=6)
    db.update("patients", pat["id"], {"user_id": uid})
    db.insert("profiles", {"id": uid, "role": "patient", "patient_id": pat["id"], "hopital_id": None, "pharmacie_id": None,
                           "nom": pat["nom"], "prenom": pat["prenom"], "specialites": []})


def seed_all(db):
    """Démo commune aux trois logiciels (Hôpital, Pharmacie, Patient) : une seule base, des comptes pour chacun."""
    a = seed_demo(db, stub=False)
    b, notes = seed_demo_pharmacie(db)
    comptes = [{"email": e, "password": p, "role": r, "app": "hopital"} for e, p, r in a]
    comptes += [{"email": e, "password": p, "role": r, "app": "pharmacie"} for e, p, r in b]
    for pat in seed_demo.patients:
        patient_account(db, pat, "123456")
        comptes.append({"email": digits(pat["telephone"]), "password": "123456", "role": "patient", "app": "patient",
                        "label": "%s %s" % (pat["prenom"], pat["nom"])})
    notes += (" Patients de démonstration : téléphone 90111111, 90222222, 90333333 ou 90444444, code secret 123456. "
              "Mawuli (90444444) a une ordonnance à chercher en pharmacie.")
    return comptes, notes



# --------------------------------------------------------------------------- pharmacie : aide au test (le « patient » qui réserve)
def dev_pinfo(db):
    out = []
    for p in db.all("pharmacies"):
        prods = [x for x in db.all("produits_pharmacie") if str(x.get("pharmacie_id")) == str(p["id"])]
        out.append({"id": p["id"], "nom": p["nom"], "publie": p.get("publie"), "taux": p.get("taux_assurances") or {},
                    "paiements": p.get("paiements_acceptes") or [], "produits": prods})
    cmds = [x for x in db.all("commandes_pharmacie") if x.get("statut") != "livre"]
    return {"pharmacies": out, "commandes": cmds}


def dev_commande(db, b):
    ph = db.get("pharmacies", b.get("pharmacie_id"))
    prod = next((p for p in db.all("produits_pharmacie") if str(p.get("pharmacie_id")) == str(b.get("pharmacie_id"))
                 and p.get("nom") == b.get("produit")), None)
    try:
        q = int(b.get("quantite"))
    except (TypeError, ValueError):
        q = 0
    if not (ph and prod and q >= 1):
        raise ApiError("Pharmacie, produit ou quantité invalide")
    mode = b.get("mode_paiement") or "Espèces"
    total = q * int(prod["prix"])
    ass = (b.get("assurance") or "").strip()
    taux = (ph.get("taux_assurances") or {}).get(ass, 0) if ass else 0
    pris = prod.get("assurances_prises") or []
    part = 0
    if ass and taux and prod.get("remboursable") and (not pris or ass in pris):
        part = round(total * taux / 100)
    else:
        ass = ""
    open_codes = {str(x.get("code_retrait")) for x in db.all("commandes_pharmacie")
                  if str(x.get("pharmacie_id")) == str(ph["id"]) and x.get("statut") != "livre"}
    code = next(c for c in ("%06d" % secrets.randbelow(1_000_000) for _ in range(1000)) if c not in open_codes)
    row = {"pharmacie_id": ph["id"], "produit": prod["nom"], "quantite": q, "montant": total - part, "part_assurance": part,
           "assurance": ass or None, "mode_paiement": mode, "code_retrait": code,
           "numero_assure": (b.get("numero_assure") or None) if ass else None,
           "assure_nom": (b.get("assure_nom") or None) if ass else None,
           "statut": "en_attente_paiement" if mode == "Espèces" else "paye"}
    if row["statut"] == "paye":  # paiement en ligne : le stock baisse tout de suite
        if prod["quantite"] < q:
            raise ApiError("stock insuffisant")
        db.update("produits_pharmacie", prod["id"], {"quantite": prod["quantite"] - q, "updated_at": now()})
    return db.insert("commandes_pharmacie", row)


def seed_demo_pharmacie(db):
    pw = "Demo1234!"
    ph = new_pharmacie("Pharmacie Démo de Lomé", "Pharm. Démo", "pharmacie@demo.local")
    ph.update(publie=True, localisation="Avenue de la Libération, Lomé", repere="En face de la poste", lat=6.1319, lng=1.2228,
              telephone="+228 90 11 22 33", agrement="PH-DEMO-01", garde=True, assurances=["AMU", "NSIA"],
              taux_assurances={"AMU": 70, "NSIA": 80})
    ph = db.insert("pharmacies", ph)
    comptes = [("pharmacie@demo.local", "admin", "Démo", "Admin"), ("pharmacien@demo.local", "pharmacien", "Yao", "Kodjo")]
    for email, role, nom, prenom in comptes:
        uid = db.create_user(email, pw)
        db.insert("profiles", {"id": uid, "role": role, "hopital_id": None, "pharmacie_id": ph["id"], "nom": nom, "prenom": prenom, "specialites": []})
    for nom, q, prix, remb in (("Paracétamol 500mg", 120, 500, True), ("Amoxicilline 500mg", 4, 1500, True),
                               ("Ibuprofène 400mg", 60, 800, True), ("Vitamine C 1g", 30, 1200, False)):
        db.insert("produits_pharmacie", {"pharmacie_id": ph["id"], "nom": nom, "quantite": q, "prix": prix, "remboursable": remb,
                                         "assurances_prises": [], "updated_at": now()})
    c1 = dev_commande(db, {"pharmacie_id": ph["id"], "produit": "Paracétamol 500mg", "quantite": 2, "mode_paiement": "T-Money"})
    c2 = dev_commande(db, {"pharmacie_id": ph["id"], "produit": "Ibuprofène 400mg", "quantite": 1, "mode_paiement": "Espèces",
                           "assurance": "AMU", "numero_assure": "AMU-0001", "assure_nom": "Afi Agbodjan"})
    c3 = dev_commande(db, {"pharmacie_id": ph["id"], "produit": "Vitamine C 1g", "quantite": 1, "mode_paiement": "Flooz"})
    db.update("commandes_pharmacie", c3["id"], {"statut": "livre", "livre_le": now()})
    db.insert("ventes_comptoir", {"pharmacie_id": ph["id"], "produit": "Paracétamol 500mg", "quantite": 3, "montant": 1500})
    notes = ("Codes de retrait de démonstration : %s (payée en ligne), %s (à payer en espèces). "
             "Le simulateur patient permet d'en créer d'autres." % (c1["code_retrait"], c2["code_retrait"]))
    return [(c[0], pw, c[1]) for c in comptes], notes

# --------------------------------------------------------------------------- serveur HTTP
class Handler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".webmanifest": "application/manifest+json",
                      ".js": "text/javascript", ".html": "text/html; charset=utf-8"}
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    # --- sécurité : uniquement notre propre page, sur 127.0.0.1
    def _host_ok(self):
        h = self.headers.get("Host") or ""
        if h in ("127.0.0.1:%d" % self.server.port, "localhost:%d" % self.server.port):
            return True
        # mode réseau (--reseau) : accepte aussi l'adresse privée du PC (téléphone sur le même Wi-Fi)
        return bool(getattr(self.server, "lan", False)) and h.endswith(":%d" % self.server.port) and ip_privee(h.rsplit(":", 1)[0])

    def do_GET(self):
        if not self._host_ok():
            return self._json({"error": "forbidden"}, 403)
        if self.path.startswith(("/api/", "/functions/")):
            return self._json({"error": "not found"}, 404)
        super().do_GET()

    def do_HEAD(self):
        if not self._host_ok():
            return self._json({"error": "forbidden"}, 403)
        super().do_HEAD()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        db = self.server.db
        try:
            if not self._host_ok() or self.headers.get("X-OM") != "1":
                raise ApiError("forbidden", 403)
            n = int(self.headers.get("Content-Length") or 0)
            if n > 12_000_000:
                raise ApiError("Fichier trop volumineux", 413)
            raw = self.rfile.read(n) if n else b""
            path = urllib.parse.urlparse(self.path).path
            tok = (self.headers.get("Authorization") or "").replace("Bearer ", "")
            user = db.session_user(tok)
            c = Ctx(db, user) if user else None

            if path.startswith("/api/storage/"):
                return self._json(self._storage(c, path[len("/api/storage/"):], raw))
            body = json.loads(raw.decode() or "{}")
            if path == "/api/mode":
                return self._json(self.server.mode_info)
            if path == "/api/carte":
                try:
                    la, lo = float(body.get("lat")), float(body.get("lng"))
                except (TypeError, ValueError):
                    raise ApiError("Coordonnées invalides", 400)
                if not (-90 <= la <= 90 and -180 <= lo <= 180):
                    raise ApiError("Coordonnées invalides", 400)
                webbrowser.open("https://www.openstreetmap.org/?mlat=%.6f&mlon=%.6f#map=18/%.6f/%.6f" % (la, lo, la, lo))
                return self._json({"ok": True})
            if path == "/api/rev":
                return self._json({"rev": db.rev})
            if path == "/api/auth/signin":
                uid = db.check_login(body.get("email") or "", body.get("password") or "")
                if not uid:
                    raise ApiError("Invalid login credentials", 400)
                return self._json({"token": db.new_session(uid), "user": {"id": uid, "email": db.email_of(uid)}})
            if path == "/api/auth/session":
                return self._json({"user": user, "token": tok if user else None})
            if path == "/api/auth/signout":
                db.drop_session(tok)
                return self._json({"ok": True})
            if path.startswith("/api/dev/"):
                act = path.rsplit("/", 1)[1]
                f = {"info": lambda: dev_info(db), "patient": lambda: dev_patient(db, body), "rdv": lambda: dev_rdv(db, body),
                     "soumettre": lambda: dev_soumettre(db, body), "pinfo": lambda: dev_pinfo(db),
                     "commande": lambda: dev_commande(db, body)}.get(act)
                if not f:
                    raise ApiError("not found", 404)
                return self._json(f())
            if path == "/functions/v1/inscription-etablissement":
                return self._json(fn_inscription(db, body))
            if path == "/functions/v1/inscription":
                return self._json(fn_patient_inscription(db, body))
            if path == "/functions/v1/recuperer-acces":
                return self._json(fn_recuperer(db, body, self.server.attempts))
            if not c:
                raise ApiError("JWT expired or missing: reconnectez-vous", 401)
            if path == "/api/auth/update":
                if "password" in body:
                    if db.same_password(c.uid, body["password"]):
                        raise ApiError("New password should be different from the old password.")
                    db.set_password(c.uid, body["password"])
                    tok = db.new_session(c.uid)
                    return self._json({"ok": True, "token": tok})
                raise ApiError("Rien à modifier")
            if path == "/api/rest":
                return self._json({"data": rest(db, c, body)})
            if path.startswith("/api/rpc/"):
                return self._json({"data": rpc(db, c, path.rsplit("/", 1)[1], body)})
            if path in ("/functions/v1/paiement-initier", "/functions/v1/paiement-test"):
                return self._json(fn_paiement(db, c, path.rsplit("/", 1)[1], body))
            if path == "/functions/v1/gestion-etablissement":
                return self._json(fn_gestion(db, c, body))
            raise ApiError("not found", 404)
        except ApiError as e:
            self._json({"error": e.message, "message": e.message, "code": e.code}, e.status)
        except Exception as e:  # erreur inattendue : ne jamais planter le serveur
            self._json({"error": "Erreur interne : %s" % e, "message": str(e)}, 500)

    def _storage(self, c, rest_path, raw):
        if not c:
            raise ApiError("JWT expired or missing", 401)
        bucket, _, name = urllib.parse.unquote(rest_path).partition("/")
        parts = [p for p in name.split("/") if p]
        if bucket != "insurance-forms" or not parts or any(p in (".", "..") for p in parts) or parts[0] != c.uid:
            raise ApiError("permission denied", 403)
        d = os.path.join(self.server.data_dir, "storage", bucket, *parts[:-1])
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, re.sub(r"[^\w.\-]", "_", parts[-1])), "wb") as f:
            f.write(raw)
        return {"path": "/".join(parts)}


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):  # connexions coupées par le navigateur : sans importance
        pass


DEMO_PORTS = (8766, 8768, 8770)


def autre_demo_active(own_port):
    """Vrai si un autre logiciel de la famille tourne déjà en mode démo (on partage alors sa base au lieu de l'effacer)."""
    for p in DEMO_PORTS:
        if p == own_port:
            continue
        try:
            socket.create_connection(("127.0.0.1", p), timeout=0.3).close()
            return True
        except OSError:
            continue
    return False


def ip_privee(h):
    m = re.match(r"^(\d{1,3})\.(\d{1,3})\.\d{1,3}\.\d{1,3}$", h or "")
    if not m:
        return False
    a, b = int(m.group(1)), int(m.group(2))
    return a == 10 or (a == 192 and b == 168) or (a == 172 and 16 <= b <= 31)


def adresses_reseau():
    """Adresses IPv4 privées de ce PC (à saisir dans l'application Android)."""
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        for r in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(r[4][0])
    except Exception:
        pass
    return sorted(i for i in ips if ip_privee(i))


def start(app_dir, data_dir, demo=False, port=8765, kind="hopital", lan=False):
    """Démarre le serveur ; renvoie (serveur, port)."""
    import functools
    os.makedirs(data_dir, exist_ok=True)
    dbfile = os.path.join(data_dir, "ordonnance_mobile.sqlite")
    if demo and not autre_demo_active(port):  # la démo repart de zéro, sauf si un autre logiciel l'utilise déjà
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(dbfile + suffix):
                os.remove(dbfile + suffix)
    fresh = not os.path.exists(dbfile)
    db = DB(dbfile)
    comptes, notes = [], ""
    if demo and fresh:
        comptes, notes = seed_all(db)
        with db.lock:
            db.con.execute("insert or replace into kv values('demo_comptes',?)", (json.dumps([comptes, notes], ensure_ascii=False),))
            db.con.commit()
    elif demo:
        r = db.con.execute("select v from kv where k='demo_comptes'").fetchone()
        if r:
            comptes, notes = json.loads(r[0])
    handler = functools.partial(Handler, directory=app_dir)
    srv = None
    for p in (port, 0):
        try:
            srv = Server(("0.0.0.0" if lan else "127.0.0.1", p), handler)
            break
        except OSError:
            continue
    srv.port = srv.server_address[1]
    srv.db, srv.data_dir, srv.attempts, srv.lan = db, data_dir, {}, lan
    srv.mode_info = {"demo": demo, "comptes": comptes, "notes": notes, "kind": kind}
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.port
