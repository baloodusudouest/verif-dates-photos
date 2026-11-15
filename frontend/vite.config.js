import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'
import tailwindcss from '@tailwindcss/vite'

// L'interface compilée est servie par FastAPI depuis app/static.
// Les icônes et le manifeste restent dans app/ (servis sans session) : pas de dossier public ici.
export default defineConfig({
  plugins: [svelte(), tailwindcss()],
  publicDir: false,
  resolve: { alias: { $lib: fileURLToPath(new URL('./src/lib', import.meta.url)) } },
  build: { outDir: '../app/static', emptyOutDir: true },
  server: { proxy: { '/api': 'http://127.0.0.1:8099', '/health': 'http://127.0.0.1:8099',
                     '/icone.svg': 'http://127.0.0.1:8099', '/icone-180.png': 'http://127.0.0.1:8099',
                     '/manifest.webmanifest': 'http://127.0.0.1:8099' } },
})
