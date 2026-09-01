<#
.SYNOPSIS
    Seeds sample emails into the lab user's mailbox via Microsoft Graph API.
.DESCRIPTION
    Uses the service principal's app-only token to send emails from the lab user
    to themselves. Requires Mail.Send application permission (admin-consented).
.PARAMETER UserUpn
    The lab user's UPN (email) to send messages to/from.
.PARAMETER TenantId
    The Entra tenant ID for token acquisition.
.PARAMETER ClientId
    The service principal's app/client ID.
.PARAMETER ClientSecret
    The service principal's client secret.
#>
param(
    [Parameter(Mandatory)][string]$UserUpn,
    [Parameter(Mandatory)][string]$TenantId,
    [Parameter(Mandatory)][string]$ClientId,
    [Parameter(Mandatory)][string]$ClientSecret
)

$ErrorActionPreference = "Stop"

function Log {
    param([string]$msg)
    Write-Output "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $msg"
}

# Get an app-only token for Microsoft Graph
function Get-GraphToken {
    $body = @{
        grant_type    = "client_credentials"
        client_id     = $ClientId
        client_secret = $ClientSecret
        scope         = "https://graph.microsoft.com/.default"
    }
    $response = Invoke-RestMethod -Method POST `
        -Uri "https://login.microsoftonline.com/$TenantId/oauth2/v2.0/token" `
        -ContentType "application/x-www-form-urlencoded" `
        -Body $body
    return $response.access_token
}

# Send an email from the user to themselves
function Send-MailMessage {
    param(
        [string]$Token,
        [string]$UserId,
        [hashtable]$Mail
    )
    $headers = @{
        Authorization  = "Bearer $Token"
        "Content-Type" = "application/json; charset=utf-8"
    }
    $url = "https://graph.microsoft.com/v1.0/users/$UserId/sendMail"
    $payload = @{ message = $Mail; saveToSentItems = $false } | ConvertTo-Json -Depth 10
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        try {
            Invoke-RestMethod -Method POST -Uri $url -Headers $headers -Body ([System.Text.Encoding]::UTF8.GetBytes($payload)) -TimeoutSec 60
            return $true
        } catch {
            $errBody = ""
            if ($_.Exception.Response) {
                $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
                $errBody = $reader.ReadToEnd()
                $reader.Close()
            }
            Log "ERROR attempt $attempt ($($_.Exception.Response.StatusCode)): $errBody"
            if ($attempt -lt 3) {
                Start-Sleep -Seconds 5
            }
        }
    }
    return $false
}

# ============================================================
# Email definitions
# ============================================================

$emails = @(
    @{
        subject      = "CALD-201 transfer checkpoint - equipment mapping approval needed"
        from         = @{ emailAddress = @{ name = "Elena Morris"; address = $UserUpn } }
        toRecipients = @(
            @{ emailAddress = @{ name = "Me"; address = $UserUpn } }
        )
        body         = @{
            contentType = "HTML"
            content     = @"
<p>Hi,</p>
<p>Summit Dose has completed the controlled document intake for <strong>CALD-201</strong> and sent its draft equipment map. One action remains open: Caldova Process Engineering must approve the proposed high-shear granulator equivalency by <strong>4 February 2027</strong>.</p>
<p>If we miss that approval date, Summit Dose says the engineering batch slot will move from 15 February to 1 March. That would put the 31 March technology-transfer package at risk.</p>
<p>Owner: Daniel Cho, Process Engineering. Please confirm whether he can close the equivalency review this week.</p>
<p>Thanks,<br/>Elena Morris<br/>Technology Transfer Lead</p>
"@
        }
    },
    @{
        subject      = "CALD-201 testing subcontractor - Piedmont evidence incomplete"
        from         = @{ emailAddress = @{ name = "Noor Patel"; address = $UserUpn } }
        toRecipients = @(
            @{ emailAddress = @{ name = "Me"; address = $UserUpn } }
        )
        body         = @{
            contentType = "HTML"
            content     = @"
<p>Hi,</p>
<p>Supplier Quality reviewed Summit Dose's qualification package for <strong>Piedmont Analytical Services</strong>, the proposed compendial microbiology testing subcontractor for CALD-201.</p>
<p>The package is still missing the closure evidence from Piedmont's 2026 data-integrity audit and the method-transfer validation summary. <strong>Caldova has not granted written subcontractor approval.</strong></p>
<p>Summit Dose owes both documents by 3 February. Supplier Quality will make the approval decision at the 6 February review. Until then, no release testing may be assigned to Piedmont.</p>
<p>Thanks,<br/>Noor Patel<br/>Supplier Quality Manager</p>
"@
        }
    },
    @{
        subject      = "Decision Friday: protect the CALD-201 April delivery"
        from         = @{ emailAddress = @{ name = "Luis Ortega"; address = $UserUpn } }
        toRecipients = @(
            @{ emailAddress = @{ name = "Me"; address = $UserUpn } }
        )
        body         = @{
            contentType = "HTML"
            content     = @"
<p>Hi team,</p>
<p>Summit Dose reported that planned coating-line maintenance now overlaps the <strong>CALD201-SD-PPQ-01</strong> window. They can still target the 30 April delivery if Caldova authorizes a reserved weekend coating slot by Friday.</p>
<p>Without that slot, Summit Dose's current estimate moves PPQ-01 release to 12 May. The alternative would also require us to rephase PPQ-02.</p>
<p>Thursday's supply review needs to decide whether to authorize the weekend slot or accept the delay and start formal change control. This email is a planning update, not an amendment to the approved purchase order.</p>
<p>Thanks,<br/>Luis Ortega<br/>Supply Planning Director</p>
"@
        }
    }
)

# ============================================================
# Main
# ============================================================

Log "Sending $($emails.Count) emails to mailbox: $UserUpn"

$token = Get-GraphToken
Log "Acquired Graph API token (length: $($token.Length))"

foreach ($email in $emails) {
    $ok = Send-MailMessage -Token $token -UserId $UserUpn -Mail $email
    if ($ok) {
        Log "Sent: $($email.subject)"
    } else {
        Log "FAILED: $($email.subject)"
    }
    Start-Sleep -Seconds 1
}

Log "Email seeding complete!"
