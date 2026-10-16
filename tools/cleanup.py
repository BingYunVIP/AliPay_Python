"""Clean disposable development files and expired rotated logs; preserve live state.

Default: preview. --apply performs the listed actions and writes a maintenance record.
Current logs, databases, credentials, dependencies and frontend/dist are never targets.
"""
import argparse
import json
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SKIP={'.git','.venv','node_modules','.tools','secrets','backups'}
CACHES={'__pycache__','.pytest_cache','.ruff_cache','playwright-report','test-results'}


def candidates(root,days=14):
    result=[]
    def visit(path):
        for item in path.iterdir():
            if item.is_symlink():continue
            if item.is_dir():
                if item.name in CACHES:result.append((item,'development_cache'))
                elif item.name not in SKIP and not item.name.startswith('.venv.previous-') and item!=root/'frontend/dist':visit(item)
            elif item.suffix=='.pyc':result.append((item,'python_cache'))
            elif re.fullmatch(r'(?:payment|demo)\.jsonl\.[1-9][0-9]*|server\.log\.[1-9][0-9]*',item.name) and item.stat().st_mtime<time.time()-days*86400:
                result.append((item,'expired_rotated_log'))
    visit(root)
    for path in (root/'frontend/node_modules/.vite',):
        if path.is_dir() and not path.is_symlink():result.append((path,'vite_cache'))
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');parser.add_argument('--retention-days',type=int,default=14)
    args=parser.parse_args()
    if args.retention_days<7:parser.error('日志保留期至少 7 天')
    targets=candidates(ROOT,args.retention_days)
    records=[]
    for path,reason in targets:
        size=sum(p.stat().st_size for p in path.rglob('*') if p.is_file() and not p.is_symlink()) if path.is_dir() else path.stat().st_size
        records.append({'path':str(path.relative_to(ROOT)),'reason':reason,'bytes':size})
        if args.apply:
            if path.is_dir():shutil.rmtree(path)
            else:path.unlink()
    total=sum(row['bytes'] for row in records)
    if args.apply:
        folder=ROOT/'data/maintenance';folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        log=folder/('cleanup-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.json')
        log.write_text(json.dumps({'retention_days':args.retention_days,'items':records,'bytes':total},ensure_ascii=False,indent=2));log.chmod(0o600)
        print(f'已清理 {len(records)} 项，共 {total} 字节；记录：{log}')
    else:
        print(json.dumps({'mode':'preview','items':records,'bytes':total},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
