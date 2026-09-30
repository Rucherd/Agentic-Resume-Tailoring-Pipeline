import unittest
from unittest.mock import patch

from renderer import _projects
from resume_models import ResumeData


class ProjectOrderTests(unittest.TestCase):
    def test_priority_overrides_dates_and_award_leads_capstone(self):
        projects = [
            {"id": "proj8", "name": "Pipeline", "date": "September 2026"},
            {"id": "proj1", "name": "Capstone", "date": "March 2026"},
            {"id": "proj3", "name": "GIS", "date": "2024"},
            {"id": "proj4", "name": "ML", "date": "Present"},
            {"id": "proj2", "name": "CUDA", "date": "August 2026"},
        ]
        for p in projects:
            p["bullets"] = [{"id": p["id"] + ".b1", "text": "Technical contribution."}]
        projects[1]["bullets"].append({"id": "proj1.b8", "text": "Placed 2nd overall."})
        resume = ResumeData.model_validate({"personal": {"name": "Test"}, "projects": projects})
        bullets = {bid: b.text for bid, b in resume.all_bullets().items()}
        with patch("renderer.project_priority", side_effect={"proj1": 10, "proj2": 9, "proj3": 8.5, "proj4": 8.5, "proj8": 7.5}.get):
            tex = _projects(resume, resume.all_item_ids(), set(bullets), bullets, [])
        expected = ["Capstone", "CUDA", "GIS", "ML", "Pipeline"]
        positions = [tex.index(name) for name in expected]
        self.assertEqual(positions, sorted(positions))
        self.assertLess(tex.index("Placed 2nd overall."), tex.index("Technical contribution."))
        self.assertEqual([p.id for p in resume.projects], [p["id"] for p in projects])
