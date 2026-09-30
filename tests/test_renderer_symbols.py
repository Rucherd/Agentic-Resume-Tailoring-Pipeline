from pathlib import Path
import shutil
import tempfile
import unittest

from pypdf import PdfReader

from compiler import compile_tex
from renderer import latex_escape, render_resume
from resume_models import ResumeData


ROOT = Path(__file__).resolve().parents[1]


class RendererSymbolTests(unittest.TestCase):
    def test_comparisons_and_special_characters_are_escaped_together(self):
        self.assertEqual(
            latex_escape('C# & Python: <1 hour, >90%'),
            r'C\# \& Python: \textless{}1 hour, \textgreater{}90\%',
        )

    @unittest.skipUnless(shutil.which('pdflatex'), 'pdflatex is required')
    def test_comparisons_survive_resume_pdf_rendering_and_text_extraction(self):
        bullet = 'Automated K-1 processing in C# and Python, cutting days to <1 hour; processed >100 forms.'
        resume = ResumeData.model_validate({
            'personal': {'name': 'Test Candidate'},
            'education': [{
                'id': 'edu1', 'institution': 'Test University', 'degree': 'Engineering',
            }],
            'experience': [{
                'id': 'exp1',
                'company': 'Test Company',
                'title': 'Engineer',
                'bullets': [{'id': 'exp1.b1', 'text': bullet}],
            }],
            'projects': [{
                'id': 'proj1', 'name': 'Test Project',
                'bullets': [{'id': 'proj1.b1', 'text': 'Built a document processor.'}],
            }],
            'skills': [{'category': 'Languages', 'items': ['C#', 'Python']}],
        })
        tex = render_resume(
            ROOT / 'template.tex',
            resume,
            {
                'selected_experience_ids': ['exp1'],
                'selected_project_ids': ['proj1'],
                'selected_bullet_ids': ['exp1.b1', 'proj1.b1'],
                'selected_skills': ['C#', 'Python'],
            },
            {'exp1.b1': bullet, 'proj1.b1': 'Built a document processor.'},
        )
        with tempfile.TemporaryDirectory() as directory:
            tex_path = Path(directory) / 'resume.tex'
            tex_path.write_text(tex, encoding='utf-8')
            pdf_path, error = compile_tex(tex_path)
            self.assertIsNotNone(pdf_path, error)
            reader = PdfReader(str(pdf_path))
            text = ' '.join(page.extract_text() for page in reader.pages)
            compact_text = ''.join(text.split())
            self.assertIn('<1hour', compact_text)
            self.assertIn('>100forms', compact_text)
            self.assertIn('C#', text)
            self.assertNotIn('\u00a1', text)
            self.assertNotIn('\u00bf', text)
            self.assertEqual(len(reader.pages), 1)
