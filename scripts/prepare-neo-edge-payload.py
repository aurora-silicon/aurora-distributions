#!/usr/bin/env python3
"""Assemble the Neo (and, with neo_edge.M5, the M5 Pro) catalog template and payload without creating a release."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import neo_edge


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def assemble(producer, filesystems, recovery, image_run, assets, evidence=None):
    native = producer / "macOS"
    run = json.loads(image_run.read_text())
    proof = (filesystems / "edge-image-proof.json").read_bytes()
    neo_edge.verify_image(run, proof, neo_edge.PROFILE)
    if evidence is not None:
        neo_edge.verify_evidence(json.loads(evidence.read_text()))
    release_id = f"build-{run['id']}-edge-alpha.1"
    base = f"https://github.com/{neo_edge.REPO}/releases/download/payload-{neo_edge.PROFILE}-{run['id']}-edge-alpha.1"
    subprocess.run(["python3", str(native / "scripts/assemble-distribution-payload.py"),
                    "--filesystems", str(filesystems), "--recovery-app", str(recovery),
                    "--output", str(assets), "--release-id", release_id], check=True)
    profile = neo_edge.PROFILE
    meta = assets / f"installer-data-{profile}.json"
    meta_json = json.loads(meta.read_text())
    template, = meta_json["os_list"]
    boards = neo_edge.boards()
    for board in boards:
        if (not template["aurora_stage1"]["qualified_machines"].get(board)
                or not template["aurora_stage2"]["board_members"].get(board)):
            raise ValueError(f"{board} Stage 2 or Stage 1 metadata is missing")
    # Restrict the install policy as well as the signed model inventory.
    template["allowed_device_classes"] = boards
    stage1 = template["aurora_stage1"]["qualified_machines"]
    template["aurora_stage1"]["qualified_machines"] = {board: stage1[board] for board in boards}
    write(meta, meta_json)

    receipt_path = assets / "delivery.json"
    receipt = json.loads(receipt_path.read_text())
    if receipt["release_id"] != release_id or receipt["installation_profile"] != profile:
        raise ValueError("Assembler returned a different delivery")
    receipt["artifacts"][meta.name] = {
        "bytes": meta.stat().st_size, "sha256": hashlib.sha256(meta.read_bytes()).hexdigest(),
    }
    write(receipt_path, receipt)
    payload = assets / f"{profile}-{release_id}.zip"
    if payload.stat().st_size >= 1_900_000_000:
        subprocess.run(["split", "-b", "1073741824", "--numeric-suffixes=0",
                        "--suffix-length=2", str(payload), str(payload) + ".part"], check=True)
    supported = json.loads((native / "scripts/supported-models.json").read_text())["supported"]
    inputs = {
        "schema_version": 5, "installation_profile": profile,
        "payload_name": payload.name, "metadata_name": meta.name,
        "evidence_revision": f"image-build-{run['id']}",
        "asahi_installer_tag": "v0.9.2",
        "asahi_installer_revision": "dffbb38ef0c00c0431c609ecd8a00f42deb5b24c",
        "asahi_installer_data_revision": "42648e71423eba308d2e3e6228253eff679b068b",
        "downstream_revision": subprocess.check_output(
            ["git", "-C", str(producer), "rev-parse", "HEAD"], text=True).strip(),
        "device_identifiers": supported,
        "installer": {"minimum_version": "0.1.0", "latest_version": "0.1.0",
                      "download_url": "https://github.com/aurora-silicon/aurora-installer/releases"},
    }
    input_path = assets / "catalog-inputs.json"
    write(input_path, inputs)
    catalog_path = assets / "catalog-template.json"
    subprocess.run(["python3", str(native / "scripts/make-unsigned-catalog.py"),
                    "--base-url", base, "--assets-dir", str(assets),
                    "--inputs", str(input_path), "--output", str(catalog_path)], check=True)
    catalog = json.loads(catalog_path.read_text())
    if catalog.pop("channel", None) not in (None, "testing") or "edgeQualification" in catalog:
        raise ValueError("Unexpected channel or qualification in the generated catalog")
    rows = [row for row in catalog["models"] if row["deviceIdentifier"] in neo_edge.models()]
    if [row["deviceIdentifier"] for row in rows] != neo_edge.models():
        raise ValueError("Assembler did not return exactly the Edge model rows")
    catalog["models"] = rows
    neo_edge.verify_metadata(catalog, meta.read_bytes())
    write(catalog_path, catalog)
    input_path.unlink()
    (assets / "image-build.json").write_bytes(image_run.read_bytes())
    (assets / "edge-image-proof.json").write_bytes(proof)
    key = assets / "trust-root.ed25519.pub"
    key.write_bytes((native / "Release/trust-root.ed25519.pub").read_bytes())
    subprocess.run(["bash", str(native / "scripts/make-release-descriptor"), "--adhoc",
                    "--public-key", str(key), "--base-url",
                    f"https://aurorasilicon.org/aurora-distributions/installer/Profiles/{profile}",
                    "--output", str(assets / "release.json")], check=True)
    if evidence is not None:
        raw_report = neo_edge.bind(catalog, receipt, run, proof, json.loads(evidence.read_text()))
        (assets / "edge-qualification.json").write_bytes(raw_report)
        write(assets / "catalog.json", catalog)
        write(receipt_path, receipt)
    if (assets / (payload.name + ".part00")).exists():
        payload.unlink()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("producer", "filesystems", "recovery", "image-run", "assets"):
        parser.add_argument("--" + option, type=Path, required=True)
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    assemble(args.producer, args.filesystems, args.recovery, args.image_run, args.assets, args.evidence)
