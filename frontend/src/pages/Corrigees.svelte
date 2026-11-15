<script>
  // « Corrigées » : ce qui a été écrit (ou validé tel quel), avec l'avant et l'après.
  import { ecran } from '$lib/etat.svelte.js'
  const e = $derived(ecran.etat)
</script>

<header class="mb-5">
  <p class="etiquette">Éphéméride</p>
  <h1 class="titre">Corrigées</h1>
</header>

<section id="corrigees">
  {#if !e.corrigees.length}
    <p class="rounded-xl border bg-card px-4 py-2.5 text-muted-foreground">Aucune photo corrigée pour l'instant.</p>
  {/if}
  {#each e.corrigees as d (d.nom)}
    <div class="mt-6 mb-2 flex flex-wrap items-baseline gap-2.5">
      <h2 class="text-[15px] font-semibold">{d.nom}</h2>
      <span class="text-sm text-muted-foreground">{d.photos.length} corrigée(s)</span>
    </div>
    <div class="grid grid-cols-[repeat(auto-fill,minmax(min(230px,100%),1fr))] gap-3">
      {#each d.photos as r (r.id)}
        <article class="card flex flex-col overflow-hidden rounded-2xl border bg-card opacity-90">
          <img loading="lazy" src={'/api/miniature/' + encodeURIComponent(r.id)} alt="" class="h-[170px] w-full bg-secondary object-cover" />
          <div class="flex flex-col gap-1 p-3 text-[13px]">
            <div class="font-semibold break-all">{r.name}</div>
            <div><span class="tag t-ok">corrigée</span> <span class="text-muted-foreground">{r.kind} · le {r.quand}</span></div>
            <div class="text-muted-foreground">Avant : {r.avant}</div>
            <div>Après : <strong>{r.apres}</strong></div>
            {#if r.note}<div class="text-muted-foreground">{r.note}</div>{/if}
          </div>
        </article>
      {/each}
    </div>
  {/each}
</section>
