// Petites fonctions de l'écran, reprises telles quelles de la page 2.x.

const JOUR = 86400000

/** « 2008-08-10 » → « 10/08/2008 » */
export function fr(iso) {
  return iso.slice(8, 10) + '/' + iso.slice(5, 7) + '/' + iso.slice(0, 4)
}

/** Durée lisible : « 42 s », « 7 min », « 1 h 05 ». */
export function fmt(s) {
  s = Math.max(0, Math.round(s))
  if (s < 60) return s + ' s'
  const m = Math.round(s / 60)
  return m < 60 ? m + ' min' : Math.floor(m / 60) + ' h ' + String(m % 60).padStart(2, '0')
}

/** Cohérence d'une date avec les autres photos du dossier : null (rien à juger), true ou false. */
export function coherente(photo, date, tolerance) {
  const v = photo.voisins
  if (!v || !v.min || !date) return null
  const d = Date.parse(date.slice(0, 10) + 'T00:00:00Z')
  const a = Date.parse(v.min + 'T00:00:00Z')
  const b = Date.parse(v.max + 'T00:00:00Z')
  return d >= a - tolerance * JOUR && d <= b + tolerance * JOUR
}

export function periode(photo) {
  const v = photo.voisins
  return v.min === v.max ? fr(v.min) : fr(v.min) + ' – ' + fr(v.max)
}

/** La photo répond-elle au filtre (une tuile) ? */
export function correspond(photo, filtre, choix, tolerance) {
  if (filtre === 'tous') return true
  if (filtre === 'incoherent') return coherente(photo, choix[photo.id]?.date, tolerance) === false
  if (filtre === 'extension') return !!photo.vrai_ext
  return photo.status === filtre
}

export function classeTag(statut) {
  return statut === 'sans date' ? 't-sans' : statut === 'à trancher' ? 't-trancher' : statut === 'abîmé' ? 't-abime' : 't-ecart'
}
