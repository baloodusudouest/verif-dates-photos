<script>
  // « Réglages » : ce qui n'est pas un service. Toujours en bas : la version et la déconnexion.
  import Download from '@lucide/svelte/icons/download'
  import { ecran } from '$lib/etat.svelte.js'
  import { Button } from '$lib/components/ui/button/index.js'
  let { version, deconnecter } = $props()
  const e = $derived(ecran.etat)
</script>

<header class="mb-5">
  <p class="etiquette">Éphéméride</p>
  <h1 class="titre">Réglages</h1>
</header>

<section class="mb-3 rounded-2xl border bg-card p-5">
  <h2 class="mb-2 text-lg font-semibold">Règles</h2>
  <dl class="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
    <dt class="text-muted-foreground">Tolérance</dt><dd>{e.tolerance} jour(s) entre la date de la photo et celle du dossier (TOLERANCE_DAYS)</dd>
    <dt class="text-muted-foreground">Vidéos converties</dt>
    <dd>{e.originals_dir ? 'l’original est gardé à part (ORIGINALS_DIR)' : 'l’original est supprimé après vérification du MP4 (régler ORIGINALS_DIR pour le garder)'}</dd>
    <dt class="text-muted-foreground">Lecture seule</dt><dd>{e.lecture_seule ? 'oui (VERIF_LECTURE_SEULE)' : 'non'}</dd>
  </dl>
</section>

<section class="mb-3 rounded-2xl border bg-card p-5">
  <h2 class="mb-1 text-lg font-semibold">Journal</h2>
  <p class="mb-3 text-sm text-muted-foreground">Chaque correction écrite, avec l'ancienne et la nouvelle date.</p>
  <Button variant="outline" href="/api/journal.csv" download="corrections.csv"><Download aria-hidden="true" />Télécharger corrections.csv</Button>
</section>

<p class="mt-6 flex items-center justify-between gap-3 text-sm text-muted-foreground">
  <span>Éphéméride {version || e.version}</span>
  <Button variant="outline" onclick={deconnecter}>Se déconnecter</Button>
</p>
