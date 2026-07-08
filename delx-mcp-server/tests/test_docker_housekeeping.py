import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docker_housekeeping import build_cleanup_plan, build_status_report, parse_size_to_bytes


class DockerHousekeepingTests(unittest.TestCase):
    def test_build_cleanup_plan_keeps_running_image_and_latest_tags(self):
        images = [
            {
                "repository": "delx-mcp",
                "tag": "20260311113501-dxfixes",
                "created_at": "2026-03-11 11:35:01 +0000 UTC",
                "size": "461MB",
            },
            {
                "repository": "delx-mcp",
                "tag": "20260311112640-dxfixes",
                "created_at": "2026-03-11 11:26:40 +0000 UTC",
                "size": "461MB",
            },
            {
                "repository": "delx-mcp",
                "tag": "20260311112403-dxfixes",
                "created_at": "2026-03-11 11:24:03 +0000 UTC",
                "size": "461MB",
            },
            {
                "repository": "delx-mcp",
                "tag": "20260311111928-dxfixes",
                "created_at": "2026-03-11 11:19:28 +0000 UTC",
                "size": "461MB",
            },
            {
                "repository": "delx-mcp",
                "tag": "20260310120057-dxfixes-nocache",
                "created_at": "2026-03-10 12:00:57 +0000 UTC",
                "size": "557MB",
            },
            {
                "repository": "python",
                "tag": "3.12-slim",
                "created_at": "2026-03-04 09:00:00 +0000 UTC",
                "size": "119MB",
            },
        ]

        plan = build_cleanup_plan(
            images,
            running_refs={"delx-mcp:20260311113501-dxfixes"},
            repository="delx-mcp",
            keep_latest=2,
        )

        self.assertEqual(
            plan["keep_refs"],
            [
                "delx-mcp:20260311113501-dxfixes",
                "delx-mcp:20260311112640-dxfixes",
            ],
        )
        self.assertEqual(
            plan["remove_refs"],
            [
                "delx-mcp:20260311112403-dxfixes",
                "delx-mcp:20260311111928-dxfixes",
                "delx-mcp:20260310120057-dxfixes-nocache",
            ],
        )
        self.assertEqual(plan["reclaimable_bytes"], parse_size_to_bytes("1479MB"))

    def test_build_cleanup_plan_keeps_running_tag_even_when_not_in_latest_window(self):
        images = [
            {
                "repository": "delx-mcp",
                "tag": "newest",
                "created_at": "2026-03-11 11:35:01 +0000 UTC",
                "size": "461MB",
            },
            {
                "repository": "delx-mcp",
                "tag": "second",
                "created_at": "2026-03-11 11:26:40 +0000 UTC",
                "size": "461MB",
            },
            {
                "repository": "delx-mcp",
                "tag": "running-old",
                "created_at": "2026-03-09 11:26:40 +0000 UTC",
                "size": "557MB",
            },
        ]

        plan = build_cleanup_plan(
            images,
            running_refs={"delx-mcp:running-old"},
            repository="delx-mcp",
            keep_latest=1,
        )

        self.assertEqual(
            plan["keep_refs"],
            [
                "delx-mcp:newest",
                "delx-mcp:running-old",
            ],
        )
        self.assertEqual(plan["remove_refs"], ["delx-mcp:second"])

    def test_build_status_report_warns_on_disk_threshold_and_unused_images(self):
        report = build_status_report(
            disk_total_bytes=100,
            disk_used_bytes=86,
            running_refs={"delx-mcp:active"},
            cleanup_plan={
                "images_total": 9,
                "keep_refs": ["delx-mcp:active", "delx-mcp:prev"],
                "remove_refs": ["delx-mcp:old-1", "delx-mcp:old-2", "delx-mcp:old-3"],
                "reclaimable_bytes": parse_size_to_bytes("1.5GB"),
            },
            warn_disk_percent=85,
            warn_unused_images=3,
        )

        self.assertFalse(report["ok"])
        self.assertEqual(report["disk"]["used_percent"], 86.0)
        self.assertEqual(report["images"]["removal_candidate_count"], 3)
        self.assertEqual(report["images"]["reclaimable_bytes"], parse_size_to_bytes("1.5GB"))
        self.assertEqual(len(report["warnings"]), 2)
        self.assertIn("disk_used_percent>=85", report["warnings"][0])
        self.assertIn("unused_image_count>=3", report["warnings"][1])

    def test_parse_size_to_bytes_supports_decimal_units(self):
        self.assertEqual(parse_size_to_bytes("461MB"), 461_000_000)
        self.assertEqual(parse_size_to_bytes("1.5GB"), 1_500_000_000)
        self.assertEqual(parse_size_to_bytes("842B"), 842)


if __name__ == "__main__":
    unittest.main()
