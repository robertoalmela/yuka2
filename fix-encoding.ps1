# fix-encoding.ps1 — re-descarga los 137 productos y reconstruye el enriched limpio.

$ErrorActionPreference = 'Continue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$eanFile = Join-Path $here 'data-mercadona-ean.json'
$enrichedFile = Join-Path $here 'data-mercadona-enriched.json'
$out = Join-Path $here 'data-mercadona-enriched-fixed.json'

[System.Net.ServicePointManager]::DefaultConnectionLimit = 100
[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12

# Map EAN -> ID desde el ean.json (EAN e ID son ASCII, no eston mojibake).
$eanData = Get-Content $eanFile -Raw -Encoding UTF8 | ConvertFrom-Json
$eanToId = @{}
foreach ($p in $eanData) { if ($p.ean) { $eanToId[$p.ean] = $p.id } }
Write-Host ("Productos en ean file: {0} | con EAN: {1}" -f $eanData.Count, $eanToId.Count)

# Carga los 137 productos enriched.
$enriched = Get-Content $enrichedFile -Raw -Encoding UTF8 | ConvertFrom-Json
Write-Host ("Productos en enriched file: {0}" -f $enriched.Count)

# Para cada enriched, re-descarga desde Mercadona con Invoke-WebRequest (respeta charset HTTP).
$fixed = [System.Collections.ArrayList]@()
$idx = 0; $total = $enriched.Count
foreach ($p in $enriched) {
  $idx++
  try {
    $id = $eanToId[$p.ean]
    if (-not $id) { Write-Host "  sin ID para EAN $($p.ean) ($($p.nombre))" -continue }
    # Invoke-WebRequest respeta el Content-Type charset header
    $r = Invoke-WebRequest -Uri "https://tienda.mercadona.es/api/products/$id/" -UseBasicParsing -TimeoutSec 20
    $j = $r.Content | ConvertFrom-Json
    # Categoría: buscar en eanFile por id
    $categ = ($eanData | Where-Object { $_.id -eq $id } | Select-Object -First 1).categoria
    $subcateg = ($eanData | Where-Object { $_.id -eq $id } | Select-Object -First 1).subcategoria
    # Construye objeto final con nombre limpio desde Mercadona, nutri-info desde OFF
    $obj = [pscustomobject]@{
      nombre       = $j.display_name
      categoria    = $categ
      subcategoria = $subcategoria
      precio       = [double]$j.price_instructions.unit_price
      ean          = $j.ean
      calorias     = $p.calorias
      proteinas    = $p.proteinas
      carbohidratos= $p.carbohidratos
      grasas       = $p.grasas
      grasas_sat   = $p.grasas_sat
      azucares     = $p.azucares
      fibra        = $p.fibra
      sal          = $p.sal
      nutriscore   = $p.nutriscore
      nova         = $p.nova
      aditivos     = $p.aditivos
    }
    $fixed.Add($obj) | Out-Null
  } catch {
    Write-Host "  ERROR $($p.ean): $($_.Exception.Message)"
  }
  if ($idx % 25 -eq 0 -or $idx -eq $total) { Write-Host ("  {0}/{1}" -f $idx, $total) }
}

Write-Host ("Procesados: {0} / Esperados: {1}" -f $fixed.Count, $enriched.Count)
$fixed | ConvertTo-Json -Depth 3 -Compress | Out-File -LiteralPath $out -Encoding utf8
$fi = Get-Item $out
Write-Host ("Guardado: {0} ({1:N1} KB)" -f $out, ($fi.Length/1KB))
Write-Host "Muestra fixeada:"
$fixed | Select-Object -First 5 | ForEach-Object { Write-Host ("  - {0} | {1} | NS={2}" -f $_.nombre, $_.categoria, $_.nutriscore) }