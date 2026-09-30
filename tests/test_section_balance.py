import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

with patch("openai.AsyncOpenAI"):
    from pipeline import (
        eligible_fill_candidates, greedily_fill_one_page, layout_constraint_violations,
        pick_drop_candidate, pick_page_compression_ids, rebalance_one_page,
    )
    from report import write_report

from resume_models import ResumeData
from section_balance import balance_policy, balance_summary, content_balance_distance


class SectionBalanceTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.multiple(
            "config", MIN_BULLETS_PER_EXPERIENCE=2, MAX_BULLETS_PER_EXPERIENCE=6,
            MIN_EXPERIENCE_LINES=2, MAX_EXPERIENCE_LINES=12,
        ))
        self.enterContext(patch.dict("config.EXPERIENCE_LIMIT_OVERRIDES", {}, clear=True))
        self.enterContext(patch("config.MAX_PROJECT_LINES", 6))
        self.resume = ResumeData.model_validate({
            "personal": {"name": "Test Candidate"},
            "experience": [
                {"id": f"exp{i}", "company": f"Employer {i}", "title": "Engineer",
                 "bullets": [{"id": f"exp{i}.b{j}", "text": "x" * 120} for j in range(1, 6)]}
                for i in range(1, 4)
            ],
            "projects": [
                {"id": f"proj{i}", "name": f"Project {i}",
                 "bullets": [{"id": f"proj{i}.b{j}", "text": "x" * 120} for j in range(1, 4)]}
                for i in range(1, 4)
            ],
        })
        self.resume.projects[0].bullets[1].id = "proj1.b8"
        self.resume.projects[0].bullets[1].text = "Placed 2nd overall."
        self.resume.projects[0].bullets[2].text = "Another technical contribution."
        self.available = {bid: b.text for bid, b in self.resume.all_bullets().items()}
        self.match = {
            "selected_experience_ids": ["exp1", "exp2", "exp3"],
            "selected_project_ids": ["proj1", "proj2", "proj3"],
            "selected_bullet_ids": [],
            "item_scores": [],
            "bullet_scores": [{"bullet_id": bid, "score": 80} for bid in self.available],
            "section_balance_policy": balance_policy({"layout_profile": "software"}),
        }

    def active(self, exp_depth=2, project_depth=2):
        result = {}
        for entry in self.resume.experience:
            result.update({b.id: b.text for b in entry.bullets[:exp_depth]})
        for entry in self.resume.projects:
            depth = max(3, project_depth) if entry.id == "proj1" else project_depth
            result.update({b.id: b.text for b in entry.bullets[:depth]})
        self.match["selected_bullet_ids"] = list(result)
        return result

    def test_role_classification_uses_main_work_not_incidental_keywords(self):
        for title, profile, target in [
            ("Software Engineer", "software", .65),
            ("Firmware Engineer", "firmware_embedded", .70),
            ("Embedded Software Intern", "firmware_embedded", .70),
            ("CUDA Developer", "gpu_ai_compiler", .58),
            ("GPU Compiler Engineer", "gpu_ai_compiler", .58),
            ("Machine Learning Engineer", "gpu_ai_compiler", .58),
            ("AI Engineer", "gpu_ai_compiler", .58),
            ("Cloud Infrastructure Engineer", "software", .65),
        ]:
            with self.subTest(title=title):
                result = balance_policy({"role_title": title, "preferred": ["Copilot", "CUDA", "GCC"]})
                self.assertEqual((result["profile"], result["experience_target"]), (profile, target))
        self.assertEqual(balance_policy({
            "role_title": "Software Engineer", "layout_profile": "gpu_ai_compiler"
        })["profile"], "gpu_ai_compiler")

    def test_same_content_favors_different_sections_for_firmware_and_ai(self):
        bullets = self.active(exp_depth=3)  # 18 experience / 12 project lines.
        for profile, prefix in [("firmware_embedded", "exp"), ("gpu_ai_compiler", "proj")]:
            with self.subTest(profile=profile):
                self.match["section_balance_policy"] = balance_policy({"layout_profile": profile})
                ranked = eligible_fill_candidates(self.resume, self.match, bullets, set(), self.available)
                self.assertTrue(ranked[0].startswith(prefix))
        self.assertEqual(self.match["bullet_scores"][0]["score"], 80)

    def test_fill_uses_prepared_text_and_does_not_drift_away_from_target(self):
        bullets = self.active(exp_depth=3)
        # Long prepared text must not slip through using its shorter source size.
        available = {**self.available, "exp1.b4": "x" * 2000}
        ranked = eligible_fill_candidates(self.resume, self.match, bullets, set(), available)
        self.assertNotIn("exp1.b4", ranked)
        self.match["section_balance_policy"] = balance_policy({"layout_profile": "firmware_embedded"})
        # Only project extras available: adding them would move further from 70%.
        available = {bid: text for bid, text in self.available.items() if bid.startswith("proj")}
        with patch("pipeline.compile_current", return_value=(None, 1, "")):
            result, _, _ = greedily_fill_one_page(
                job_name="test", tex_path=Path("unused.tex"), resume=self.resume,
                match=self.match, bullets=bullets.copy(), available_texts=available,
            )
        self.assertEqual(result, bullets)

    def test_compression_and_trimming_protect_the_underrepresented_section(self):
        bullets = self.active(exp_depth=3, project_depth=3)  # 50/50.
        self.match["section_balance_policy"] = balance_policy({"layout_profile": "firmware_embedded"})
        self.assertTrue(all(b.startswith("proj") for b in pick_page_compression_ids(self.resume, self.match, bullets)))
        self.assertTrue(all(b.startswith("proj") for b in pick_drop_candidate(self.resume, self.match, bullets)))
        bullets = self.active(exp_depth=4, project_depth=2)  # 67/33.
        self.match["section_balance_policy"] = balance_policy({"layout_profile": "gpu_ai_compiler"})
        self.assertTrue(all(b.startswith("exp") for b in pick_drop_candidate(self.resume, self.match, bullets)))
        self.assertNotIn("proj1.b8", pick_page_compression_ids(self.resume, self.match, bullets))

    def test_full_page_can_swap_project_depth_for_experience(self):
        bullets = self.active(exp_depth=2, project_depth=3)
        initial_count = len(bullets)
        initial_distance = content_balance_distance(self.resume, self.match, bullets)
        def compile_stub(path, resume, match, active):
            self.assertEqual(layout_constraint_violations(resume, match, active), [])
            self.assertIn("proj1.b8", active)
            return None, 1 if len(active) <= initial_count else 2, ""
        with patch("pipeline.compile_current", side_effect=compile_stub):
            result, pages, warnings = rebalance_one_page(
                job_name="test", tex_path=Path("unused.tex"), resume=self.resume,
                match=self.match, bullets=bullets, available_texts=self.available,
            )
        self.assertEqual(pages, 1)
        self.assertEqual(warnings, [])
        self.assertLess(content_balance_distance(self.resume, self.match, result), initial_distance)
        self.assertEqual(len(result), initial_count)
        self.assertEqual(set(self.match["selected_bullet_ids"]), set(result))

    def test_failed_balance_trials_restore_original_artifact_and_selection(self):
        bullets = self.active(exp_depth=2, project_depth=3)
        original_match = copy.deepcopy(self.match)
        rendered = []
        def compile_stub(path, resume, match, active):
            rendered.append(dict(active))
            return None, 1 if active == bullets else 2, ""
        with patch("pipeline.compile_current", side_effect=compile_stub):
            result, pages, _ = rebalance_one_page(
                job_name="test", tex_path=Path("unused.tex"), resume=self.resume,
                match=self.match, bullets=bullets, available_texts=self.available,
            )
        self.assertEqual(result, bullets)
        self.assertEqual(self.match, original_match)
        self.assertEqual(rendered[-1], bullets)
        self.assertEqual(pages, 1)

    def test_reports_measure_final_wording_and_treat_ratio_as_soft(self):
        bullets = self.active(exp_depth=2, project_depth=2)
        self.assertEqual(layout_constraint_violations(self.resume, self.match, bullets), [])
        self.assertFalse(balance_summary(self.resume, self.match, bullets)["within_target_band"])
        self.match["section_balance"] = {"experience_share": .99}  # Stale cache ignored.
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "report.txt"
            write_report(path, job_name="test", analysis={}, match=self.match,
                         resume=self.resume, bullets=bullets, qa={}, truth={},
                         keyword_coverage={}, pages=1, usages=[], warnings=[])
            report = path.read_text(encoding="utf-8")
        self.assertIn("Target: 65% experience / 35% projects", report)
        self.assertIn("Actual: 50.0% experience / 50.0% projects", report)
        self.assertIn("Soft target", report)


if __name__ == "__main__":
    unittest.main()
