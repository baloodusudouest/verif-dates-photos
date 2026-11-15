// État partagé de l'écran : ce que renvoie /api/etat, la progression de la tâche en cours,
// et les choix faits à l'écran (date et case « Corriger » de chaque photo, dossiers repliés).
import { api, ErreurApi } from '$lib/api.js'

class Ecran {
  etat = $state(null)          // /api/etat
  prog = $state(null)          // /api/progression pendant une tâche
  choix = $state({})           // id → { date, coche }
  replies = $state({})         // nom du dossier → texte du résumé
  erreur = $state('')
  ondeconnecte = () => {}
  #suivi = null

  async charger() {
    try {
      const e = await api.get('/api/etat')
      for (const d of e.dossiers)
        for (const p of d.photos)
          if (!(p.id in this.choix)) this.choix[p.id] = { date: p.proposed || '', coche: false }
      this.etat = e
      this.erreur = ''
      if (e.running) this.suivre()
    } catch (err) {
      this.#echec(err)
    }
  }

  #echec(err) {
    if (err instanceof ErreurApi && err.statut === 401) this.ondeconnecte()
    else this.erreur = err.message
  }

  /** Suit la tâche en cours ; quand elle se termine, relit l'état (comme le rechargement de la page 2.x). */
  suivre() {
    if (this.#suivi) return
    const tour = async () => {
      try {
        const st = await api.get('/api/progression')
        this.prog = st
        if (st.running) {
          this.#suivi = setTimeout(tour, 1500)
          return
        }
      } catch (err) {
        if (err instanceof ErreurApi && err.statut === 401) return this.ondeconnecte()
        this.#suivi = setTimeout(tour, 3000)
        return
      }
      this.#suivi = null
      this.prog = null
      await this.charger()
    }
    this.#suivi = setTimeout(tour, 300)
  }

  /** Un geste qui écrit ou lance une tâche : POST, puis relecture et suivi. Rend true si accepté. */
  async agir(chemin, corps) {
    try {
      await api.post(chemin, corps)
    } catch (err) {
      if (err instanceof ErreurApi && err.statut === 401) { this.ondeconnecte(); return false }
      alert('Échec : ' + err.message)
      await this.charger()
      return false
    }
    await this.charger()
    this.suivre()
    return true
  }
}

export const ecran = new Ecran()
