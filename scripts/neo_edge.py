"""Build and verify the model-limited Fedora KDE Edge delivery: the Neo, and the M5 Pro once M5 is set."""
import hashlib
import json
from pathlib import Path
import re

REPO = "aurora-silicon/fedora-aurora"
PROFILE = "fedora-kde"
MODEL = "apple,j700"
IMAGE_BRANCH = "edge/neo-f44-image-ef112c2b"
IMAGE_HEAD = "389751ff9564a7d91250ce32beb6207097a67f5e"
IMAGE_WORKFLOW = ".github/workflows/edge-f44-image.yml"
SIGNING_RUN = 44329906984
MESA_SIGNING_RUN = 44380265630
IMAGE_RUN = 44380859872
SOURCES = {
    name: {"repository": "aurora-silicon/" + name, "branch": "aurora-edge",
           "revision": revision, "tree": tree}
    for name, revision, tree in (
        ("linux", "6020f67d5ce13e7aae6820cd51ab70826404932c", "f2dbcb1ebd59343e2496360bc572ef1a071f2159"),
        ("mesa", "ef112c2b86e4e19b2317cd88918e673de7fd55cf", "5a383e03de1e86d727def1279d2328c4f9b27bc4"),
    )
}
KREL = "7.3.0-42.fc44.aurora.aarch64"
KERNEL_DIGEST = "d6903588ce991dec6b65beafede9f6b0361d3904af8fd989b6c4bf143bb00fea"
FILES = {
    f"root:usr/lib/modules/{KREL}/vmlinuz": KERNEL_DIGEST,
    f"boot:vmlinuz-{KREL}": KERNEL_DIGEST,
    "root:usr/lib64/libvulkan_asahi.so": "ce060163100691dd68a8f07da1a945d66b2c3f75b26175aa2dd461753de5212b",
    "root:usr/share/vulkan/icd.d/asahi_icd.aarch64.json": "5d4804f4a540be6d376d11ca6a9d34fec703b84aea97dd04a392b01d3bb1ead3",
}
REQUIRED_PACKAGES = {
    "kernel-aurora-core.aarch64": f"kernel-aurora-core-{KREL}.rpm",
    "kernel-aurora-modules-extra.aarch64": f"kernel-aurora-modules-extra-{KREL}.rpm",
    "mesa-aurora-vulkan-drivers.aarch64": "mesa-aurora-vulkan-drivers-26.3.0-33.fc44.aurora.aarch64.rpm",
    "mesa-aurora-dri-drivers.aarch64": "mesa-aurora-dri-drivers-26.3.0-33.fc44.aurora.aarch64.rpm",
}
GATES = {"vulkanCTS", "glCTS", "deepSleep", "restoration"}
EVIDENCE_PAIR = {
    "kernelImageSHA256": "sha256:" + KERNEL_DIGEST,
    "mesaDsoSHA256": "sha256:" + FILES["root:usr/lib64/libvulkan_asahi.so"],
    "mesaSigningRunID": MESA_SIGNING_RUN,
}
# The MacBook Pro (M5 Pro, J714s) rides the same payload when M5 is True: the image then
# lists it, the install policy admits its board, and the M5's own evidence for the same
# kernel and Mesa pair sits beside the Neo's. False keeps the delivery Neo-only.
M5 = False
BOARD = "j700ap"
M5_MODEL = "apple,j714s"
M5_BOARD = "j714sap"
M5_GATES = {"releaseGate", "deepSleep", "freshInstall", "restoration"}


def models():
    return [MODEL, M5_MODEL] if M5 else [MODEL]


def boards():
    return [BOARD, M5_BOARD] if M5 else [BOARD]


def digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def sha256(value):
    return isinstance(value, str) and re.fullmatch(r"sha256:[a-f0-9]{64}", value) is not None


def verify_image(run, raw_proof, profile):
    repo = run.get("head_repository") or {}
    proof = json.loads(raw_proof)
    if (profile != PROFILE or run.get("conclusion") != "success"
            or run.get("status") != "completed" or run.get("path") != IMAGE_WORKFLOW
            or run.get("head_branch") != IMAGE_BRANCH or repo.get("full_name") != REPO
            or run.get("event") not in ("push", "workflow_dispatch")
            or run.get("id") != IMAGE_RUN
            or run.get("head_sha") != IMAGE_HEAD):
        raise ValueError("Expected a completed Fedora Neo Edge image run")
    if (proof.get("schema") != 1 or proof.get("fedora") != 44
            or proof.get("profile") != PROFILE or proof.get("models") != models()
            or proof.get("signing_run") != SIGNING_RUN
            or proof.get("replacement_signing_run") != MESA_SIGNING_RUN
            or proof.get("replacement_mesa") != SOURCES["mesa"]["revision"]
            or proof.get("files") != FILES):
        raise ValueError("Edge image proof differs from the Neo-smoked file inventory")
    packages = proof.get("installed_edge_packages", {})
    for key, filename in REQUIRED_PACKAGES.items():
        pkg = packages.get(key, {})
        if (pkg.get("filename") != filename
                or any(not sha256("sha256:" + str(pkg.get(field)))
                       for field in ("sha256", "main_header_and_payload_sha256"))):
            raise ValueError(f"Edge image lacks the exact signed package: {key}")
    return proof


