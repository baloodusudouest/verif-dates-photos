<script>
  // Une photo à traiter : miniature, dates lues, date proposée ou boutons de choix, case « Corriger ».
  import { ecran } from '$lib/etat.svelte.js'
  import { classeTag, coherente, periode } from '$lib/outils.js'

  let { photo } = $props()
  const e = $derived(ecran.etat)
  const c = $derived(ecran.choix[photo.id])
  const coh = $derived(coherente(photo, c?.date, e.tolerance))

  function choisir(v) {
    c.date = v
    c.coche = true
  }
  const jma = (iso) => iso.slice(8, 10) + '/' + iso.slice(5, 7) + '/' + iso.slice(0, 4)
</script>

<article class="card flex flex-col overflow-hidden rounded-2xl border bg-card" data-status={photo.status}>
  <img loading="lazy" src={'/api/miniature/' + encodeURIComponent(photo.id)} alt="" class="h-[170px] w-full bg-secondary object-cover" />
  <div class="flex flex-col gap-1 p-3 text-[13px]">
    <div class="font-semibold break-all">{photo.name}</div>
    <div>
      <span class="tag {classeTag(photo.status)}">{photo.status}</span>
      <span class="text-muted-foreground">{photo.kind}{photo.gap ? ` · ${photo.gap} j d'écart` : ''}</span>
    </div>
    <div>Fichier : {photo.date || '—'} {#if photo.source}<span class="text-muted-foreground">({photo.source})</span>{/if}</div>
    {#if photo.name_date}<div>Nom du fichier : {photo.name_date}</div>{/if}
    {#if photo.immich_date}<div class="text-muted-foreground">Immich : {photo.immich_date}</div>{/if}
    <div class="text-muted-foreground">Voisines : {photo.voisins ? photo.voisins.label : "pas assez d'autres photos datées dans le dossier"}</div>
    {#if photo.status === 'abîmé'}
      <div class="ko font-semibold">Fichier illisible ({photo.erreur}) : rien ne sera modifié. À remplacer depuis une sauvegarde.</div>
    {/if}
    {#if photo.vrai_ext}
      <div class="ko">⚠ Extension incorrecte : le contenu est un {photo.vrai_ext.toUpperCase()}. Le fichier sera renommé en .{photo.vrai_ext} avant d'écrire la date (Immich le verra comme un nouveau fichier). Si ce nom est déjà pris : nom alternatif, ou mise de côté si c'est un doublon identique.</div>
    {/if}
    {#if photo.alerte && photo.status !== 'abîmé'}<div class="text-muted-foreground">exiftool : {photo.alerte}</div>{/if}
    {#if photo.convertir}
      <div class="ko">⚠ Format où la date ne peut pas être écrite : la vidéo sera convertie en .mp4 (sans perte si possible), puis l'original {e.originals_dir ? 'déplacé' : 'supprimé'}. Immich la verra comme un nouveau fichier.</div>
    {/if}
    {#if photo.deja}<div class="ko font-semibold">Déjà corrigée le {photo.deja}, écart toujours présent</div>{/if}
    {#if photo.echec}<div class="ko font-semibold">✗ échec — {photo.echec}</div>{/if}

    {#if photo.status !== 'abîmé' && c}
      {#if photo.status === 'à trancher' || !photo.proposed}
        <div class="text-muted-foreground">Choisis la date à écrire :</div>
        <div class="flex flex-wrap gap-1.5">
          {#if photo.choice_folder}<button type="button" class="champ text-xs hover:bg-secondary" onclick={() => choisir(photo.choice_folder)}>Dossier ({photo.ref})</button>{/if}
          {#if photo.choice_name}<button type="button" class="champ text-xs hover:bg-secondary" onclick={() => choisir(photo.choice_name)}>Nom ({photo.name_date.slice(0, 10)})</button>{/if}
          {#if photo.choice_voisins}<button type="button" class="champ text-xs hover:bg-secondary" onclick={() => choisir(photo.choice_voisins)}>Voisines ({jma(photo.choice_voisins)})</button>{/if}
        </div>
      {:else}
        <div class="text-muted-foreground">Date proposée ({photo.proposed_src}) :</div>
      {/if}
      <input type="datetime-local" step="1" class="champ w-full" aria-label={'Date à écrire pour ' + photo.name} bind:value={c.date} />
      {#if coh === true}
        <div class="text-xs font-semibold text-valide">✓ cohérente avec les autres photos du dossier</div>
      {:else if coh === false}
        <div class="attention text-xs font-semibold">⚠ hors de la période des autres photos ({periode(photo)})</div>
      {/if}
      <label class="mt-1 flex items-center gap-1.5 font-semibold">
        <input type="checkbox" name="sel" value={photo.id} bind:checked={c.coche} class="size-4 accent-[var(--app)]" /> Corriger
      </label>
    {/if}
  </div>
</article>
