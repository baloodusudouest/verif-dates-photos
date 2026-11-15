<script>
  import { api } from '$lib/api.js'
  import { Button } from '$lib/components/ui/button/index.js'

  let { onconnecte } = $props()
  let motDePasse = $state('')
  let erreur = $state('')
  let envoi = $state(false)

  async function envoyer(e) {
    e.preventDefault()
    envoi = true
    erreur = ''
    try {
      await api.post('/api/connexion', { mot_de_passe: motDePasse })
      onconnecte()
    } catch (err) {
      erreur = err.message
    } finally {
      envoi = false
    }
  }
</script>

<main class="grid min-h-dvh place-items-center px-4">
  <form class="flex w-full max-w-xs flex-col gap-4" onsubmit={envoyer}>
    <h1 class="titre">Éphéméride</h1>
    <p class="text-sm text-muted-foreground">Cet outil modifie les fichiers de la Pellicule.</p>
    <label class="flex flex-col gap-1.5 text-sm">
      Mot de passe (celui de carnet)
      <!-- svelte-ignore a11y_autofocus -->
      <input
        id="mot-de-passe"
        type="password"
        autocomplete="current-password"
        autofocus
        bind:value={motDePasse}
        class="h-10 rounded-lg border border-input bg-transparent px-3 text-base outline-none focus:border-ring"
        required
      />
    </label>
    {#if erreur}<p class="text-sm text-destructive" role="alert">{erreur}</p>{/if}
    <Button type="submit" size="lg" disabled={envoi}>{envoi ? 'Connexion…' : 'Se connecter'}</Button>
  </form>
</main>
