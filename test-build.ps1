# test build (WebClient, sin generic-list, paralelismo real)
$ErrorActionPreference = 'Continue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$raw  = Join-Path $here 'data-mercadona-raw.json'

[System.Net.ServicePointManager]::DefaultConnectionLimit = 100
[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12

$products = Get-Content $raw -Raw | ConvertFrom-Json
$batch = $products | Select-Object -First 200
Write-Host ("Productos prueba: {0}" -f $batch.Count)

$sw = [Diagnostics.Stopwatch]::StartNew()

# Fase A: una WebClient por peticion, todas en paralelo
[System.Collections.ArrayList]$tasksA = @()
[System.Collections.ArrayList]$clientsA = @()
foreach ($p in $batch) {
  $w = New-Object System.Net.WebClient
  $w.Headers['User-Agent'] = 'MercaScore/1.0 (educational)'
  $null = $clientsA.Add($w)
  $null = $tasksA.Add($w.DownloadStringTaskAsync("https://tienda.mercadona.es/api/products/$($p.id)/"))
}
[System.Threading.Tasks.Task]::WaitAll($tasksA.ToArray())
Write-Host ("Fase A (Mercadona detalle) en {0:N1}s" -f $sw.Elapsed.TotalSeconds)

# Fase B: para los que tienen EAN, lanzar queries a OFF
[System.Collections.ArrayList]$tasksB = @()
[System.Collections.ArrayList]$idxForB = @()
[System.Collections.ArrayList]$clientsB = @()
for ($i = 0; $i -lt $batch.Count; $i++) {
  $prod = $batch[$i]
  try {
    if ($tasksA[$i].IsFaulted) { $prod | Add-Member -MemberType NoteProperty -Name off_match -Value 0 -Force; continue }
    $body = $tasksA[$i].Result
    $j = $body | ConvertFrom-Json
    if ($j.ean) {
      $prod | Add-Member -MemberType NoteProperty -Name ean -Value $j.ean -Force
      $prod | Add-Member -MemberType NoteProperty -Name ingredientes -Value $j.nutrition_information.ingredients -Force
      $w2 = New-Object System.Net.WebClient
      $w2.Headers['User-Agent'] = 'MercaScore/1.0 (educational)'
      $null = $clientsB.Add($w2)
      $null = $idxForB.Add($i)
      $null = $tasksB.Add($w2.DownloadStringTaskAsync("https://world.openfoodfacts.org/api/v2/product/$($j.ean).json?fields=product_name,nutriscore_grade,nova_group,additives_tags,nutriments"))
    } else {
      $prod | Add-Member -MemberType NoteProperty -Name off_match -Value 0 -Force
    }
  } catch { $prod | Add-Member -MemberType NoteProperty -Name off_match -Value 0 -Force }
}

if ($tasksB.Count -gt 0) {
  try { [System.Threading.Tasks.Task]::WaitAll($tasksB.ToArray()) } catch {}
}
Write-Host ("Fase B (OFF por EAN) en {0:N1}s total" -f $sw.Elapsed.TotalSeconds)

# Procesar resultados OFF
$done = 0
for ($k = 0; $k -lt $tasksB.Count; $k++) {
  $prod = $batch[$idxForB[$k]]
  try {
    if ($tasksB[$k].IsFaulted) { $prod | Add-Member -MemberType NoteProperty -Name off_match -Value 0 -Force; continue }
    $oj = $tasksB[$k].Result | ConvertFrom-Json
    if ($oj.status -eq 1 -and $oj.product -and $oj.product.nutriments) {
      $n = $oj.product.nutriments
      $fld = { param($k) $v = $n.PSObject.Properties[$k].Value; if ($v -ne $null) { [double]$v } else { 0 } }
      $prod | Add-Member -MemberType NoteProperty -Name calorias      -Value (& $fld 'energy-kcal_100g')    -Force
      $prod | Add-Member -MemberType NoteProperty -Name proteinas     -Value (& $fld 'proteins_100g')      -Force
      $prod | Add-Member -MemberType NoteProperty -Name carbohidratos -Value (& $fld 'carbohydrates_100g') -Force
      $prod | Add-Member -MemberType NoteProperty -Name grasas        -Value (& $fld 'fat_100g')          -Force
      $prod | Add-Member -MemberType NoteProperty -Name grasas_sat    -Value (& $fld 'saturated-fat_100g')-Force
      $prod | Add-Member -MemberType NoteProperty -Name azucares      -Value (& $fld 'sugars_100g')       -Force
      $prod | Add-Member -MemberType NoteProperty -Name fibra          -Value (& $fld 'fiber_100g')        -Force
      $prod | Add-Member -MemberType NoteProperty -Name sal           -Value (& $fld 'salt_100g')         -Force
      $prod | Add-Member -MemberType NoteProperty -Name nutriscore    -Value $oj.product.nutriscore_grade -Force
      $prod | Add-Member -MemberType NoteProperty -Name nova          -Value $oj.product.nova_group       -Force
      $adds = @()
      if ($oj.product.additives_tags) { foreach ($a in $oj.product.additives_tags) { $adds += ($a -replace '^en:','' -replace '^e','E-') } }
      $prod | Add-Member -MemberType NoteProperty -Name aditivos -Value $adds -Force
      $prod | Add-Member -MemberType NoteProperty -Name off_match -Value 1 -Force
      $done++
    } else { $prod | Add-Member -MemberType NoteProperty -Name off_match -Value 0 -Force }
  } catch { $prod | Add-Member -MemberType NoteProperty -Name off_match -Value 0 -Force }
}

$sw.Stop()
Write-Host "---- Resumen ----"
Write-Host ("Cruce OFF exitoso: {0}/{1} ({2:N0}%) | Total: {3:N1}s" -f $done, $batch.Count, ($done/$batch.Count*100), $sw.Elapsed.TotalSeconds)
$batch | Where-Object { $_.off_match -eq 1 } | Select-Object -First 5 | ForEach-Object { Write-Host ("  OK  {0} | ean={1} | ns={2} | nova={3} | adds={4}" -f $_.nombre, $_.ean, $_.nutriscore, $_.nova, (($_.aditivos) -join ',')) }
$batch | Where-Object { $_.off_match -eq 0 } | Select-Object -First 5 | ForEach-Object { Write-Host ("  NO  {0} | ean={1}" -f $_.nombre, $(if ($_.ean) { $_.ean } else { 'sin-ean' })) }