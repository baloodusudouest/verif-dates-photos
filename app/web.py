"""Page, connexion et adresses de l'outil (Flask)."""
import shutil
import datetime as dt
import hashlib
import os
import threading
from time import sleep as time_sleep
from pathlib import Path
import hmac
import secrets

from flask import Flask, Response, abort, redirect, render_template_string, request, send_file, session, url_for

from . import __version__
from . import reglages as R
from . import etat as E
from . import exif as X
from . import immich as I
from . import analyse as A
from . import actions as C


app = Flask(__name__)


def _cle_session() -> bytes:
    """Clé qui signe le cookie de session, gardée dans DATA_DIR pour survivre aux redémarrages."""
    f = R.DATA_DIR / "cle-session"
    try:
        if f.exists() and f.stat().st_size >= 32:
            return f.read_bytes()
        cle = secrets.token_bytes(48)
        f.write_bytes(cle)
        f.chmod(0o600)
        return cle
    except OSError:
        return secrets.token_bytes(48)


app.secret_key = _cle_session()


app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict", SESSION_COOKIE_NAME="verif_dates",
                  PERMANENT_SESSION_LIFETIME=dt.timedelta(days=30))


ECHECS = {"n": 0}     # mots de passe faux à la suite : chaque essai attend un peu plus
PAGE_CONNEXION = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Éphéméride</title>
<link rel="icon" href="/icone.svg" type="image/svg+xml"><link rel="apple-touch-icon" href="/icone-180.png"><link rel="manifest" href="/manifest.webmanifest"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-title" content="Éphéméride">
<style>
:root{color-scheme:dark;--bg:#151311;--card:#1f1c19;--ink:#f2ece2;--mute:#a89e91;--line:#312c27;--bad:#ef7b72;--acc:#7f9cc4}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--ink);font:15px/1.4 system-ui,sans-serif}
form{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:22px;width:min(22rem,calc(100vw - 2rem));display:grid;gap:10px}
h1{font-size:18px;margin:0}p{margin:0;color:var(--mute);font-size:13px}.ko{color:var(--bad);font-weight:600}
input{padding:9px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink);font-size:16px}
button{border:0;border-radius:8px;padding:10px 14px;background:var(--acc);color:#fff;font-weight:600;cursor:pointer;font-size:15px}
</style></head><body>
<form method="post" action="{{ url_for('connexion') }}">
  <h1>Éphéméride</h1>
  <p>Cet outil modifie les fichiers de la Pellicule.</p>
  <label for="mdp">Mot de passe (celui de carnet)</label>
  <input id="mdp" name="mot_de_passe" type="password" autocomplete="current-password" autofocus required>
  {% if erreur %}<p class="ko" role="alert">{{ erreur }}</p>{% endif %}
  <button>Se connecter</button>
</form></body></html>"""


COMMUN = "maison_session"   # cookie commun aux applis de la maison, posé par l'Anneau


def connexion_commune() -> bool:
    """Le cookie posé par l'Anneau, signé avec le mot de passe commun : s'il est bon, pas besoin de se reconnecter.
    Il vaut trente jours, ou jusqu'à ce que le mot de passe change."""
    expire, _, signature = (request.cookies.get(COMMUN) or "").partition(".")
    if not (R.PASSWORD and expire.isdigit() and int(expire) > dt.datetime.now().timestamp()):
        return False
    cle = hashlib.sha256(b"maison-session|" + R.PASSWORD.encode()).digest()
    return hmac.compare_digest(signature, hmac.new(cle, expire.encode(), hashlib.sha256).hexdigest())


@app.before_request
def garde():
    """Rien ne répond sans session, sauf la page du mot de passe et le contrôle de santé."""
    if request.endpoint in ("connexion", "sante", "icone"):
        return None
    if not R.PASSWORD:
        return "Variable VERIF_PASSWORD absente : à définir dans la stack (même valeur que CARNET_PASSWORD).", 503
    if session.get("ok") or connexion_commune():
        return None
    if request.method == "GET" and request.endpoint == "index":
        return redirect(url_for("connexion"))
    return {"ok": False, "msg": "Connexion nécessaire."}, 401


ICONES = {"icone.svg": "image/svg+xml", "icone-180.png": "image/png", "icone-512.png": "image/png",
          "manifest.webmanifest": "application/manifest+json"}


@app.get("/<nom>")
def icone(nom):
    """Icône de l'appli et manifeste (2.0.2) : quatre fichiers fixes, servis sans session."""
    if nom not in ICONES:
        abort(404)
    return send_file(Path(__file__).parent / nom, mimetype=ICONES[nom], max_age=86400)


@app.get("/health")
def sante():
    return {"ok": True, "version": __version__}


@app.route("/connexion", methods=["GET", "POST"])
def connexion():
    if not R.PASSWORD:
        return "Variable VERIF_PASSWORD absente : à définir dans la stack (même valeur que CARNET_PASSWORD).", 503
    if request.method == "GET":
        return redirect(url_for("index")) if session.get("ok") or connexion_commune() else render_template_string(PAGE_CONNEXION, erreur="")
    time_sleep(min(5.0, 0.5 * ECHECS["n"]))
    if hmac.compare_digest(request.form.get("mot_de_passe", "").encode(), R.PASSWORD.encode()):
        ECHECS["n"] = 0
        session.clear()
        session["ok"] = True
        session.permanent = True
        return redirect(url_for("index"))
    ECHECS["n"] += 1
    return render_template_string(PAGE_CONNEXION, erreur="Mot de passe incorrect."), 401


@app.post("/deconnexion")
def deconnexion():
    session.clear()
    reponse = redirect(url_for("connexion"))
    reponse.delete_cookie(COMMUN)      # « Se déconnecter » ferme aussi la connexion commune
    return reponse


PAGE = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Éphéméride</title>
<link rel="icon" href="/icone.svg" type="image/svg+xml"><link rel="apple-touch-icon" href="/icone-180.png"><link rel="manifest" href="/manifest.webmanifest"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-title" content="Éphéméride">
<style>
/* 2.0.0 : habillage commun aux applis (celui de carnet) : fonds neutres chauds, sombre par défaut,
   clair quand l'appareil le demande. Couleur de l'outil : bleu ardoise. Les noms des variables et
   des classes d'avant sont gardés : le script de la page et les gestes n'ont pas changé. */
:root{color-scheme:dark;--bg:#151311;--card:#1f1c19;--ink:#f2ece2;--mute:#a89e91;--line:#312c27;--bord:#4b443c;
  --warn:#e39a4b;--bad:#ef7b72;--ok:#7fc493;--acc:#7f9cc4;--sur-acc:#10161f;--ombre:rgb(0 0 0 / .5)}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 Inter,system-ui,-apple-system,'Segoe UI',sans-serif;-webkit-font-smoothing:antialiased}
header{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);padding:14px 20px;display:flex;flex-wrap:wrap;gap:10px;align-items:center}
h1{font-size:1.9rem;font-weight:300;letter-spacing:-.03em;line-height:1.15;margin:0 12px 0 0;flex-basis:100%}main{padding:16px 20px;max-width:1400px}
button,.btn{border:1px solid var(--acc);border-radius:8px;padding:8px 14px;background:var(--acc);color:var(--sur-acc);font-weight:600;font:inherit;font-weight:600;cursor:pointer;text-decoration:none}button:disabled{opacity:.45;cursor:not-allowed}
button.sec,.btn.sec{background:transparent;color:var(--ink);border:1px solid var(--bord);font-weight:500}button.sec:hover,.btn.sec:hover{background:var(--card)}
.stats{color:var(--mute);font-size:13px}.msg{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 14px;margin-bottom:14px}
.folder{margin:22px 0 8px;display:flex;flex-direction:column;gap:8px}.folder h2{font-size:15px;margin:0}
.folder-titre{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}
.folder{scroll-margin-top:150px}.plier{padding:2px 9px;font-size:13px}.grid.replie{display:none!important}
.folder.replie .folder-actions{display:none}.resume{font-size:13px;font-weight:600;color:var(--ok)}
.folder-actions{display:flex;gap:6px;align-items:center;flex-wrap:wrap;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:6px 8px}
.folder-actions button{padding:5px 10px;font-size:13px}.folder-actions .jour{display:inline-flex;gap:4px}
.folder-actions input[type=date]{padding:4px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink)}
.dossier-info{font-size:12px}.folder-actions.renommage input[type=text]{flex:1;min-width:200px;padding:5px 8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden;display:flex;flex-direction:column}
.card img{width:100%;height:170px;object-fit:cover;background:var(--line)}.body{padding:10px;font-size:13px;display:flex;flex-direction:column;gap:4px}
.name{font-weight:600;word-break:break-all}.tag{display:inline-block;border-radius:5px;padding:1px 6px;font-size:12px;font-weight:600;color:#fff}
.tag{color:var(--bg)}.t-ecart{background:var(--warn)}.t-sans{background:var(--bad)}.t-abime{background:var(--mute)}.t-trancher{background:var(--acc)}.mute{color:var(--mute)}
.t-ok{background:var(--ok)}.card.done{opacity:.9}.actions{display:flex;gap:8px;flex-wrap:wrap}
.coh{font-size:12px;font-weight:600}.coh-ok{color:var(--ok)}.coh-ko{color:var(--warn)}
.choix{display:flex;gap:6px;flex-wrap:wrap}.choix button{padding:4px 8px;font-size:12px}
input[type=datetime-local]{width:100%;padding:5px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink)}
label.sel{display:flex;gap:6px;align-items:center;font-weight:600;margin-top:4px}
.res-ok{color:var(--ok);font-weight:600}.res-ko{color:var(--bad);font-weight:600}
.prog{display:flex;flex-direction:column;gap:8px}.prog[hidden]{display:none}.prog-head,.prog-foot{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap}
.prog-foot span:first-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:70%}
.bar{height:12px;border-radius:6px;background:var(--line);overflow:hidden;position:relative}
.fill{height:100%;width:0;background:var(--acc);border-radius:6px;transition:width .4s ease}
.bar.indet .fill{width:30%;position:absolute;animation:indet 1.2s ease-in-out infinite}
@keyframes indet{0%{left:-30%}100%{left:100%}}
.scanbtns{display:flex;gap:8px}.realign{border-color:var(--warn)}.realign button{margin-left:8px}
.steps{list-style:none;margin:4px 0 0;padding:0;display:flex;flex-direction:column;gap:4px;font-size:13px}
.steps:empty{display:none}.steps li{display:flex;justify-content:space-between;gap:10px;padding:4px 8px;border-radius:6px}
.steps li.current{background:color-mix(in srgb,var(--acc) 12%,transparent);font-weight:600}
.steps li.done{color:var(--ok)}.steps li.todo,.steps li.skipped{color:var(--mute)}
.prog-total{font-weight:700;text-align:right}.prog-total:empty{display:none}
select{padding:7px;border-radius:8px;border:1px solid var(--line);background:var(--bg);color:var(--ink)}
/* navigation commune : rail à gauche sur PC, pilule d'icônes en bas sur téléphone */
.rail{position:fixed;z-index:20;left:50%;transform:translateX(-50%);bottom:calc(.6rem + env(safe-area-inset-bottom));display:flex;gap:.2rem;padding:.35rem;
  border-radius:9999px;background:color-mix(in srgb,var(--bg) 82%,transparent);border:1px solid var(--line);box-shadow:0 10px 30px var(--ombre);backdrop-filter:blur(14px)}
