import hashlib
from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Models65WorkflowTests(unittest.TestCase):
    def test_downloaded_refresh_is_pinned_to_the_reviewed_source(self):
        source = (ROOT / 'scripts/refresh-cohort65-v1.py').read_bytes()
        workflow = (ROOT / '.github/workflows/build-payload.yml').read_text()
        self.assertIn(hashlib.sha256(source).hexdigest() + '  refresh65.py', workflow)
        self.assertIn("inputs.delivery-revision == 'models58' && 'b34998dae173411bbaff90b7bd1ad90e5987477b'", workflow)

    def test_every_bash_block_parses_with_the_metadata_only_branch(self):
        lines = (ROOT / '.github/workflows/build-payload.yml').read_text().splitlines()
        blocks = []
        for index, line in enumerate(lines):
            if line != '        run: |':
                continue
            body = []
            for later in lines[index + 1:]:
                if later and not later.startswith('          '):
                    break
                body.append(later[10:])
            blocks.append('\n'.join(body))
        self.assertGreater(len(blocks), 5)
        for script in blocks:
            script = re.sub(r'\$\{\{.*?\}\}', 'fixture', script)
            subprocess.run(['bash', '-n'], input=script, text=True, capture_output=True, check=True)


if __name__ == '__main__':
    unittest.main()
