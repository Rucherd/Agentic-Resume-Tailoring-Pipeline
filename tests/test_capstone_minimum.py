from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

with patch("openai.AsyncOpenAI"):
    from matcher import apply_initial_content_budget
    from pipeline import (
        drop_candidate_packages, enforce_item_minimums, fill_package_for_bullet,
        layout_constraint_violations, rebalance_one_page,
    )
    from report import write_report

from resume_models import ResumeData
from section_balance import balance_policy


class CapstoneMinimumTests(unittest.TestCase):
    def setUp(self):
        self.resume = ResumeData.model_validate({
            "personal": {"name": "Test Candidate"},
            "experience": [
                {"id": f"exp{i}", "company": "Example", "title": "Engineer",
                 "bullets": [{"id": f"exp{i}.b{j}", "text": "x" * 120} for j in range(1, 4)]}
                for i in (1, 2)
            ],
            "projects": [
                {"id": "proj1", "name": "V2I Capstone", "bullets": [
                    {"id": f"proj1.b{j}", "text": "x" * 120} for j in range(1, 5)
                ] + [{"id": "proj1.b8", "text": "Placed 2nd overall. " + "x" * 100}]},
                {"id": "proj2", "name": "CUDA", "bullets": [
                    {"id": f"proj2.b{j}", "text": "x" * 120} for j in (1, 2)
                ]},
            ],
        })
        self.available = {bid: b.text for bid, b in self.resume.all_bullets().items()}
        self.match = {
            "item_scores": [],
            "bullet_scores": [{"bullet_id": bid, "score": 0 if bid == "proj1.b8" else 90 - i}
                              for i, bid in enumerate(self.available)],
            "rewrite_bullet_ids": list(self.available),
        }
        apply_initial_content_budget(self.resume, self.match)
        self.bullets = {bid: self.available[bid] for bid in self.match["selected_bullet_ids"]}

    def capstone_ids(self, bullets):
        return {bid for bid in bullets if bid.startswith("proj1.")}

    def test_seed_keeps_award_and_two_technical_bullets_within_six_lines(self):
        self.assertEqual(len(self.capstone_ids(self.bullets)), 3)
        self.assertIn("proj1.b8", self.bullets)
        self.assertNotIn("proj1.b8", self.match["rewrite_bullet_ids"])
        self.assertEqual(sum(bid.startswith("proj2.") for bid in self.bullets), 2)
        self.assertEqual(layout_constraint_violations(self.resume, self.match, self.bullets), [])

    def test_minimum_repair_and_new_entry_package_require_three_bullets(self):
        for bid in self.capstone_ids(self.bullets) - {"proj1.b8"}:
            self.bullets.pop(bid)
        added = enforce_item_minimums(self.resume, self.match, self.bullets, self.available)
        self.assertEqual(len(added), 2)
        self.assertEqual(len(self.capstone_ids(self.bullets)), 3)
        package = fill_package_for_bullet(self.resume, self.match, {}, self.available, "proj1.b8")
        self.assertEqual(len(package), 3)
        self.assertIn("proj1.b8", package)

    def test_trimming_can_remove_fourth_bullet_but_never_reduce_capstone_to_two(self):
        packages = drop_candidate_packages(self.resume, self.match, self.bullets)
        self.assertFalse(any(self.capstone_ids(package) for package in packages))
        technical = next(bid for bid in self.capstone_ids(self.bullets) if bid != "proj1.b8")
        self.bullets[technical] = "Short technical contribution."
        self.bullets["proj1.b4"] = "Additional contribution."
        self.assertEqual(len(self.capstone_ids(self.bullets)), 4)
        packages = drop_candidate_packages(self.resume, self.match, self.bullets)
        capstone_drops = [package for package in packages if self.capstone_ids(package)]
        self.assertTrue(capstone_drops)
        self.assertTrue(all(len(package) == 1 and "proj1.b8" not in package for package in capstone_drops))

    def test_ratio_preferences_cannot_override_three_bullet_floor(self):
        for profile in ("software", "firmware_embedded", "gpu_ai_compiler"):
            with self.subTest(profile=profile):
                self.match["section_balance_policy"] = balance_policy({"layout_profile": profile})
                with patch("pipeline.compile_current", return_value=(None, 1, "")):
                    result, _, _ = rebalance_one_page(
                        job_name="test", tex_path=Path("unused.tex"), resume=self.resume,
                        match=self.match, bullets=dict(self.bullets), available_texts=self.available,
                    )
                self.assertGreaterEqual(len(self.capstone_ids(result)), 3)
                self.assertEqual(result["proj1.b8"], self.available["proj1.b8"])

    def test_two_bullets_fail_both_layout_validation_and_report(self):
        self.bullets.pop(next(bid for bid in self.capstone_ids(self.bullets) if bid != "proj1.b8"))
        violations = layout_constraint_violations(self.resume, self.match, self.bullets)
        self.assertIn("proj1: 2 project bullets outside 3-4", violations)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "report.txt"
            write_report(path, job_name="test", analysis={}, match=self.match,
                         resume=self.resume, bullets=self.bullets, qa={}, truth={},
                         keyword_coverage={}, pages=1, usages=[], warnings=[])
            density = path.read_text(encoding="utf-8").split("PROJECT DENSITY")[1]
        capstone_line = next(line for line in density.splitlines() if line.startswith("- proj1:"))
        self.assertIn("capstone_award=YES [FAIL]", capstone_line)


if __name__ == "__main__":
    unittest.main()
