$resourceGroupName = "@lab.CloudResourceGroup(ILL344Final-ResourceGroup).Name"

# Permanently remove Foundry soft-delete records. The Foundry account uses a
# same-named backing Azure ML workspace that otherwise blocks the next lab run.
$foundryAccounts = Get-AzResource `
    -ResourceGroupName $resourceGroupName `
    -ResourceType "Microsoft.CognitiveServices/accounts" | `
    Where-Object { $_.Name -like "ill344-foundry-*" }

foreach ($account in $foundryAccounts) {
    az cognitiveservices account delete `
        --resource-group $resourceGroupName `
        --name $account.Name `
        --only-show-errors

    az cognitiveservices account purge `
        --resource-group $resourceGroupName `
        --name $account.Name `
        --location $account.Location `
        --only-show-errors

    az ml workspace delete `
        --resource-group $resourceGroupName `
        --name $account.Name `
        --permanently-delete `
        --yes `
        --only-show-errors

    Write-Output "Permanently deleted Foundry account and backing workspace: $($account.Name)"
}

# Delete Fabric capacity in the resource group to release regional quota.
$fabricCapacities = Get-AzResource -ResourceGroupName $resourceGroupName -ResourceType "Microsoft.Fabric/capacities"
foreach ($fc in $fabricCapacities) {
    Remove-AzResource -ResourceId $fc.ResourceId -Force
    Write-Output "Deleted Fabric capacity: $($fc.Name)"
}