.rail .marque{display:none}.rail a{display:flex;align-items:center;gap:.7rem;height:3rem;padding:0 1rem;border-radius:9999px;color:var(--mute);font-weight:600;text-decoration:none;white-space:nowrap}
.rail a svg{width:1.45rem;height:1.45rem;flex:none}.rail a span.nom{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
.rail a[aria-current=page]{background:var(--ink);color:var(--bg)}.rail .pastille{min-width:1.3rem;height:1.2rem;padding:0 .35rem;border-radius:9999px;background:var(--acc);color:var(--sur-acc);font-size:.7rem;font-weight:700;line-height:1.2rem;text-align:center}
.contenu{padding-bottom:calc(5.5rem + env(safe-area-inset-bottom))}
@media (min-width:900px){.rail{top:0;bottom:0;left:0;transform:none;width:13rem;flex-direction:column;gap:.25rem;padding:1.25rem .75rem;border-radius:0;border:0;border-right:1px solid var(--line);background:var(--bg);box-shadow:none;backdrop-filter:none}
  .rail .marque{display:block;font-size:1.25rem;font-weight:300;letter-spacing:-.02em;padding:0 .85rem 1.1rem;color:var(--ink)}
  .rail a{height:2.75rem;padding:0 .85rem}.rail a span.nom{position:static;width:auto;height:auto;clip:auto;flex:1}
  .rail a[aria-current=page]{background:var(--card);color:var(--ink)}.contenu{margin-left:13rem;padding-bottom:2rem}}
/* tuiles : une par motif, à la place du filtre déroulant */
.tuiles{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.6rem;flex-basis:100%}
@media (min-width:700px){.tuiles{grid-template-columns:repeat(4,minmax(0,1fr))}}@media (min-width:1200px){.tuiles{grid-template-columns:repeat(7,minmax(0,1fr))}}
.tuile{display:grid;gap:.1rem;text-align:left;padding:.7rem .85rem;border-radius:12px;background:var(--card);border:1px solid var(--line);color:var(--ink);font-weight:500}
.tuile:hover,.tuile[aria-pressed=true]{border-color:var(--acc)}.tuile[aria-pressed=true]{background:color-mix(in srgb,var(--acc) 14%,var(--card))}
.tuile .chiffre{font-size:1.6rem;font-weight:300;letter-spacing:-.03em;line-height:1.1}.tuile .nom-tuile{font-size:.85rem;color:var(--mute)}.tuile.vide{background:transparent}.tuile.vide .chiffre{color:var(--mute)}
#filtre{display:none}.vue{padding:16px 20px;max-width:60rem}.vue h1{flex-basis:auto;margin-bottom:.8rem}
.bloc{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:12px}.bloc h2{font-size:1.05rem;margin:0 0 .4rem}
.bloc dl{display:grid;grid-template-columns:max-content 1fr;gap:.25rem 1rem;margin:0}.bloc dt{color:var(--mute)}.bloc dd{margin:0}
.voyant{display:inline-block;width:.6rem;height:.6rem;border-radius:9999px;background:var(--bord);margin-right:.4rem}.voyant.allume{background:var(--ok)}
[hidden]{display:none!important}
/* l'en-tête (outils et tuiles) ne reste collé en haut que sur grand écran : sur téléphone il masquerait les photos */
.folder{scroll-margin-top:270px}
@media (max-width:899px){header{position:static}.folder{scroll-margin-top:12px}}
</style></head><body>
<nav class="rail" aria-label="Sections">
  <span class="marque">Éphéméride</span>
  <a href="#a-traiter" data-vue="a-traiter" aria-label="À traiter"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 13.5 6.2 5.6A1.6 1.6 0 0 1 7.8 4.5h8.4a1.6 1.6 0 0 1 1.6 1.1L20 13.5"/><path d="M4 13.5V18a1.5 1.5 0 0 0 1.5 1.5h13A1.5 1.5 0 0 0 20 18v-4.5h-4.6l-1.2 2.2h-4.4l-1.2-2.2z"/></svg><span class="nom">À traiter</span>{% if pending %}<span class="pastille">{{ pending|length if pending|length < 1000 else '999+' }}</span>{% endif %}</a>
  <a href="#corrigees" data-vue="corrigees" aria-label="Corrigées"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/><path d="m8.2 12.3 2.6 2.6 5-5.4"/></svg><span class="nom">Corrigées</span></a>
  <a href="#connexion" data-vue="connexion" aria-label="Connexion"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="2.6"/><circle cx="5" cy="6" r="1.8"/><circle cx="19" cy="6" r="1.8"/><circle cx="5" cy="18" r="1.8"/><circle cx="19" cy="18" r="1.8"/><path d="m6.4 7.2 3.6 3M17.6 7.2l-3.6 3M6.4 16.8l3.6-3M17.6 16.8l-3.6-3"/></svg><span class="nom">Connexion</span></a>
  <a href="#reglages" data-vue="reglages" aria-label="Réglages"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 7.5h6M15 7.5h4M5 16.5h3M12 16.5h7"/><circle cx="13" cy="7.5" r="2"/><circle cx="10" cy="16.5" r="2"/></svg><span class="nom">Réglages</span></a>
</nav>
<div class="contenu">
<div id="vue-travail">
<header>
  <h1 id="titre-vue">À traiter</h1>
  <form id="scanform" method="post" action="{{ url_for('scan') }}" class="scanbtns">
  {% if state.running %}
    <button disabled>{{ 'Correction en cours…' if state.kind == 'correction' else 'Analyse en cours…' }}</button>
  {% elif has_cache %}
    <button name="mode" value="nouveaux" title="Analyse seulement les fichiers ajoutés ou modifiés depuis la dernière analyse">Scanner les nouveaux</button>
    <button class="sec" name="mode" value="tout" onclick="return confirm('Relire tous les fichiers de la Pellicule ? Cela peut prendre du temps.')" title="Relit tous les fichiers">Tout rescanner</button>
  {% else %}
    <button name="mode" value="tout">Lancer l'analyse</button>
  {% endif %}
  </form>
  {% if pending or corrected %}
  <select id="filtre" onchange="filtrer()">
    <option value="tous">À traiter — tout ({{ pending|length }})</option>
    <option value="écart">Écarts de date ({{ counts['écart'] }})</option>
    <option value="sans date">Sans date EXIF ({{ counts['sans date'] }})</option>
    <option value="à trancher">À trancher, nom ≠ dossier ({{ counts['à trancher'] }})</option>
    <option value="incoherent">Date proposée incohérente avec les voisines</option>
    <option value="abîmé">Fichiers abîmés ({{ counts['abîmé'] }})</option>
    <option value="extension">Extension incorrecte</option>
    <option value="corrigees">Déjà corrigées ({{ corrected|length }})</option>
  </select>
  <div class="tuiles" id="tuiles" role="group" aria-label="Motifs à traiter">
    <button type="button" class="tuile" data-filtre="tous" aria-pressed="true"><span class="chiffre">{{ pending|length }}</span><span class="nom-tuile">Tout</span></button>
    <button type="button" class="tuile{{ '' if counts['écart'] else ' vide' }}" data-filtre="écart" aria-pressed="false"><span class="chiffre">{{ counts['écart'] }}</span><span class="nom-tuile">Écarts de date</span></button>
    <button type="button" class="tuile{{ '' if counts['sans date'] else ' vide' }}" data-filtre="sans date" aria-pressed="false"><span class="chiffre">{{ counts['sans date'] }}</span><span class="nom-tuile">Sans date</span></button>
    <button type="button" class="tuile{{ '' if counts['à trancher'] else ' vide' }}" data-filtre="à trancher" aria-pressed="false"><span class="chiffre">{{ counts['à trancher'] }}</span><span class="nom-tuile">À trancher (nom ≠ dossier)</span></button>
    <button type="button" class="tuile vide" data-filtre="incoherent" aria-pressed="false"><span class="chiffre" id="n-incoherent">0</span><span class="nom-tuile">Hors période des voisines</span></button>
    <button type="button" class="tuile vide" data-filtre="extension" aria-pressed="false"><span class="chiffre" id="n-extension">0</span><span class="nom-tuile">Extension incorrecte</span></button>
    <button type="button" class="tuile{{ '' if counts['abîmé'] else ' vide' }}" data-filtre="abîmé" aria-pressed="false"><span class="chiffre">{{ counts['abîmé'] }}</span><span class="nom-tuile">Fichiers abîmés</span></button>
  </div>
  {% endif %}
  {% if pending %}
  <span class="actions">
  <button class="sec" type="button" onclick="cocher(true)">Tout cocher (visible)</button>
  <button class="sec" type="button" onclick="cocher(false)">Tout décocher</button>
  <button form="f" onclick="return confirmer()" {% if state.running %}disabled{% endif %}>Corriger la sélection</button>
  </span>
  {% endif %}
  <span class="stats">
  {% if state.scanned_at %}Analyse du {{ state.scanned_at }} · {{ state.stats.fichiers }} fichiers ·
  {{ state.stats.ok }} OK · {{ state.stats.ecart }} écarts · {{ state.stats.sans_date }} sans date · {{ state.stats.a_trancher or 0 }} à trancher ·{% if state.stats.abime %} {{ state.stats.abime }} abîmé(s) ·{% endif %}
  {{ state.stats.sans_ref }} hors dossier daté · tolérance {{ tol }} j{% endif %}{% if corrected %} · {{ corrected|length }} corrigée(s){% endif %}{% if state.stats and state.stats.valide %} · {{ state.stats.valide }} validée(s) telles quelles{% endif %}</span>
</header>
<main>
<div id="prog" class="msg prog" {% if not state.running %}hidden{% endif %}>
  <div class="prog-head"><strong id="prog-phase">{{ state.phase }}</strong><span id="prog-count" class="mute"></span></div>
  <div class="bar"><div id="prog-fill" class="fill"></div></div>
  <div class="prog-foot"><span id="prog-detail" class="mute"></span><span id="prog-eta" class="mute"></span></div>
  <ol id="prog-steps" class="steps"></ol>
  <div id="prog-total" class="prog-total"></div>
</div>
{% if lecture_seule %}<div class="msg realign" role="status"><strong>Mode lecture seule.</strong> L'outil analyse et propose des dates, mais ne modifie, ne déplace et ne supprime aucun fichier (VERIF_LECTURE_SEULE).</div>{% endif %}
{% if state.error %}<div class="msg res-ko">Erreur : {{ state.error }}</div>{% endif %}
{% if state.immich %}<div class="msg mute">{{ state.immich }}</div>{% endif %}
{% for m in messages %}<div class="msg">{{ m }}</div>{% endfor %}
{% if a_realigner %}<div class="msg realign"><strong>{{ a_realigner }} vidéo(s) modifiée(s) à tort par une version précédente.</strong>
  Elles avaient déjà une date d'origine (CreationDate) que l'outil ne voyait pas : seuls les champs QuickTime secondaires
  et la date du fichier ont été changés (Immich affiche toujours la date d'origine).
  <form method="post" action="{{ url_for('realigner') }}" style="display:inline"><button {% if state.running %}disabled{% endif %}
  onclick="return confirm('Remettre les dates QuickTime et la date du fichier de ces vidéos sur leur date d\u2019origine (CreationDate) ?')">Réaligner ces vidéos</button></form></div>{% endif %}
{% if not pending and state.scanned_at and not state.running %}<div class="msg res-ok" id="rien">Aucune photo à traiter.</div>{% endif %}
<form id="f" method="post" action="{{ url_for('apply') }}">
{% for folder, group in groups %}
  {% set modifiables = group|rejectattr('status', 'equalto', 'abîmé')|list %}
  <div class="folder" data-folder data-nom="{{ folder }}">
    <div class="folder-titre"><button class="sec plier" type="button" onclick="plier(this)" aria-expanded="true" title="Replier ou déplier ce dossier">▾</button><h2>{{ folder }}</h2><span class="mute">dossier : {{ group[0].ref }} · {{ group|length }} photo(s)</span><span class="resume"></span></div>
    {% if modifiables %}
    <div class="folder-actions">
      <span class="mute">Tout le dossier :</span>
      {% if modifiables|selectattr('choice_name')|list %}<button class="sec" type="button" onclick="appliquerDossier(this,'name')">Date du nom ({{ modifiables|selectattr('choice_name')|list|length }})</button>{% endif %}
      {% if modifiables|selectattr('choice_voisins')|list %}<button class="sec" type="button" onclick="appliquerDossier(this,'voisins')">Voisines ({{ modifiables|selectattr('choice_voisins')|list|length }})</button>{% endif %}
      {% if modifiables|selectattr('choice_folder')|list %}<button class="sec" type="button" onclick="appliquerDossier(this,'folder')">Date du dossier ({{ modifiables|selectattr('choice_folder')|list|length }})</button>{% endif %}
      {% if modifiables|selectattr('proposed')|list %}<button class="sec" type="button" onclick="appliquerDossier(this,'proposed')">Dates proposées ({{ modifiables|selectattr('proposed')|list|length }})</button>{% endif %}
      <span class="jour"><input type="date" class="dossier-jour" aria-label="Même jour pour tout le dossier"><button class="sec" type="button" onclick="appliquerDossier(this,'jour')">Même jour pour toutes</button></span>
      <button class="sec" type="button" onclick="cocherDossier(this)">Cocher le dossier</button>
      <button type="button" onclick="corrigerDossier(this)">Corriger ce dossier</button>
      <button class="sec" type="button" onclick="validerDossier(this)" title="Le dossier est mal daté mais les photos ont la bonne date : rien n'est modifié">Les dates des photos sont bonnes</button>
      <span class="dossier-info mute"></span>
    </div>
    <div class="folder-actions renommage">
      <span class="mute">{% if state.folders and state.folders.get(folder) %}Dossier mal daté ? {{ state.folders[folder].label }} — renommer en :{% else %}Renommer le dossier :{% endif %}</span>
      <input type="text" class="dossier-nom" value="{{ state.folders[folder].nouveau if state.folders and state.folders.get(folder) else folder }}" size="40" aria-label="Nouveau nom du dossier">
      <button class="sec" type="button" onclick="renommerDossier(this)">Renommer le dossier</button>
    </div>
    {% endif %}
  </div>
  <div class="grid">
  {% for it in group %}
    <div class="card" data-status="{{ it.status }}" data-c-name="{{ it.choice_name }}" data-c-voisins="{{ it.choice_voisins }}"
         data-c-folder="{{ it.choice_folder }}" data-c-proposed="{{ it.proposed }}" data-heure="{{ it.date[11:19] if it.date else '12:00:00' }}" data-ext="{{ 1 if it.vrai_ext else 0 }}" data-convertir="{{ 1 if it.convertir else 0 }}"{% if it.voisins %} data-vmin="{{ it.voisins.min }}" data-vmax="{{ it.voisins.max }}"{% endif %}>
      <img loading="lazy" src="{{ url_for('thumb', item_id=it.id) }}" alt="">
      <div class="body">
        <div class="name">{{ it.name }}</div>
        <div><span class="tag {{ 't-sans' if it.status == 'sans date' else ('t-trancher' if it.status == 'à trancher' else ('t-abime' if it.status == 'abîmé' else 't-ecart')) }}">{{ it.status }}</span>
          <span class="mute">{{ it.kind }}{% if it.gap %} · {{ it.gap }} j d'écart{% endif %}</span></div>
        <div>Fichier : {{ it.date or '—' }} {% if it.source %}<span class="mute">({{ it.source }})</span>{% endif %}</div>
        {% if it.name_date %}<div>Nom du fichier : {{ it.name_date }}</div>{% endif %}
        {% if it.immich_date %}<div class="mute">Immich : {{ it.immich_date }}</div>{% endif %}
        <div class="mute">Voisines : {{ it.voisins.label if it.voisins else "pas assez d'autres photos datées dans le dossier" }}</div>
        {% if it.status == 'abîmé' %}<div class="res-ko">Fichier illisible ({{ it.erreur }}) : rien ne sera modifié. À remplacer depuis une sauvegarde.</div>{% endif %}
        {% if it.vrai_ext %}<div class="res-ko" style="font-weight:500">⚠ Extension incorrecte : le contenu est un {{ it.vrai_ext|upper }}. Le fichier sera renommé en .{{ it.vrai_ext }} avant d'écrire la date (Immich le verra comme un nouveau fichier). Si ce nom est déjà pris : nom alternatif, ou mise de côté si c'est un doublon identique.</div>{% endif %}
        {% if it.alerte and it.status != 'abîmé' %}<div class="mute">exiftool : {{ it.alerte }}</div>{% endif %}
        {% if it.convertir %}<div class="res-ko" style="font-weight:500">⚠ Format où la date ne peut pas être écrite : la vidéo sera convertie en .mp4 (sans perte si possible), puis l'original {{ 'déplacé' if originals_dir else 'supprimé' }}. Immich la verra comme un nouveau fichier.</div>{% endif %}
        {% if it.deja %}<div class="res-ko">Déjà corrigée le {{ it.deja }}, écart toujours présent</div>{% endif %}
        {% if it.result and not it.result.ok %}<div class="res-ko">✗ échec — {{ it.result.msg }}</div>{% endif %}
        {% if it.status != 'abîmé' %}
        {% if it.status == 'à trancher' or not it.proposed %}
        <div class="mute">Choisis la date à écrire :</div>
        <div class="choix">
          {% if it.choice_folder %}<button class="sec" type="button" onclick="choisir(this,'{{ it.choice_folder }}')">Dossier ({{ it.ref }})</button>{% endif %}
          {% if it.choice_name %}<button class="sec" type="button" onclick="choisir(this,'{{ it.choice_name }}')">Nom ({{ it.name_date[:10] }})</button>{% endif %}
          {% if it.choice_voisins %}<button class="sec" type="button" onclick="choisir(this,'{{ it.choice_voisins }}')">Voisines ({{ it.choice_voisins[8:10] }}/{{ it.choice_voisins[5:7] }}/{{ it.choice_voisins[:4] }})</button>{% endif %}
        </div>
        {% else %}
        <div class="mute">Date proposée ({{ it.proposed_src }}) :</div>
        {% endif %}
        <input type="datetime-local" step="1" name="date_{{ it.id }}" value="{{ it.proposed }}" oninput="coherence(this)">
        <div class="coh"></div>
        <label class="sel"><input type="checkbox" name="sel" value="{{ it.id }}"> Corriger</label>
        {% endif %}
      </div>
    </div>
  {% endfor %}
  </div>
{% endfor %}
</form>
<section id="corrigees" hidden>
{% if not corrected %}<div class="msg mute">Aucune photo corrigée pour l'instant.</div>{% endif %}
{% for folder, group in cgroups %}
  <div class="folder"><h2>{{ folder }}</h2><span class="mute">{{ group|length }} corrigée(s)</span></div>
  <div class="grid">
  {% for r in group %}
    <div class="card done">
      <img loading="lazy" src="{{ url_for('thumb', item_id=r.id) }}" alt="">
      <div class="body">
        <div class="name">{{ r.name }}</div>
        <div><span class="tag t-ok">corrigée</span> <span class="mute">{{ r.kind }} · le {{ r.quand }}</span></div>
        <div class="mute">Avant : {{ r.avant }}</div>
        <div>Après : <strong>{{ r.apres }}</strong></div>
        {% if r.note %}<div class="mute">{{ r.note.split(', relu')[0] }}</div>{% endif %}
      </div>
    </div>
  {% endfor %}
  </div>
{% endfor %}
</section>
</main>
</div>
<section class="vue" id="vue-connexion" hidden>
  <h1>Connexion</h1>
  <div class="bloc"><h2><span class="voyant{{ ' allume' if dossier_present else '' }}"></span>La Pellicule</h2>
    <dl><dt>Dossier analysé</dt><dd>{{ dossier }}{{ '' if dossier_present else ' (introuvable)' }}</dd>
      <dt>Dernière analyse</dt><dd>{{ state.scanned_at or 'jamais' }}</dd>
      {% if state.scanned_at %}<dt>Fichiers</dt><dd>{{ state.stats.fichiers }}, dont {{ state.stats.ok }} sans rien à signaler</dd>{% endif %}
      <dt>Écriture</dt><dd>{{ 'lecture seule : aucun fichier n’est modifié' if lecture_seule else 'les dates sont écrites dans les fichiers, sur validation' }}</dd></dl></div>
  <div class="bloc"><h2><span class="voyant{{ ' allume' if immich_configure else '' }}"></span>Immich</h2>
    {% if immich_configure %}<p class="mute">L'outil lit dans Immich la date connue et les miniatures, et lui demande de relire les métadonnées après une correction. Il n'y écrit rien d'autre.</p>
    {% if state.immich %}<p>{{ state.immich }}</p>{% endif %}
    {% else %}<p class="mute">Non configuré (IMMICH_URL, IMMICH_API_KEY) : l'outil fonctionne sans, avec ses propres miniatures.</p>{% endif %}</div>
</section>
<section class="vue" id="vue-reglages" hidden>
  <h1>Réglages</h1>
  <div class="bloc"><h2>Règles</h2>
    <dl><dt>Tolérance</dt><dd>{{ tol }} jour(s) entre la date de la photo et celle du dossier (TOLERANCE_DAYS)</dd>
      <dt>Vidéos converties</dt><dd>{{ 'l’original est gardé à part (ORIGINALS_DIR)' if originals_dir else 'l’original est supprimé après vérification du MP4 (régler ORIGINALS_DIR pour le garder)' }}</dd>
      <dt>Lecture seule</dt><dd>{{ 'oui (VERIF_LECTURE_SEULE)' if lecture_seule else 'non' }}</dd></dl></div>
  <div class="bloc"><h2>Journal</h2>
    <p class="mute">Chaque correction écrite, avec l'ancienne et la nouvelle date.</p>
    <p><a class="btn sec" href="{{ url_for('journal') }}">Télécharger corrections.csv</a></p></div>
  <div class="mute" style="display:flex;justify-content:space-between;align-items:center;gap:1rem"><span>version {{ version }}</span>
    <form method="post" action="{{ url_for('deconnexion') }}"><button class="sec">Se déconnecter</button></form></div>
</section>
</div>
<script>
function visibles(){return [...document.querySelectorAll('.card')].filter(c=>c.style.display!=='none')}
function cocher(v){visibles().forEach(c=>{const b=c.querySelector('input[type=checkbox]');if(b)b.checked=v})}
function grilleDe(btn){return btn.closest('[data-folder]').nextElementSibling}
function cocherDossier(btn){grilleDe(btn).querySelectorAll('.card').forEach(c=>{
  if(c.style.display==='none')return;const b=c.querySelector('input[type=checkbox]');if(b)b.checked=true})}
function appliquerDossier(btn,type){
  const entete=btn.closest('[data-folder]'),info=entete.querySelector('.dossier-info');let jour='';
  if(type==='jour'){jour=entete.querySelector('.dossier-jour').value;if(!jour){alert('Choisis d\u2019abord le jour.');return}}
  let faits=0,sautes=0;
  grilleDe(btn).querySelectorAll('.card').forEach(c=>{
    if(c.style.display==='none')return;
    const inp=c.querySelector('input[type=datetime-local]'),b=c.querySelector('input[name=sel]');if(!inp||!b)return;
    const k={name:'cName',voisins:'cVoisins',folder:'cFolder',proposed:'cProposed'}[type];
    const v=type==='jour'?jour+'T'+(c.dataset.heure||'12:00:00'):c.dataset[k];
    if(!v){sautes++;return}
    inp.value=v;coherence(inp);b.checked=true;faits++});
  info.textContent=faits+' photo(s) réglée(s) et cochée(s)'+(sautes?', '+sautes+' sans date de ce type (inchangées)':'')+' — vérifie puis « Corriger ce dossier ».';
  if(faits){replier(entete,'✓ '+faits+' photo(s) réglée(s) et cochée(s)'+(sautes?' · '+sautes+' inchangée(s)':''));suivant(entete)}}
function replier(entete,texte){entete.classList.add('replie');entete.nextElementSibling.classList.add('replie');
  const b=entete.querySelector('.plier');b.textContent='▸';b.setAttribute('aria-expanded','false');
  entete.querySelector('.resume').textContent=texte||''}
function deplier(entete){entete.classList.remove('replie');entete.nextElementSibling.classList.remove('replie');
  const b=entete.querySelector('.plier');b.textContent='▾';b.setAttribute('aria-expanded','true');entete.querySelector('.resume').textContent=''}
function plier(btn){const e=btn.closest('[data-folder]');e.classList.contains('replie')?deplier(e):replier(e,'replié')}
function suivant(entete){const tous=[...document.querySelectorAll('#f [data-folder]')].filter(h=>h.style.display!=='none');
  const prochain=tous.slice(tous.indexOf(entete)+1).find(h=>!h.classList.contains('replie'));
  (prochain||entete).scrollIntoView({behavior:'smooth',block:'start'})}
async function poster(url,corps){const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(corps)});
  let j={};try{j=await r.json()}catch(e){}return [r.ok,j]}
