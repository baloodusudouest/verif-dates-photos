<script>
  // « Connexion » : une tuile par service, avec son voyant, ce qu'elle lit et ce qu'elle écrit.
  import { ecran } from '$lib/etat.svelte.js'
  const e = $derived(ecran.etat)
</script>

<header class="mb-5">
  <p class="etiquette">Éphéméride</p>
  <h1 class="titre">Connexion</h1>
</header>

<div class="grid gap-3 md:grid-cols-2">
  <section class="rounded-2xl border bg-card p-5">
    <h2 class="mb-2 flex items-center gap-2 text-lg font-semibold">
      <span class={['inline-block size-2.5 rounded-full', e.dossier_present ? 'bg-valide' : 'bg-muted-foreground/50']}></span>La Pellicule
    </h2>
    <dl class="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
      <dt class="text-muted-foreground">Dossier analysé</dt><dd>{e.dossier}{e.dossier_present ? '' : ' (introuvable)'}</dd>
      <dt class="text-muted-foreground">Dernière analyse</dt><dd>{e.scanned_at || 'jamais'}</dd>
      {#if e.scanned_at}<dt class="text-muted-foreground">Fichiers</dt><dd>{e.stats.fichiers}, dont {e.stats.ok} sans rien à signaler</dd>{/if}
      <dt class="text-muted-foreground">Écriture</dt>
      <dd>{e.lecture_seule ? 'lecture seule : aucun fichier n’est modifié' : 'les dates sont écrites dans les fichiers, sur validation'}</dd>
    </dl>
  </section>
  <section class="rounded-2xl border bg-card p-5">
    <h2 class="mb-2 flex items-center gap-2 text-lg font-semibold">
      <span class={['inline-block size-2.5 rounded-full', e.immich_configure ? 'bg-valide' : 'bg-muted-foreground/50']}></span>Immich
    </h2>
    {#if e.immich_configure}
      <p class="text-sm text-muted-foreground">L'outil lit dans Immich la date connue et les miniatures, et lui demande de relire les métadonnées après une correction. Il n'y écrit rien d'autre.</p>
      {#if e.immich}<p class="mt-2 text-sm">{e.immich}</p>{/if}
    {:else}
      <p class="text-sm text-muted-foreground">Non configuré (IMMICH_URL, IMMICH_API_KEY) : l'outil fonctionne sans, avec ses propres miniatures.</p>
    {/if}
  </section>
</div>
