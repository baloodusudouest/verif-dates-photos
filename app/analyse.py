"""Analyse de la Pellicule : photos signalées, voisines, cache, propositions de renommage."""
from collections import Counter
import datetime as dt
import hashlib
from pathlib import Path

from . import reglages as R
from . import etat as E
from . import dates as D
from . import exif as X
from . import immich as I


STATUS_KEY = {"écart": "ecart", "sans date": "sans_date", "à trancher": "a_trancher", "abîmé": "abime"}


def iid(path: str) -> str:
    return hashlib.sha1(path.encode("utf-8")).hexdigest()[:14]


def kind_of(ext: str) -> str:
    ext = ext.lower()
    if ext in R.VIDEO_EXT:
        return "vidéo"
    if ext in R.RAW_EXT:
        return "raw"
    return "photo"


def analyse_record(rec, immich_idx, stats):
    path = Path(rec["SourceFile"])
    try:
        rel = path.relative_to(R.SCAN_DIR)
    except ValueError:
        return None
    stats["fichiers"] += 1
    ref = D.folder_ref(rel.parts[:-1])
    d, src = X.pick_date(rec)
    nd, nd_time = D.name_date(path.name)
    name_ref = ("jour", nd.year, nd.month, nd.day) if nd else None

    erreur = rec.get("Error") or ""
    fte = (rec.get("FileTypeExtension") or "").lower()
    vrai_ext = fte if (fte and fte in R.ALL_EXT and not X.same_ext(fte, path.suffix.lstrip("."))) else ""
    if fte and fte not in R.ALL_EXT and not erreur:
        erreur = f"le contenu n'est pas une photo ni une vidéo ({fte.upper()})"
        fte = ""
    if erreur and not fte:
        # 0. Fichier illisible ou qui n'est pas une photo : signalé, jamais modifié
        status, key, proposed, psrc = "abîmé", "abime", None, ""
    # 1. Le nom du fichier contredit le dossier : toujours à trancher
    elif nd and ref and D.gap_days(ref, nd) > R.TOLERANCE_DAYS:
        status, key, proposed = "à trancher", "a_trancher", None
    else:
        # 2. Référence de détection : le dossier, sinon la date du nom du fichier
        det_ref = ref or name_ref
        if det_ref is None:
            stats["sans_ref"] += 1
            if d:
                return None
        if d and det_ref and D.gap_days(det_ref, d) <= R.TOLERANCE_DAYS:
            stats["ok"] += 1
            return None
        status, key = ("sans date", "sans_date") if d is None else ("écart", "ecart")
        if nd:
            proposed, psrc = D.propose_from_name(nd, nd_time, d), "nom du fichier"
        elif ref:
            proposed, psrc = D.propose(ref, d), "dossier"
        else:
            proposed, psrc = None, ""
    stats[key] += 1
    if status == "à trancher":
        psrc = ""

    immich_id, immich_date = None, None
    if immich_idx:
        hit = immich_idx.get(I.to_immich_path(path))
        if hit:
            immich_id, immich_date = hit
    gap_ref = ref or name_ref
    folder_choice = D.propose(ref, d) if ref else None
    name_choice = D.propose_from_name(nd, nd_time, d) if nd else None
    fmt_in = "%Y-%m-%dT%H:%M:%S"
    return {
        "id": iid(str(path)),
        "path": str(path),
        "name": path.name,
        "folder": str(rel.parent),
        "kind": kind_of(path.suffix.lstrip(".")),
        "status": status,
        "ref": D.ref_label(ref) if ref else "aucune",
        "date": d.strftime("%d/%m/%Y %H:%M:%S") if d else "",
        "source": src or "",
        "gap": D.gap_days(gap_ref, d) if (d and gap_ref) else None,
        "name_date": (nd.strftime("%d/%m/%Y %H:%M:%S") if nd_time else nd.strftime("%d/%m/%Y")) if nd else "",
        "choice_folder": folder_choice.strftime(fmt_in) if folder_choice else "",
        "choice_name": name_choice.strftime(fmt_in) if name_choice else "",
        "immich_id": immich_id,
        "immich_date": (immich_date or "")[:16].replace("T", " "),
        "has_creationdate": "CreationDate" in rec,
        "erreur": erreur,
        "vrai_ext": vrai_ext,
        "alerte": rec.get("Warning") or "",
        "proposed_base": proposed.strftime(fmt_in) if proposed else "",
        "proposed_src_base": psrc,
        "proposed": proposed.strftime(fmt_in) if proposed else "",
        "proposed_src": psrc,
        "result": None,
    }