async function validerDossier(btn){const nom=btn.closest('[data-folder]').dataset.nom;
  if(!confirm('Les photos de « '+nom+' » gardent leur date actuelle et ne seront plus signalées (sauf les photos sans date). Aucun fichier n\u2019est modifié. Continuer ?'))return;
  const [ok,j]=await poster('{{ url_for('valider') }}',{folder:nom});
  if(!ok){alert('Échec : '+(j.msg||'erreur'));return}location.replace('{{ url_for('index') }}')}
async function renommerDossier(btn){const h=btn.closest('[data-folder]'),nom=h.dataset.nom,neuf=h.querySelector('.dossier-nom').value.trim();
  if(!neuf||neuf===nom){alert('Indique le nouveau nom du dossier.');return}
  if(!confirm('Renommer « '+nom+' » en « '+neuf+' » sur le NAS ?\\n\\nImmich verra ces photos comme de nouveaux fichiers. Une analyse des fichiers déplacés est relancée ensuite.'))return;
  const [ok,j]=await poster('{{ url_for('renommer') }}',{folder:nom,nouveau:neuf});
  if(!ok){alert('Échec : '+(j.msg||'erreur'));return}location.replace('{{ url_for('index') }}')}
function corrigerDossier(btn){
  const grille=grilleDe(btn),autres=[...document.querySelectorAll('input[name=sel]')].filter(b=>!grille.contains(b));
  if(!grille.querySelector('input[name=sel]:checked')){alert('Aucune photo cochée dans ce dossier.');return}
  autres.forEach(b=>b.disabled=true);
  if(confirmer())document.getElementById('f').submit();else autres.forEach(b=>b.disabled=false)}
