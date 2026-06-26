$json = Get-Content -Raw 'C:\Users\josh.rubenstein\.claude\projects\C--Dev-Agents-Unreal-Spreadsheet-Importer\46f01740-28ca-49f8-8b07-3ee429c2281b\tool-results\mcp-hodor-hodor_execute_tool-1782433762574.txt' | ConvertFrom-Json
$inner = $json[0].text | ConvertFrom-Json
$rows = $inner.values
$tsv = $rows | ForEach-Object { $_ -join "`t" }
$tsv | Out-File -FilePath 'D:\projects\DevBulldog\FortniteGame\Saved\TSVImport\husky_currencies.tsv' -Encoding UTF8
Write-Host "Wrote $($rows.Count) rows"
