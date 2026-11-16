<script>
  // « Réglages » : ce qui n'est pas un service. Toujours en bas : la version et la déconnexion.
  // 3.2.0 : le dossier analysé et la tolérance se règlent ici (plus dans la stack), et « Restaurer »
  // reprend les réglages d'une sauvegarde gardée par l'Anneau.
  import { onMount } from 'svelte'
  import Download from '@lucide/svelte/icons/download'
  import RotateCcw from '@lucide/svelte/icons/rotate-ccw'
  import { ecran } from '$lib/etat.svelte.js'
  import { api, ErreurApi } from '$lib/api.js'
  import { Button } from '$lib/components/ui/button/index.js'
  let { version, deconnecter } = $props()
  const e = $derived(ecran.etat)

  const NOMS = { sous_dossier: 'Dossier analysé', tolerance_jours: 'Tolérance (jours)' }

  let vue = $state(null)            // GET /api/reglages
  let sousDossier = $state('')
  let tolerance = $state(1)
  let message = $state('')
  let erreur = $state('')
  let envoi = $state(false)

  // Restaurer
  let dates = $state(null)          // null : pas encore demandé
  let choisie = $state('')
  let apercu = $state(null)         // { le, version, differences }
  let messageRest = $state('')
  let erreurRest = $state('')

  function echec(err, poser) {
    if (err instanceof ErreurApi && err.statut === 401) return ecran.ondeconnecte()
    poser(err.message)
  }

  function remplir(v) {
    vue = v
    sousDossier = v.reglages.sous_dossier
    tolerance = v.reglages.tolerance_jours
  }

  async function charger() {
    try { remplir(await api.get('/api/reglages')) } catch (err) { echec(err, (m) => (erreur = m)) }
  }

  const modifie = $derived(vue && (sousDossier.trim() !== vue.reglages.sous_dossier || Number(tolerance) !== vue.reglages.tolerance_jours))

  async function enregistrer(ev) {
    ev.preventDefault()
    erreur = ''; message = ''; envoi = true
    try {
      remplir(await api.put('/api/reglages', { sous_dossier: sousDossier, tolerance_jours: tolerance ?? '' }))
      message = 'Réglages enregistrés. Relancer l’analyse pour les appliquer à la liste.'
      ecran.charger()
    } catch (err) { echec(err, (m) => (erreur = m)) }
    envoi = false
  }

  async function chercherDates() {
    erreurRest = ''; messageRest = ''; apercu = null
    try {
      dates = (await api.get('/api/restaurer')).dates
      choisie = dates[0] || ''
      if (choisie) await voir()
    } catch (err) { dates = null; echec(err, (m) => (erreurRest = m)) }
  }

  async function voir() {
    erreurRest = ''; messageRest = ''; apercu = null
    if (!choisie) return
    try { apercu = await api.get('/api/restaurer?le=' + encodeURIComponent(choisie)) } catch (err) { echec(err, (m) => (erreurRest = m)) }
  }

  async function restaurer() {
    if (!apercu?.differences.length) return
    if (!confirm(`Restaurer ${apercu.differences.length} réglage(s) de la sauvegarde du ${choisie} ?`)) return
    erreurRest = ''
    try {
      const r = await api.post('/api/restaurer', { le: choisie })
      remplir(r)
      messageRest = `${r.restaures} réglage(s) restauré(s) depuis la sauvegarde du ${choisie}.`
      apercu = null
      ecran.charger()
    } catch (err) { echec(err, (m) => (erreurRest = m)) }
  }

  const montrer = (v) => (v === '' ? '(dossier des photos entier)' : String(v))

  onMount(charger)
</script>

<header class="mb-5">
  <p class="etiquette">Éphéméride</p>
  <h1 class="titre">Réglages</h1>
</header>

