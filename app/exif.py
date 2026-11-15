"""Lecture et écriture des dates dans les fichiers (exiftool, ffmpeg), réparations et vérification."""
import shutil
import datetime as dt
import hashlib
import json
import os
import subprocess
from pathlib import Path

from PIL import Image

from . import reglages as R
from . import etat as E
from . import dates as D


def run_exiftool(target: Path, recursive: bool):
    cmd = ["exiftool", "-j", "-q", "-q", "-api", "QuickTimeUTC", "-d", R.FMT,
           "-i", "@eaDir", "-i", "#recycle", "-i", "#snapshot", *R.READ_TAGS]
    for e in sorted(R.ALL_EXT):
        cmd += ["-ext", e]
    if recursive:
        cmd.append("-r")
    cmd.append(str(target))
    p = subprocess.run(cmd, capture_output=True, text=True)
    out = p.stdout.strip()
    if not out:
        return []
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return []


def list_files(on_progress=None):
    """Recense les fichiers à analyser (ignore les dossiers techniques Synology)."""
    files, last = [], 0
    for root, dirs, names in os.walk(R.SCAN_DIR):
        dirs[:] = sorted(d for d in dirs if d not in R.SKIP_DIRS and not d.startswith("."))
        for n in sorted(names):
            if not n.startswith(".") and Path(n).suffix.lstrip(".").lower() in R.ALL_EXT:
                files.append(Path(root) / n)
        if on_progress and len(files) - last >= 200:
            last = len(files)
            on_progress(len(files), root)
    return files


def _exiftool_list(paths, rapide):
    argfile = R.DATA_DIR / "exiftool-args.txt"
    argfile.write_text("\n".join(str(x) for x in paths) + "\n", encoding="utf-8")
    cmd = ["exiftool", "-j", "-q", "-q", *(["-fast2"] if rapide else []), "-api", "QuickTimeUTC", "-d", R.FMT,
           "-charset", "filename=utf8", *R.READ_TAGS, "-@", str(argfile)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    out = p.stdout.strip()
    if not out:
        return []
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return []


def run_exiftool_files(paths):
    """Lit les dates d'une liste de fichiers. Le mode rapide (-fast2) n'est utilisé que pour les photos :
    sur une vidéo dont l'atome moov est placé après les données (mdat), il ne voit aucune date."""
    if not paths:
        return []
    photos = [x for x in paths if Path(x).suffix.lstrip(".").lower() not in R.VIDEO_EXT]
    videos = [x for x in paths if Path(x).suffix.lstrip(".").lower() in R.VIDEO_EXT]
    return (_exiftool_list(photos, True) if photos else []) + (_exiftool_list(videos, False) if videos else [])


def same_ext(a: str, b: str) -> bool:
    a, b = a.lower(), b.lower()
    return a == b or any(a in g and b in g for g in R.EXT_EQUIV)


def content_ext(path: Path) -> str:
    """Extension correspondant au contenu réel du fichier (selon exiftool), '' si inconnu."""
    p = subprocess.run(["exiftool", "-q", "-q", "-s3", "-FileTypeExtension", str(path)],
                       capture_output=True, text=True)
    return p.stdout.strip().lower()


def same_content(a: Path, b: Path) -> bool:
    if a.stat().st_size != b.stat().st_size:
        return False
    ha, hb = hashlib.sha256(), hashlib.sha256()
    with a.open("rb") as fa, b.open("rb") as fb:
        for chunk in iter(lambda: fa.read(1 << 20), b""):
            ha.update(chunk)
        for chunk in iter(lambda: fb.read(1 << 20), b""):
            hb.update(chunk)
    return ha.digest() == hb.digest()


def free_path(p: Path) -> Path:
    n = 2
    cand = p
    while cand.exists():
        cand = p.with_name(f"{p.stem}-{n}{p.suffix}")
        n += 1
    return cand


def move_sidecars(src: Path, dst: Path):
    for sc in (Path(str(src) + ".xmp"), src.with_suffix(".xmp")):
        if sc.exists():
            target = Path(str(dst) + ".xmp")
            if not target.exists():
                sc.rename(target)


def rename_to_real_ext(item, real: str):
    """Renomme le fichier avec l'extension de son vrai format. Si ce nom est déjà pris :
    - fichier identique octet pour octet → le fichier mal nommé est mis de côté dans DOUBLONS_DIR
      et la date sera écrite sur le fichier déjà bien nommé ;
    - sinon → nom alternatif « nom-ancienneext.vraieext ». Retourne (nouveau chemin, note)."""
    E.exiger_ecriture()
    src = Path(item["path"])
    dst = src.with_suffix("." + real)
    old_ext = src.suffix.lstrip(".").lower()
    if dst.exists() and same_content(src, dst):
        cote = free_path(R.DOUBLONS_DIR / src.relative_to(R.LOCAL_ROOT))
        cote.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(cote))
        for sc in (Path(str(src) + ".xmp"), src.with_suffix(".xmp")):
            if sc.exists() and sc != dst.with_suffix(".xmp"):
                shutil.move(str(sc), str(free_path(Path(str(cote) + ".xmp"))))
        item.update(path=str(dst), name=dst.name, immich_id=None)
        return dst, f"doublon identique de {dst.name} : mis de côté dans {cote}, date écrite sur {dst.name}"
    if dst.exists():
        dst = free_path(src.with_name(f"{src.stem}-{old_ext}.{real}"))
    src.rename(dst)
    move_sidecars(src, dst)
    item.update(path=str(dst), name=dst.name, immich_id=None)
    return dst, f"renommé en {dst.name}"


