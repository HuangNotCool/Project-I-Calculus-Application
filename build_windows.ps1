param(
    [switch]$SkipClean
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot 'env\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python environment not found: $Python"
}

Push-Location $ProjectRoot
try {
    & $Python -c "import PyInstaller, waitress, webview"
    if ($LASTEXITCODE -ne 0) {
        throw 'Missing build dependencies. Run: .\env\Scripts\python.exe -m pip install -r requirements-build.txt'
    }

    $Arguments = @('-m', 'PyInstaller', '--noconfirm')
    if (-not $SkipClean) {
        $Arguments += '--clean'
    }
    $Arguments += 'GiaiTichHUST.spec'

    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }

    Write-Host ''
    Write-Host 'Packaged application created:' -ForegroundColor Green
    Write-Host (Join-Path $ProjectRoot 'dist\GiaiTichHUST\GiaiTichHUST.exe')
}
finally {
    Pop-Location
}
