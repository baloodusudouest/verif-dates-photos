<script>
  // Navigation commune (socle) : rail à gauche sur PC, pilule d'icônes en bas sur iPhone.
  // Entrées : À traiter (tuiles), Corrigées, Connexion (une tuile par service), Réglages.
  // Les adresses #a-traiter, #corrigees… sont celles de la 2.x : un favori garde sa section.
  import { onMount } from 'svelte'
  import Inbox from '@lucide/svelte/icons/inbox'
  import CircleCheck from '@lucide/svelte/icons/circle-check'
  import Network from '@lucide/svelte/icons/network'
  import SlidersHorizontal from '@lucide/svelte/icons/sliders-horizontal'
  import { api } from '$lib/api.js'
  import { ecran } from '$lib/etat.svelte.js'
  import Connexion from './pages/Connexion.svelte'
  import ATraiter from './pages/ATraiter.svelte'
  import Corrigees from './pages/Corrigees.svelte'
  import Services from './pages/Services.svelte'
  import Reglages from './pages/Reglages.svelte'

  const onglets = [
    { page: 'a-traiter', nom: 'À traiter', icone: Inbox },
    { page: 'corrigees', nom: 'Corrigées', icone: CircleCheck },
    { page: 'connexion', nom: 'Connexion', icone: Network },
    { page: 'reglages', nom: 'Réglages', icone: SlidersHorizontal },
  ]

  let connecte = $state(null) // null : on ne sait pas encore
  let session = $state({ version: '', lecture_seule: false })
  let page = $state(lirePage())

  function lirePage() {
    const p = location.hash.replace(/^#\/?/, '')
    return onglets.some((o) => o.page === p) ? p : 'a-traiter'
  }

  async function verifier() {
    try {
      session = await api.get('/api/session')
      connecte = session.connecte
    } catch {
      connecte = false
    }
    if (connecte) ecran.charger()
  }

  async function deconnecter() {
    try { await api.post('/api/deconnexion') } catch { /* déjà déconnecté */ }
    connecte = false
  }

  ecran.ondeconnecte = () => (connecte = false)
  onMount(verifier)

  const pastille = $derived(ecran.etat?.a_traiter ? (ecran.etat.a_traiter < 1000 ? ecran.etat.a_traiter : '999+') : '')
</script>

<svelte:window onhashchange={() => (page = lirePage())} />

{#if connecte === false}
  <Connexion onconnecte={verifier} />
{:else if connecte}
  <div class="md:pl-52">
    <nav
      aria-label="Sections"
      class="rail fixed bottom-[calc(0.75rem+env(safe-area-inset-bottom))] left-1/2 z-20 flex -translate-x-1/2 items-center gap-1 rounded-full border bg-card/80 p-1.5 shadow-lg backdrop-blur-md
             md:inset-y-0 md:left-0 md:w-52 md:translate-x-0 md:flex-col md:items-stretch md:gap-1 md:rounded-none md:border-0 md:border-r md:bg-background md:p-4 md:shadow-none md:backdrop-blur-none"
    >
      <span class="mb-4 hidden px-2 text-xl font-light tracking-tight md:block">Éphéméride</span>
      {#each onglets as o}
        {@const Icone = o.icone}
        <a
          href={'#' + o.page}
          aria-label={o.nom}
          aria-current={page === o.page ? 'page' : undefined}
          class="flex h-11 items-center gap-2 rounded-full px-4 text-sm font-medium text-muted-foreground transition-colors
                 hover:text-foreground aria-[current=page]:bg-foreground aria-[current=page]:text-background
                 md:h-10 md:rounded-lg md:px-3 md:aria-[current=page]:bg-secondary md:aria-[current=page]:text-foreground"
        >
          <Icone class="size-[1.15rem]" aria-hidden="true" />
          <span class="sr-only md:not-sr-only md:flex-1">{o.nom}</span>
          {#if o.page === 'a-traiter' && pastille}
            <span class="hidden rounded-full bg-primary px-1.5 text-[0.7rem] leading-5 font-bold text-primary-foreground md:inline">{pastille}</span>
          {/if}
        </a>
      {/each}
    </nav>

    <main
      class="mx-auto max-w-[90rem] px-[max(1rem,env(safe-area-inset-left))] pt-[calc(1.25rem+env(safe-area-inset-top))] pb-[calc(6rem+env(safe-area-inset-bottom))] md:px-8 md:pt-8 md:pb-10"
    >
      {#if ecran.erreur}
        <p class="mb-4 rounded-xl border px-4 py-2 text-sm ko" role="alert">{ecran.erreur}</p>
      {/if}
      {#if !ecran.etat}
        <p class="text-muted-foreground">Chargement…</p>
      {:else if page === 'corrigees'}
        <Corrigees />
      {:else if page === 'connexion'}
        <Services />
      {:else if page === 'reglages'}
        <Reglages version={session.version} {deconnecter} />
      {:else}
        <ATraiter />
      {/if}
    </main>
  </div>
{/if}
