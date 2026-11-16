<script>
  // « À traiter » : analyse, tuiles par motif (elles filtrent), dossiers et photos.
  import { ecran } from '$lib/etat.svelte.js'
  import { correspond } from '$lib/outils.js'
  import { corriger } from '$lib/corriger.js'
  import { Button } from '$lib/components/ui/button/index.js'
  import Progression from '$lib/components/Progression.svelte'
  import Dossier from '$lib/components/Dossier.svelte'

  const FILTRES = ['tous', 'écart', 'sans date', 'à trancher', 'incoherent', 'extension', 'abîmé']
  let filtre = $state(lireFiltre())

  function lireFiltre() {
    try {
      const f = localStorage.getItem('verif-filtre')
      return FILTRES.includes(f) ? f : 'tous'
    } catch { return 'tous' }
  }
  function choisirFiltre(f) {
    filtre = f
    try { localStorage.setItem('verif-filtre', f) } catch { /* navigateur privé */ }
  }

  const e = $derived(ecran.etat)
  const toutes = $derived(e.dossiers.flatMap((d) => d.photos))
  const vues = $derived(
    e.dossiers
      .map((d) => ({ dossier: d, visibles: d.photos.filter((p) => correspond(p, filtre, ecran.choix, e.tolerance)) }))
      .filter((x) => x.visibles.length),
  )
  const tuiles = $derived([
    { f: 'tous', nom: 'Tout', n: e.a_traiter },
    { f: 'écart', nom: 'Écarts de date', n: e.compte['écart'] },
    { f: 'sans date', nom: 'Sans date', n: e.compte['sans date'] },
    { f: 'à trancher', nom: 'À trancher (nom ≠ dossier)', n: e.compte['à trancher'] },
    { f: 'incoherent', nom: 'Hors période des voisines', n: toutes.filter((p) => correspond(p, 'incoherent', ecran.choix, e.tolerance)).length },
    { f: 'extension', nom: 'Extension incorrecte', n: toutes.filter((p) => p.vrai_ext).length },
    { f: 'abîmé', nom: 'Fichiers abîmés', n: e.compte['abîmé'] },
  ])

  function cocher(v) {
    for (const x of vues) for (const p of x.visibles) if (p.status !== 'abîmé') ecran.choix[p.id].coche = v
  }

  function analyser(mode) {
    if (mode === 'tout' && e.has_cache && !confirm('Relire tous les fichiers de la Pellicule ? Cela peut prendre du temps.')) return
    ecran.agir('/api/analyse', { mode })
  }

  function realigner() {
    if (!confirm('Remettre les dates QuickTime et la date du fichier de ces vidéos sur leur date d’origine (CreationDate) ?')) return
    ecran.agir('/api/realigner')
  }
</script>

<header class="mb-4 flex flex-col gap-3">
  <div class="flex flex-wrap items-end justify-between gap-3">
    <div>
      <p class="etiquette">Éphéméride</p>
      <h1 class="titre">À traiter</h1>
    </div>
    <div class="flex flex-wrap gap-2">
      {#if e.running}
        <Button disabled>{e.kind === 'correction' ? 'Correction en cours…' : 'Analyse en cours…'}</Button>
      {:else if e.has_cache}
        <Button title="Analyse seulement les fichiers ajoutés ou modifiés depuis la dernière analyse" onclick={() => analyser('nouveaux')}>Scanner les nouveaux</Button>
        <Button variant="outline" title="Relit tous les fichiers" onclick={() => analyser('tout')}>Tout rescanner</Button>
      {:else}
        <Button onclick={() => analyser('tout')}>Lancer l'analyse</Button>
      {/if}
    </div>
  </div>

  {#if e.a_traiter || e.nb_corrigees}
    <div id="tuiles" class="grid grid-cols-2 gap-2 sm:grid-cols-4 xl:grid-cols-7" role="group" aria-label="Motifs à traiter">
      {#each tuiles as t}
        <button type="button" aria-pressed={filtre === t.f} onclick={() => choisirFiltre(t.f)}
          class={['tuile grid gap-0.5 rounded-xl border px-3.5 py-2.5 text-left transition-colors hover:border-primary aria-pressed:border-primary aria-pressed:bg-primary/14',
                  t.n || t.f === 'tous' ? 'bg-card' : 'bg-transparent']}>
          <span class={['chiffre text-2xl', !t.n && t.f !== 'tous' && 'text-muted-foreground']}>{t.n}</span>
          <span class="text-[0.85rem] text-muted-foreground">{t.nom}</span>
        </button>
      {/each}
    </div>
  {/if}

  {#if e.a_traiter}
    <div class="flex flex-wrap items-center gap-2">
      <Button variant="outline" onclick={() => cocher(true)}>Tout cocher (visible)</Button>
      <Button variant="outline" onclick={() => cocher(false)}>Tout décocher</Button>
      <Button disabled={e.running} onclick={() => corriger(toutes)}>Corriger la sélection</Button>
    </div>
  {/if}

  {#if e.scanned_at}
    <p class="text-[13px] text-muted-foreground">
      Analyse du {e.scanned_at} · {e.stats.fichiers} fichiers · {e.stats.ok} OK · {e.stats.ecart} écarts · {e.stats.sans_date} sans date
      · {e.stats.a_trancher || 0} à trancher{#if e.stats.abime} · {e.stats.abime} abîmé(s){/if} · {e.stats.sans_ref} hors dossier daté
      · tolérance {e.tolerance} j{#if e.nb_corrigees} · {e.nb_corrigees} corrigée(s){/if}{#if e.stats.valide} · {e.stats.valide} validée(s) telles quelles{/if}
    </p>
  {/if}
</header>

<Progression />

{#if e.lecture_seule}
  <p class="mb-4 rounded-xl border border-[var(--ecart)] bg-card px-4 py-2.5" role="status">
    <strong>Mode lecture seule.</strong> L'outil analyse et propose des dates, mais ne modifie, ne déplace et ne supprime aucun fichier ({e.lecture_seule_origine === 'maison' ? 'toute la maison : MAISON_LECTURE_SEULE' : 'cette appli : VERIF_LECTURE_SEULE'}).
  </p>
{/if}
{#if e.error}<p class="ko mb-4 rounded-xl border bg-card px-4 py-2.5 font-semibold">Erreur : {e.error}</p>{/if}
{#if e.immich}<p class="mb-4 rounded-xl border bg-card px-4 py-2.5 text-muted-foreground">{e.immich}</p>{/if}
{#each e.messages as m}<p class="mb-4 rounded-xl border bg-card px-4 py-2.5">{m}</p>{/each}

{#if e.a_realigner}
  <div class="mb-4 rounded-xl border border-[var(--ecart)] bg-card px-4 py-2.5">
    <strong>{e.a_realigner} vidéo(s) modifiée(s) à tort par une version précédente.</strong>
    Elles avaient déjà une date d'origine (CreationDate) que l'outil ne voyait pas : seuls les champs QuickTime secondaires
    et la date du fichier ont été changés (Immich affiche toujours la date d'origine).
    <Button size="sm" class="ml-2" disabled={e.running || e.lecture_seule} onclick={realigner}>Réaligner ces vidéos</Button>
  </div>
{/if}

{#if !e.a_traiter && e.scanned_at && !e.running}
  <p id="rien" class="mb-4 rounded-xl border bg-card px-4 py-2.5 font-semibold text-valide">Aucune photo à traiter.</p>
{/if}

<div id="f">
  {#each vues as x (x.dossier.nom)}
    <Dossier dossier={x.dossier} visibles={x.visibles} />
  {/each}
</div>