function filtrer(){const f=document.getElementById('filtre').value,corr=f==='corrigees';
  document.getElementById('corrigees').hidden=!corr;document.getElementById('f').hidden=corr;
  const rien=document.getElementById('rien');if(rien)rien.hidden=corr;
  document.querySelectorAll('.actions').forEach(a=>a.style.display=corr?'none':'');
  document.querySelectorAll('#f .card').forEach(c=>{
    let show=f==='tous'||(f==='incoherent'?c.dataset.coh==='0':f==='extension'?c.dataset.ext==='1':c.dataset.status===f);c.style.display=show?'':'none'});
  document.querySelectorAll('#f [data-folder]').forEach(h=>{const g=h.nextElementSibling;
  const any=[...g.querySelectorAll('.card')].some(c=>c.style.display!=='none');h.style.display=g.style.display=any?'':'none'});
  try{localStorage.setItem('verif-filtre',f)}catch(e){}}
const TOL={{ tol }},JOUR=86400000;
function fr(iso){return iso.slice(8,10)+'/'+iso.slice(5,7)+'/'+iso.slice(0,4)}
function coherence(inp){const c=inp.closest('.card'),out=c.querySelector('.coh');if(!out)return;
  if(!c.dataset.vmin||!inp.value){out.textContent='';out.className='coh';delete c.dataset.coh;return}
  const d=Date.parse(inp.value.slice(0,10)+'T00:00:00Z'),a=Date.parse(c.dataset.vmin+'T00:00:00Z'),b=Date.parse(c.dataset.vmax+'T00:00:00Z');
  const ok=d>=a-TOL*JOUR&&d<=b+TOL*JOUR;c.dataset.coh=ok?'1':'0';out.className='coh '+(ok?'coh-ok':'coh-ko');
  out.textContent=ok?'✓ cohérente avec les autres photos du dossier'
    :'⚠ hors de la période des autres photos ('+(c.dataset.vmin===c.dataset.vmax?fr(c.dataset.vmin):fr(c.dataset.vmin)+' – '+fr(c.dataset.vmax))+')'}
