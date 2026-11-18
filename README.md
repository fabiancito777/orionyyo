# rgta — lector de códigos en directo + canje automático

Lee la zona derecha de la pantalla, detecta el overlay del código (25 caracteres = Xbox,
12 = PlayStation), lo pasa por OCR y lo escribe en la tienda correspondiente usando un
Chrome ya logueado. El canje corre en hilos aparte: la vigilancia no se para mientras
se está enviando un código.

## Puesta en marcha

1. `pip install -r requirements.txt`
2. `.\run.ps1` — abre tres Chrome con perfiles separados:
   - `profiles\stream` (puerto 9222) con el directo
   - `profiles\xbox` (9223) en xbox.com/es-CO/redeem
   - `profiles\ps` (9224) en store.playstation.com/es-co/pages/latest
3. **Inicia sesión una vez** en las dos tiendas y deja el diálogo de canje abierto
   (o deja la página en el estado en que se ve el campo de código). Los perfiles
   guardan la sesión, así que no habrá que repetirlo.
4. En Twitch pon calidad **Source** y modo de **baja latencia**. El stream debe
   quedar maximizado delante; las tiendas trabajan detrás sin que se vean.
5. `python -m rgta doctor` — comprueba captura, OCR y las tres pestañas.
6. `python -m rgta run` — arranca.

Para probar sin enviar nada: `python -m rgta run --no-submit`.

## Comandos

| Comando | Qué hace |
|---|---|
| `python -m rgta doctor` | Comprueba captura, OCR y pestañas CDP |
| `python -m rgta selftest --save logs/x.png` | Pipeline completo sobre imágenes sintéticas (Xbox y PS) |
| `python -m rgta grab --out logs/frame.png` | Guarda lo que está viendo el detector |
| `python -m rgta tune --image logs/frame.png --show` | Cajas detectadas, OCR y códigos extraídos |
| `python -m rgta probe` | Vuelca inputs/botones reales de las tiendas para ajustar heurísticas |
| `python -m rgta run --no-submit` | Solo leer y registrar |
| `python -m rgta run` | Lectura + canje |

**Teclas:** F8 para, F9 guarda el frame actual (y una copia con las cajas dibujadas).

## Configuración (`config.toml`)

- `region_x0/y0/x1/y1`: zona vigilada, en fracciones del monitor. Por defecto
  solo el lado derecho (x≥45%, y≥28%). Si el overlay aparece a la izquierda,
  baja `region_x0` a 0.
- `fps`: frecuencia de vigilancia (30 por defecto).
- `ocr_backend`: `auto` usa Windows OCR; `rapid` usa RapidOCR si está instalado.
- `charset_hint`: alfabeto conocido de Xbox; **solo** se usa para desempatar
  confusiones (O/0, I/1, S/5...), nunca para descartar caracteres.
- `xbox_*_selector` / `ps_*_selector` (en `config.toml` como `xbox_input`, etc.):
  selectores CSS opcionales. Vacíos = búsqueda por texto del botón/placeholder.

## Cómo funciona

1. **Captura** de la zona con `mss` (~4-8 ms) o `dxcam` si está instalado.
2. **Detección** a media resolución con dos detectores: contraste (top-hat /
   black-hat, funciona con texto claro u oscuro) y color (el código de Xbox va
   en cian y el de PS en rosa, la etiqueta va en blanco). Se unen las cajas y
   se filtran por geometría: 12 caracteres ≈3.5:1, 25 ≈6.5:1 o más.
3. **OCR** del recorte a resolución completa, reescalado y con inversiones de
   polaridad si hace falta. Windows OCR tarda ~10-20 ms.
4. **Normalización**: se quita el prefijo `CODIGO:`, se limpian guiones, se
   exigen exactamente 25 o 12 caracteres y se generan variantes con las
   confusiones típicas.
5. **Canje**: `HTMLInputElement.value` nativo + eventos `input`/`change` (para
   que React lo vea), click en Siguiente/Continuar, y un bucle de 8 ms dentro
   de la página que pulsa el botón final en cuanto aparece.

Si un código falla, se reintentan hasta dos variantes. Después se vuelve a
dejar la tienda lista para el siguiente. Todo queda en `logs/eventos-*.csv`.

## Límites y riesgos

- La latencia propia es ~40-70 ms; el resto (red + validación de Microsoft/Sony)
  no se puede acelerar. Twitch con delay arruina cualquier optimización.
- Automatizar el canje puede ir contra las condiciones de Xbox/PlayStation.
  Los intentos inválidos repetidos pueden provocar un bloqueo temporal.
- El OCR puede fallar en caracteres ambiguos; las variantes y las relecturas
  mitigan, pero no eliminan, ese riesgo.
- El overlay puede cambiar de sitio, tamaño o color: el detector no usa una
  posición fija, pero conviene pasar `grab` + `tune` si cambia de diseño.
