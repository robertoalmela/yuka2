# MercaScore — Cómo se construyó el dataset (full Mercadona + Open Food Facts)

## 📁 Archivos

| Archivo | Propósito |
|---|---|
| `mercascore.html` | **App final** (autocontenida, lista para abrir). 79 KB. |
| `build-dataset.ps1` |.pipeline para (re)generar `data-mercadona-enriched.json`. |
| `run-build.bat` | Lanzador en background del script anterior. |
| `fix-encoding.ps1` | Re-descarga nombres limpios resolviendo mojibake de WebClient. |
| `data-mercadona-raw.json` | 4.386 productos del catálogo Mercadona (id, nombre, precio, categoría). |
| `data-mercadona-ean.json` | Intermedio con EAN + ingredientes (texto). |
| `data-mercadona-enriched.json` | Cruce con OFF (con mojibake). |
| `data-mercadona-enriched-fixed.json` | Nombres re-descargados con `Invoke-WebRequest`. |
| `data-mercadona-enriched-utf8.json` | UTF-8 limpio tras revertir doble encoding. |
| `data-final.json` | Dataset normalizado incrustado en el HTML (137 productos). |
| `merge-dataset.py` | Script Python para mergear catálogo completo con subset enriquecido (sin dependencias). |
| `data-completo.json` | Catálogo completo (4.386 productos) con flags `score_disponible` y `fuente_datos`. |

## 🔁 Cómo regenerar el dataset

Cuando quieras actualizar precios / productos nuevos:

```powershell
# 1) Catálogo base (Mercadona) — ~3 min
$cats = (Invoke-WebRequest "https://tienda.mercadona.es/api/categories/").Content | ConvertFrom-Json
$subs = @(); foreach ($p in $cats.results) { foreach ($s in $p.categories) { $subs += [pscustomobject]@{ parentName=$p.name; subId=$s.id; subName=$s.name } } }
$all = @{}; foreach ($s in $subs) {
  $r = Invoke-WebRequest "https://tienda.mercadona.es/api/categories/$($s.subId)/" -UseBasicParsing
  $j = $r.Content | ConvertFrom-Json
  foreach ($g in $j.categories) { foreach ($x in $g.products) { $all[$x.id] = [pscustomobject]@{ id=$x.id; nombre=$x.display_name; precio=[decimal]$x.price_instructions.bulk_price; categoria=$s.parentName; subcategoria=$s.subName } } }
}
$all.Values | ConvertTo-Json -Depth 3 | Out-File data-mercadona-raw.json -Encoding utf8

# 2) Cruce EAN+OFF — ~20 min (background)
.\run-build.bat
# Ver log en tiempo real:
Get-Content build.log -Wait -Tail 10

# 3) Fix encoding de Mercadona
.\fix-encoding.ps1

# 4) Revertir doble-encoding UTF8
$b = [System.IO.File]::ReadAllBytes("data-mercadona-enriched-fixed.json")
$start = if ($b[0] -eq 0xEF -and $b[1] -eq 0xBB -and $b[2] -eq 0xBF) { 3 } else { 0 }
$wrong = [System.Text.Encoding]::UTF8.GetString($b, $start, $b.Length - $start)
$latin1 = [System.Text.Encoding]::GetEncoding("ISO-8859-1")
$utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText("data-final.json", [System.Text.Encoding]::UTF8.GetString($latin1.GetBytes($wrong)), $utf8)
```

## 🔧 Distribución de fuentes

| Fuente | Qué aporta | Limitación |
|---|---|---|
| **Mercadona API** (`tienda.mercadona.es/api`) | 4.386 productos: nombre, precio, categoría, **EAN**, ingredientes en texto | NO tabla nutricional, NO Nutri-Score, NO NOVA, NO aditivos clasificados |
| **API de Mercadona no es oficial pública**, es el endpoint interno que usa su web | — | Es gratuita y documentada de facto, sin auth |
| **Open Food Facts** (`world.openfoodfacts.org`) | Nutrientes por 100g, Nutri-Score, NOVA, aditivos E-XXX | Cobertura parcial (es colaborativa): solo **137/4.386** productos de Mercadona cruzaron correctamente |

## 📊 Resultado

- **4.386** productos totales descargados de Mercadona.
- **4.372** con EAN asignado (14 sin EAN, falsos productos como cubos de basura).
- **137 productos** con nutri-info verificada y clasificación completa (cobertura 3,1%) — vía OFF por EAN.
- **153 productos** con `score_disponible=true` en `data-completo.json` (137 EAN + 16 por nombre/categoría).
- **4.233 productos** solo con datos de catálogo (nombre, precio, categoría) — `score_disponible=false`.
- Categorías cubiertas: 21 de las categorías padre de Mercadona (Congelados, Panadería, Postres, Charcutería, Salsas, Bebidas...)

## 🐍 Merge script Python (`merge-dataset.py`)

El script `merge-dataset.py` combina el catálogo completo de Mercadona (con EAN) con el subset enriquecido de Open Food Facts:

```bash
python3 merge-dataset.py
# → genera data-completo.json
```

**Entrada:**
- `data-mercadona-ean.json` (4.386 productos con EAN) — o fallback a `data-mercadona-raw.json`
- `data-final.json` (137 productos con datos nutricionales de OFF)

**Salida: `data-completo.json`**
- **Todos** los 4.386 productos del catálogo, cada uno con:
  - `score_disponible`: `true` si tiene datos nutricionales, `false` si solo tiene datos de catálogo
  - `fuente_datos`: `"off"` (Open Food Facts) o `"mercadona"` (solo catálogo)
  - Campos nutricionales (`calorias`, `proteinas`, etc.) como `null` cuando no hay datos
  - `aditivos` siempre como array (nunca `{}` como en la salida de PowerShell)

**Merge:** por EAN primero; fallback a nombre normalizado + categoría (cubre ~16 productos adicionales como distintos formatos del mismo producto que comparten perfil nutricional).

**Sin dependencias externas** — solo usa `json`, `os`, `re`, `unicodedata` de la stdlib de Python.

## ❓ Por qué solo 137 productos

Mercadona **no expone** información nutricional estructurada. Su API interna solo da nombre+precio+EAN+texto ingredientes. La única forma de obtener nutrientes datos es:
1. Cruzar con Open Food Facts por EAN (lo que hicimos — cobertura depende del trabajo colaborativo).
2. Usar **Google Gemini Vision API leyendo las imágenes de los envases** (estrategia del repo `m0wer/mercaapi` — requiere API key + Docker).

## 🚀 Mejora futura

Si quieres cubrir los 4.000+ productos restantes, montar el flujo `m0wer/mercaapi` con Gemini Vision es la mejor opción. Lista de tareas:
1. Clonar `github.com/m0wer/mercaapi`
2. Solicitar `GEMINI_API_KEY` en Google AI Studio (gratuito con quotas)
3. `docker-compose up -d`
4. `python cli.py parse` (descarga Mercadona)
5. `python cli.py process-nutritional-information` (extrae con Gemini Vision de las imágenes zoom)
6. Vuelve a ejecutar el pipeline PowerShell + merge-dataset.py para generar `data-completo.json` actualizado