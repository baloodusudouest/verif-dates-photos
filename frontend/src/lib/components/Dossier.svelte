<script>
  // Un dossier de la Pellicule : actions pour tout le dossier (1.9), renommage proposé, cartes.
  // Après une action de dossier, il se replie et l'écran passe au dossier suivant (1.9.1).
  import { tick, untrack } from 'svelte'
  import { ecran } from '$lib/etat.svelte.js'
  import { corriger } from '$lib/corriger.js'
  import { Button } from '$lib/components/ui/button/index.js'
  import Carte from './Carte.svelte'

  let { dossier, visibles } = $props()   // visibles : les photos du dossier qui répondent au filtre
  let element = $state()
  let jour = $state('')
  let info = $state('')
  let nouveau = $state(untrack(() => dossier.proposition?.nouveau || dossier.nom))   // valeur de départ, modifiable

  const e = $derived(ecran.etat)
  const modifiables = $derived(dossier.photos.filter((p) => p.status !== 'abîmé'))
  const n = $derived({
    name: modifiables.filter((p) => p.choice_name).length,
    voisins: modifiables.filter((p) => p.choice_voisins).length,
    folder: modifiables.filter((p) => p.choice_folder).length,
    proposed: modifiables.filter((p) => p.proposed).length,
  })
  const replie = $derived(dossier.nom in ecran.replies)

  function replier(texte) { ecran.replies[dossier.nom] = texte || '' }
  function deplier() { delete ecran.replies[dossier.nom] }

  async function suivant() {
    await tick()
    const tous = [...document.querySelectorAll('#f [data-folder]')]
    const prochain = tous.slice(tous.indexOf(element) + 1).find((h) => !h.hasAttribute('data-replie'))
    ;(prochain || element).scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  function appliquer(type) {
    if (type === 'jour' && !jour) { alert('Choisis d’abord le jour.'); return }
    const champ = { name: 'choice_name', voisins: 'choice_voisins', folder: 'choice_folder', proposed: 'proposed' }[type]
    let faits = 0, sautes = 0
    for (const p of visibles) {
      if (p.status === 'abîmé') continue
      const v = type === 'jour' ? jour + 'T' + (p.heure || '12:00:00') : p[champ]
      if (!v) { sautes++; continue }
      ecran.choix[p.id].date = v
      ecran.choix[p.id].coche = true
      faits++
    }
    info = faits + ' photo(s) réglée(s) et cochée(s)' + (sautes ? ', ' + sautes + ' sans date de ce type (inchangées)' : '') + ' — vérifie puis « Corriger ce dossier ».'
    if (faits) {
      replier('✓ ' + faits + ' photo(s) réglée(s) et cochée(s)' + (sautes ? ' · ' + sautes + ' inchangée(s)' : ''))
      suivant()
    }
  }

  function cocherDossier() {
    for (const p of visibles) if (p.status !== 'abîmé') ecran.choix[p.id].coche = true
  }

  async function valider() {
    if (!confirm('Les photos de « ' + dossier.nom + ' » gardent leur date actuelle et ne seront plus signalées (sauf les photos sans date). Aucun fichier n’est modifié. Continuer ?')) return
    await ecran.agir('/api/valider', { folder: dossier.nom })
  }

  async function renommer() {
    const neuf = nouveau.trim()
    if (!neuf || neuf === dossier.nom) { alert('Indique le nouveau nom du dossier.'); return }
    if (!confirm('Renommer « ' + dossier.nom + ' » en « ' + neuf + ' » sur le NAS ?\n\nImmich verra ces photos comme de nouveaux fichiers. Une analyse des fichiers déplacés est relancée ensuite.')) return
    await ecran.agir('/api/renommer', { folder: dossier.nom, nouveau: neuf })
  }
</script>

<section bind:this={element} class="folder mt-6 scroll-mt-4" data-folder data-nom={dossier.nom} data-replie={replie ? '' : undefined}>
  <div class="mb-2 flex flex-wrap items-baseline gap-2.5">
    <button type="button" class="champ px-2 py-0 text-sm" aria-expanded={!replie} title="Replier ou déplier ce dossier"
      onclick={() => (replie ? deplier() : replier('replié'))}>{replie ? '▸' : '▾'}</button>
    <h2 class="text-[15px] font-semibold">{dossier.nom}</h2>
    <span class="text-sm text-muted-foreground">dossier : {dossier.ref} · {dossier.photos.length} photo(s)</span>
    {#if replie}<span class="resume text-sm font-semibold text-valide">{ecran.replies[dossier.nom]}</span>{/if}
  </div>

  {#if !replie}
    {#if modifiables.length}
      <div class="mb-2 flex flex-wrap items-center gap-1.5 rounded-xl border bg-card px-2 py-1.5">
        <span class="text-sm text-muted-foreground">Tout le dossier :</span>
        {#if n.name}<Button size="sm" variant="outline" onclick={() => appliquer('name')}>Date du nom ({n.name})</Button>{/if}
        {#if n.voisins}<Button size="sm" variant="outline" onclick={() => appliquer('voisins')}>Voisines ({n.voisins})</Button>{/if}
        {#if n.folder}<Button size="sm" variant="outline" onclick={() => appliquer('folder')}>Date du dossier ({n.folder})</Button>{/if}
        {#if n.proposed}<Button size="sm" variant="outline" onclick={() => appliquer('proposed')}>Dates proposées ({n.proposed})</Button>{/if}
        <span class="inline-flex gap-1">
          <input type="date" class="champ py-0.5 text-sm" aria-label="Même jour pour tout le dossier" bind:value={jour} />
          <Button size="sm" variant="outline" onclick={() => appliquer('jour')}>Même jour pour toutes</Button>
        </span>
        <Button size="sm" variant="outline" onclick={cocherDossier}>Cocher le dossier</Button>
        <Button size="sm" disabled={e.running} onclick={() => corriger(dossier.photos)}>Corriger ce dossier</Button>
        <Button size="sm" variant="outline" disabled={e.running} title="Le dossier est mal daté mais les photos ont la bonne date : rien n'est modifié" onclick={valider}>Les dates des photos sont bonnes</Button>
        {#if info}<span class="text-xs text-muted-foreground">{info}</span>{/if}
      </div>
      <div class="mb-2 flex flex-wrap items-center gap-1.5 rounded-xl border bg-card px-2 py-1.5">
        <span class="text-sm text-muted-foreground">
          {dossier.proposition ? `Dossier mal daté ? ${dossier.proposition.label} — renommer en :` : 'Renommer le dossier :'}
        </span>
        <input type="text" class="champ min-w-0 flex-1 basis-52 text-sm" aria-label="Nouveau nom du dossier" bind:value={nouveau} />
        <Button size="sm" variant="outline" disabled={e.running || e.lecture_seule} onclick={renommer}>Renommer le dossier</Button>
      </div>
    {/if}
    <div class="grid grid-cols-[repeat(auto-fill,minmax(min(230px,100%),1fr))] gap-3">
      {#each visibles as photo (photo.id)}
        <Carte {photo} />
      {/each}
    </div>
  {/if}
</section>
