from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

with patch("openai.AsyncOpenAI"):
    from matcher import apply_initial_content_budget
    from pipeline import (
        eligible_fill_candidates,
        enforce_item_minimums,
        greedily_fill_one_page,
        layout_constraint_violations,
        section_line_counts,
    )
    from report import write_report

from resume_models import ResumeData


class ExperienceLimitTests(unittest.TestCase):
    def setUp(self):
        # Pin the scenario; production budgets are intentionally user-editable.
        self.enterContext(patch.multiple(
            "config", MIN_BULLETS_PER_EXPERIENCE=2, MAX_BULLETS_PER_EXPERIENCE=5,
            MIN_EXPERIENCE_LINES=3, MAX_EXPERIENCE_LINES=11,
        ))
        self.enterContext(patch.dict("config.EXPERIENCE_LIMIT_OVERRIDES", {
            "exp2": {"min_bullets": 3, "min_lines": 4},
            "exp3": {"max_bullets": 2, "min_lines": 2, "max_lines": 4},
        }, clear=True))
        self.resume = ResumeData.model_validate({
            "personal": {"name": "Test Candidate"},
            "experience": [
                {
                    "id": f"exp{i}",
                    "company": "TRREB" if i == 3 else "Example",
                    "title": "Engineer",
                    "bullets": [
                        {"id": f"exp{i}.b{j}", "text": "x" * (106 if i == 2 and j == 1 else 105)}
                        for j in range(1, 6)
                    ],
                }
                for i in range(1, 4)
            ],
            "projects": [
                {
                    "id": "proj1", "name": "Capstone",
                    "bullets": [
                        {"id": "proj1.b1", "text": "Technical work."},
                        {"id": "proj1.b8", "text": "Placed 2nd overall."},
                        {"id": "proj1.b2", "text": "Another technical contribution."},
                    ],
                },
                {
                    "id": "proj2", "name": "CUDA",
                    "bullets": [
                        {"id": "proj2.b1", "text": "CPU reference."},
                        {"id": "proj2.b2", "text": "GPU implementation."},
                    ],
                },
            ],
        })
        self.match = {
            "item_scores": [],
            "bullet_scores": [
                {"bullet_id": bid, "score": 100 - index}
                for index, bid in enumerate(self.resume.all_bullets())
            ],
            "rewrite_bullet_ids": [],
        }

    def seed(self):
        apply_initial_content_budget(self.resume, self.match)
        return {
            bid: self.resume.all_bullets()[bid].text
            for bid in self.match["selected_bullet_ids"]
        }

    def test_seed_respects_each_employers_bullet_and_line_minimums(self):
        bullets = self.seed()
        self.assertEqual(sum(b.startswith("exp3.") for b in bullets), 2)
        self.assertEqual(sum(b.startswith("exp1.") for b in bullets), 3)
        self.assertEqual(sum(b.startswith("exp2.") for b in bullets), 3)
        self.assertEqual(section_line_counts(self.resume, bullets)[0]["exp2"], 4)
        self.assertEqual(layout_constraint_violations(self.resume, self.match, bullets), [])

    def test_seed_skips_high_scoring_bullet_that_would_exceed_four_lines(self):
        trreb = self.resume.experience[2]
        trreb.bullets[0].text = "a" * 211  # Three estimated lines.
        trreb.bullets[1].text = "b" * 211
        bullets = self.seed()
        self.assertEqual(
            {bid for bid in bullets if bid.startswith("exp3.")},
            {"exp3.b1", "exp3.b3"},
        )
        self.assertEqual(section_line_counts(self.resume, bullets)[0]["exp3"], 4)

    def test_filler_respects_both_trreb_caps_and_keeps_other_jobs_eligible(self):
        bullets = self.seed()
        candidates = eligible_fill_candidates(self.resume, self.match, bullets, set())
        self.assertFalse(any(bid.startswith("exp3.") for bid in candidates))
        self.assertIn("exp2.b4", candidates)

        bullets.pop("exp3.b2")
        self.resume.experience[2].bullets[1].text = "y" * 316
        candidates = eligible_fill_candidates(self.resume, self.match, bullets, set())
        self.assertNotIn("exp3.b2", candidates)  # Would total five lines.
        self.assertIn("exp3.b3", candidates)

    def test_minimum_repair_does_not_add_a_third_trreb_bullet(self):
        bullets = self.seed()
        bullets.pop("exp3.b2")
        available = {bid: b.text for bid, b in self.resume.all_bullets().items()}
        added = enforce_item_minimums(self.resume, self.match, bullets, available)
        self.assertEqual(added, ["exp3.b2"])
        self.assertEqual(sum(b.startswith("exp3.") for b in bullets), 2)

    def test_new_trreb_entry_is_filled_as_a_two_bullet_package(self):
        bullets = self.seed()
        bullets = {bid: text for bid, text in bullets.items() if not bid.startswith("exp3.")}
        self.match["selected_experience_ids"].remove("exp3")
        self.match["selected_bullet_ids"] = list(bullets)
        available = {bid: b.text for bid, b in self.resume.all_bullets().items()}
        compiled_counts = []

        def compile_stub(path, resume, match, active):
            self.assertEqual(layout_constraint_violations(resume, match, active), [])
            compiled_counts.append(sum(b.startswith("exp3.") for b in active))
            return None, 1, ""

        with patch("pipeline.compile_current", side_effect=compile_stub):
            result, pages, warnings = greedily_fill_one_page(
                job_name="test", tex_path=Path("unused.tex"), resume=self.resume,
                match=self.match, bullets=bullets, available_texts=available,
            )
        self.assertEqual(pages, 1)
        self.assertEqual(warnings, [])
        self.assertEqual(sum(b.startswith("exp3.") for b in result), 2)
        self.assertTrue(all(count in (0, 2) for count in compiled_counts))

    def test_validation_and_report_reject_excess_bullets_or_lines(self):
        for case in ("valid", "bullets", "lines"):
            with self.subTest(case=case):
                bullets = self.seed()
                if case == "bullets":
                    bullets["exp3.b3"] = "Third bullet."
                elif case == "lines":
                    bullets["exp3.b1"] = "x" * 211
                    bullets["exp3.b2"] = "y" * 106
                violations = layout_constraint_violations(self.resume, self.match, bullets)
                if case == "valid":
                    self.assertEqual(violations, [])
                else:
                    self.assertTrue(any(v.startswith("exp3:") and case in v for v in violations))

                with tempfile.TemporaryDirectory() as temp:
                    path = Path(temp) / "report.txt"
                    write_report(
                        path, job_name="test", analysis={}, match=self.match,
                        resume=self.resume, bullets=bullets, qa={}, truth={},
                        keyword_coverage={}, pages=1, usages=[], warnings=[],
                    )
                    density = path.read_text(encoding="utf-8").split("EXPERIENCE DENSITY")[1]
                trreb_line = next(line for line in density.splitlines() if line.startswith("- exp3:"))
                self.assertIn("[PASS]" if case == "valid" else "[FAIL]", trreb_line)


if __name__ == "__main__":
    unittest.main()
