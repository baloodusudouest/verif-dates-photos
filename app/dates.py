"""Règles de dates : date du dossier, date du nom du fichier, propositions. Aucune écriture."""
import calendar
from collections import Counter
import datetime as dt
import re
from pathlib import Path

from . import reglages as R


RE_DATE_PREFIX = re.compile(r"^\d{4}(?:[-_.]\d{2}){0,2}\s*(?:[-–_]\s*)?")


def tz_offset(d: dt.datetime) -> str:
    off = R.TZ.utcoffset(d.replace(tzinfo=None)) or dt.timedelta(0)
    minutes = int(off.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    minutes = abs(minutes)
    return f"{sign}{minutes // 60:02d}:{minutes % 60:02d}"


def parse_date(value):
    if not value or not isinstance(value, str):
        return None
    try:
        d = dt.datetime.strptime(value[:19], R.FMT)
    except ValueError:
        return None
    if d.year < 1900:
        return None
    return d


RE_DAY = re.compile(r"^(\d{4})[-_.](\d{2})[-_.](\d{2})(?!\d)")


RE_MONTH = re.compile(r"^(\d{4})[-_.](\d{2})(?!\d)")


RE_YEAR = re.compile(r"^(\d{4})(?!\d)")


RANK = {"an": 1, "mois": 2, "jour": 3}


def valid_ref(ref) -> bool:
    p, y, m, d = ref
    if not 1900 <= y <= 2100:
        return False
    if p in ("mois", "jour") and not 1 <= m <= 12:
        return False
    if p == "jour":
        try:
            dt.date(y, m, d)
        except ValueError:
            return False
    return True


def folder_ref(parts):
    """Retourne la référence la plus précise trouvée dans les noms de dossiers."""
    ref = None
    for part in parts:
        cand = None
        m = RE_DAY.match(part)
        if m:
            cand = ("jour", int(m[1]), int(m[2]), int(m[3]))
        else:
            m = RE_MONTH.match(part)
            if m:
                cand = ("mois", int(m[1]), int(m[2]), None)
            else:
                m = RE_YEAR.match(part)
                if m:
                    cand = ("an", int(m[1]), None, None)
        if cand and valid_ref(cand) and (ref is None or RANK[cand[0]] >= RANK[ref[0]]):
            ref = cand
    return ref


def ref_label(ref) -> str:
    p, y, m, d = ref
    if p == "jour":
        return f"{d:02d}/{m:02d}/{y}"
    if p == "mois":
        return f"{m:02d}/{y}"
    return str(y)


def ref_bounds(ref):
    p, y, m, d = ref
    if p == "jour":
        s = e = dt.date(y, m, d)
    elif p == "mois":
        s, e = dt.date(y, m, 1), dt.date(y, m, calendar.monthrange(y, m)[1])
    else:
        s, e = dt.date(y, 1, 1), dt.date(y, 12, 31)
    return s, e


def gap_days(ref, d: dt.datetime) -> int:
    s, e = ref_bounds(ref)
    x = d.date()
    if x < s:
        return (s - x).days
    if x > e:
        return (x - e).days
    return 0


def propose(ref, d):
    """Date du dossier + heure d'origine (12:00 si aucune date)."""
    p, y, m, day = ref
    t = d.time().replace(microsecond=0) if d else dt.time(12, 0, 0)
    if p == "jour":
        nd = dt.date(y, m, day)
    elif p == "mois":
        last = calendar.monthrange(y, m)[1]
        nd = dt.date(y, m, min(d.day, last) if d else 1)
    else:
        if d:
            try:
                nd = dt.date(y, d.month, d.day)
            except ValueError:
                nd = dt.date(y, d.month, 28)
        else:
            nd = dt.date(y, 1, 1)
    return dt.datetime.combine(nd, t)


RE_NAME_DT = re.compile(
    r"(?<!\d)((?:19|20)\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])"
    r"[-_ T.]?([01]\d|2[0-3])[-_.:h]?([0-5]\d)[-_.:m]?([0-5]\d)")


RE_NAME_D = re.compile(r"(?<!\d)((?:19|20)\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])(?!\d)")


def name_date(name: str):
    """Date (et heure si présente) contenue dans le nom du fichier, sinon (None, False)."""
    stem = Path(name).stem
    m = RE_NAME_DT.search(stem)
    if m:
        try:
            return dt.datetime(*map(int, m.groups())), True
        except ValueError:
            pass
    m = RE_NAME_D.search(stem)
    if m:
        try:
            return dt.datetime(*map(int, m.groups()), 12, 0, 0), False
        except ValueError:
            pass
    return None, False


def propose_from_name(nd: dt.datetime, has_time: bool, d):
    """Jour du nom de fichier ; heure du nom si présente, sinon heure EXIF, sinon 12:00."""
    if has_time:
        return nd
    t = d.time().replace(microsecond=0) if d else dt.time(12, 0, 0)
    return dt.datetime.combine(nd.date(), t)


def propose_folder(folder: str, entries):
    """Si la majorité des photos datées contredit la date du dossier, propose un nouveau chemin
    « année/AAAA-MM-JJ évènement » (ou AAAA-MM / AAAA selon la dispersion des dates)."""
    parts = Path(folder).parts
    ref = folder_ref(parts)
    if not parts or ref is None:
        return None
    dates = []
    for p, d, _ok in entries:
        if d:
            dates.append(dt.datetime.strptime(d, R.FMT))
        else:
            nd, _ = name_date(Path(p).name)
            if nd:
                dates.append(nd)
    if len(dates) < 1:
        return None
    hors = [x for x in dates if gap_days(ref, x) > R.TOLERANCE_DAYS]
    if len(hors) * 2 < len(dates):
        return None
    n = len(dates)
    jour, nj = Counter(x.date() for x in dates).most_common(1)[0]
    mois, nm = Counter((x.year, x.month) for x in dates).most_common(1)[0]
    an, na = Counter(x.year for x in dates).most_common(1)[0]
    if nj * 2 > n:
        prefixe, label = jour.strftime("%Y-%m-%d"), f"{nj}/{n} photos datées du {jour:%d/%m/%Y}"
    elif nm * 2 > n:
        prefixe, label = f"{mois[0]}-{mois[1]:02d}", f"{nm}/{n} photos datées de {mois[1]:02d}/{mois[0]}"
    elif na * 2 > n:
        prefixe, label = str(an), f"{na}/{n} photos datées de {an}"
    else:
        return None
    evenement = RE_DATE_PREFIX.sub("", parts[-1]).strip()
    nom = f"{prefixe} {evenement}".strip()
    annee = prefixe[:4]
    if len(parts) >= 2 and re.fullmatch(r"\d{4}", parts[-2]):
        nouveau = Path(*parts[:-2], annee, nom)
    else:
        nouveau = Path(*parts[:-1], nom)
    if str(nouveau) == folder:
        return None
    return {"nouveau": str(nouveau).replace("\\", "/"), "label": label}