def neighbor_info(rec):
    """(dossier, date EXIF ISO ou None, cohérente avec le dossier) : sert à juger les voisines."""
    path = Path(rec["SourceFile"])
    try:
        rel = path.relative_to(R.SCAN_DIR)
    except ValueError:
        return None
    ref = D.folder_ref(rel.parts[:-1])
    d, _ = X.pick_date(rec)
    coherent = bool(d) and (ref is None or D.gap_days(ref, d) <= R.TOLERANCE_DAYS)
    return [str(rel.parent), d.strftime(R.FMT) if d else None, coherent]


def apply_neighbors(items, voisins):
    """Ajoute à chaque photo signalée la période de ses voisines et, à défaut de date dans le nom,
    propose le jour dominant des voisines (heure d'origine conservée)."""
    fmt_in = "%Y-%m-%dT%H:%M:%S"
    for it in items:
        it["convertir"] = Path(it["path"]).suffix.lstrip(".").lower() in R.NEEDS_MP4
        it["proposed"], it["proposed_src"] = it.get("proposed_base", it["proposed"]), it.get("proposed_src_base", "")
        it["voisins"], it["choice_voisins"] = None, ""
        dates = sorted(dt.datetime.strptime(d, R.FMT) for p, d, ok in voisins.get(it["folder"], [])
                       if ok and d and p != it["path"])
        if len(dates) < R.VOISINES_MIN:
            continue
        jours = Counter(x.date() for x in dates)
        jour, nb = jours.most_common(1)[0]
        vmin, vmax = dates[0].date(), dates[-1].date()
        if vmin == vmax:
            label = f"toutes le {jour:%d/%m/%Y} ({len(dates)} photos)"
        else:
            label = f"{jour:%d/%m/%Y} pour {nb}/{len(dates)} photos · du {vmin:%d/%m/%Y} au {vmax:%d/%m/%Y}"
        it["voisins"] = {"n": len(dates), "min": vmin.isoformat(), "max": vmax.isoformat(), "label": label}
        if nb / len(dates) >= R.VOISINES_PART:
            try:
                t = dt.datetime.strptime(it["date"], "%d/%m/%Y %H:%M:%S").time() if it["date"] else dt.time(12)
            except ValueError:
                t = dt.time(12)
            choix = dt.datetime.combine(jour, t)
            it["choice_voisins"] = choix.strftime(fmt_in)
            # priorité : nom du fichier > voisines > dossier
            if it["status"] not in ("à trancher", "abîmé") and it["proposed_src"] != "nom du fichier":
                it["proposed"], it["proposed_src"] = it["choice_voisins"], "autres photos du dossier"