<section id="reglages" class="mb-3 rounded-2xl border bg-card p-5">
  <h2 class="mb-1 text-lg font-semibold">Règles</h2>
  <p class="mb-3 text-sm text-muted-foreground">Prennent effet tout de suite, sans redémarrer ; la liste change à la prochaine analyse.</p>
  {#if vue}
    <form class="grid gap-3 text-sm" onsubmit={enregistrer}>
      <label class="grid gap-1 sm:grid-cols-[12rem_1fr] sm:items-center">
        <span class="text-muted-foreground">Dossier analysé</span>
        <input class="champ w-full min-w-0" name="sous_dossier" bind:value={sousDossier} autocomplete="off" spellcheck="false" />
      </label>
      <p class="-mt-2 text-xs text-muted-foreground sm:ml-[12.75rem]">Dans le dossier des photos ; vide : le dossier entier.{vue.fixes.dossier_present ? '' : ' Introuvable pour l’instant.'}</p>
      <label class="grid gap-1 sm:grid-cols-[12rem_1fr] sm:items-center">
        <span class="text-muted-foreground">Tolérance (jours)</span>
        <input class="champ w-28" name="tolerance_jours" type="number" min="0" max={vue.tolerance_max} step="1" inputmode="numeric" bind:value={tolerance} />
      </label>
      <p class="-mt-2 text-xs text-muted-foreground sm:ml-[12.75rem]">Écart permis entre la date de la photo et celle du dossier.</p>
      <div class="flex flex-wrap items-center gap-3">
        <Button type="submit" disabled={!modifie || envoi}>Enregistrer</Button>
        {#if message}<span role="status" class="text-muted-foreground">{message}</span>{/if}
        {#if erreur}<span role="alert" class="ko">{erreur}</span>{/if}
      </div>
    </form>
    <dl class="mt-4 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 border-t pt-3 text-sm">
      <dt class="text-muted-foreground">Vidéos converties</dt>
      <dd>{vue.fixes.originaux_gardes ? 'l’original est gardé à part' : 'l’original est supprimé après vérification du MP4'}</dd>
      <dt class="text-muted-foreground">Lecture seule</dt>
      <dd>{vue.fixes.lecture_seule ? (vue.fixes.lecture_seule_origine === 'maison' ? 'oui, pour toute la maison (stack)' : 'oui, pour cette appli (VERIF_LECTURE_SEULE)') : 'non'}</dd>
    </dl>
  {:else if erreur}
    <p role="alert" class="ko text-sm">{erreur}</p>
  {/if}
</section>

<section id="restaurer" class="mb-3 rounded-2xl border bg-card p-5">
  <h2 class="mb-1 text-lg font-semibold">Restaurer les réglages</h2>
  <p class="mb-3 text-sm text-muted-foreground">L'Anneau garde chaque nuit une copie de ces réglages. Rien d'autre n'est touché, même en lecture seule.</p>
  {#if dates === null}
    <Button variant="outline" onclick={chercherDates}><RotateCcw aria-hidden="true" />Voir les sauvegardes</Button>
  {:else}
    <label class="flex flex-wrap items-center gap-2 text-sm">
      <span class="text-muted-foreground">Sauvegarde du</span>
      <select class="champ" bind:value={choisie} onchange={voir}>
        {#each dates as d (d)}<option value={d}>{d}</option>{/each}
      </select>
    </label>
    {#if apercu}
      {#if apercu.differences.length}
        <ul class="mt-3 grid gap-1 text-sm">
          {#each apercu.differences as d (d.cle)}
            <li class="break-words"><span class="text-muted-foreground">{NOMS[d.cle] || d.cle} :</span> {montrer(d.actuel)} → <strong>{montrer(d.sauve)}</strong></li>
          {/each}
        </ul>
        <Button class="mt-3" onclick={restaurer}>Restaurer</Button>
      {:else}
        <p class="mt-3 text-sm text-muted-foreground">Rien à restaurer : les réglages sont déjà ceux de cette sauvegarde.</p>
      {/if}
    {/if}
  {/if}
  {#if messageRest}<p role="status" class="mt-3 text-sm text-muted-foreground">{messageRest}</p>{/if}
  {#if erreurRest}<p role="alert" class="mt-3 text-sm ko">{erreurRest}</p>{/if}
</section>

<section class="mb-3 rounded-2xl border bg-card p-5">
  <h2 class="mb-1 text-lg font-semibold">Journal</h2>
  <p class="mb-3 text-sm text-muted-foreground">Chaque correction écrite, avec l'ancienne et la nouvelle date, et chaque changement de réglage.</p>
  <Button variant="outline" href="/api/journal.csv" download="corrections.csv"><Download aria-hidden="true" />Télécharger corrections.csv</Button>
</section>

<p class="mt-6 flex items-center justify-between gap-3 text-sm text-muted-foreground">
  <span>Éphéméride {version || e?.version}</span>
  <Button variant="outline" onclick={deconnecter}>Se déconnecter</Button>
</p>
