import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from tools import cleanup


class CleanupTests(unittest.TestCase):
    def test_cleanup_preserves_live_build_database_credentials_and_current_logs(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            keep=['frontend/dist/index.html','data/web.sqlite3','data/merchant-master.key','data/logs/payment.jsonl','data/logs/payment.jsonl.1','data/backups/payment.jsonl.2','.venv/lib/__pycache__/keep.pyc','.env']
            remove=['web/__pycache__/app.pyc','data/logs/payment.jsonl.2','frontend/node_modules/.vite/cache.json']
            for name in keep+remove:
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('fixture')
            old=root/'data/logs/payment.jsonl.2';os.utime(old,(time.time()-20*86400,)*2)
            with patch.object(cleanup,'ROOT',root),patch.object(sys,'argv',['cleanup.py','--apply']),patch('builtins.print'):
                cleanup.main()
            for name in keep:self.assertTrue((root/name).exists(),name)
            for name in remove:self.assertFalse((root/name).exists(),name)
            records=list((root/'data/maintenance').glob('cleanup-*.json'))
            self.assertEqual(len(records),1);self.assertEqual(len(json.loads(records[0].read_text())['items']),3)

    def test_preview_does_not_delete_files_or_follow_symlinks(self):
        with tempfile.TemporaryDirectory() as folder,tempfile.TemporaryDirectory() as outside:
            root=Path(folder);cache=root/'__pycache__';cache.mkdir();(cache/'a.pyc').write_text('cache')
            target=Path(outside)/'important';target.write_text('preserve');(root/'linked').symlink_to(Path(outside),target_is_directory=True)
            with patch.object(cleanup,'ROOT',root),patch.object(sys,'argv',['cleanup.py']),patch('builtins.print'):cleanup.main()
            self.assertTrue((cache/'a.pyc').exists());self.assertTrue(target.exists())


if __name__=='__main__':unittest.main()