document.querySelectorAll('#f input[type=datetime-local]').forEach(coherence);
function choisir(btn,v){const c=btn.closest('.body'),inp=c.querySelector('input[type=datetime-local]');inp.value=v;coherence(inp);
  const b=c.querySelector('input[type=checkbox]');if(b)b.checked=true}
try{const f0=localStorage.getItem('verif-filtre'),sel=document.getElementById('filtre');
  if(f0&&sel&&f0!=='corrigees'&&[...sel.options].some(o=>o.value===f0)){sel.value=f0;filtrer()}}catch(e){}
function fmt(s){s=Math.max(0,Math.round(s));if(s<60)return s+' s';const m=Math.round(s/60);
  return m<60?m+' min':Math.floor(m/60)+' h '+String(m%60).padStart(2,'0')}
function duree(s){return s==null?'':'≈ '+fmt(s)+' restantes'}
function etapes(st){const ol=document.getElementById('prog-steps');ol.innerHTML='';
  (st.steps||[]).forEach(x=>{const li=document.createElement('li');li.className=x.status;
    let droite='';
    if(x.status==='done')droite='✓ terminé en '+fmt(x.duration||0);
    else if(x.status==='current')droite='en cours'+(x.remaining!=null?' · ≈ '+fmt(x.remaining):' · estimation en cours…');
    else if(x.status==='skipped')droite='sans objet';
    else droite=x.estimate!=null?'≈ '+fmt(x.estimate)+' (d’après la dernière analyse)':'durée inconnue (1re analyse)';
    const a=document.createElement('span');a.textContent=(x.status==='done'?'✓ ':x.status==='current'?'▶ ':'○ ')+x.label;
    const b=document.createElement('span');b.textContent=droite;li.append(a,b);ol.append(li)});
  document.getElementById('prog-total').textContent=(st.steps&&st.steps.length)
    ?(st.total_remaining!=null?'Temps restant estimé au total : ≈ '+fmt(st.total_remaining):'Temps total : estimation disponible dès que les étapes en cours auront avancé'):''}