def strip_jpeg_header(item):
    """JPEG précédé d'octets parasites : retire ce qui précède le marqueur de début d'image (FF D8 FF).
    Aucun pixel n'est modifié ; l'original est copié dans CLEANED_DIR. Retourne un message ou None."""
    E.exiger_ecriture()
    src = Path(item["path"])
    data = src.read_bytes()
    pos = data.find(b"\xff\xd8\xff", 0, 65536)
    if pos <= 0:
        return None
    tmp = src.with_name(f".{src.stem}.nettoyage{src.suffix}")
    tmp.write_bytes(data[pos:])
    try:
        with Image.open(tmp) as im:
            im.verify()
    except Exception:
        tmp.unlink(missing_ok=True)
        return None
    backup = R.CLEANED_DIR / src.relative_to(R.LOCAL_ROOT)
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, backup)
    st = src.stat()
    tmp.replace(src)
    os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns))
    return f"{pos} octet(s) parasite(s) retiré(s) avant l'image (original gardé dans {backup})"


def write_date_robust(item, nd: dt.datetime):
    """write_date + réparations sûres quand exiftool refuse le fichier :
    1. contenu d'un autre format que l'extension → renommage ; 2. octets parasites → nettoyage ;
    sinon le fichier est déclaré abîmé et n'est pas modifié."""
    E.exiger_ecriture()
    notes = []
    real = item.get("vrai_ext") or ""
    if real:
        dst, note = rename_to_real_ext(item, real)
        notes.append(note)
    ok, msg = write_date(item, nd)
    if not ok and "Not a valid" in msg:
        real = content_ext(Path(item["path"]))
        cur = Path(item["path"]).suffix.lstrip(".")
        if real and real in R.ALL_EXT and not same_ext(real, cur):
            dst, note = rename_to_real_ext(item, real)
            notes.append(f"{note} (le contenu est un {real.upper()})")
            ok, msg = write_date(item, nd)
        elif cur.lower() in ("jpg", "jpeg"):
            note = strip_jpeg_header(item)
            if note:
                notes.append(note)
                ok, msg = write_date(item, nd)
    if not ok and ("Not a valid" in msg or "Corrupted" in msg or "format error" in msg.lower()
                   or "Truncated" in msg):
        item.update(status="abîmé", erreur=msg)
        return False, f"fichier abîmé, non modifié : {msg}", notes
    if ok:
        ok, msg = verify(item, nd)
    return ok, msg, notes


def pick_date(rec):
    for tag in R.DATE_PRIORITY:
        d = D.parse_date(rec.get(tag))
        if d:
            return d, tag
    return None, None


def file_sig(path: Path):
    try:
        st = path.stat()
        return [st.st_size, st.st_mtime_ns]
    except OSError:
        return None


def probe_duration(path: Path):
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    try:
        return float(p.stdout.strip())
    except ValueError:
        return None


def remux_mp4(src: Path, dst: Path):
    """Copie sans perte vers MP4 ; à défaut réencode l'audio (AAC), puis tout (H.264 + AAC)."""
    base = ["ffmpeg", "-y", "-loglevel", "error", "-fflags", "+genpts", "-i", str(src),
            "-map", "0:v:0", "-map", "0:a?", "-map_metadata", "-1", "-movflags", "+faststart"]
    essais = [
        ("copie sans perte", ["-c", "copy"]),
        ("vidéo copiée, son réencodé", ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k"]),
        ("réencodée", ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                       "-c:a", "aac", "-b:a", "192k"]),
    ]
    derniere = ""
    for libelle, opts in essais:
        dst.unlink(missing_ok=True)
        p = subprocess.run(base + opts + [str(dst)], capture_output=True, text=True)
        if p.returncode == 0 and dst.exists() and dst.stat().st_size > 0:
            return libelle, ""
        derniere = (p.stderr or "").strip().splitlines()[-1:] or ["échec ffmpeg"]
        derniere = derniere[0][:200]
    dst.unlink(missing_ok=True)
    return None, derniere


