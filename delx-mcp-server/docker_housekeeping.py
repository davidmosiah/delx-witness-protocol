from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DEFAULT_REPOSITORY = "delx-mcp"
DEFAULT_KEEP_LATEST = 4
DEFAULT_WARN_DISK_PERCENT = 85
DEFAULT_WARN_UNUSED_IMAGES = 8
DEFAULT_STATUS_PATH = Path("/opt/delx-mcp-server/state/docker-housekeeping-status.json")

SIZE_UNITS = [
    ("TB", 1_000_000_000_000),
    ("GB", 1_000_000_000),
    ("MB", 1_000_000),
    ("KB", 1_000),
    ("B", 1),
]


@dataclass(frozen=True)
class DockerImage:
    repository: str
    tag: str
    created_at: str
    size: str

    @property
    def ref(self) -> str:
        return f"{self.repository}:{self.tag}"

    @property
    def size_bytes(self) -> int:
        return parse_size_to_bytes(self.size)


def parse_size_to_bytes(value: str) -> int:
    text = value.strip().upper()
    if not text:
        raise ValueError("size value cannot be empty")
    for unit, multiplier in SIZE_UNITS:
        if text.endswith(unit):
            number = text[: -len(unit)].strip()
            return int(float(number) * multiplier)
    raise ValueError(f"unsupported size value: {value}")


def build_cleanup_plan(
    images: list[dict[str, Any]],
    *,
    running_refs: set[str],
    repository: str = DEFAULT_REPOSITORY,
    keep_latest: int = DEFAULT_KEEP_LATEST,
) -> dict[str, Any]:
    relevant = [
        DockerImage(
            repository=image["repository"],
            tag=image["tag"],
            created_at=image["created_at"],
            size=image["size"],
        )
        for image in images
        if image.get("repository") == repository and image.get("tag") not in {"", "<none>", None}
    ]
    relevant.sort(key=lambda image: image.created_at, reverse=True)

    keep_refs: list[str] = []
    for image in relevant[:keep_latest]:
        keep_refs.append(image.ref)
    for image in relevant:
        if image.ref in running_refs and image.ref not in keep_refs:
            keep_refs.append(image.ref)

    keep_set = set(keep_refs)
    remove_refs: list[str] = []
    reclaimable_bytes = 0
    for image in relevant:
        if image.ref in keep_set:
            continue
        remove_refs.append(image.ref)
        reclaimable_bytes += image.size_bytes

    return {
        "repository": repository,
        "images_total": len(relevant),
        "keep_refs": keep_refs,
        "remove_refs": remove_refs,
        "reclaimable_bytes": reclaimable_bytes,
    }


def build_status_report(
    *,
    disk_total_bytes: int,
    disk_used_bytes: int,
    running_refs: set[str],
    cleanup_plan: dict[str, Any],
    warn_disk_percent: int = DEFAULT_WARN_DISK_PERCENT,
    warn_unused_images: int = DEFAULT_WARN_UNUSED_IMAGES,
) -> dict[str, Any]:
    used_percent = round((disk_used_bytes / max(1, disk_total_bytes)) * 100, 1)
    warnings: list[str] = []
    removal_candidate_count = len(cleanup_plan["remove_refs"])
    if used_percent >= float(warn_disk_percent):
        warnings.append(f"disk_used_percent>={warn_disk_percent}")
    if removal_candidate_count >= warn_unused_images:
        warnings.append(f"unused_image_count>={warn_unused_images}")

    return {
        "ok": not warnings,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "host": socket.gethostname(),
        "disk": {
            "total_bytes": disk_total_bytes,
            "used_bytes": disk_used_bytes,
            "free_bytes": max(0, disk_total_bytes - disk_used_bytes),
            "used_percent": used_percent,
        },
        "images": {
            "repository": cleanup_plan.get("repository", DEFAULT_REPOSITORY),
            "tracked_total": cleanup_plan["images_total"],
            "running_refs": sorted(running_refs),
            "kept_refs": cleanup_plan["keep_refs"],
            "removal_candidate_count": removal_candidate_count,
            "removal_candidate_refs": cleanup_plan["remove_refs"],
            "reclaimable_bytes": cleanup_plan["reclaimable_bytes"],
        },
        "warnings": warnings,
    }


def _run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, capture_output=True, text=True)


def _load_images() -> list[dict[str, Any]]:
    result = _run_command(
        [
            "docker",
            "images",
            "--format",
            "{{json .}}",
        ]
    )
    rows: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        rows.append(
            {
                "repository": payload.get("Repository", ""),
                "tag": payload.get("Tag", ""),
                "created_at": payload.get("CreatedAt", ""),
                "size": payload.get("Size", "0B"),
            }
        )
    return rows


