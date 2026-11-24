"""
test_resume_diff.py
===================
The "What changed" list (compute_resume_diff) and how project headings are normalized
before rendering. A restyled project heading must never read as a removed + added project.
"""

import os
import sys
import unittest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import yaml

from config.latex_sanitizer import normalize_resume_data, project_key, split_project_heading
from main import compute_resume_diff


def as_yaml(data: dict) -> str:
    return yaml.dump(data, sort_keys=False, allow_unicode=True)


ORIGINAL = {
    "name": "Jane",
    "projects": [
        {"name": "LetsCelebrate", "stack": ["React", "Node.js"], "highlights": ["Built a party planner"]},
        {"name": "Litho-Guessr", "stack": ["Express", "MongoDB"], "highlights": ["Made a map game"]},
        {"name": "SubDub", "stack": ["Node.js", "JWT"], "highlights": ["Tracked subscriptions"]},
    ],
}


def labels(changes):
    return [c["label"] for c in changes]


class TestProjectHeadings(unittest.TestCase):
    def test_split_moves_the_stack_out_of_the_name(self):
        self.assertEqual(split_project_heading("LetsCelebrate | React, Google Gemini, Node.js"),
                         ("LetsCelebrate", ["React", "Google Gemini", "Node.js"]))

    def test_plain_names_are_untouched(self):
        self.assertEqual(split_project_heading("Litho-Guessr"), ("Litho-Guessr", []))
        self.assertEqual(split_project_heading(None), ("", []))

    def test_key_ignores_case_spacing_and_the_stack(self):
        self.assertEqual(project_key("Litho-Guessr | Node.js, Leaflet"), project_key("litho guessr"))

    def test_rendered_heading_shows_the_stack_once(self):
        data = normalize_resume_data({"projects": [
            {"name": "SubDub | Node.js, JWT, bcrypt", "stack": ["Node.js", "JWT"]},
        ]})
        proj = data["projects"][0]
        self.assertEqual(proj["name"], "SubDub")
        self.assertEqual(proj["stack"], ["Node.js", "JWT", "bcrypt"])  # merged, no duplicates

    def test_stack_only_in_the_name_still_renders(self):
        proj = normalize_resume_data({"projects": [{"name": "Sculpt | Python, React"}]})["projects"][0]
        self.assertEqual((proj["name"], proj["stack_str"]), ("Sculpt", "Python, React"))


class TestProjectChanges(unittest.TestCase):
    def test_restyled_headings_are_not_added_or_removed(self):
        """The reported bug: stacks appended to names showed 3 removed + 3 added."""
        optimized = {"name": "Jane", "projects": [
            {"name": f"{p['name']} | {', '.join(p['stack'])}", "stack": p["stack"], "highlights": p["highlights"]}
            for p in ORIGINAL["projects"]
        ]}
        changes = labels(compute_resume_diff(as_yaml(ORIGINAL), as_yaml(optimized)))
        self.assertFalse(any("project(s) added" in c or "project(s) removed" in c for c in changes), changes)

    def test_rewritten_bullets_are_reported_as_rewritten(self):
        optimized = {"name": "Jane", "projects": [dict(p) for p in ORIGINAL["projects"]]}
        optimized["projects"][0] = {**optimized["projects"][0], "highlights": ["Built a planner used by 500 people"]}
        changes = compute_resume_diff(as_yaml(ORIGINAL), as_yaml(optimized))
        rewritten = [c for c in changes if c["label"].startswith("Bullets rewritten")]
        self.assertEqual(len(rewritten), 1)
        self.assertEqual(rewritten[0]["items"], ["LetsCelebrate"])

    def test_real_additions_and_removals_still_show(self):
        optimized = {"name": "Jane", "projects": [
            ORIGINAL["projects"][0], ORIGINAL["projects"][1],
            {"name": "Sculpt | Python", "stack": ["Python"], "highlights": ["Wrote a resume tailor"]},
        ]}
        changes = {c["label"]: c["items"] for c in compute_resume_diff(as_yaml(ORIGINAL), as_yaml(optimized))}
        self.assertEqual(changes["1 project(s) added"], ["Sculpt"])
        self.assertEqual(changes["1 project(s) removed"], ["SubDub"])


class TestExperienceChanges(unittest.TestCase):
    def test_company_matched_despite_case_and_spacing(self):
        orig = {"experience": [{"company": "Acme Corp", "achievements": ["Did a", "Did b"]}]}
        opti = {"experience": [{"company": "ACME corp ", "achievements": ["Did a better", "Did b"]}]}
        changes = labels(compute_resume_diff(as_yaml(orig), as_yaml(opti)))
        self.assertIn("1 bullet(s) enhanced at Acme Corp", changes)
        self.assertFalse(any("removed" in c or "added" in c for c in changes), changes)

    def test_an_added_bullet_counts(self):
        orig = {"experience": [{"company": "Acme", "achievements": ["Did a"]}]}
        opti = {"experience": [{"company": "Acme", "achievements": ["Did a", "Did b"]}]}
        self.assertIn("1 bullet(s) enhanced at Acme", labels(compute_resume_diff(as_yaml(orig), as_yaml(opti))))


if __name__ == "__main__":
    unittest.main()