def convert_and_date(item, nd: dt.datetime):
    """AVI/MTS/MKV → MP4 daté. L'original n'est supprimé qu'après vérification de la copie et de sa date."""
    E.exiger_ecriture()
    src = Path(item["path"])
    final = src.with_suffix(".mp4")
    if final.exists():
        final = src.with_name(f"{src.stem}-{src.suffix.lstrip('.').lower()}.mp4")
    if final.exists():
        return False, f"{final.name} existe déjà : conversion annulée", None
    tmp = src.with_name(f".{src.stem}.conversion.mp4")
    mode, err = remux_mp4(src, tmp)
    if not mode:
        return False, f"conversion MP4 impossible : {err}", None
    d_src, d_tmp = probe_duration(src), probe_duration(tmp)
    if d_src and (not d_tmp or abs(d_src - d_tmp) > max(1.0, d_src * 0.01)):
        tmp.unlink(missing_ok=True)
        return False, f"durée différente après conversion ({d_src:.1f} s → {d_tmp or 0:.1f} s) : original conservé", None
    tmp_item = dict(item, path=str(tmp), has_creationdate=False)
    ok, msg = write_date(tmp_item, nd)
    if ok:
        ok, msg = verify(tmp_item, nd)
    if not ok:
        tmp.unlink(missing_ok=True)
        return False, f"date non écrite dans le MP4 : {msg}", None
    tmp.rename(final)
    os.utime(final, (nd.timestamp(), nd.timestamp()))
    # sidecar éventuel de l'original : suit la vidéo
    for sc in (Path(str(src) + ".xmp"), src.with_suffix(".xmp")):
        if sc.exists():
            sc.rename(Path(str(final) + ".xmp"))
    if R.ORIGINALS_DIR:
        dest = Path(R.ORIGINALS_DIR) / src.relative_to(R.LOCAL_ROOT)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        sort = f"original déplacé dans {dest}"
    else:
        src.unlink()
        sort = "original supprimé"
    return True, f"converti en {final.name} ({mode}), {msg}, {sort}", final


def write_date(item, nd: dt.datetime):
    E.exiger_ecriture()
    path = Path(item["path"])
    v = nd.strftime("%Y:%m:%d %H:%M:%S")
    off = D.tz_offset(nd)
    ext = path.suffix.lstrip(".").lower()
    args = ["exiftool", "-overwrite_original", "-q", "-m"]
    if ext in R.VIDEO_EXT:
        args += ["-api", "QuickTimeUTC"]
        for tag in ("CreateDate", "ModifyDate", "TrackCreateDate", "TrackModifyDate",
                    "MediaCreateDate", "MediaModifyDate"):
            args.append(f"-QuickTime:{tag}={v}")
        if item.get("has_creationdate") or ext in R.QUICKTIME_EXT:
            # CreationDate (Keys) passe avant CreateDate chez Immich : toujours l'écrire
            args.append(f"-Keys:CreationDate={v}{off}")
    else:
        args += [f"-AllDates={v}", f"-OffsetTimeOriginal={off}", f"-OffsetTimeDigitized={off}",
                 f"-OffsetTime={off}"]
    args += [f"-FileModifyDate={v}{off}", str(path)]
    p = subprocess.run(args, capture_output=True, text=True)
    if p.returncode != 0:
        return False, (p.stderr or p.stdout).strip()[:300]

    # sidecars XMP : Immich les lit en priorité
    for sc in (Path(str(path) + ".xmp"), path.with_suffix(".xmp")):
        if sc.exists():
            subprocess.run(["exiftool", "-overwrite_original", "-q", "-m",
                            f"-XMP-exif:DateTimeOriginal={v}{off}", f"-XMP-xmp:CreateDate={v}{off}",
                            str(sc)], capture_output=True, text=True)
    return True, ""


def verify(item, nd: dt.datetime):
    """Relit le fichier après écriture et compare à la date demandée."""
    recs = run_exiftool(Path(item["path"]), False)
    if not recs:
        return False, "relecture impossible"
    d, src = pick_date(recs[0])
    if d is None:
        return False, "aucune date relue"
    if abs((d - nd).total_seconds()) > 60:
        return False, f"relu {d.strftime('%d/%m/%Y %H:%M:%S')} ({src})"
    return True, f"relu {d.strftime('%d/%m/%Y %H:%M:%S')} ({src})"


def dates_avec_fuseau(path, tags):
    """Dates lues avec leur fuseau horaire (datetime « aware ») : une CreationDate en heure de New York
    et une CreateDate en heure de Paris peuvent désigner le même instant."""
    cmd = ["exiftool", "-j", "-q", "-q", "-api", "QuickTimeUTC", "-d", "%Y-%m-%d %H:%M:%S%z",
           *[f"-{t}" for t in tags], str(path)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    try:
        rec = json.loads(p.stdout)[0]
    except Exception:
        return {}
    out = {}
    for t in tags:
        v = rec.get(t)
        if not isinstance(v, str):
            continue
        for fmt in ("%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
            try:
                d = dt.datetime.strptime(v, fmt)
                out[t] = d if d.tzinfo else d.replace(tzinfo=R.TZ)
                break
            except ValueError:
                pass
    return out
