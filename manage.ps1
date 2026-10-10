param(
    [Parameter(Position = 0, Mandatory = $true)]
    [ValidateSet('run', 'setup', 'storage', 'clean', 'cli')]
    [string]$Action,
    [switch]$Dev,
    [switch]$LegacyOnly,
    [ValidateSet('stdio', 'streamable-http')]
    [string]$Transport = 'stdio',
    [int]$Port = 8000,
    [switch]$SitesBackend,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CliArguments
)

$taskArguments = @('run', '--isolated', '--no-project', '--no-cache', '--python', '3.12',
    'python', '-B', (Join-Path $PSScriptRoot 'manage.py'), $Action)
if ($Action -eq 'setup' -and $Dev) { $taskArguments += '--dev' }
if ($Action -eq 'clean' -and $LegacyOnly) { $taskArguments += '--legacy-only' }
if ($Action -eq 'run') { $taskArguments += @('--transport', $Transport, '--port', "$Port") }
if ($Action -eq 'run' -and $SitesBackend) { $taskArguments += '--sites-backend' }
if ($Action -eq 'cli') { $taskArguments += $CliArguments }
& uv @taskArguments
exit $LASTEXITCODE
