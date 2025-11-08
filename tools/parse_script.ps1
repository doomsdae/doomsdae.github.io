$path = 'C:\Users\Bruce\Sites\doomsdae\Update-BestNewMovies.ps1'
$errors = $null
[void][System.Management.Automation.Language.Parser]::ParseInput((Get-Content -Raw -Path $path), [ref]$null, [ref]$errors)
if ($errors) {
    foreach ($e in $errors) {
        Write-Output "ERROR: $($e.Message) at $($e.Extent.StartLineNumber):$($e.Extent.StartColumn)"
    }
} else {
    Write-Output "PARSE_OK"
}
