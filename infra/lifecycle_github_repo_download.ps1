# Set variables
$token = "SECRET"
$targetPath = "C:\Users\LabUser\Desktop\aitour-ILL344"
$tempZip = "$env:TEMP\repo.zip"

# Download as ZIP using GitHub API
$headers = @{
    Authorization = "Bearer $token"
    Accept = "application/vnd.github+json"
}

$zipUrl = "https://api.github.com/repos/microsoft/aitour27-ILL344-turning-data-into-agent-ready-knowledge-with-microsoft-iq/zipball/main"

Invoke-WebRequest -Uri $zipUrl -Headers $headers -OutFile $tempZip -UseBasicParsing

# Extract to temp location
$tempExtract = "$env:TEMP\extracted"
if (Test-Path $tempExtract) {
    Remove-Item $tempExtract -Recurse -Force
}
Expand-Archive -Path $tempZip -DestinationPath $tempExtract -Force

# Find the extracted folder and move to final location
$extractedFolder = Get-ChildItem $tempExtract -Directory | Select-Object -First 1

if (Test-Path $targetPath) {
    Remove-Item $targetPath -Recurse -Force
}
Move-Item $extractedFolder.FullName $targetPath -Force
