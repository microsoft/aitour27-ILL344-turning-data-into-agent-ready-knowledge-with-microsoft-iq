"""Synchronize the Caldova PDF corpora used by ILL344."""

import argparse
import json
import shutil
import subprocess
from pathlib import Path


REPOSITORY_URL = "https://github.com/pamelafox/aitour27-caldova-data"
EXPECTED_COUNTS = {
    "supplier-evidence": 25,
    "sourcing-documents": 11,
    "file-source": 1,
}
FABRIC_JSON_FILES = (
    "suppliers.json",
    "supplier-kpi-profiles.json",
    "medicinal-product-ontology.json",
    "waypoint-supplier-invoices.json",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source",
        type=Path,
        help="Path to a local checkout of pamelafox/aitour27-caldova-data",
    )
    return parser.parse_args()


def load_upstream_manifest(source_root: Path) -> dict[str, list[str]]:
    manifest_path = source_root / "sample-data" / "corpora.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        invoice_paths = manifest["invoice-investigation"]
        procurement_paths = manifest["procurement"]
        policy_paths = manifest["policy-and-diagrams"]
    except (FileNotFoundError, KeyError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Invalid upstream corpus manifest: {manifest_path}") from error

    corpora = {
        "supplier-evidence": invoice_paths,
        "sourcing-documents": procurement_paths
        + [path for path in policy_paths if Path(path).name.startswith("CAL-MAP-")],
        "file-source": [
            path for path in policy_paths if Path(path).name == "CAL-POL-PUR-001.pdf"
        ],
    }
    validate_manifest(corpora, source_root / "sample-data")
    return corpora


def validate_manifest(corpora: dict[str, list[str]], source_data_root: Path) -> None:
    all_paths: list[str] = []
    for corpus_name, expected_count in EXPECTED_COUNTS.items():
        paths = corpora.get(corpus_name)
        if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
            raise RuntimeError(f"Corpus '{corpus_name}' must be a list of paths.")
        if len(paths) != expected_count:
            raise RuntimeError(
                f"Corpus '{corpus_name}' contains {len(paths)} files; expected {expected_count}."
            )
        if len(paths) != len(set(paths)):
            raise RuntimeError(f"Corpus '{corpus_name}' contains duplicate paths.")
        for relative_path in paths:
            if Path(relative_path).suffix.lower() != ".pdf":
                raise RuntimeError(f"Corpus path is not a PDF: {relative_path}")
            if not (source_data_root / relative_path).is_file():
                raise RuntimeError(f"Corpus file does not exist: {relative_path}")
        all_paths.extend(paths)

    if len(all_paths) != len(set(all_paths)):
        raise RuntimeError("Indexed corpora and the reserved file source overlap.")


def get_commit(source_root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(source_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def synchronize(source_root: Path) -> None:
    source_root = source_root.resolve()
    repo_root = Path(__file__).resolve().parents[1]
    destination_root = repo_root / "data" / "caldova"
    destination_pdfs = destination_root / "pdfs"
    destination_json = destination_root / "json"
    corpora = load_upstream_manifest(source_root)

    destination_pdfs.mkdir(parents=True, exist_ok=True)
    destination_json.mkdir(parents=True, exist_ok=True)
    expected_names = {Path(path).name for paths in corpora.values() for path in paths}
    for existing_pdf in destination_pdfs.glob("*.pdf"):
        if existing_pdf.name not in expected_names:
            existing_pdf.unlink()

    for relative_path in sorted(path for paths in corpora.values() for path in paths):
        source_pdf = source_root / "sample-data" / relative_path
        shutil.copy2(source_pdf, destination_pdfs / source_pdf.name)

    for filename in FABRIC_JSON_FILES:
        source_json = source_root / "sample-data" / "json" / filename
        if not source_json.is_file():
            raise RuntimeError(f"Fabric source file does not exist: {source_json}")
        shutil.copy2(source_json, destination_json / filename)

    portable_corpora = {
        name: [f"pdfs/{Path(path).name}" for path in paths]
        for name, paths in corpora.items()
    }
    (destination_root / "corpora.json").write_text(
        json.dumps(portable_corpora, indent=2) + "\n",
        encoding="utf-8",
    )
    (destination_root / "provenance.json").write_text(
        json.dumps(
            {
                "repository": REPOSITORY_URL,
                "commit": get_commit(source_root),
                "source_manifest": "sample-data/corpora.json",
                "fabric_sources": [
                    f"sample-data/json/{filename}" for filename in FABRIC_JSON_FILES
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        f"Synchronized {len(expected_names)} Caldova PDFs and "
        f"{len(FABRIC_JSON_FILES)} Fabric JSON files."
    )


if __name__ == "__main__":
    synchronize(parse_args().source)