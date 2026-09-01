import os
import asyncio
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from azure.core.credentials import AzureKeyCredential
from azure.search.documents.aio import SearchClient
from azure.search.documents.indexes.aio import SearchIndexClient
from azure.search.documents.indexes.models import SearchIndex

load_dotenv(override=True)

endpoint = os.environ["AZURE_SEARCH_SERVICE_ENDPOINT"]
admin_key = os.getenv("AZURE_SEARCH_ADMIN_KEY")
credential = AzureKeyCredential(admin_key)

azure_openai_endpoint = os.environ["AZURE_OPENAI_ENDPOINT"]

LOG_FILE = str(Path(__file__).parent / "index-creation.log")

def log_message(message, log_file=LOG_FILE):
    """Write message to log file with timestamp"""
    # Ensure the directory exists
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {message}\n")

async def restore_index(endpoint: str, index_name: str, index_file: str, records_file: str, azure_openai_endpoint: str, credential: AzureKeyCredential):
    default_path = str(Path(__file__).parent.parent / "data" / "index-data")
    
    try:
        log_message(f"[{index_name}] Starting index restoration...")
        
        # Create or update index
        async with SearchIndexClient(endpoint=endpoint, credential=credential) as client:
            index_file_path = os.path.join(default_path, index_file)
            log_message(f"[{index_name}] Reading index definition from: {index_file_path}")
            
            with open(index_file_path, "r", encoding="utf-8") as in_file:
                index_data = json.load(in_file)
                index = SearchIndex(index_data)
                index.name = index_name
                index.vector_search.vectorizers[0].parameters.resource_url = azure_openai_endpoint
                
                log_message(f"[{index_name}] Creating/updating index in Foundry IQ (Azure AI Search)...")
                await client.create_or_update_index(index)
                log_message(f"[{index_name}] Index created/updated successfully")

        # Upload documents
        async with SearchClient(endpoint=endpoint, index_name=index_name, credential=credential) as client:
            records_file_path = os.path.join(default_path, records_file)
            log_message(f"[{index_name}] Reading documents from: {records_file_path}")

            existing_results = await client.search(search_text="*", select=["chunk_id"])
            existing_records = [
                {"chunk_id": result["chunk_id"]} async for result in existing_results
            ]
            for offset in range(0, len(existing_records), 1000):
                delete_results = await client.delete_documents(
                    documents=existing_records[offset : offset + 1000]
                )
                failures = [result for result in delete_results if not result.succeeded]
                if failures:
                    raise RuntimeError(f"Failed to remove stale documents from {index_name}")
            if existing_records:
                log_message(
                    f"[{index_name}] Removed {len(existing_records)} stale documents"
                )
            
            records = []
            expected_count = 0
            total_uploaded = 0
            batch_count = 0

            async def upload_batch(batch):
                nonlocal batch_count, total_uploaded
                batch_count += 1
                log_message(f"[{index_name}] Uploading batch #{batch_count} ({len(batch)} documents)...")
                upload_results = await client.upload_documents(documents=batch)
                failures = [result for result in upload_results if not result.succeeded]
                if failures:
                    details = ", ".join(
                        f"{result.key}: {result.error_message}" for result in failures
                    )
                    raise RuntimeError(f"Document upload failed: {details}")
                total_uploaded += len(upload_results)
            
            with open(records_file_path, "r", encoding="utf-8") as in_file:
                for line_num, line in enumerate(in_file, 1):
                    try:
                        record = json.loads(line)
                        records.append(record)
                        expected_count += 1
                        
                        if len(records) >= 100:
                            await upload_batch(records)
                            records = []
                    except json.JSONDecodeError as e:
                        raise ValueError(
                            f"Invalid JSON in {records_file_path} on line {line_num}: {e}"
                        ) from e

            # Upload remaining documents
            if records:
                await upload_batch(records)

            if total_uploaded != expected_count:
                raise RuntimeError(
                    f"Uploaded {total_uploaded} documents, expected {expected_count}"
                )
            for _ in range(30):
                restored_count = await client.get_document_count()
                if restored_count == expected_count:
                    break
                await asyncio.sleep(2)
            if restored_count != expected_count:
                raise RuntimeError(
                    f"Restored index contains {restored_count} documents, "
                    f"expected {expected_count}"
                )
        
        log_message(
            f"[{index_name}] SUCCESS - Index restored! "
            f"Expected and uploaded documents: {total_uploaded}"
        )
        print(f"Index {index_name} restored using {index_file} and {records_file}")
        return True
        
    except FileNotFoundError as e:
        error_msg = f"[{index_name}] ERROR - File not found: {e}"
        log_message(error_msg)
        log_message(f"[{index_name}] Traceback:\n{traceback.format_exc()}")
        print(f"Index {index_name} failed - see log file for details")
        return False
    except PermissionError as e:
        error_msg = f"[{index_name}] ERROR - Permission denied: {e}"
        log_message(error_msg)
        log_message(f"[{index_name}] This indicates insufficient permissions for the service principal")
        log_message(f"[{index_name}] Traceback:\n{traceback.format_exc()}")
        print(f"Index {index_name} failed - see log file for details")
        return False
    except Exception as e:
        error_msg = f"[{index_name}] ERROR - {type(e).__name__}: {str(e)}"
        log_message(error_msg)
        log_message(f"[{index_name}] Traceback:\n{traceback.format_exc()}")
        print(f"Index {index_name} failed - see log file for details")
        return False


