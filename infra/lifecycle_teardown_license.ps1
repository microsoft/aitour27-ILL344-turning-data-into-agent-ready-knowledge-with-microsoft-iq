$securityGroups = @("_Skillable-M365Copilot", "_Skillable-CopilotStudio", "_Skillable-E5", "_Skillable Events GHE w/ Copilot")

foreach($secgrp in $securityGroups){

Remove-AzADGroupMember -GroupDisplayName $secgrp -MemberUserPrincipalName "@lab.CloudPortalCredential(User1).Username"

}

$subscriptionId = "@lab.CloudSubscription.Id"
$signInName = "@lab.CloudPortalCredential(User1).Username"
$scope = "/subscriptions/$subscriptionId"
$projectId = "@lab.CloudResourceTemplate(ILL344).Outputs[AZURE_AIFOUNDRY_PROJECT_ID]"

# Delete project first
az resource delete --ids $projectId --force

# Wait until project is gone
while (az resource show --ids $projectId 2>$null) {
    Start-Sleep -Seconds 5
}

# Remove Sub role
$userRoles = @(
    "Search Service Contributor",
    "Search Index Data Reader",
    "Azure AI User",
    "Cognitive Services User",
    "Cognitive Services OpenAI User"
)

foreach ($role in $userRoles) {
    $assignments = az role assignment list `
        --assignee $signInName `
        --role "$role" `
        --scope $scope -o json | ConvertFrom-Json

    foreach ($a in $assignments) {
        az role assignment delete --ids $a.id
    }
}

# Remove Search Service role
$searchMI = "@lab.CloudResourceTemplate(ILL344).Outputs[searchMIPrincipalId]"

$searchRoles = @(
    "Search Index Data Contributor"
)

foreach ($role in $searchRoles) {
    $assignments = az role assignment list `
        --assignee $searchMI `
        --role "$role" `
        --scope $scope -o json | ConvertFrom-Json

    foreach ($a in $assignments) {
        az role assignment delete --ids $a.id
    }
}