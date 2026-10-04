param([switch]$SkipPull, [string]$WeightPath)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$sourceModel = 'hf.co/BioMistral/BioMistral-7B-GGUF:Q4_K_M'
$localModel = 'variantgate-biomistral:7b-q4km'
$expectedHash = '0fc1397c3eb2ba46904540accce468479c17aaabe4c51f06665fddc1babef75d'
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    throw 'Install Ollama for Windows from https://ollama.com/download/windows first.'
}
if (-not $WeightPath -and -not $SkipPull) {
    & ollama pull $sourceModel
    if ($LASTEXITCODE -ne 0) { throw 'Ollama model download failed.' }
}
# Verify the actual weight blob against the official Hugging Face LFS digest.
if ($WeightPath) {
    $blobPath = (Resolve-Path -LiteralPath $WeightPath).Path
} else {
    $show = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:11434/api/show' -ContentType 'application/json' -Body (@{model=$sourceModel} | ConvertTo-Json)
    $fromLine = ($show.modelfile -split "`n" | Where-Object { $_ -match '^FROM ' } | Select-Object -First 1)
    if (-not $fromLine) { throw 'Ollama did not return a weight file.' }
    $blobPath = $fromLine.Substring(5).Trim().Trim('"')
}
if (-not (Test-Path -LiteralPath $blobPath -PathType Leaf)) { throw 'Model weight file not found.' }
$actualHash = (Get-FileHash -LiteralPath $blobPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualHash -ne $expectedHash) { throw 'Model weights changed: expected official pinned Q4_K_M digest.' }
$receiptDir = Join-Path $projectRoot 'runs/variantgate_llm/local_setup'
New-Item -ItemType Directory -Force -Path $receiptDir | Out-Null
$profilePath = Join-Path $receiptDir 'verified.Modelfile'
$profileText = Get-Content -Raw (Join-Path $projectRoot 'configs/variantgate/biomistral.Modelfile')
$profileLines = $profileText -split "`r?`n"
$profileLines[0] = 'FROM "' + $blobPath.Replace('\','/') + '"'
$profileLines -join "`n" | Set-Content -Encoding ascii $profilePath
& ollama create $localModel -f $profilePath
if ($LASTEXITCODE -ne 0) { throw 'Ollama model profile creation failed.' }
$profile = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:11434/api/show' -ContentType 'application/json' -Body (@{model=$localModel} | ConvertTo-Json)
$tags = Invoke-RestMethod 'http://127.0.0.1:11434/api/tags'
$version = Invoke-RestMethod 'http://127.0.0.1:11434/api/version'
$receipt = [ordered]@{
    created_at_utc = [DateTime]::UtcNow.ToString('o')
    source = 'https://huggingface.co/BioMistral/BioMistral-7B-GGUF'
    source_revision = 'de8c2dfcead24fd23ccb33f6ca5ff015e9ecdb4b'
    weight_sha256 = $actualHash
    model = $localModel
    model_digest = ($tags.models | Where-Object { $_.name -eq $localModel }).digest
    ollama_version = $version.version
    parameters = $profile.parameters
    cpu = (Get-CimInstance Win32_Processor).Name
    usable_ram_bytes = (Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory
    license = 'Apache-2.0'
    purpose = 'Short research extraction fixtures; no clinical validation'
}
$receipt | ConvertTo-Json -Depth 5 | Set-Content -Encoding utf8 (Join-Path $receiptDir 'model_receipt.json')
Write-Output "Ready: $localModel at http://127.0.0.1:11434/v1"
Write-Output 'Use the short synthetic corpus first. Longer inputs need context-budget validation.'
