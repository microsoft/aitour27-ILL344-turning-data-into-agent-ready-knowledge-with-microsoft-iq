## Summary

Reverts Parts 3 and 4 to the working `2026-05-01-preview` implicit Work IQ model and stops running the Entra-app provisioning during deployment. The `2026-08-01-preview` Entra-app model (#8) is blocked by a lab service-principal permission gap, so this restores a working lab while that is sorted with Skillable.

## Why

Work IQ retrieval already worked end to end on the old implicit model (a real `workIQ` reference was returned). The new model needs the deploy service principal to create an Entra app registration, which currently fails with Microsoft Graph `Directory_ObjectNotFound` ("Unable to read the company information from the directory") because the SP lacks directory-read permission. Until Skillable grants that, the old model is the reliable path.

## Changes

- `src/notebooks/part3-work-iq-to-kb.ipynb` and `part4-work-iq-fabric-iq-to-kb.ipynb`: restored to the pre-#8 implicit model (`InteractiveBrowserCredential` delegated Search token passed as `query_source_authorization`, `WorkIQKnowledgeSource` without `entraAppAuthentication`).
- `infra/hooks/postprovision.ps1` / `.sh`: removed the `create-workiq-entra.py` provisioning call.

## Kept (harmless, ready for re-enable)

`infra/create-workiq-entra.py`, `infra/delete-workiq-entra.py`, and the `SEARCH_SERVICE_PRINCIPAL_ID` Bicep output remain in the repo so the new model can be re-enabled once the SP permission is granted.

## Skillable action

Remove the Work IQ provisioning block from the Skillable "Bicep Deployment" lifecycle action to match (it is not in the repo hooks anymore).

## Validation

- Notebooks are valid JSON; `postprovision.ps1` parses.
