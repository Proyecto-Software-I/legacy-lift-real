"""Run ONLY in a separate migration-worker process/container, never inside Uvicorn.

Start with `python -m app.migration_worker`; no automatic retries after a restart.
"""
from __future__ import annotations
import argparse
import os
import time
import fcntl
from .settings import data_dir
from . import db, migration_runs, migration_verify


def main():
    parser=argparse.ArgumentParser(description='LegacyLift isolated migration worker')
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    if os.getenv('LEGACYLIFT_MIGRATION_WORKER_MODE','disabled')!='separate':
        parser.error('Requiere LEGACYLIFT_MIGRATION_WORKER_MODE=separate')
    # Exclusive OS-level lock prevents two workers claiming shared workspace paths.
    # Never call db.init here: that would incorrectly mark active ingestion jobs failed.
    lock_file=data_dir()/'migration-worker.lock'
    lock_file.parent.mkdir(parents=True,exist_ok=True)
    with lock_file.open('w') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:parser.error('Otro migration-worker está activo')
        migration_runs.recover_interrupted()
        while True:
            migration_runs.heartbeat()
            result=migration_runs.run_one()
            if not result and os.getenv('LEGACYLIFT_VERIFY_IN_HOST','false').lower()!='true':
                result=migration_verify.run_one()
            if result:print(f"Migration attempt {result['id']}: {result['status']}",flush=True)
            if args.once:break
            if not result:time.sleep(2)

if __name__=='__main__':main()