async def main():
    # Initialize log file
    log_message("="*80)
    log_message("Foundry IQ (Azure AI Search) Index Restoration Script - Starting")
    log_message("="*80)
    log_message(f"Azure Search Endpoint: {endpoint}")
    log_message(f"Azure OpenAI Endpoint: {azure_openai_endpoint}")
    
    results = {}
    
    # Restore supplier evidence index
    log_message("\n--- Processing supplier-evidence index ---")
    results['supplier-evidence'] = await restore_index(
        endpoint,
        "supplier-evidence",
        "supplier-evidence-index.json",
        "supplier-evidence-exported.jsonl",
        azure_openai_endpoint,
        credential
    )
    
    # Add delay between operations to avoid rate limiting
    log_message("Waiting 3 seconds before processing next index...")
    await asyncio.sleep(3)
    
    # Restore sourcing documents index
    log_message("\n--- Processing sourcing-documents index ---")
    results['sourcing-documents'] = await restore_index(
        endpoint,
        "sourcing-documents",
        "sourcing-documents-index.json",
        "sourcing-documents-exported.jsonl",
        azure_openai_endpoint,
        credential
    )
    
    # Summary
    log_message("\n" + "="*80)
    log_message("EXECUTION SUMMARY")
    log_message("="*80)
    
    success_count = sum(1 for v in results.values() if v)
    
    for index_name, success in results.items():
        status = "SUCCESS" if success else " FAILED"
        log_message(f"{status}: {index_name}")
    
    if success_count == len(results):
        log_message("\n All indexes created successfully!")
        print("\n Setup completed!")
    else:
        log_message(f"\n WARNING: {len(results) - success_count} index(es) failed to create.")
        log_message("\nPossible causes when using service principal:")
        log_message("  1. Insufficient Azure RBAC permissions on the AI Search service")
        log_message("  2. Missing 'Search Service Contributor' or 'Search Index Data Contributor' role")
        log_message("  3. Rate limiting from Azure OpenAI or AI Search")
        log_message("  4. Network/firewall restrictions")
        log_message("  5. Quota limits on Azure OpenAI or AI Search service")
        log_message("\nRecommended actions:")
        log_message("  - Verify service principal has 'Search Service Contributor' role")
        log_message("  - Verify service principal has 'Search Index Data Contributor' role")
        log_message("  - Check Azure OpenAI access permissions")
        log_message("  - Review detailed error messages above in this log file")
        
        print(f"\n Setup completed with errors. Check log file: {LOG_FILE}")
        sys.exit(1)
    
    log_message("="*80)
    log_message("Script execution completed")
    log_message("="*80)


if __name__ == "__main__":
    asyncio.run(main())
