# Quini 6 - backend de datos

Scraper de `https://www.quini-6-resultados.com.ar/` que corre en GitHub
Actions y publica `data/latest.json` en el repo. La app Android solo lee
ese JSON — nunca scrapea desde el celular del usuario.

## Setup

1. Creá un repo nuevo en GitHub (puede ser público, así los Actions no
   consumen minutos limitados) y subí esta carpeta.
2. No hace falta ningún secret ni token: el workflow usa el token
   automático del repo (`GITHUB_TOKEN`) para commitear.
3. Andá a la pestaña **Actions** del repo y activalo si GitHub te pide
   habilitarlo (pasa en repos nuevos).
4. Corré el workflow manualmente una vez (botón "Run workflow" en
   `Actions > Scrapear resultados Quini 6`) para generar el primer
   `data/latest.json` con historial. Las corridas siguientes son
   automáticas según el cron.

## Cómo lo consume la app Android

GET a:

```
https://raw.githubusercontent.com/TU-USUARIO/TU-REPO/main/data/latest.json
```

Mejor todavía: activá **GitHub Pages** (Settings → Pages → Deploy from
branch → main → carpeta /data) y usá esa URL en vez de raw — es más
estable y con mejor caché.

Estructura del JSON:

```json
{
  "last_updated": "2026-09-05T00:20:00+00:00",
  "proximo_sorteo": { "numero": 3406, "fecha": "2026-09-06", "pozo_acumulado": 12000000000 },
  "sorteos": [
    {
      "numero": 3405,
      "fecha": "2026-09-02",
      "fuente_url": "https://www.quini-6-resultados.com.ar/quini6/sorteo-3405-del-dia-02-09-2026.htm",
      "tradicional": [0, 5, 10, 22, 26, 45],
      "segunda": [2, 3, 16, 22, 24, 44],
      "revancha": [2, 7, 14, 25, 34, 38],
      "siempre_sale": [2, 5, 8, 10, 31, 38]
    }
  ]
}
```

Si un sorteo no tiene alguna modalidad (falló el parseo de esa sección
puntual), esa clave directamente no está presente — la app debe manejar
esto como "dato no disponible", nunca asumir un valor.

## Importante

- Este scraper NUNCA inventa números ni montos. Si no puede validar un
  dato (6 números únicos entre 0 y 45), lo descarta y lo loguea; el
  `data.json` existente no se toca en ese caso.
- El sitio scrapeado aclara en su propio pie de página que "no posee
  vinculación con organismos oficiales" — tu app debe repetir esa
  aclaración y decir que no está afiliada a la Lotería de Santa Fe.
- Los montos de premio (columna "Premio" de la tabla de ganadores) no se
  parsean todavía — de mínima el scraper solo confirma números y pozo
  acumulado del próximo sorteo. Si querés agregar montos de premio, hay
  que sumar un parser más para esa tabla siguiendo el mismo principio:
  validar antes de guardar, nunca inventar si no matchea.
