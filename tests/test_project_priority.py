import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

# Selection and report tests are offline; importing the service must not need
# an API key or instantiate a live client.
with patch("openai.AsyncOpenAI"):
    from matcher import apply_initial_content_budget
    from pipeline import (
        eligible_fill_candidates,
        layout_constraint_violations,
        pick_drop_candidate,
        pick_page_compression_ids,
    )
    from report import write_report

from config import CAPSTONE_AWARD_BULLET_ID, PROJECT_PRIORITIES
from project_priority import project_priority
from resume_models import ResumeData


class ProjectPriorityTests(unittest.TestCase):
    def setUp(self):
        # Pin this test scenario rather than depending on the user's editable
        # production priorities and weight.
        self.enterContext(patch("project_priority.PROJECT_PRIORITY_WEIGHT", 0.05))
        self.enterContext(patch("matcher.PROJECT_PRIORITY_WEIGHT", 0.05))
        self.enterContext(patch.dict(PROJECT_PRIORITIES, {
            "proj1": 10.0, "proj2": 9.0, "proj3": 8.5, "proj4": 8.5,
            "proj5": 7.0, "proj6": 7.0, "proj7": 6.0, "proj8": 7.5,
        }, clear=True))

        def bullets(item_id):
            ids = [f"{item_id}.b1", f"{item_id}.b2"]
            ids.append(CAPSTONE_AWARD_BULLET_ID if item_id == "proj1" else f"{item_id}.b3")
            return [{"id": bid, "text": "Earned 2nd place. " + "x" * 105} for bid in ids]

        self.resume = ResumeData.model_validate({
            "personal": {"name": "Test Candidate"},
            "experience": [
                {"id": f"exp{i}", "company": "Example", "title": "Engineer",
                 "bullets": bullets(f"exp{i}")}
                for i in range(1, 4)
            ],
            "projects": [
                {"id": pid, "name": pid, "bullets": bullets(pid)}
                for pid in PROJECT_PRIORITIES
            ],
        })

    def match(self, portfolio_score=81):
        scores = {item_id: 50 for item_id in self.resume.all_item_ids()}
        scores.update(proj1=0, proj2=80, proj3=90, proj7=portfolio_score)
        return {
            "item_scores": [{"item_id": pid, "score": score} for pid, score in scores.items()],
            "bullet_scores": [
                {"bullet_id": bid, "score": scores[bid.split(".")[0]]}
                for bid in self.resume.all_bullets()
            ],
            "rewrite_bullet_ids": list(self.resume.all_bullets()),
        }

    def active(self, *project_ids, depth=2):
        result = {}
        for project in self.resume.projects:
            if project.id not in project_ids:
                continue
            chosen = project.bullets[:depth]
            if project.id == "proj1":
                chosen = project.bullets[:]
            result.update({b.id: b.text for b in chosen})
        return result

    def test_seed_close_scores_favor_priority_and_preserve_raw_scores(self):
        match = self.match()
        raw = copy.deepcopy((match["item_scores"], match["bullet_scores"]))
        apply_initial_content_budget(self.resume, match)
        self.assertEqual(match["selected_project_ids"], ["proj1", "proj2", "proj3"])
        self.assertIn(CAPSTONE_AWARD_BULLET_ID, match["selected_bullet_ids"])
        self.assertNotIn(CAPSTONE_AWARD_BULLET_ID, match["rewrite_bullet_ids"])
        self.assertEqual((match["item_scores"], match["bullet_scores"]), raw)
        active = {bid: self.resume.all_bullets()[bid].text for bid in match["selected_bullet_ids"]}
        self.assertEqual(layout_constraint_violations(self.resume, match, active), [])
        first = copy.deepcopy(match)
        apply_initial_content_budget(self.resume, match)
        self.assertEqual(match, first)  # Never compound the preference on reuse.

    def test_stronger_low_priority_project_wins_seed(self):
        match = apply_initial_content_budget(self.resume, self.match(portfolio_score=83))
        self.assertEqual(match["selected_project_ids"], ["proj1", "proj3", "proj7"])

    def test_filler_preserves_eligibility_and_relevance_can_win(self):
        for score, winner, loser in [(81, "proj2", "proj7"), (83, "proj7", "proj2")]:
            with self.subTest(score=score):
                candidates = eligible_fill_candidates(
                    self.resume, self.match(score), self.active("proj1"), set()
                )
                self.assertLess(candidates.index(f"{winner}.b1"), candidates.index(f"{loser}.b1"))
        candidates = eligible_fill_candidates(
            self.resume, self.match(0), self.active("proj1"), set()
        )
        self.assertIn("proj7.b1", candidates)

    def test_whole_project_drop_uses_preferences_and_keeps_capstone(self):
        for score, dropped in [(81, "proj7"), (83, "proj2")]:
            with self.subTest(score=score):
                ids = pick_drop_candidate(
                    self.resume, self.match(score), self.active("proj1", "proj2", "proj7")
                )
                self.assertEqual(set(ids), {f"{dropped}.b1", f"{dropped}.b2"})

    def test_optional_depth_and_compression_use_same_soft_preference(self):
        active = self.active("proj1", "proj2", "proj7", depth=3)
        for score, lower_value in [(81, "proj7"), (83, "proj2")]:
            with self.subTest(score=score):
                match = self.match(score)
                dropped = pick_drop_candidate(self.resume, match, active)
                self.assertEqual(len(dropped), 1)
                self.assertTrue(dropped[0].startswith(lower_value + "."))
                # Isolate the optional projects when comparing compression.
                compressible = {bid: text for bid, text in active.items() if not bid.startswith("proj1.")}
                compressible[CAPSTONE_AWARD_BULLET_ID] = active[CAPSTONE_AWARD_BULLET_ID]
                compressed = pick_page_compression_ids(self.resume, match, compressible)
                self.assertEqual(len(compressed), 2)
                self.assertTrue(all(bid.startswith(lower_value + ".") for bid in compressed))

    def test_reports_expose_preferences_for_selected_and_unselected_projects(self):
        match = apply_initial_content_budget(self.resume, self.match())
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "report.txt"
            write_report(
                path, job_name="test", analysis={}, match=match, resume=self.resume,
                bullets={}, qa={}, truth={}, keyword_coverage={}, pages=None,
                usages=[], warnings=[],
            )
            report = path.read_text(encoding="utf-8")
        self.assertIn("95% relevance / 5% priority", report)
        self.assertIn("priority=9/10, relevance=80/100, selection=80.50/100 [selected]", report)
        self.assertIn("priority=6/10, relevance=81/100, selection=79.95/100 [not selected]", report)
        self.assertIn("[selected, mandatory]", report)

    def test_unrated_project_has_a_default_and_remains_selectable(self):
        project = self.resume.projects[-1].model_copy(deep=True)
        project.id = "proj9"
        for index, bullet in enumerate(project.bullets, 1):
            bullet.id = f"proj9.b{index}"
        self.resume.projects.append(project)
        match = self.match()
        next(row for row in match["item_scores"] if row["item_id"] == "proj9")["score"] = 99
        apply_initial_content_budget(self.resume, match)
        self.assertEqual(project_priority("proj9"), 7.5)
        self.assertIn("proj9", match["selected_project_ids"])


if __name__ == "__main__":
    unittest.main()
