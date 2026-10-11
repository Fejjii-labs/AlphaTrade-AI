"""First verification of the NEW hosted export; never touches the live database.

Single restore into an empty PostgreSQL18 container: no networking, published
ports, app processes, provider/exchange credentials, migrations or cloud writes.
All dump contents, pg_restore diagnostics and detailed receipts remain private.
"""
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from uuid import uuid4

PRIVATE = Path('/workspace/AlphaTrade-AI/.ai/local/pr249-release/hosted-backup')
IMAGE = 'postgres@sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336'
DOCKER = ['docker', '--host=unix:///var/run/docker.sock']
MAX_UNPACKED = 2 * 1024**3


def main():
    os.umask(0o077)
    metadata = PRIVATE / 'download-receipt.json'
    completed = PRIVATE / 'restore-receipt.json'
    if completed.exists():
        raise RuntimeError('restore_receipt_already_exists_reuse_completed_evidence')
    receipt = json.loads(metadata.read_text())
    if receipt.get('database_id') != 'dpg-d8fuok57vvec739lm0u0-a':
        raise RuntimeError('wrong_source_database')
    artifact = Path(receipt['archive_path'])
    if artifact.is_symlink() or artifact.resolve().parent != PRIVATE.resolve():
        raise RuntimeError('private_artifact_path_required')
    if artifact.stat().st_mode & 0o077:
        raise RuntimeError('artifact_permissions_not_private')
    with artifact.open('rb') as source:
        sha = hashlib.file_digest(source, 'sha256').hexdigest()
    if sha != receipt['sha256'] or artifact.stat().st_size != receipt['size_bytes']:
        raise RuntimeError('artifact_identity_mismatch')
    env = os.environ.copy()
    for key in ('DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'):
        env.pop(key, None)
    # Preserve client/daemon proxy and CA configuration. Nothing is passed to PG.
    name = 'pr249-hosted-restore-'+uuid4().hex[:12]
    work = Path(tempfile.mkdtemp(prefix='restore-', dir=PRIVATE))
    extracted = work/'unpacked'
    extracted.mkdir(mode=0o700)
    started = False
    with (PRIVATE/'restore-diagnostics.log').open('xb') as log:
        def command(args, *, data=None, timeout=120, check=True):
            return subprocess.run(DOCKER+args, input=data, stdout=subprocess.PIPE,
                                  stderr=log, env=env, timeout=timeout, check=check)
        def pg(args, **kwargs):
            return command(['exec','-i','-u','postgres',name]+args, **kwargs)
        try:
            unpacked = 0
            with tarfile.open(artifact, 'r:gz') as archive:
                for index, member in enumerate(archive):
                    path = PurePosixPath(member.name)
                    if (index >= 100000 or path.is_absolute() or '..' in path.parts
                            or not (member.isfile() or member.isdir())):
                        raise RuntimeError('unsafe_export_archive_member')
                    unpacked += member.size
                    if unpacked > MAX_UNPACKED:
                        raise RuntimeError('export_exceeds_isolated_resource_bound')
                    destination = extracted.joinpath(*path.parts)
                    if member.isdir():
                        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
                    else:
                        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                        with archive.extractfile(member) as source, destination.open('xb') as target:
                            shutil.copyfileobj(source, target)
            toc = list(extracted.rglob('toc.dat'))
            if len(toc) != 1:
                raise RuntimeError('one_directory_format_dump_required')
            dump = toc[0].parent
            command(['image','inspect',IMAGE])  # Refuse implicit pull/change during verification.
            command(['run','-d','--name',name,'--network','none','--read-only',
                     '--cap-drop','ALL','--security-opt','no-new-privileges',
                     '--user','999:999','--memory','4g','--cpus','2',
                     '--tmpfs','/var/lib/postgresql:rw,uid=999,gid=999,mode=700,size=2g',
                     '--tmpfs','/var/run/postgresql:rw,uid=999,gid=999,mode=700,size=16m',
                     '--tmpfs','/tmp:rw,uid=999,gid=999,mode=700,size=2g',
                     '-e','PGDATA=/var/lib/postgresql/18/docker',
                     '-e','POSTGRES_PASSWORD='+uuid4().hex,
                     '-e','POSTGRES_INITDB_ARGS=--auth-local=trust --auth-host=reject',
                     '-e','POSTGRES_HOST_AUTH_METHOD=reject',IMAGE])
            started = True
            for _ in range(30):
                if pg(['pg_isready','-U','postgres'], timeout=5, check=False).returncode == 0:
                    break
                time.sleep(1)
            else:
                raise RuntimeError('isolated_postgres_not_ready')
            pg(['mkdir','-m','700','/tmp/export'])
            # Stream the extracted directory privately; no host dump bind or live DSN.
            packed = work/'transfer.tar'
            with tarfile.open(packed,'w') as transfer:
                for item in dump.iterdir():
                    transfer.add(item, arcname=item.name, recursive=True)
            with packed.open('rb') as source:
                subprocess.run(DOCKER+['exec','-i','-u','postgres',name,'tar','-xf','-',
                               '-C','/tmp/export'], stdin=source, stdout=log, stderr=log,
                               env=env, timeout=120, check=True)
            pg(['createdb','-U','postgres','restored_export'])
            pg(['pg_restore','-U','postgres','--exit-on-error','--no-owner','--no-acl',
                '-d','restored_export','/tmp/export'], timeout=900)
            head = pg(['psql','-X','-A','-t','-v','ON_ERROR_STOP=1','-U','postgres',
                       '-d','restored_export','-c','SELECT version_num FROM public.alembic_version']).stdout.decode().strip()
            if head != 'a8agentcapture001':
                raise RuntimeError('backup_head_requires_review')
            tables = pg(['psql','-X','-A','-t','-v','ON_ERROR_STOP=1','-U','postgres',
                         '-d','restored_export','-c',
                         "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'"]).stdout.decode().strip()
            if int(tables) < 1:
                raise RuntimeError('restored_schema_empty')
            result = {**receipt, 'restore_verified':True,
                      'restore_verified_at':datetime.now(UTC).isoformat(),
                      'restored_target':'empty isolated local PostgreSQL18; destroyed after verification',
                      'image':IMAGE,'source_alembic_head':head,'public_tables':int(tables),
                      'object_owners_and_acls_not_recreated':True,
                      'app_acceptance':'reused prior qualification; app not run with hosted data',
                      'live_database_touched':False,'network':'none','migrations_run':0}
            completed.write_text(json.dumps(result,indent=2)+'\n')
            print(json.dumps({'restore_verified':True,'private_receipt':str(completed)}))
        finally:
            if started:
                command(['rm','-f','-v',name], timeout=30, check=False)
            shutil.rmtree(work)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        reason = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        print(json.dumps({'restore_verified':False,'safe_reason':reason}))
        raise SystemExit(1)
