"""Actions : analyse, corrections, réalignement des vidéos, validation et renommage d'un dossier.

3.0.0 : ce qui était dans les adresses Flask est ici, à l'identique, pour que l'API FastAPI
n'ait plus qu'à appeler ces fonctions."""
import csv
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

from . import reglages as R
from . import etat as E
from . import exif as X
from . import immich as I
from . import analyse as A


def apply_worker(jobs):
    """Corrige les fichiers un par un en mettant la barre de progression à jour."""
    ok_count, ko_count, refresh = 0, 0, []
    try:
        E.set_progress(phase="Correction des dates", total=len(jobs), done=0,
                     started=dt.datetime.now().timestamp())
        for n, (item, nd) in enumerate(jobs, 1):
            E.set_progress(progress=item["name"])
            if nd is None:
                item["result"] = {"ok": False, "msg": "date saisie invalide"}
                ko_count += 1
            else:
                if Path(item["path"]).suffix.lstrip(".").lower() in R.NEEDS_MP4:
                    E.set_progress(progress=f"Conversion en MP4 : {item['name']}")
                    ok, msg, new_path = X.convert_and_date(item, nd)
                    E.log_correction(item, nd, ok, msg)
                    if ok:
                        item.update(path=str(new_path), name=new_path.name, immich_id=None)
                elif item.get("status") == "abîmé":
                    ok, msg = False, "fichier abîmé : non modifié"
                else:
                    try:
                        ok, msg, notes = X.write_date_robust(item, nd)
                    except OSError as exc:
                        ok, msg, notes = False, f"renommage impossible : {exc}", []
                    if notes:
                        msg = " ; ".join(notes) + (" — " + msg if msg else "")
                    E.log_correction(item, nd, ok, msg)
                item["result"] = {"ok": ok, "msg": msg}
                R.THUMBS.joinpath(f"{item['id']}.jpg").unlink(missing_ok=True)
                if ok:
                    with E.LOCK:
                        E.CORRECTED[item["path"]] = {
                            "id": item["id"], "path": item["path"], "name": item["name"],
                            "folder": item["folder"], "kind": item["kind"], "immich_id": item.get("immich_id"),
                            "avant": item["date"] or "sans date", "source": item.get("source", ""),
                            "apres": nd.strftime("%d/%m/%Y %H:%M:%S"),
                            "note": msg.split(" — relu")[0] if (msg.startswith("converti") or msg.startswith("renommé")
                                                               or "parasite" in msg or msg.startswith("doublon")) else "",
                            "quand": dt.datetime.now().strftime("%d/%m/%Y %H:%M")}
                    E.save_corrected()
                    ok_count += 1
                    if item.get("immich_id"):
                        refresh.append(item["immich_id"])
                else:
                    ko_count += 1
            E.set_progress(done=n)
        E.save_results()
        msgs = [f"{ok_count} fichier(s) corrigé(s) et vérifié(s), {ko_count} échec(s). Journal : {R.LOG_FILE}"]
        r = I.immich_refresh(refresh)
        if r:
            msgs.append(r)
        E.set_progress(messages=msgs)
    except Exception as exc:
        E.set_progress(error=str(exc))
    finally:
        E.set_progress(running=False, kind="", phase="")


RE_RELU_CREATION = re.compile(r"^relu .* \(CreationDate\)$")


def videos_a_realigner():
    """Vidéos modifiées à tort par les versions < 1.10 : passées pour « sans date » à cause du mode rapide,
    elles ont reçu une date dans les champs QuickTime alors que leur CreationDate (prioritaire) était là.
    Repérées dans corrections.csv (échec « relu … (CreationDate) »), hors celles déjà réalignées."""
    if not R.LOG_FILE.exists():
        return []
    try:
        faits = set(json.loads(R.REALIGNED_FILE.read_text(encoding="utf-8")))
    except Exception:
        faits = set()
    liste = []
    with R.LOG_FILE.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter=";"):
            p = row.get("fichier", "")
            if row.get("statut") == "échec" and RE_RELU_CREATION.match(row.get("detail", "")) \
                    and p not in faits and p not in liste and Path(p).exists():
                liste.append(p)
    return liste


