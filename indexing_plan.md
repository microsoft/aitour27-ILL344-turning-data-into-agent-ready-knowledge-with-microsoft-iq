# Caldova indexing plan

## Objective

Replace the pre-indexed Zava `hrdocs` and `healthdocs` data with two portable Caldova Azure AI Search indexes while keeping attendee setup fast and reliable.

The ILL344 repository will own the indexing and export workflow. [`pamelafox/aitour27-caldova-data`](https://github.com/pamelafox/aitour27-caldova-data) remains the authoritative source for Caldova documents and structured data. The LTG242 repository can be consulted as a reference for its proven Content Understanding configuration, but ILL344 will not depend on it at build time or runtime.

## Phase 1: Build and restore the Caldova indexes

Complete the infrastructure and script work in this phase before changing the notebooks. Phase 1 produces the committed PDFs and portable index snapshots, updates both deployment paths, and verifies that the restored indexes are ready for notebook development.

### Target indexes

#### `supplier-evidence`

Use the 25-document `invoice-investigation` corpus:

- Supplier invoices
- Cold-chain temperature and excursion reports
- Production yield evidence
- Quality and nonconformance reports
- Certificates of analysis
- GMP inspection evidence

#### `sourcing-documents`

Use an 11-document subset of the sourcing corpus:

- Caldova RFP and three supplier responses
- Manufacturing agreements
- Summit Dose amendment and purchase order
- Supplier and evidence diagrams

ILL344 will not apply document-level ACL filtering to this index. The purpose of this lab is multi-source knowledge retrieval rather than permission-aware retrieval.

#### Reserved file-source document

Reserve `CAL-POL-PUR-001.pdf` exclusively for the direct File Knowledge Source exercise in Part 1.

- Store it with the complete browsable PDF corpus at `data/caldova/pdfs/CAL-POL-PUR-001.pdf`.
- Do not include it in `supplier-evidence` or `sourcing-documents`.
- Use it for questions about purchasing controls, approval thresholds, sourcing routes, supplier governance, and escalation responsibilities.
- Keep the same file source available in later notebooks when they extend the knowledge base.

This explicit exclusion prevents the same policy content from being retrieved from both a file source and a Search index.

### Repository layout

```text
data/
  caldova/
    corpora.json
    provenance.json
    pdfs/
      CAL-POL-PUR-001.pdf
      ...all other Caldova PDFs used by the lab
  index-data/
    supplier-evidence-index.json
    supplier-evidence-exported.jsonl
    sourcing-documents-index.json
    sourcing-documents-exported.jsonl
infra/
  create-index-snapshots.py
  create-knowledge.py
  main.bicep
scripts/
  sync-caldova-data.py
  export-search-index.py
```

The exact script locations may be adjusted to match existing repository conventions. Commit the complete set of PDFs used by the two indexes plus the reserved policy PDF so attendees can inspect the original evidence. The corpus manifest, rather than directory placement, determines which documents are indexed.

### Data synchronization

Add a maintainer script that copies manifest-selected assets from a local checkout of `aitour27-caldova-data`.

The synchronization workflow will:

1. Read the upstream corpus manifest.
2. Copy the 25 `invoice-investigation` PDFs for `supplier-evidence` into `data/caldova/pdfs/`.
3. Copy the nine `procurement` PDFs and two diagram PDFs for `sourcing-documents` into `data/caldova/pdfs/`.
4. Copy `CAL-POL-PUR-001.pdf` into `data/caldova/pdfs/` but mark it as the reserved file-source document in `corpora.json` rather than assigning it to an index.
5. Record the upstream repository URL and commit SHA in `provenance.json`.
6. Reject missing files, duplicate paths, overlap between indexed and file-source documents, and unexpected corpus counts.
7. Avoid copying generated HTML, templates, source images, or unrelated JSON datasets.

The synchronized source data and generated index snapshots must be reviewed and committed together.

### Maintainer-only infrastructure

Add an opt-in Bicep parameter:

```bicep
@description('Deploy temporary Blob Storage used by the Azure AI Search indexing pipeline')
param deployIndexingStorage bool = false
```

Add the corresponding parameter-file setting:

```json
"deployIndexingStorage": {
  "value": "${DEPLOY_INDEXING_STORAGE=false}"
}
```

When enabled, deploy a Storage Account through `br/public:avm/res/storage/storage-account:0.33.0`, the latest stable storage-account AVM version when this plan was written. Recheck the registry before implementation; if a newer stable version is selected, validate its interface and pin that exact version in Bicep.

The module will:

- Disable public blob access.
- Create a private `knowledge` container.
- Use managed identity and Azure RBAC rather than account keys.
- Grant the maintainer identity permission to upload and remove source PDFs.
- Grant the Azure AI Search managed identity permission to read the container.
- Deploy only when `deployIndexingStorage` is `true`.

Expose empty outputs when indexing storage is disabled:

```text
INDEXING_STORAGE_ACCOUNT_ID
INDEXING_STORAGE_ACCOUNT_NAME
```

Normal Skillable and self-deployment environments will leave `DEPLOY_INDEXING_STORAGE=false`. No storage account, indexer, skillset, or source PDF ingestion is required for attendees.

### Index generation

Add a maintainer-only script that builds both indexes from the synchronized Caldova PDFs.

The pipeline will:

1. Require indexing storage to be enabled.
2. Upload only the indexed documents under separate blob prefixes. Do not upload `CAL-POL-PUR-001.pdf` to either indexer prefix.
3. Create or update one Search index per corpus.
4. Configure a Blob data source, Content Understanding skillset, and indexer for each corpus.
5. Clear stale projected chunks before rerunning an indexer.
6. Wait for each indexer run and fail on indexing errors.
7. Report uploaded PDF, indexed chunk, and failed-item counts.

Use the proven retrieval settings from LTG242 as a reference:

- Semantic Content Understanding chunks
- Maximum chunk length of 2,000 tokens
- Use only `locationMetadata` in the Content Understanding extraction options for the initial implementation
- Disable image extraction entirely for the initial implementation and evaluate its effect through the representative retrieval questions
- Do not request `normalized_images`, configure a knowledge-store file projection, or create an `extracted-images` container
- Do not define or map an `image_path` index field; image paths must not be stored in the source indexes or snapshots
- `text-embedding-3-large`
- 3,072-dimensional vectors
- Semantic configuration over the chunk text
- HNSW cosine vector search

Pin `azure-search-documents==12.1.0b1`, matching the current notebook environment. Use the `2026-05-01-preview` Search REST API for Content Understanding skill properties that the pinned SDK models do not serialize, including `modelName` and `modelDeployment`.

The portable index schema should contain:

```text
chunk_id
parent_id
title
blob_path
chunk
page_number_from
page_number_to
text_vector
```

Define `text_vector` as retrievable and stored so the exporter can read its 3,072 values and the attendee restore script can upload them. Both indexes must use the same portable schema unless a documented retrieval requirement justifies a difference.

Do not include these fields in the portable schema:

```text
image_path
user_ids
group_ids
```

### Snapshot export

Add `scripts/export-search-index.py` to export a populated Search index into an index-definition JSON file and a JSONL document file.

The exporter will:

1. Read the live index definition.
2. Select every required retrievable field explicitly.
3. Retrieve all indexed documents with correct paging.
4. Sort records by the index key for deterministic output.
5. Verify that `image_path`, ACL fields, and permission-filter configuration are absent from the source index definition and records; fail rather than silently removing unexpected fields.
6. Normalize `blob_path` to a stable logical value such as `/supplier-evidence/INV-SUP-001-2026-10.pdf` or `/sourcing-documents/RFP-CAL-OSD-2026-01.pdf`; no storage-account URL or hostname may be exported.
7. Remove the source Azure OpenAI endpoint from the vectorizer configuration.
8. Write compact UTF-8 JSONL with one record per line and a trailing newline.
9. Verify unique keys, complete vectors, vector dimensions, schema compatibility, and source/export counts.
10. Avoid printing secrets or vectors.

The index-definition JSON will preserve semantic and vector-search configuration. Its vectorizer resource URL will be `null` or a documented placeholder so the attendee restore script can replace it with `AZURE_OPENAI_ENDPOINT`.

Do not export data sources, indexers, skillsets, storage identities, or container names. The indexing pipeline will not persist extracted images or image locations.

### Attendee restore path

Update `infra/create-knowledge.py` to restore:

- `supplier-evidence-index.json` and `supplier-evidence-exported.jsonl`
- `sourcing-documents-index.json` and `sourcing-documents-exported.jsonl`

Before index creation, the restore script will replace the portable vectorizer endpoint with the provisioned `AZURE_OPENAI_ENDPOINT`. It will then create each index and upload JSONL records in bounded batches.

The restore workflow must:

- Check every upload result and fail if any document upload fails.
- Report expected and uploaded record counts.
- Use the existing Search admin-key authentication path unless the broader lab is migrated to identity-based authentication.
- Avoid running Content Understanding or generating embeddings.

Update the post-provision scripts, notebook environment descriptions, restore notebook, and Skillable automation to use the new index names and expected document counts.

Update `infra/setup-knowledge.ps1`, which remains the Skillable entry point:

- Keep writing the `.env` file, creating the virtual environment, installing notebook dependencies, and invoking `infra/create-knowledge.py`.
- Remove the legacy `data/ai-search-data` existence check and `LOCAL_DOCS_PATH` environment variable because attendee setup restores committed JSONL records rather than ingesting source documents.
- Change the `Uploading documents to blob storage...` message to `Restoring pre-indexed Caldova data...`.
- Do not add indexing-storage parameters or Content Understanding steps. Those belong only to the maintainer workflow.
- Continue passing Search, Azure OpenAI, tenant, project, and Fabric settings needed by the notebooks.

### Phase 1 validation

No automated test files are required for this workflow. Validation will be performed by running the maintainer scripts and checking their outputs and generated artifacts.

#### Script validation

The synchronization, generation, export, and restore scripts must fail clearly when they detect:

- Corpus manifest validation
- Missing or duplicate corpus paths
- Overlap between indexed corpora and the reserved file-source document
- Unexpected corpus counts
- Indexer failures
- Duplicate document keys
- Missing or incorrectly sized vectors
- Record fields absent from the exported schema
- Source, export, or restored document-count mismatches

After export, run a script-level artifact check that confirms:

- `image_path`, ACL fields, permission configuration, credentials, and source-environment endpoints are absent
- Records are ordered deterministically by the index key
- Every record field exists in the exported schema
- The JSONL file is valid UTF-8 with one JSON object per line and a trailing newline

#### End-to-end maintainer validation

1. Synchronize the pinned Caldova corpus.
2. Confirm `CAL-POL-PUR-001.pdf` exists in the browsable PDF directory, is marked as the reserved file-source document, and is absent from both indexed corpora.
3. Enable indexing storage and provision infrastructure.
4. Generate both source indexes.
5. Confirm both indexers complete without failures.
6. Export both portable snapshots.
7. Confirm excluded field names and source-environment endpoints occur nowhere in the artifacts.
8. Restore both snapshots into newly named temporary indexes using direct upload only.
9. Confirm source, export, and restored chunk counts match.
10. Create temporary knowledge sources and a knowledge base over the restored indexes.
11. Run the indexed-document questions defined in Phase 2 against the restored indexes.
12. Record whether disabling image extraction materially affects answers involving diagrams or visually structured evidence.

### Phase 1 completion gate

Phase 1 is complete when both Caldova indexes can be restored from committed JSON and JSONL files through the self-deployment and Skillable setup paths. Do not begin notebook migration until the restored indexes have matching source/export/restore counts and can answer the Phase 2 indexed-document questions with grounded citations.

## Phase 2: Migrate the notebooks

After Phase 1 is complete, rewrite the four notebooks around a progressive Caldova supply-chain investigation. Preserve the existing technical progression: indexed and file knowledge sources, then Fabric IQ, then Work IQ, then all sources together.

### Part 1: Indexed documents and a file source

Replace the Zava HR, benefits, and cloud-architecture examples with Caldova supplier and sourcing evidence.

- Create Search index knowledge sources for `supplier-evidence` and `sourcing-documents`.
- Upload `data/caldova/pdfs/CAL-POL-PUR-001.pdf` as the direct File Knowledge Source.
- Establish that indexed documents provide supplier evidence and contractual records while the uploaded policy provides Caldova's purchasing controls.

Recommended questions:

- File source: `What approval thresholds, sourcing routes, and escalation responsibilities does Caldova's purchasing policy define?`
- Supplier evidence: `Shipment SHIP-VC-1026-BOS-FRA exceeded its temperature range. What happened, and can the 4,800 units be distributed?`
- Supplier evidence: `Was lot LOT-MD-1026-A released, and should Caldova pay the $125,600 yield adjustment?`
- Sourcing documents: `Compare Summit Dose's documented CALD-201 commitments across its RFP response, agreement amendment, and purchase order.`

### Part 2: Add Fabric IQ

Add the Caldova Fabric IQ ontology to the Part 1 knowledge sources. Use Meridian API Works because its indexed invoice and quality evidence identify `AUR-API-7`, while the ontology connects its active substance to Caldova medicinal products.

Recommended question:

`Which Caldova medicinal product uses the active substance in Meridian API Works' invoiced AUR-API-7 lot? Summarize the lot evidence, then explain the manufacturer-to-substance and substance-to-product relationships.`

The answer must distinguish facts retrieved from indexed documents from relationships retrieved through Fabric IQ.

### Part 3: Add Work IQ

Replace the Zava hammer and Seattle email thread with seeded Caldova sourcing messages about Summit Dose and `CALD-201`. The messages should describe current actions or decisions that are not duplicated in the approved sourcing documents, such as a technology-transfer milestone, testing-subcontractor evidence, capacity confirmation, or an upcoming supply-review decision.

Recommended question:

`What open Summit Dose actions and decisions appear in my recent work context? Compare them with the approved CALD-201 commitments and identify anything still unresolved.`

The answer must distinguish current workplace discussion from approved document commitments.

### Part 4: Combine all knowledge sources

Create one knowledge base containing the file source, both Search index sources, Fabric IQ, and Work IQ. Frame the attendee as preparing an executive supply-chain briefing.

Recommended question:

`Prepare a Caldova supply-chain briefing. Summarize the approved CALD-201 sourcing commitments, identify current Summit Dose actions from my work context, and use Fabric IQ to identify medicinal-product dependencies for approved manufacturers that may create additional supply risk. Clearly distinguish policy, contractual evidence, structured Fabric facts, and workplace discussions.`

If the Fabric ontology does not connect Summit Dose directly to a medicinal product, the answer must not infer that relationship. It should report the independently retrieved manufacturer dependencies and explain the evidence boundary.

### Phase 2 validation

Run each recommended question after its notebook is migrated. Confirm that the answer uses the intended source or sources, includes grounded citations where available, and does not merge document commitments, Fabric relationships, or workplace discussions into unsupported claims. Update prompts only after checking the actual indexed, Fabric, and Work IQ results.

## Completion criteria

- ILL344 can reproduce both index snapshots using only this repository, a pinned Caldova data checkout, and provisioned Azure resources.
- Attendee deployment restores both indexes without Blob Storage or Content Understanding.
- Neither portable index contains image paths, ACL fields, source endpoints, or credentials.
- Representative retrieval produces grounded answers with document titles and page metadata.
- Skillable and self-deployment instructions describe only the attendee restore path.
