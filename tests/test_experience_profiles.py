import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from experience_profiles import prepare_job_resume
from resume_models import ResumeData

with patch('openai.AsyncOpenAI'):
    import pipeline
    from matcher import apply_initial_content_budget, compact_resume, sanitize_match
    from renderer import render_resume
    from report import write_report


ROOT = Path(__file__).resolve().parents[1]


class ExperienceProfileTests(unittest.TestCase):
    def setUp(self):
        self.master = ResumeData.model_validate_json(
            (ROOT / 'resume_data.json').read_text(encoding='utf-8')
        )
        self.entry = next(e for e in self.master.experience if e.company == 'Miniveil Systems')

    def test_default_is_software_even_for_firmware_posting(self):
        resume, text, profile = prepare_job_resume(self.master, 'Firmware engineer with STM32 experience')
        entry = next(e for e in resume.experience if e.id == self.entry.id)
        self.assertEqual(profile['profile'], 'software')
        self.assertEqual(profile['selected_by'], 'default')
        self.assertEqual(entry.title, 'Software Systems Engineer')
        self.assertEqual(entry.bullets, self.entry.profiles['software'].bullets)
        self.assertEqual(entry.profiles, {})
        self.assertEqual(text, 'Firmware engineer with STM32 experience')

    def test_header_cli_and_config_precedence(self):
        scenarios = [
            ('\ufeff\nMiniveil: Firmware\n\nActual job', None, 'firmware', 'job header'),
            ('Miniveil: software\r\nActual job', 'FIRMWARE', 'firmware', 'command line'),
            ('Miniveil: firmware\nActual job', 'software', 'software', 'command line'),
            ('Actual job', None, 'software', 'default'),
        ]
        for text, override, expected, source in scenarios:
            with self.subTest(text=text, override=override):
                resume, cleaned, metadata = prepare_job_resume(self.master, text, override)
                self.assertEqual(metadata['profile'], expected)
                self.assertEqual(metadata['selected_by'], source)
                self.assertEqual(cleaned.strip(), 'Actual job')
                self.assertEqual(resume.experience[0].title, self.entry.profiles[expected].title)
        with patch('config.DEFAULT_MINIVEIL_PROFILE', 'firmware'):
            self.assertEqual(prepare_job_resume(self.master, 'Job')[2]['profile'], 'firmware')
        body = 'Actual job description\nMiniveil: firmware\nMore job content'
        self.assertEqual(prepare_job_resume(self.master, body)[2]['profile'], 'software')
        self.assertEqual(prepare_job_resume(self.master, body)[1], body)

    def test_invalid_or_missing_pool_fails_instead_of_mixing(self):
        for text, override in [('Miniveil: firmwre\nJob', None), ('Job', 'automatic')]:
            with self.subTest(text=text, override=override), self.assertRaisesRegex(ValueError, 'software.*firmware'):
                prepare_job_resume(self.master, text, override)
        del self.entry.profiles['software']
        with self.assertRaisesRegex(ValueError, 'missing.*software'):
            prepare_job_resume(self.master, 'Job')

    def test_profiles_use_independent_copies_and_preserve_other_experiences(self):
        original = self.master.model_dump()
        software = prepare_job_resume(self.master, 'Job')[0]
        firmware = prepare_job_resume(self.master, 'Miniveil: firmware\nJob')[0]
        self.assertEqual(firmware.experience[0].title, 'Firmware Engineer')
        self.assertEqual(firmware.experience[0].bullets, self.entry.profiles['firmware'].bullets)
        self.assertEqual(software.experience[1:], self.master.experience[1:])
        self.assertEqual(software.projects, self.master.projects)
        software.experience[0].bullets[0].text = 'Changed one job only'
        self.assertEqual(self.master.model_dump(), original)
        self.assertNotEqual(firmware.experience[0].bullets[0].text, 'Changed one job only')

    def test_inactive_ids_cannot_reach_matching_seed_or_filler(self):
        for profile in ('software', 'firmware'):
            with self.subTest(profile=profile):
                active = prepare_job_resume(self.master, 'Job', profile)[0]
                inactive = 'firmware' if profile == 'software' else 'software'
                excluded = {b.id for b in self.entry.profiles[inactive].bullets}
                allowed = {b.id for b in self.entry.profiles[profile].bullets}
                payload = compact_resume(active)
                miniveil = next(e for e in payload['experience'] if e['id'] == self.entry.id)
                self.assertEqual({b['id'] for b in miniveil['bullets']}, allowed)
                match, warnings = sanitize_match({
                    'selected_bullet_ids': list(allowed | excluded),
                    'rewrite_bullet_ids': list(allowed | excluded),
                    'bullet_scores': [{'bullet_id': bid, 'score': 100} for bid in excluded],
                }, active)
                self.assertEqual(set(match['selected_bullet_ids']), allowed)
                self.assertEqual(set(match['rewrite_bullet_ids']), allowed)
                self.assertEqual(match['bullet_scores'], [])
                self.assertTrue(warnings)
                match = apply_initial_content_budget(active, match)
                bullets = {bid: active.all_bullets()[bid].text for bid in match['selected_bullet_ids']}
                candidates = pipeline.eligible_fill_candidates(active, match, bullets, set())
                self.assertFalse(excluded & set(match['selected_bullet_ids']))
                self.assertFalse(excluded & set(candidates))

    def test_active_title_is_rendered_and_reported(self):
        for profile in ('software', 'firmware'):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as directory:
                active, _, metadata = prepare_job_resume(self.master, 'Job', profile)
                match = apply_initial_content_budget(active, {'item_scores': [], 'bullet_scores': [], 'rewrite_bullet_ids': []})
                match['miniveil_profile'] = metadata
                bullets = {bid: active.all_bullets()[bid].text for bid in match['selected_bullet_ids']}
                tex = render_resume(ROOT / 'template.tex', active, match, bullets)
                self.assertIn('{' + metadata['title'] + '}', tex)
                other_title = 'Firmware Engineer' if profile == 'software' else 'Software Systems Engineer'
                self.assertNotIn('{' + other_title + '}', tex)
                report_path = Path(directory) / 'report.txt'
                write_report(report_path, job_name='test', analysis={}, match=match, resume=active,
                             bullets=bullets, qa={}, truth={}, keyword_coverage={}, pages=1, usages=[], warnings=[])
                self.assertIn(f"MINIVEIL PROFILE: {profile} — {metadata['title']}", report_path.read_text(encoding='utf-8'))

    def test_duplicate_active_ids_are_rejected(self):
        pool = self.entry.profiles['software']
        pool.bullets[1].id = pool.bullets[0].id
        with self.assertRaisesRegex(ValueError, 'duplicate bullet IDs'):
            prepare_job_resume(self.master, 'Job')


class ProfilePipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_jobs_resolve_before_analysis_and_matching(self):
        master = ResumeData.model_validate_json((ROOT / 'resume_data.json').read_text(encoding='utf-8'))
        original = master.model_dump()
        seen = {}

        class StopAfterMatching(Exception):
            pass

        async def analyze(text, name):
            self.assertNotIn('Miniveil:', text)
            return {'job_name': name}, None

        async def match(resume, analysis):
            await asyncio.sleep(0)  # Both jobs overlap before inspecting sources.
            seen[analysis['job_name']] = compact_resume(resume)['experience'][0]
            raise StopAfterMatching()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            software = root / 'software.txt'
            firmware = root / 'firmware.txt'
            software.write_text('Software posting', encoding='utf-8')
            firmware.write_text('Miniveil: firmware\nFirmware posting', encoding='utf-8')
            with patch.object(pipeline, 'OUTPUT_DIR', root / 'output'), \
                 patch.object(pipeline, 'analyze_job', side_effect=analyze), \
                 patch.object(pipeline, 'match_resume', side_effect=match):
                results = await asyncio.gather(pipeline.process_job(software, master),
                                               pipeline.process_job(firmware, master), return_exceptions=True)
            self.assertTrue(all(isinstance(result, StopAfterMatching) for result in results), results)
            self.assertEqual(seen['software']['title'], 'Software Systems Engineer')
            self.assertEqual(seen['firmware']['title'], 'Firmware Engineer')
            self.assertFalse({b['id'] for b in seen['software']['bullets']} & {b['id'] for b in seen['firmware']['bullets']})
            self.assertEqual(master.model_dump(), original)

    async def test_bad_header_makes_no_api_calls_or_output(self):
        master = ResumeData.model_validate({'personal': {'name': 'Test'}})
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory) / 'bad.txt'
            job.write_text('Miniveil: firmwre\nJob', encoding='utf-8')
            output = Path(directory) / 'output'
            with patch.object(pipeline, 'OUTPUT_DIR', output), \
                 patch.object(pipeline, 'analyze_job', new_callable=AsyncMock) as analyze:
                with self.assertRaises(ValueError):
                    await pipeline.process_job(job, master)
                analyze.assert_not_called()
                self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