let wasRunning={{ 'true' if state.running else 'false' }};
async function suivre(){
  let st;try{st=await (await fetch('{{ url_for('progress') }}',{cache:'no-store'})).json()}catch(e){return setTimeout(suivre,3000)}
  const box=document.getElementById('prog');
  if(st.running){
    wasRunning=true;box.hidden=false;
    document.getElementById('prog-phase').textContent=st.phase||'…';
    const bar=box.querySelector('.bar'),fill=document.getElementById('prog-fill');
    if(st.total>0){bar.classList.remove('indet');const pc=Math.min(99,Math.floor(st.done*100/st.total));fill.style.width=pc+'%';
      document.getElementById('prog-count').textContent=st.done.toLocaleString('fr-FR')+' / '+(st.total_estime?'≈ ':'')+st.total.toLocaleString('fr-FR')+' · '+pc+' %';}
    else{bar.classList.add('indet');fill.style.width='';
      document.getElementById('prog-count').textContent=st.done?st.done.toLocaleString('fr-FR')+' trouvés':'';}
    etapes(st);
    document.getElementById('prog-detail').textContent=st.progress||'';
    document.getElementById('prog-eta').textContent=duree(st.remaining);
    setTimeout(suivre,1500);
  } else if(wasRunning){location.replace('{{ url_for('index') }}')}
}
if(wasRunning)suivre();
// 2.0.0 : les tuiles pilotent le filtre d'avant ; le rail montre une vue à la fois
function compterTuiles(){const cartes=[...document.querySelectorAll('#f .card')];
  const maj=(id,n)=>{const e=document.getElementById(id);if(!e)return;e.textContent=n;e.closest('.tuile').classList.toggle('vide',!n)};
  maj('n-incoherent',cartes.filter(c=>c.dataset.coh==='0').length);maj('n-extension',cartes.filter(c=>c.dataset.ext==='1').length)}
