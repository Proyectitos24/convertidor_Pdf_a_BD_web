param(
    [string]$ToolsDirectory = (Join-Path ([IO.Path]::GetTempPath()) 'repaso-postgres17-backup\pgsql\bin'),
    [string]$Destination = (Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'RepasoAlbaranes-respaldos')
)

# Solo lectura remota. Nunca recibe la contrasena como argumento ni la guarda.
$ErrorActionPreference = 'Stop'
$projectRef = 'ijvpemzgnhlhukuwmwfa'
$connection = 'host=aws-0-eu-west-1.pooler.supabase.com port=5432 dbname=postgres user=postgres.ijvpemzgnhlhukuwmwfa sslmode=require connect_timeout=20'
$dumpTool = Join-Path $ToolsDirectory 'pg_dump.exe'
$restoreTool = Join-Path $ToolsDirectory 'pg_restore.exe'
$rolesTool = Join-Path $ToolsDirectory 'pg_dumpall.exe'
foreach ($tool in @($dumpTool, $restoreTool, $rolesTool)) {
    if (-not (Test-Path -LiteralPath $tool)) { throw "No se encuentra la herramienta: $tool" }
}

$backupFolder = Join-Path $Destination ('produccion-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $backupFolder -Force | Out-Null
$archive = Join-Path $backupFolder 'base.dump'
$roles = Join-Path $backupFolder 'roles-sin-contrasenas.sql'
$inventory = Join-Path $backupFolder 'contenido.txt'
$securePassword = Read-Host 'Contrasena de la BASE DE DATOS de convertidorPdf (no la de la tienda)' -AsSecureString
$previousPassword = [Environment]::GetEnvironmentVariable('PGPASSWORD', 'Process')
$previousAppName = [Environment]::GetEnvironmentVariable('PGAPPNAME', 'Process')
$passwordPointer = [IntPtr]::Zero
try {
    $passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
    $env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer)
    $env:PGAPPNAME = 'repaso-respaldo-produccion-solo-lectura'
    Write-Host 'Creando respaldo de produccion. No cierres esta ventana.'
    & $dumpTool --dbname=$connection --no-password --format=custom --no-owner --no-privileges --lock-wait-timeout=10000 --file=$archive
    if ($LASTEXITCODE -ne 0) { throw 'Fallo pg_dump. El respaldo NO esta completo; no aplicar migraciones.' }
    & $rolesTool --dbname=$connection --no-password --roles-only --no-role-passwords --file=$roles
    if ($LASTEXITCODE -ne 0) { throw 'Fallo la copia de roles. No aplicar migraciones.' }
    $archiveContents = & $restoreTool --list $archive
    if ($LASTEXITCODE -ne 0 -or -not $archiveContents) { throw 'No se puede leer el archivo de respaldo.' }
    $archiveContents | Set-Content -LiteralPath $inventory -Encoding utf8
    if (-not ($archiveContents -match 'TABLE DATA public stores')) { throw 'El respaldo no contiene los datos de tiendas esperados.' }
    if (-not ($archiveContents -match 'TABLE DATA public store_app_users')) { throw 'El respaldo no contiene los accesos de la app.' }
    $files = @($archive, $roles, $inventory) | ForEach-Object {
        [pscustomobject]@{ file = [IO.Path]::GetFileName($_); bytes = (Get-Item -LiteralPath $_).Length; sha256 = (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash }
    }
    [pscustomobject]@{
        project = $projectRef
        createdAtUtc = [DateTime]::UtcNow.ToString('o')
        files = $files
        archiveReadable = $true
        restoreTested = $false
        notes = 'Copia de PostgreSQL; no incluye archivos de R2, Storage ni bases de los moviles. Roles sin contrasenas. Lectura del archivo comprobada; restauracion pendiente.'
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $backupFolder 'manifest.json') -Encoding utf8
    Write-Host "Copia creada y legible: $backupFolder"
    Write-Host 'No publiques estos archivos: contienen datos privados.'
    Write-Host 'Todavia falta ensayar la restauracion antes de darla por verificada.'
} finally {
    [Environment]::SetEnvironmentVariable('PGPASSWORD', $previousPassword, 'Process')
    [Environment]::SetEnvironmentVariable('PGAPPNAME', $previousAppName, 'Process')
    if ($passwordPointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer) }
    $securePassword.Dispose()
}
