// « Corriger la sélection » et « Corriger ce dossier » : mêmes contrôles et mêmes confirmations qu'en 2.x.
import { ecran } from '$lib/etat.svelte.js'
import { coherente } from '$lib/outils.js'

/** photos : les photos (objets de /api/etat) parmi lesquelles on prend celles qui sont cochées. */
export async function corriger(photos) {
  const e = ecran.etat
  const cochees = photos.filter((p) => p.status !== 'abîmé' && ecran.choix[p.id]?.coche)
  const vides = cochees.filter((p) => !ecran.choix[p.id].date).length
  if (vides) { alert(vides + ' photo(s) à trancher cochée(s) sans date choisie.'); return }
  if (!cochees.length) { alert('Aucun fichier coché.'); return }
  const ko = cochees.filter((p) => coherente(p, ecran.choix[p.id].date, e.tolerance) === false).length
  if (ko && !confirm(ko + ' photo(s) cochée(s) ont une date hors de la période des autres photos du dossier. Continuer quand même ?')) return
  const conv = cochees.filter((p) => p.convertir).length
  if (!confirm(cochees.length + ' fichier(s) vont être modifiés directement sur le NAS (sans copie _original).'
    + (conv ? '\n\n' + conv + ' vidéo(s) AVI/MTS/MKV seront converties en MP4 et leur original ' + (e.originals_dir ? 'déplacé' : 'SUPPRIMÉ') + '.' : '')
    + '\n\nContinuer ?')) return
  await ecran.agir('/api/corriger', { photos: cochees.map((p) => ({ id: p.id, date: ecran.choix[p.id].date })) })
}
