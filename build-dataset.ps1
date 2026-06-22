# build-dataset.ps1 — genera data-mercadona-enriched.json
#
# Recorre el catalogo de Mercadona (data-mercadona-raw.json), obtiene el
# EAN de cada producto y los ingredientes, y cruza cada EAN con Open Food
# Facts para enriquecer con: calorías, macros, Nutri-Score, NOVA y aditivos.
#
# Los productos sin informacion nutricional en OFF se EXCLUYEN del output.
# El output se serializa compacto (.json) listo para incrustar en HTML.

$ErrorActionPreference = 'Continue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$raw  = Join-Path $here 'data-mercadona-raw.json'
$intermediate = Join-Path $here 'data-mercadona-ean.json'
$out  = Join-Path $here 'data-mercadona-enriched.json'
$log  = Join-Path $here 'build.log'

# Redirige stdout/stderr al log pero tambien a consola (tee manual)
function Log($msg) {
  $line = "[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $msg
  Write-Host $line
  Add-Content -LiteralPath $log -Value $line -Encoding utf8
}
if (Test-Path $log) { Clear-Content $log -ErrorAction SilentlyContinue }

[System.Net.ServicePointManager]::DefaultConnectionLimit = 100
[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12

if (-not (Test-Path $raw)) { Log "ERROR: falta $raw"; exit 1 }
$products = Get-Content $raw -Raw | ConvertFrom-Json
Log ("Productos en bruto: {0}" -f $products.Count)

# ============================================================
# Helper: paralelo por chunks con WebClient + retries
# ============================================================
function FetchBatch($urls, $concurrency, $retries, $progressEvery, $phaseLabel) {
  $total = $urls.Count
  $results = @()
  for ($i = 0; $i -lt $total; $i++) { $results += $null }
  $idx = 0
  $chunkNo = 0
  $swLocal = [Diagnostics.Stopwatch]::StartNew()
  while ($idx -lt $total) {
    $tasks = [System.Collections.ArrayList]@()
    $clients = [System.Collections.ArrayList]@()
    $chunkIdx = [System.Collections.ArrayList]@()
    for ($k = 0; ($k -lt $concurrency) -and ($idx -lt $total); $k++) {
      $w = New-Object System.Net.WebClient
      $w.Encoding = [System.Text.Encoding]::UTF8
      $w.Headers['User-Agent'] = 'MercaScore/1.0 (educational)'
      $null = $clients.Add($w)
      $null = $chunkIdx.Add([int]$idx)
      $null = $tasks.Add($w.DownloadStringTaskAsync($urls[$idx]))
      $idx++
    }
    try { [System.Threading.Tasks.Task]::WaitAll($tasks.ToArray()) } catch {}
    for ($k = 0; $k -lt $tasks.Count; $k++) {
      $i = [int]$chunkIdx[$k]
      if ($tasks[$k].IsFaulted) {
        $body = $null
        for ($try = 0; $try -lt $retries; $try++) {
          try {
            $rw = New-Object System.Net.WebClient
            $rw.Encoding = [System.Text.Encoding]::UTF8
            $rw.Headers['User-Agent'] = 'MercaScore/1.0 (educational)'
            $body = $rw.DownloadString($urls[$i])
            $rw.Dispose()
            break
          } catch { Start-Sleep -Milliseconds 200 }
        }
        $results[$i] = $body
      } else {
        $results[$i] = $tasks[$k].Result
      }
    }
    $clients.GetEnumerator() | ForEach-Object { try { $_.Dispose() } catch {} }
    $chunkNo++
    if ($progressEvery -gt 0 -and ($chunkNo % $progressEvery -eq 0)) {
      Log ("    {0} | chunk {1} | {2}/{3} | {4:N1}s" -f $phaseLabel, $chunkNo, $idx, $total, $swLocal.Elapsed.TotalSeconds)
    }
  }
  return ,$results
}

$sw = [Diagnostics.Stopwatch]::StartNew()

# ============================================================
# Fase A: Mercadona detalle (EAN)
# ============================================================
Log "Fase A: obteniendo EAN de Mercadona (concurrency=32)..."
$urlsA = @()
foreach ($p in $products) { $urlsA += "https://tienda.mercadona.es/api/products/$($p.id)/" }
$resA = FetchBatch $urlsA 32 2 4 "A"
$eanCount = 0
$idx = 0
foreach ($p in $products) {
  $body = $resA[$idx]; $idx++
  if (-not $body) { continue }
  try {
    $j = $body | ConvertFrom-Json
    if ($j.ean) {
      $p | Add-Member -MemberType NoteProperty -Name ean -Value $j.ean -Force
      $p | Add-Member -MemberType NoteProperty -Name ingredientes -Value $j.nutrition_information.ingredients -Force
      $eanCount++
    }
  } catch {}
}
Log ("  Fase A OK en {0:N1}s | producten con EAN: {1}/{2}" -f $sw.Elapsed.TotalSeconds, $eanCount, $products.Count)

$products | ConvertTo-Json -Depth 3 -Compress | Out-File -LiteralPath $intermediate -Encoding utf8
Log "  Intermedio: $intermediate"

# ============================================================
# Fase B: OFF por EAN (concurrency baja)
# ============================================================
Log "Fase B: cruzando con Open Food Facts (concurrency=10)..."
$indicesConEan = [System.Collections.ArrayList]@()
for ($i = 0; $i -lt $products.Count; $i++) { if ($products[$i].ean) { $null = $indicesConEan.Add($i) } }
$urlsB = [System.Collections.ArrayList]@()
foreach ($i in $indicesConEan) {
  $null = $urlsB.Add("https://world.openfoodfacts.org/api/v2/product/$($products[$i].ean).json?fields=product_name,nutriscore_grade,nova_group,additives_tags,nutriments")
}
$resB = FetchBatch $urlsB 10 1 25 "B"

$done = 0
$withNutri = 0
for ($k = 0; $k -lt $indicesConEan.Count; $k++) {
  $prod = $products[$indicesConEan[$k]]
  $body = $resB[$k]
  if (-not $body) { $prod | Add-Member -MemberType NoteProperty -Name off_match -Value 0 -Force; continue }
  try {
    $oj = $body | ConvertFrom-Json
    if ($oj.status -eq 1 -and $oj.product -and $oj.product.nutriments) {
      $n = $oj.product.nutriments
      $fld = { param($kk) $v = $n.PSObject.Properties[$kk].Value; if ($v -ne $null) { [double]$v } else { 0 } }
      # Comprueba que tenga al menos calorías y proteínas; si no, descartamos
      $hasData = $false
      foreach ($kk in @('energy-kcal_100g','proteins_100g','fat_100g','carbohydrates_100g')) {
        if ($n.PSObject.Properties[$kk] -and $n.$kk -ne $null -and [double]$n.$kk -gt 0) { $hasData = $true; break }
      }
      if (-not $hasData) { $prod | Add-Member -MemberType NoteProperty -Name off_match -Value 0 -Force; continue }
      $prod | Add-Member -MemberType NoteProperty -Name calorias      -Value (& $fld 'energy-kcal_100g')     -Force
      $prod | Add-Member -MemberType NoteProperty -Name proteinas     -Value (& $fld 'proteins_100g')       -Force
      $prod | Add-Member -MemberType NoteProperty -Name carbohidratos -Value (& $fld 'carbohydrates_100g')  -Force
      $prod | Add-Member -MemberType NoteProperty -Name grasas        -Value (& $fld 'fat_100g')           -Force
      $prod | Add-Member -MemberType NoteProperty -Name grasas_sat    -Value (& $fld 'saturated-fat_100g') -Force
      $prod | Add-Member -MemberType NoteProperty -Name azucares      -Value (& $fld 'sugars_100g')        -Force
      $prod | Add-Member -MemberType NoteProperty -Name fibra          -Value (& $fld 'fiber_100g')         -Force
      $prod | Add-Member -MemberType NoteProperty -Name sal           -Value (& $fld 'salt_100g')          -Force
      $prod | Add-Member -MemberType NoteProperty -Name nutriscore    -Value $oj.product.nutriscore_grade  -Force
      $prod | Add-Member -MemberType NoteProperty -Name nova          -Value $oj.product.nova_group        -Force
      $adds = [System.Collections.ArrayList]@()
      if ($oj.product.additives_tags) {
        foreach ($a in $oj.product.additives_tags) {
          $ea = $a -replace '^en:',''
          if ($ea -match '^e') { $null = $adds.Add('E-' + $ea.Substring(1).ToUpper()) }
        }
      }
      $prod | Add-Member -MemberType NoteProperty -Name aditivos -Value $adds.ToArray() -Force
      $prod | Add-Member -MemberType NoteProperty -Name off_match -Value 1 -Force
      $done++
      if ($oj.product.nutriments.'energy-kcal_100g') { $withNutri++ }
    } else {
      $prod | Add-Member -MemberType NoteProperty -Name off_match -Value 0 -Force
    }
  } catch {
    $prod | Add-Member -MemberType NoteProperty -Name off_match -Value 0 -Force
  }
}

$sw.Stop()
Log ("==== FASE B OK ====")
Log ("  Fase B en {0:N1}s total" -f $sw.Elapsed.TotalSeconds)
Log ("  OFF cruces exitosos: {0}/{1} EANs | con nutriments: {2} | cobertura = {3:N1}%" -f $done, $indicesConEan.Count, $withNutri, ($done/$products.Count*100))

# ============================================================
# Filtrar: SOLO productos con off_match=1
# ============================================================
$final = $products | Where-Object { $_.off_match -eq 1 } | ForEach-Object {
  [pscustomobject]@{
    nombre=$_.nombre; categoria=$_.categoria; subcategoria=$_.subcategoria;
    precio=$_.precio; ean=$_.ean;
    calorias=$_.calorias; proteinas=$_.proteinas; carbohidratos=$_.carbohidratos;
    grasas=$_.grasas; grasas_sat=$_.grasas_sat; azucares=$_.azucares;
    fibra=$_.fibra; sal=$_.sal;
    nutriscore=$_.nutriscore; nova=$_.nova; aditivos=$_.aditivos
  }
}
Log ("Dataset FINAL: {0} productos (excluidos {1} sin nutri-info OFF)" -f $final.Count, ($products.Count - $final.Count))

$final | ConvertTo-Json -Depth 3 -Compress | Out-File -LiteralPath $out -Encoding utf8
$fi = Get-Item $out
Log ("Guardado: {0} ({1:N1} KB)" -f $out, ($fi.Length/1KB))
Log "BUILD COMPLETADO."