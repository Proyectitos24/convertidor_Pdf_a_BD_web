param(
    [Parameter(Mandatory = $true)][string]$BackupFolder,
    [string]$ToolsDirectory = 'C:\Users\migue\AppData\Local\Temp\repaso-postgres17-backup\pgsql\bin'
)

# Ensayo aislado. No tiene URL ni contrasena de produccion.
$ErrorActionPreference = 'Stop'
$manifest = Get-Content -LiteralPath (Join-Path $BackupFolder 'manifest.json') -Raw | ConvertFrom-Json
if ($manifest.project -ne 'ijvpemzgnhlhukuwmwfa') { throw 'Proyecto inesperado en el respaldo.' }
foreach ($file in $manifest.files) {
    $filePath = Join-Path $BackupFolder $file.file
    if ((Get-FileHash -LiteralPath $filePath -Algorithm SHA256).Hash -ne $file.sha256) { throw "Huella incorrecta: $($file.file)" }
}
$temporaryRoot = Join-Path ([IO.Path]::GetTempPath()) ('repaso-ensayo-' + [guid]::NewGuid().ToString('N'))
$cluster = Join-Path $temporaryRoot 'cluster'
New-Item -ItemType Directory -Path $temporaryRoot | Out-Null
$listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
$listener.Start()
$localPort = $listener.LocalEndpoint.Port
$listener.Stop()
$previousPassword = [Environment]::GetEnvironmentVariable('PGPASSWORD', 'Process')
$localPassword = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
$connection = "host=127.0.0.1 port=$localPort dbname=postgres user=restore_admin connect_timeout=10"
$started = $false
$passwordFile = Join-Path $temporaryRoot 'local-password.txt'
function Invoke-LocalSql([string]$sql) {
    $result = & (Join-Path $ToolsDirectory 'psql.exe') --dbname=$connection --no-password -X -q -t -A --set=ON_ERROR_STOP=1 --command=$sql
    if ($LASTEXITCODE -ne 0) { throw 'Fallo una comprobacion en la base temporal.' }
    return $result
}
try {
    Set-Content -LiteralPath $passwordFile -Value $localPassword -Encoding ascii
    try {
        & (Join-Path $ToolsDirectory 'initdb.exe') -D $cluster -U restore_admin --auth=scram-sha-256 --pwfile=$passwordFile --encoding=UTF8 --no-locale *> (Join-Path $temporaryRoot 'init.log')
        $initExitCode = $LASTEXITCODE
    } finally {
        Remove-Item -LiteralPath $passwordFile -ErrorAction SilentlyContinue
    }
    if ($initExitCode -ne 0) { throw "No se pudo crear la base temporal. Revisar $temporaryRoot" }
    $env:PGPASSWORD = $localPassword
    & (Join-Path $ToolsDirectory 'pg_ctl.exe') -D $cluster -l (Join-Path $temporaryRoot 'postgres.log') -o "-h 127.0.0.1 -p $localPort -c max_connections=10" -w -t 30 start
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo iniciar el PostgreSQL temporal.' }
    $started = $true
    Invoke-LocalSql 'CREATE ROLE anon NOLOGIN; CREATE ROLE authenticated NOLOGIN; CREATE ROLE service_role NOLOGIN BYPASSRLS; CREATE ROLE supabase_auth_admin NOLOGIN; CREATE SCHEMA auth; CREATE SCHEMA extensions; CREATE EXTENSION pgcrypto WITH SCHEMA extensions; CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA extensions;' | Out-Null
    # Se recuperan la aplicacion y Auth. Los servicios gestionados de Supabase
    # (Vault/Realtime/Storage y sus extensiones) requieren un entorno Supabase.
    & (Join-Path $ToolsDirectory 'pg_restore.exe') --dbname=$connection --no-password --schema=public --schema=auth --no-owner --no-privileges --exit-on-error --single-transaction (Join-Path $BackupFolder 'base.dump') 2> (Join-Path $temporaryRoot 'restore.log')
    if ($LASTEXITCODE -ne 0) { throw "Fallo la restauracion; revisar $temporaryRoot\restore.log" }
    $fingerprintSql = @'
select jsonb_build_object(
 'stores', (select md5(coalesce(string_agg(to_jsonb(t)::text, '' order by id),'')) from public.stores t),
 'store_app_users', (select md5(coalesce(string_agg(to_jsonb(t)::text, '' order by user_id),'')) from public.store_app_users t),
 'converted_files', (select md5(coalesce(string_agg(to_jsonb(t)::text, '' order by id),'')) from public.converted_files t),
 'shared_files', (select md5(coalesce(string_agg(to_jsonb(t)::text, '' order by id),'')) from public.shared_files t),
 'reservations', (select md5(coalesce(string_agg(to_jsonb(t)::text, '' order by store_id,db_file_name),'')) from public.converted_file_reservations t),
 'auth_users', (select md5(coalesce(string_agg(to_jsonb(t)::text, '' order by id),'')) from auth.users t)
)::text;
'@
    $before = Invoke-LocalSql $fingerprintSql
    $repoRoot = Split-Path $PSScriptRoot -Parent
    foreach ($migration in @('006_incidencias_sync_base.sql', '007_incidencias_sync_rpc.sql')) {
        $migrationPath = Join-Path $repoRoot "sql\$migration"
        & (Join-Path $ToolsDirectory 'psql.exe') --dbname=$connection --no-password -X -q --set=ON_ERROR_STOP=1 --file $migrationPath 2> (Join-Path $temporaryRoot "$migration.log")
        if ($LASTEXITCODE -ne 0) { throw "Fallo la migracion local: $migration" }
    }
    $after = Invoke-LocalSql $fingerprintSql
    if ($before -ne $after) { throw 'Las migraciones alteraron datos anteriores en el ensayo local.' }
    $checks = Invoke-LocalSql "select jsonb_build_object('stores',(select count(*) from public.stores),'accounts',(select count(*) from public.store_app_users),'auth_users',(select count(*) from auth.users),'sync_tables',(select count(*) from pg_tables where schemaname='public' and tablename like 'inc_sync_%'),'rpc',(to_regprocedure('public.inc_sync_apply(uuid,uuid,text,jsonb)') is not null))::text;"
    foreach ($smoke in @('006_smoke_pruebas.sql', '007_smoke_pruebas.sql')) {
        $smokePath = Join-Path $repoRoot "sql\$smoke"
        & (Join-Path $ToolsDirectory 'psql.exe') --dbname=$connection --no-password -X -q --set=ON_ERROR_STOP=1 --file $smokePath *> (Join-Path $temporaryRoot "$smoke.log")
        if ($LASTEXITCODE -ne 0) { throw "Fallo la comprobacion local: $smoke. Revisar $temporaryRoot" }
    }
    [pscustomobject]@{
        project = $manifest.project
        verifiedAtUtc = [DateTime]::UtcNow.ToString('o')
        archiveHashesMatch = $true
        restoredSchemas = @('public', 'auth')
        ownersAndPrivilegesRestored = $false
        managedSupabaseServicesRestored = $false
        migrations006007TestedLocally = $true
        existingRowsUnchanged = $true
        smokeTestsPassed = $true
        counts = ($checks | ConvertFrom-Json)
        localLogs = $temporaryRoot
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $BackupFolder 'verificacion-local.json') -Encoding utf8
    Write-Host 'OK: restaurados public y auth; migraciones y comprobaciones locales correctas; datos anteriores intactos.'
    Write-Host 'No se ha modificado produccion. El ensayo no cubre servicios gestionados, propietarios ni permisos originales de Supabase.'
} finally {
    if ($started) { & (Join-Path $ToolsDirectory 'pg_ctl.exe') -D $cluster -w -t 30 stop -m fast }
    [Environment]::SetEnvironmentVariable('PGPASSWORD', $previousPassword, 'Process')
    $localPassword = $null
}