def scan_worker(mode="tout"):
    """mode « tout » : tout relire ; mode « nouveaux » : seuls les fichiers nouveaux ou modifiés."""
    try:
        stats = {"fichiers": 0, "ok": 0, "ecart": 0, "sans_date": 0, "a_trancher": 0, "abime": 0, "sans_ref": 0}
        meta = E.load_meta()
        fichiers_prev = meta.get("files_total")
        estime = lambda n, cle: (n * meta[cle]) if (n and meta.get(cle) is not None) else None
        with E.LOCK:
            E.STATE["steps"] = [
                {"key": "index", "label": "1/3 · Lecture de l'index Immich", "status": "todo",
                 "estimate": None if not I.immich_enabled() else estime(meta.get("index_total"), "index_rate")},
                {"key": "walk", "label": "2/3 · Recensement des fichiers", "status": "todo",
                 "estimate": estime(fichiers_prev, "walk_rate")},
                {"key": "analyse", "label": "3/3 · Analyse des dates", "status": "todo",
                 "estimate": estime(fichiers_prev, "analyse_rate") if mode == "tout" else None},
            ]
            if not I.immich_enabled():
                E.STATE["steps"][0].update(status="skipped")

        t0 = E.now_ts()
        immich_idx, immich_msg, nb_immich = I.immich_index(meta)
        duree_index = E.step_end("index")

        E.step_start("walk", total=fichiers_prev or 0, total_estime=bool(fichiers_prev))
        E.set_progress(immich=immich_msg)

        def avance(n, dossier):
            with E.LOCK:
                if E.STATE["total"] and n > E.STATE["total"]:
                    E.STATE["total"] = n
                try:
                    E.STATE.update(done=n, progress=str(Path(dossier).relative_to(R.LOCAL_ROOT)))
                except ValueError:
                    E.STATE.update(done=n)
        files = X.list_files(avance)
        duree_walk = E.step_end("walk")
        old_cache = E.load_cache() if mode == "nouveaux" else {}
        new_cache, items, to_scan, reused = {}, [], [], 0
        voisins = {}   # dossier → [(chemin, date, cohérente)]
        for f in files:
            sig = X.file_sig(f)
            entry = old_cache.get(str(f))
            if sig and entry and entry.get("sig") == sig:
                # fichier inchangé depuis la dernière analyse : résultat repris tel quel
                reused += 1
                for k, v in entry["stats"].items():
                    stats[k] += v
                item = entry.get("item")
                if item:
                    item = dict(item, result=None)
                    if immich_idx:   # l'index Immich a pu changer : id et date relus
                        hit = immich_idx.get(I.to_immich_path(f))
                        item["immich_id"], item["immich_date"] = (
                            (hit[0], (hit[1] or "")[:16].replace("T", " ")) if hit else (None, ""))
                    items.append(item)
                if entry.get("v"):
                    voisins.setdefault(entry["v"][0], []).append((str(f), entry["v"][1], entry["v"][2]))
                new_cache[str(f)] = entry
            else:
                to_scan.append(f)

        E.step_estimate("analyse", estime(len(to_scan), "analyse_rate"))
        if mode == "nouveaux":
            with E.LOCK:
                E.STATE["steps"][2]["label"] = (f"3/3 · Analyse des dates — {len(to_scan):,} nouveaux ou modifiés, "
                                              f"{reused:,} repris").replace(",", " ")
        E.step_start("analyse", total=len(to_scan))
        t_analyse = E.now_ts()
        for i in range(0, len(to_scan), R.CHUNK):
            chunk = to_scan[i:i + R.CHUNK]
            E.set_progress(progress=str(chunk[0].parent.relative_to(R.LOCAL_ROOT)))
            for rec in X.run_exiftool_files(chunk):
                before = dict(stats)
                item = analyse_record(rec, immich_idx, stats)
                if item:
                    items.append(item)
                path = Path(rec["SourceFile"])
                v = neighbor_info(rec)
                if v:
                    voisins.setdefault(v[0], []).append((str(path), v[1], v[2]))
                sig = X.file_sig(path)
                if sig:
                    delta = {k: stats[k] - before[k] for k in stats if stats[k] != before[k]}
                    new_cache[str(path)] = {"sig": sig, "stats": delta, "item": item, "v": v}
            E.set_progress(done=i + len(chunk))

        apply_neighbors(items, voisins)
        gardes = []
        for it in items:   # photos validées telles quelles : plus signalées tant que le fichier n'a pas changé
            sig = E.VALIDATED.get(it["path"])
            if sig and sig == X.file_sig(Path(it["path"])):
                stats[STATUS_KEY.get(it["status"], "ecart")] -= 1
                stats["valide"] = stats.get("valide", 0) + 1
            else:
                gardes.append(it)
        items = gardes
        dossiers_signales = {it["folder"] for it in items}
        folders = {}
        for dossier in dossiers_signales:
            prop = D.propose_folder(dossier, voisins.get(dossier, []))
            if prop:
                folders[dossier] = prop
        for it in items:   # photo déjà corrigée mais toujours en écart : signalée à nouveau
            rec = E.CORRECTED.get(it["path"])
            it["deja"] = rec["quand"] if rec else ""
        items.sort(key=lambda it: (it["folder"], it["name"]))
        with E.LOCK:
            E.STATE.update(items=items, stats=stats, folders=folders, scanned_at=dt.datetime.now().strftime("%d/%m/%Y %H:%M"),
                         progress="Terminé", error=None, last_mode=mode,
                         messages=([f"Analyse incrémentale : {len(to_scan)} fichier(s) relu(s), "
                                    f"{reused} repris de l'analyse précédente."] if mode == "nouveaux" else []))
            E.ITEMS.clear()
            E.ITEMS.update({it["id"]: it for it in items})
        duree_analyse = E.now_ts() - t_analyse
        E.step_end("analyse")
        # mesures pour estimer la prochaine analyse
        if nb_immich and duree_index:
            meta.update(index_total=nb_immich, index_rate=duree_index / nb_immich)
        if files and duree_walk is not None:
            meta.update(files_total=len(files), walk_rate=duree_walk / len(files))
        if len(to_scan) >= 50:
            meta.update(analyse_rate=duree_analyse / len(to_scan))
        meta["last_total_s"] = round(E.now_ts() - t0)
        E.save_meta(meta)
        E.save_cache(new_cache)
        E.save_results()
    except Exception as exc:
        E.set_progress(error=str(exc))
    finally:
        E.set_progress(running=False, kind="", phase="")