def _load_running_refs() -> set[str]:
    result = _run_command(
        [
            "docker",
            "ps",
            "--format",
            "{{json .}}",
        ]
    )
    refs: set[str] = set()
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        image_ref = payload.get("Image")
        if image_ref:
            refs.add(image_ref)
    return refs


def _write_status(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _cleanup_images(plan: dict[str, Any], *, prune_dangling: bool) -> dict[str, Any]:
    removed_refs: list[str] = []
    removal_errors: list[dict[str, str]] = []

    for ref in plan["remove_refs"]:
        try:
            _run_command(["docker", "image", "rm", ref])
            removed_refs.append(ref)
        except subprocess.CalledProcessError as exc:
            removal_errors.append({"ref": ref, "stderr": exc.stderr.strip()})

    if prune_dangling:
        try:
            _run_command(["docker", "image", "prune", "-f"])
        except subprocess.CalledProcessError as exc:
            removal_errors.append({"ref": "<dangling>", "stderr": exc.stderr.strip()})

    return {
        "removed_refs": removed_refs,
        "removal_errors": removal_errors,
    }


def _collect_report(
    *,
    repository: str,
    keep_latest: int,
    warn_disk_percent: int,
    warn_unused_images: int,
) -> tuple[dict[str, Any], dict[str, Any], set[str]]:
    disk = shutil.disk_usage("/")
    images = _load_images()
    running_refs = _load_running_refs()
    cleanup_plan = build_cleanup_plan(
        images,
        running_refs=running_refs,
        repository=repository,
        keep_latest=keep_latest,
    )
    report = build_status_report(
        disk_total_bytes=disk.total,
        disk_used_bytes=disk.used,
        running_refs=running_refs,
        cleanup_plan=cleanup_plan,
        warn_disk_percent=warn_disk_percent,
        warn_unused_images=warn_unused_images,
    )
    return report, cleanup_plan, running_refs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Delx Docker image cleanup and disk monitoring")
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    parser.add_argument("--keep-latest", type=int, default=DEFAULT_KEEP_LATEST)
    parser.add_argument("--warn-disk-percent", type=int, default=DEFAULT_WARN_DISK_PERCENT)
    parser.add_argument("--warn-unused-images", type=int, default=DEFAULT_WARN_UNUSED_IMAGES)
    parser.add_argument("--status-path", type=Path, default=DEFAULT_STATUS_PATH)

    subparsers = parser.add_subparsers(dest="command", required=True)

    report_parser = subparsers.add_parser("report")
    report_parser.add_argument("--fail-on-warning", action="store_true")

    cleanup_parser = subparsers.add_parser("cleanup")
    cleanup_parser.add_argument("--prune-dangling", action="store_true")
    cleanup_parser.add_argument("--fail-on-warning", action="store_true")

    args = parser.parse_args(argv)

    report, cleanup_plan, _running_refs = _collect_report(
        repository=args.repository,
        keep_latest=args.keep_latest,
        warn_disk_percent=args.warn_disk_percent,
        warn_unused_images=args.warn_unused_images,
    )

    if args.command == "cleanup":
        cleanup_result = _cleanup_images(cleanup_plan, prune_dangling=args.prune_dangling)
        refreshed_report, refreshed_plan, running_refs = _collect_report(
            repository=args.repository,
            keep_latest=args.keep_latest,
            warn_disk_percent=args.warn_disk_percent,
            warn_unused_images=args.warn_unused_images,
        )
        report = {
            **refreshed_report,
            "actions": cleanup_result,
            "before": {
                "images": {
                    "tracked_total": cleanup_plan["images_total"],
                    "removal_candidate_count": len(cleanup_plan["remove_refs"]),
                    "removal_candidate_refs": cleanup_plan["remove_refs"],
                    "reclaimable_bytes": cleanup_plan["reclaimable_bytes"],
                },
            },
            "images": {
                **refreshed_report["images"],
                "running_refs": sorted(running_refs),
                "kept_refs": refreshed_plan["keep_refs"],
            },
        }

    _write_status(args.status_path, report)
    print(json.dumps(report, indent=2, sort_keys=True))

    fail_on_warning = getattr(args, "fail_on_warning", False)
    if fail_on_warning and not report["ok"]:
        return 1
    if args.command == "cleanup" and report.get("actions", {}).get("removal_errors"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
