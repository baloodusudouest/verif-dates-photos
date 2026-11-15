"""Actions qui écrivent : corrections, réalignement des vidéos."""
import csv
import datetime as dt
import json
import re
import subprocess
from pathlib import Path

from . import reglages as R
from . import etat as E
from . import exif as X
from . import immich as I


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
