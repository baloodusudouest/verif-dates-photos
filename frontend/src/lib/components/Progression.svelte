<script>
  // Barre de progression, étapes et temps restant (1.6 / 1.7), d'après /api/progression.
  import { ecran } from '$lib/etat.svelte.js'
  import { fmt } from '$lib/outils.js'

  const st = $derived(ecran.prog)
  const pc = $derived(st && st.total > 0 ? Math.min(99, Math.floor((st.done * 100) / st.total)) : 0)

  function droite(x) {
    if (x.status === 'done') return '✓ terminé en ' + fmt(x.duration || 0)
    if (x.status === 'current') return 'en cours' + (x.remaining != null ? ' · ≈ ' + fmt(x.remaining) : ' · estimation en cours…')
    if (x.status === 'skipped') return 'sans objet'
    return x.estimate != null ? '≈ ' + fmt(x.estimate) + ' (d’après la dernière analyse)' : 'durée inconnue (1re analyse)'
  }
</script>

<div id="prog" class="mb-4 flex flex-col gap-2 rounded-2xl border bg-card p-4" hidden={!ecran.etat?.running} aria-live="polite">
  <div class="flex flex-wrap justify-between gap-2">
    <strong id="prog-phase">{st?.phase || (ecran.etat?.kind === 'correction' ? 'Correction en cours…' : 'Analyse en cours…')}</strong>
    <span class="text-sm text-muted-foreground">
      {#if st && st.total > 0}
        {st.done.toLocaleString('fr-FR')} / {st.total_estime ? '≈ ' : ''}{st.total.toLocaleString('fr-FR')} · {pc} %
      {:else if st?.done}
        {st.done.toLocaleString('fr-FR')} trouvés
      {/if}
    </span>
  </div>
  <div class="relative h-3 overflow-hidden rounded-full bg-secondary">
    {#if st && st.total > 0}
      <div class="h-full rounded-full bg-primary transition-[width] duration-500" style:width={pc + '%'}></div>
    {:else}
      <div class="indet absolute h-full w-[30%] rounded-full bg-primary"></div>
    {/if}
  </div>
  <div class="flex flex-wrap justify-between gap-2 text-sm text-muted-foreground">
    <span class="max-w-[70%] truncate">{st?.progress || ''}</span>
    <span>{st?.remaining != null ? '≈ ' + fmt(st.remaining) + ' restantes' : ''}</span>
  </div>
  {#if st?.steps?.length}
    <ol id="prog-steps" class="mt-1 flex flex-col gap-1 text-sm">
      {#each st.steps as x}
        <li class={['flex justify-between gap-3 rounded-md px-2 py-1',
                    x.status === 'current' && 'bg-primary/12 font-semibold',
                    x.status === 'done' && 'text-valide',
                    (x.status === 'todo' || x.status === 'skipped') && 'text-muted-foreground']}>
          <span>{x.status === 'done' ? '✓ ' : x.status === 'current' ? '▶ ' : '○ '}{x.label}</span>
          <span class="text-right">{droite(x)}</span>
        </li>
      {/each}
    </ol>
    <div class="text-right font-bold">
      {st.total_remaining != null ? 'Temps restant estimé au total : ≈ ' + fmt(st.total_remaining)
        : 'Temps total : estimation disponible dès que les étapes en cours auront avancé'}
    </div>
  {/if}
</div>

<style>
  .indet { animation: indet 1.2s ease-in-out infinite; }
  @keyframes indet { 0% { left: -30%; } 100% { left: 100%; } }
</style>