function tuileActive(f){document.querySelectorAll('#tuiles .tuile').forEach(t=>t.setAttribute('aria-pressed',t.dataset.filtre===f?'true':'false'))}
let dernierFiltre='tous';
document.querySelectorAll('#tuiles .tuile').forEach(t=>t.addEventListener('click',()=>{
  const sel=document.getElementById('filtre');if(!sel)return;dernierFiltre=t.dataset.filtre;sel.value=dernierFiltre;filtrer();tuileActive(dernierFiltre)}));
function vue(){let v=(location.hash||'#a-traiter').slice(1);if(!['a-traiter','corrigees','connexion','reglages'].includes(v))v='a-traiter';
  document.querySelectorAll('.rail a').forEach(a=>{if(a.dataset.vue===v)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current')});
  const travail=v==='a-traiter'||v==='corrigees';
  document.getElementById('vue-travail').hidden=!travail;document.getElementById('vue-connexion').hidden=v!=='connexion';document.getElementById('vue-reglages').hidden=v!=='reglages';
  if(!travail)return;
  document.getElementById('titre-vue').textContent=v==='corrigees'?'Corrigées':'À traiter';
  const sel=document.getElementById('filtre'),tuiles=document.getElementById('tuiles'),scan=document.getElementById('scanform');
  if(tuiles)tuiles.hidden=v==='corrigees';if(scan)scan.hidden=v==='corrigees';
  if(sel){sel.value=v==='corrigees'?'corrigees':dernierFiltre;filtrer();if(v!=='corrigees')tuileActive(dernierFiltre)}}
window.addEventListener('hashchange',vue);
try{const f1=document.getElementById('filtre');if(f1&&f1.value!=='corrigees')dernierFiltre=f1.value}catch(e){}
compterTuiles();document.querySelectorAll('#f input[type=datetime-local]').forEach(i=>i.addEventListener('input',compterTuiles));vue();
function confirmer(){
  const vides=[...document.querySelectorAll('input[name=sel]:checked:not(:disabled)')].filter(b=>!b.closest('.body').querySelector('input[type=datetime-local]').value).length;
  if(vides){alert(vides+' photo(s) à trancher cochée(s) sans date choisie.');return false}const n=document.querySelectorAll('input[name=sel]:checked:not(:disabled)').length;
  if(!n){alert('Aucun fichier coché.');return false}
  const ko=[...document.querySelectorAll('input[name=sel]:checked:not(:disabled)')].filter(b=>b.closest('.card').dataset.coh==='0').length;
  if(ko&&!confirm(ko+' photo(s) cochée(s) ont une date hors de la période des autres photos du dossier. Continuer quand même ?'))return false;
  const conv=[...document.querySelectorAll('input[name=sel]:checked:not(:disabled)')].filter(b=>b.closest('.card').dataset.convertir==='1').length;
  return confirm(n+' fichier(s) vont être modifiés directement sur le NAS (sans copie _original).'
    +(conv?'\\n\\n'+conv+' vidéo(s) AVI/MTS/MKV seront converties en MP4 et leur original {{ 'déplacé' if originals_dir else 'SUPPRIMÉ' }}.':'')+'\\n\\nContinuer ?')}
</script>
</body></html>"""


@app.get("/")
def index():
    with E.LOCK:
        items = list(E.STATE["items"])
        state = dict(E.STATE)
        corrected_all = list(E.CORRECTED.values())
    pending = [it for it in items if not (it.get("result") or {}).get("ok")]
    pending_paths = {it["path"] for it in pending}
    corrected = sorted((r for r in corrected_all if r["path"] not in pending_paths),
                       key=lambda r: (r["folder"], r["name"]))
    groups, cgroups = {}, {}
    for it in pending:
        groups.setdefault(it["folder"], []).append(it)
    for r in corrected:
        cgroups.setdefault(r["folder"], []).append(r)
    counts = {k: sum(1 for it in pending if it["status"] == k) for k in ("écart", "sans date", "à trancher", "abîmé")}
    messages = request.args.getlist("m") + list(state.get("messages") or [])
    return render_template_string(PAGE, state=state, pending=pending, groups=list(groups.items()),
                                  corrected=corrected, cgroups=list(cgroups.items()), counts=counts,
                                  tol=R.TOLERANCE_DAYS, messages=messages, version=__version__,
                                  has_cache=R.CACHE_FILE.exists(), originals_dir=bool(R.ORIGINALS_DIR),
                                  lecture_seule=R.LECTURE_SEULE, dossier=R.SCAN_DIR.name, dossier_present=R.SCAN_DIR.is_dir(),
                                  immich_configure=I.immich_enabled(),
                                  a_realigner=0 if state["running"] else len(C.videos_a_realigner()))


@app.post("/scan")
def scan():
    with E.LOCK:
        if E.STATE["running"]:
            return redirect(url_for("index"))
        E.STATE.update(running=True, kind="analyse", error=None, messages=[], phase="Démarrage…",
                     progress="", done=0, total=0, started=None, steps=[], total_estime=False)
    mode = "nouveaux" if request.form.get("mode") == "nouveaux" and R.CACHE_FILE.exists() else "tout"
    if mode == "tout":
        for f in R.THUMBS.glob("*.jpg"):
            f.unlink(missing_ok=True)
    threading.Thread(target=A.scan_worker, args=(mode,), daemon=True).start()
    return redirect(url_for("index"))


APPLY_THREAD = None


@app.post("/apply")
def apply():
    global APPLY_THREAD
    if R.LECTURE_SEULE:
        return redirect(url_for("index", m="Mode lecture seule : aucune photo n'a été corrigée."))
    jobs = []
    for item_id in request.form.getlist("sel"):
        item = E.ITEMS.get(item_id)
        if not item or (item.get("result") or {}).get("ok"):
            continue
        raw = request.form.get(f"date_{item_id}", "")
        nd = None
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
            try:
                nd = dt.datetime.strptime(raw, fmt)
                break
            except ValueError:
                pass
        jobs.append((item, nd))
    with E.LOCK:
        if E.STATE["running"] or not jobs:
            return redirect(url_for("index"))
        E.STATE.update(running=True, kind="correction", error=None, messages=[], progress="", done=0,
                     total=len(jobs), steps=[], total_estime=False)
    APPLY_THREAD = threading.Thread(target=C.apply_worker, args=(jobs,), daemon=True)
    APPLY_THREAD.start()
    return redirect(url_for("index"))


@app.get("/progress")
def progress():
    with E.LOCK:
        st = {k: E.STATE.get(k) for k in ("running", "kind", "phase", "progress", "done", "total", "started",
                                        "error", "total_estime")}
        steps = [dict(x) for x in E.STATE.get("steps") or []]
    remaining = None
    if st["running"] and st["started"] and st["total"] and 0 < st["done"] < st["total"]:
        elapsed = E.now_ts() - st["started"]
        if elapsed > 2:   # trop tôt, l'estimation n'aurait pas de sens
            remaining = int(elapsed / st["done"] * (st["total"] - st["done"]))
    st["remaining"] = remaining
    # temps restant total : étape en cours + étapes à venir estimées
    total_rest, inconnu = remaining or 0, remaining is None and st["total"] != st["done"]
    for x in steps:
        x.pop("started", None)
        if x["status"] == "current":
            x["remaining"] = remaining
            if remaining is None:
                x["remaining"] = x.get("estimate")
                total_rest = x.get("estimate") or 0
                inconnu = x.get("estimate") is None
        elif x["status"] == "todo":
            if x.get("estimate") is None:
                inconnu = True
            else:
                total_rest += x["estimate"]
    st["steps"] = steps
    st["total_remaining"] = None if (inconnu or not steps) else int(total_rest)
    return st


@app.post("/realigner")
def realigner():
    if R.LECTURE_SEULE:
        return redirect(url_for("index", m="Mode lecture seule : aucune vidéo n'a été modifiée."))
    paths = C.videos_a_realigner()
    with E.LOCK:
        if E.STATE["running"] or not paths:
            return redirect(url_for("index"))
        E.STATE.update(running=True, kind="correction", error=None, messages=[], progress="", done=0,
                     total=len(paths), steps=[], total_estime=False)
    threading.Thread(target=C.realign_worker, args=(paths,), daemon=True).start()
    return redirect(url_for("index"))


@app.post("/valider")
def valider():
    """Le dossier est mal daté mais les dates des photos sont bonnes : rien n'est modifié,
    les photos ne sont plus signalées (tant que le fichier ne change pas)."""
    folder = (request.get_json(silent=True) or {}).get("folder", "")
    n = 0
    with E.LOCK:
        if E.STATE["running"]:
            return {"ok": False, "msg": "une tâche est en cours"}, 409
        for it in E.STATE["items"]:
            if it["folder"] != folder or it["status"] in ("abîmé", "sans date") or (it.get("result") or {}).get("ok"):
                continue
            sig = X.file_sig(Path(it["path"]))
            if not sig:
                continue
            E.VALIDATED[it["path"]] = sig
            date = it["date"] or "—"
            E.CORRECTED[it["path"]] = {
                "id": it["id"], "path": it["path"], "name": it["name"], "folder": it["folder"],
                "kind": it["kind"], "immich_id": it.get("immich_id"), "avant": date, "source": it.get("source", ""),
                "apres": date, "note": "dates validées telles quelles (fichier non modifié)",
                "quand": dt.datetime.now().strftime("%d/%m/%Y %H:%M")}
            it["result"] = {"ok": True, "msg": "validée sans modification"}
            n += 1
    E.save_validated()
    E.save_corrected()
    E.save_results()
    return {"ok": True, "n": n}


@app.post("/renommer")
def renommer():
    """Renomme (déplace) un dossier de la Pellicule, puis relance une analyse des nouveaux fichiers."""
    if R.LECTURE_SEULE:
        return {"ok": False, "msg": "mode lecture seule : le dossier n'a pas été renommé"}, 409
    data = request.get_json(silent=True) or {}
    folder, nouveau = data.get("folder", ""), (data.get("nouveau") or "").strip().strip("/")
    src = (R.SCAN_DIR / folder).resolve()
    dst = (R.SCAN_DIR / nouveau).resolve()
    racine = R.SCAN_DIR.resolve()
    if not nouveau or ".." in Path(nouveau).parts or racine not in dst.parents or racine not in src.parents:
        return {"ok": False, "msg": "chemin invalide"}, 400
    if not src.is_dir():
        return {"ok": False, "msg": "dossier introuvable"}, 404
    if dst.exists():
        return {"ok": False, "msg": f"« {nouveau} » existe déjà"}, 409
    if src in dst.parents:
        return {"ok": False, "msg": "impossible de déplacer un dossier dans lui-même"}, 400
    with E.LOCK:
        if E.STATE["running"]:
            return {"ok": False, "msg": "une tâche est en cours"}, 409
        E.STATE.update(running=True, kind="analyse", error=None, messages=[], phase="Renommage du dossier…",
                     progress="", done=0, total=0, started=None, steps=[], total_estime=False)
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            src.rename(dst)
        except OSError:
            shutil.move(str(src), str(dst))
    except Exception as exc:
        E.set_progress(running=False, kind="", phase="", error=f"renommage impossible : {exc}")
        return {"ok": False, "msg": str(exc)}, 500
    ancien, neuf = str(R.SCAN_DIR / folder), str(R.SCAN_DIR / nouveau)
    with E.LOCK:   # les photos corrigées ou validées suivent le dossier
        for store in (E.CORRECTED, E.VALIDATED):
            for p in [p for p in store if p == ancien or p.startswith(ancien + os.sep)]:
                rec = store.pop(p)
                q = neuf + p[len(ancien):]
                if isinstance(rec, dict):
                    rec = dict(rec, path=q, folder=str(Path(q).parent.relative_to(R.SCAN_DIR)), name=Path(q).name)
                store[q] = rec
    E.save_corrected()
    E.save_validated()
    E.noter_journal([dt.datetime.now().isoformat(timespec="seconds"), ancien, "", "dossier", neuf, "ok", "dossier renommé"])

    def suite():
        A.scan_worker("nouveaux")
        with E.LOCK:
            E.STATE["messages"] = [f"Dossier renommé : « {folder} » → « {nouveau} ». Immich verra ces photos "
                                 f"comme de nouveaux fichiers au prochain scan de la bibliothèque."] + E.STATE["messages"]
    threading.Thread(target=suite, daemon=True).start()
    return {"ok": True}


@app.get("/thumb/<item_id>")
def thumb(item_id):
    item = E.ITEMS.get(item_id) or next((r for r in list(E.CORRECTED.values()) if r["id"] == item_id), None)
    if item is None:
        abort(404)
    cache = R.THUMBS / f"{item_id}.jpg"
    if not cache.exists():
        I.make_thumb(item, cache)
    if cache.exists() and cache.stat().st_size > 0:
        return send_file(cache, mimetype="image/jpeg", max_age=0)
    return Response(I.PLACEHOLDER, mimetype="image/svg+xml")


@app.get("/journal.csv")
def journal():
    if not R.LOG_FILE.exists():
        abort(404)
    return send_file(R.LOG_FILE, mimetype="text/csv", as_attachment=True, download_name="corrections.csv")


if __name__ == "__main__":
    E.load_results()
    E.load_corrected()
    E.load_validated()
    if not R.PASSWORD:
        raise SystemExit("Variable VERIF_PASSWORD absente : à définir dans la stack (même valeur que CARNET_PASSWORD).")
    if not R.SCAN_DIR.is_dir():
        print(f"ATTENTION : dossier introuvable {R.SCAN_DIR}")
    app.run(host="0.0.0.0", port=R.PORT, threaded=True)