def realign_worker(paths):
    E.exiger_ecriture()
    faits = []
    try:
        faits = json.loads(R.REALIGNED_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    ok_n, ko = 0, []
    try:
        E.set_progress(phase="Réalignement des vidéos sur leur CreationDate", total=len(paths), done=0,
                     started=E.now_ts(), steps=[])
        for n, p in enumerate(paths, 1):
            E.set_progress(progress=Path(p).name)
            tags = ["CreateDate", "ModifyDate", "TrackCreateDate", "TrackModifyDate", "MediaCreateDate", "MediaModifyDate"]
            args = ["exiftool", "-overwrite_original", "-q", "-m", "-api", "QuickTimeUTC",
                    *[f"-QuickTime:{t}<CreationDate" for t in tags], "-FileModifyDate<CreationDate", p]
            r = subprocess.run(args, capture_output=True, text=True)
            d = X.dates_avec_fuseau(p, ["CreationDate", "CreateDate"])
            a, b = d.get("CreationDate"), d.get("CreateDate")
            if r.returncode == 0 and a and b and abs((a - b).total_seconds()) <= 60:
                ok_n += 1
                faits.append(p)
                E.noter_journal([dt.datetime.now().isoformat(timespec="seconds"), p, "", "CreationDate",
                         a.strftime("%d/%m/%Y %H:%M:%S"), "ok", "réaligné sur CreationDate"])
            else:
                ko.append(Path(p).name)
            E.set_progress(done=n)
        E.ecrire_json(R.REALIGNED_FILE, json.dumps(faits, ensure_ascii=False))
        msg = f"{ok_n} vidéo(s) réalignée(s) sur leur date d'origine (CreationDate)."
        if ko:
            msg += f" {len(ko)} non réalignée(s) : " + ", ".join(ko[:10]) + ("…" if len(ko) > 10 else "")
        E.set_progress(messages=[msg + " Lance « Scanner les nouveaux » pour mettre la liste à jour."])
    except Exception as exc:
        E.set_progress(error=str(exc))
    finally:
        E.set_progress(running=False, kind="", phase="")


# ================================================================ 3.0.0 : lancées par l'API

class Refus(Exception):
    """Demande refusée sans rien toucher (message lisible, code HTTP)."""
    def __init__(self, msg, statut=409):
        super().__init__(msg)
        self.statut = statut


APPLY_THREAD = None


def lancer_analyse(mode):
    """Analyse en tâche de fond : « nouveaux » (fichiers ajoutés ou modifiés) ou « tout »."""
    with E.LOCK:
        if E.STATE["running"]:
            raise Refus("une tâche est en cours")
        E.STATE.update(running=True, kind="analyse", error=None, messages=[], phase="Démarrage…",
                       progress="", done=0, total=0, started=None, steps=[], total_estime=False)
    mode = "nouveaux" if mode == "nouveaux" and R.CACHE_FILE.exists() else "tout"
    if mode == "tout":
        for f in R.THUMBS.glob("*.jpg"):
            f.unlink(missing_ok=True)
    threading.Thread(target=A.scan_worker, args=(mode,), daemon=True).start()
    return mode


def lire_date(raw):
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return dt.datetime.strptime(raw or "", fmt)
        except ValueError:
            pass
    return None


def lancer_corrections(choix):
    """choix : [(id, date « AAAA-MM-JJTHH:MM:SS »)]. Corrige en tâche de fond ; rend le nombre de fichiers."""
    global APPLY_THREAD
    if R.LECTURE_SEULE:
        raise Refus("Mode lecture seule : aucune photo n'a été corrigée.")
    jobs = []
    for item_id, raw in choix:
        item = E.ITEMS.get(item_id)
        if not item or (item.get("result") or {}).get("ok") or item.get("status") == "abîmé":
            continue
        jobs.append((item, lire_date(raw)))
    if not jobs:
        raise Refus("aucune photo à corriger", 400)
    with E.LOCK:
        if E.STATE["running"]:
            raise Refus("une tâche est en cours")
        E.STATE.update(running=True, kind="correction", error=None, messages=[], progress="", done=0,
                       total=len(jobs), steps=[], total_estime=False)
    APPLY_THREAD = threading.Thread(target=apply_worker, args=(jobs,), daemon=True)
    APPLY_THREAD.start()
    return len(jobs)


def lancer_realignement():
    if R.LECTURE_SEULE:
        raise Refus("Mode lecture seule : aucune vidéo n'a été modifiée.")
    paths = videos_a_realigner()
    if not paths:
        raise Refus("aucune vidéo à réaligner", 400)
    with E.LOCK:
        if E.STATE["running"]:
            raise Refus("une tâche est en cours")
        E.STATE.update(running=True, kind="correction", error=None, messages=[], progress="", done=0,
                       total=len(paths), steps=[], total_estime=False)
    threading.Thread(target=realign_worker, args=(paths,), daemon=True).start()
    return len(paths)


def valider_dossier(folder):
    """Le dossier est mal daté mais les dates des photos sont bonnes : rien n'est modifié,
    les photos ne sont plus signalées (tant que le fichier ne change pas)."""
    n = 0
    with E.LOCK:
        if E.STATE["running"]:
            raise Refus("une tâche est en cours")
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
    return n


def renommer_dossier(folder, nouveau):
    """Renomme (déplace) un dossier de la Pellicule, puis relance une analyse des nouveaux fichiers."""
    if R.LECTURE_SEULE:
        raise Refus("mode lecture seule : le dossier n'a pas été renommé")
    nouveau = (nouveau or "").strip().strip("/")
    src = (R.SCAN_DIR / (folder or "")).resolve()
    dst = (R.SCAN_DIR / nouveau).resolve()
    racine = R.SCAN_DIR.resolve()
    if not folder or not nouveau or ".." in Path(nouveau).parts or racine not in dst.parents or racine not in src.parents:
        raise Refus("chemin invalide", 400)
    if not src.is_dir():
        raise Refus("dossier introuvable", 404)
    if dst.exists():
        raise Refus(f"« {nouveau} » existe déjà")
    if src in dst.parents:
        raise Refus("impossible de déplacer un dossier dans lui-même", 400)
    with E.LOCK:
        if E.STATE["running"]:
            raise Refus("une tâche est en cours")
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
        raise Refus(str(exc), 500)
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


def progression():
    """État de la tâche en cours, avec le temps restant par étape et au total."""
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


def _carte(it):
    """Ce que l'interface montre d'une photo à traiter (le chemin complet reste sur le serveur)."""
    garder = ("id", "name", "folder", "kind", "status", "ref", "date", "source", "gap", "name_date",
              "choice_folder", "choice_name", "choice_voisins", "immich_date", "erreur", "vrai_ext", "alerte",
              "proposed", "proposed_src", "voisins", "convertir", "deja")
    c = {k: it.get(k) for k in garder}
    c["echec"] = (it.get("result") or {}).get("msg") if it.get("result") and not it["result"].get("ok") else None
    c["heure"] = it["date"][11:19] if it.get("date") else "12:00:00"
    return c


def etat_complet():
    """Tout ce que montre l'écran : photos à traiter par dossier, corrigées, compteurs, réglages."""
    with E.LOCK:
        items = list(E.STATE["items"])
        state = dict(E.STATE)
        corrected_all = list(E.CORRECTED.values())
    pending = [it for it in items if not (it.get("result") or {}).get("ok")]
    pending_paths = {it["path"] for it in pending}
    corrected = sorted((r for r in corrected_all if r["path"] not in pending_paths),
                       key=lambda r: (r["folder"], r["name"]))
    dossiers, corrigees = {}, {}
    for it in pending:
        dossiers.setdefault(it["folder"], []).append(_carte(it))
    for r in corrected:
        corrigees.setdefault(r["folder"], []).append(
            {k: r.get(k) for k in ("id", "name", "kind", "avant", "apres", "quand")}
            | {"note": (r.get("note") or "").split(", relu")[0]})
    folders = state.get("folders") or {}
    compte = {k: sum(1 for it in pending if it["status"] == k) for k in ("écart", "sans date", "à trancher", "abîmé")}
    return {
        "running": bool(state.get("running")), "kind": state.get("kind") or "",
        "scanned_at": state.get("scanned_at"), "stats": state.get("stats") or {},
        "error": state.get("error"), "immich": state.get("immich"),
        "messages": list(state.get("messages") or []),
        "a_traiter": len(pending), "compte": compte, "nb_corrigees": len(corrected),
        "dossiers": [{"nom": f, "ref": cartes[0]["ref"], "photos": cartes,
                      "proposition": folders.get(f)} for f, cartes in dossiers.items()],
        "corrigees": [{"nom": f, "photos": rs} for f, rs in corrigees.items()],
        "tolerance": R.TOLERANCE_DAYS, "has_cache": R.CACHE_FILE.exists(),
        "originals_dir": bool(R.ORIGINALS_DIR), "lecture_seule": R.LECTURE_SEULE,
        "lecture_seule_origine": R.ORIGINE_LECTURE_SEULE,
        "dossier": R.SCAN_DIR.name, "dossier_present": R.SCAN_DIR.is_dir(),
        "immich_configure": I.immich_enabled(),
        "a_realigner": 0 if state.get("running") else len(videos_a_realigner()),
    }
