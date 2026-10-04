$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$modelDir = Join-Path $env:LOCALAPPDATA 'VariantGate/models/medgemma-1.5-4b'
$receiptDir = Join-Path $projectRoot 'runs/variantgate_llm/medgemma_setup'
$modelName = 'variantgate-medgemma:1.5-4b-q4km'
$weightName = 'medgemma-1.5-4b-it-Q4_K_M.gguf'
$quantRevision = '3855f948626b7ae42bccd082757f15078c53e758'
$originalRevision = '91850547d9f0b2fdd21aa7c5f4f3d1a8a52c243b'
$expectedHash = 'b31becdf4f39561800505514cce67681604fe449d04dd35c8c92fd7848c6d7bd'
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw 'Install uv first.' }
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) { throw 'Install Ollama first.' }
New-Item -ItemType Directory -Force -Path $modelDir,$receiptDir | Out-Null
# This gated read verifies that the locally authenticated account can access
# Google's original model. Authentication is handled by hf, never stored here.
& uvx hf download google/medgemma-1.5-4b-it config.json --revision $originalRevision --local-dir (Join-Path $modelDir 'original_config')
if ($LASTEXITCODE -ne 0) { throw 'Run uvx hf auth login with the account that accepted the MedGemma license, then retry.' }
$previousDisableXet = $env:HF_HUB_DISABLE_XET
try {
    # Avoid the Xet transfer's large RAM buffers on this 16 GB shared-memory PC.
    $env:HF_HUB_DISABLE_XET = '1'
    & uvx hf download unsloth/medgemma-1.5-4b-it-GGUF $weightName --revision $quantRevision --local-dir $modelDir
    if ($LASTEXITCODE -ne 0) { throw 'MedGemma quantized-weight download failed.' }
} finally {
    $env:HF_HUB_DISABLE_XET = $previousDisableXet
}
$weightPath = Join-Path $modelDir $weightName
$actualHash = (Get-FileHash -LiteralPath $weightPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualHash -ne $expectedHash) { throw 'Quantized model weight hash does not match the pinned release.' }
$profileLines = (Get-Content -Raw (Join-Path $projectRoot 'configs/variantgate/medgemma.Modelfile')) -split "`r?`n"
for ($lineIndex = 0; $lineIndex -lt $profileLines.Length; $lineIndex++) {
    if ($profileLines[$lineIndex] -match '^FROM ') { $profileLines[$lineIndex] = 'FROM "' + $weightPath.Replace('\','/') + '"' }
}
$profilePath = Join-Path $receiptDir 'verified.Modelfile'
$profileLines -join "`n" | Set-Content -Encoding ascii $profilePath
& ollama create $modelName -f $profilePath
if ($LASTEXITCODE -ne 0) { throw 'Ollama could not import this Gemma 3 model. Check runtime compatibility before testing.' }
$show = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:11434/api/show' -ContentType 'application/json' -Body (@{model=$modelName} | ConvertTo-Json)
$tags = Invoke-RestMethod 'http://127.0.0.1:11434/api/tags'
$receipt = [ordered]@{
    created_at_utc = [DateTime]::UtcNow.ToString('o')
    original_model = 'google/medgemma-1.5-4b-it'
    original_revision = $originalRevision
    original_access_verified = $true
    quantization_publisher = 'unsloth'
    quantization_repository = 'unsloth/medgemma-1.5-4b-it-GGUF'
    quantization_revision = $quantRevision
    weight_sha256 = $actualHash
    weight_bytes = (Get-Item -LiteralPath $weightPath).Length
    model = $modelName
    model_digest = ($tags.models | Where-Object { $_.name -eq $modelName }).digest
    ollama_version = (Invoke-RestMethod 'http://127.0.0.1:11434/api/version').version
    parameters = $show.parameters
    template = $show.template
    profile_sha256 = (Get-FileHash -LiteralPath $profilePath -Algorithm SHA256).Hash.ToLowerInvariant()
    experiment = 'Text extraction only; vision projector not downloaded'
    license = 'Health AI Developer Foundations terms accepted by user'
}
$receipt | ConvertTo-Json -Depth 5 | Set-Content -Encoding utf8 (Join-Path $receiptDir 'model_receipt.json')
Write-Output "Ready: $modelName at http://127.0.0.1:11434/v1"
