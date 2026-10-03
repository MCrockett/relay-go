import os
import re
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHIPPED = ("bin/", "relaylib/", "prompts/", "skills/", "tests/", "tools/", "menubar/", ".github/",
           "README.md", "AGENTS.md", "LICENSE", "install.sh", "config.example.toml", "RULES.example.md",
           "Dockerfile", ".dockerignore", "compose.example.yaml", "docker/")
# Machine paths (the image's own /home/relay aside), and links to working docs that stay out of a public copy.
PRIVATE = re.compile(r"/Users/|/home/(?!relay\b)[a-z]|/private/(tmp|var)/|docs/(superpowers|studies)/")


def shipped_files():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [p for p in out.splitlines() if p.startswith(SHIPPED) and p != "tests/test_public.py"]


class PublicTest(unittest.TestCase):
    def test_license_is_mit(self):
        with open(os.path.join(ROOT, "LICENSE")) as f:
            self.assertTrue(f.read().startswith("MIT License"))

    def test_shipped_files_have_no_machine_paths_or_internal_doc_links(self):
        hits = []
        for path in shipped_files():
            with open(os.path.join(ROOT, path), errors="replace") as f:
                hits += [f"{path}:{n}" for n, line in enumerate(f, 1) if PRIVATE.search(line)]
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