def verify_gates(record, gates, expected, machine):
    if (not isinstance(record, dict) or set(record) != {"pair", "checks"}
            or record["pair"] != EVIDENCE_PAIR
            or not isinstance(record["checks"], dict) or set(record["checks"]) != gates):
        raise ValueError(expected)
    for gate, evidence in record["checks"].items():
        if (not isinstance(evidence, dict) or set(evidence) != {"status", "sha256"}
                or evidence["status"] != "passed" or not sha256(evidence["sha256"])):
            raise ValueError(f"Missing passing {machine} evidence: {gate}")


def verify_evidence(record):
    """The Neo's record alone, or with M5 one record per model, each for the same pair."""
    neo = record
    if M5:
        if not isinstance(record, dict) or set(record) != {MODEL, M5_MODEL}:
            raise ValueError("Expected the Neo and the M5 Pro evidence")
        neo = record[MODEL]
        verify_gates(record[M5_MODEL], M5_GATES,
                     "Expected M5 Pro release gate, deep sleep, fresh install and restoration evidence",
                     "M5 Pro")
    verify_gates(neo, GATES, "Expected full Vulkan CTS, GL CTS, deep sleep and restoration evidence", "Neo")


def bind(catalog, receipt, run, image_proof, gates):
    verify_image(run, image_proof, receipt.get("installation_profile"))
    verify_evidence(gates)
    rows = [row for row in catalog["models"] if row.get("deviceIdentifier") in models()]
    if (catalog.get("schemaVersion") != 5 or catalog.get("channel") is not None
            or [row.get("deviceIdentifier") for row in rows] != models()
            or any(row.get("installationProfile") != PROFILE or row.get("status") != "enabled"
                   for row in rows)
            or not sha256(rows[0].get("payloadDigest"))
            or any(row.get("payloadDigest") != rows[0]["payloadDigest"] for row in rows)):
        raise ValueError("Invalid unsigned Neo template")
    evidence = {"neoEvidence": gates[MODEL], "m5Evidence": gates[M5_MODEL]} if M5 else {"neoEvidence": gates}
    report = {
        "schemaVersion": 1, "releaseKind": "alpha", "status": "passed",
        "imageRunID": run["id"], "payloadDigest": rows[0]["payloadDigest"],
        **{name: source.copy() for name, source in SOURCES.items()},
        "imageProofDigest": digest(image_proof), **evidence,
    }
    raw = (json.dumps(report, sort_keys=True, indent=2) + "\n").encode()
    catalog.update(channel="edge", edgeQualification={**report, "evidenceDigest": digest(raw)}, models=rows)
    receipt.update(release_channel="edge", release_kind="alpha", packaged_sources={name: source.copy() for name, source in SOURCES.items()})
    return raw


def verify_metadata(catalog, raw):
    template, = json.loads(raw)["os_list"]
    stage1 = template["aurora_stage1"]["qualified_machines"]
    members = template["aurora_stage2"]["board_members"]
    if (len(catalog["models"]) != len(models())
            or any(model["metadataArtifact"]["fileName"] != "installer-data-fedora-kde.json"
                   or model["metadataArtifact"]["sizeBytes"] != len(raw)
                   or model["metadataDigest"] != digest(raw) for model in catalog["models"])
            or template.get("allowed_device_classes") != boards() or set(stage1) != set(boards())
            or not all(stage1[board] and members.get(board) for board in boards())):
        raise ValueError("Edge metadata must bind the same Neo-only install policy")


def verify_binding(catalog, receipt, run, image_proof):
    verify_image(run, image_proof, receipt.get("installation_profile"))
    report = catalog["edgeQualification"]
    if ({name: report.get(name) for name in SOURCES} != SOURCES
            or report.get("imageProofDigest") != digest(image_proof)
            or [row.get("deviceIdentifier") for row in catalog["models"]] != models()):
        raise ValueError("Edge qualification differs from the frozen Neo image or source pins")
    verify_evidence({MODEL: report.get("neoEvidence"), M5_MODEL: report.get("m5Evidence")} if M5
                    else report.get("neoEvidence"))


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args()
    assets = args.assets
    catalog = json.loads((assets / "catalog-template.json").read_text())
    receipt = json.loads((assets / "delivery.json").read_text())
    run = json.loads((assets / "image-build.json").read_text())
    proof = (assets / "edge-image-proof.json").read_bytes()
    gates = json.loads(args.evidence.read_text())
    report = bind(catalog, receipt, run, proof, gates)
    verify_metadata(catalog, (assets / "installer-data-fedora-kde.json").read_bytes())
    (assets / "edge-qualification.json").write_bytes(report)
    for name, document in (("catalog.json", catalog), ("delivery.json", receipt)):
        (assets / name).write_text(json.dumps(document, indent=2) + "\n")


if __name__ == "__main__":
    main()
