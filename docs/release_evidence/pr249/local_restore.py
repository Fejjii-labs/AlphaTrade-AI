"""Dump/restore only the disposable synthetic PostgreSQL18 image-qualification DB."""
import hashlib,json,os,subprocess,sys
from pathlib import Path
from uuid import uuid4
from sqlalchemy import create_engine,text

root=Path('/tmp/pr249-a13-qualification')
sys.path[:0]=['/workspace/AlphaTrade-AI/backend','/workspace/AlphaTrade-AI/backend/src']
from tests.test_pr237_migration_aware_rollback import _snapshot

env=os.environ.copy()
for key in ['DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH']:env.pop(key,None)
docker=['docker','--host=unix:///var/run/docker.sock','exec','-i','-u','postgres','pr249-a13-pg']
dsn='postgresql+psycopg://alphatrade:synthetic-local-only@127.0.0.1:55432/'
engine=create_engine(dsn+'alphatrade_test')
with engine.connect() as c:
    schemas=list(c.scalars(text("SELECT schema_name FROM information_schema.schemata WHERE schema_name LIKE 'pr249_image_%'")))
assert len(schemas)==1
schema=schemas[0]
engine=create_engine(dsn+'alphatrade_test?options=-csearch_path%3D'+schema)
before=_snapshot(engine)
blob=subprocess.check_output(docker+['pg_dump','-U','alphatrade','-d','alphatrade_test','-Fc','--schema',schema],env=env,timeout=60)
dump=root/'synthetic-a13.dump';dump.write_bytes(blob);dump.chmod(0o600)
scratch='pr249_restore_'+uuid4().hex
subprocess.run(docker+['createdb','-U','alphatrade',scratch],env=env,check=True,timeout=30)
subprocess.run(docker+['pg_restore','-U','alphatrade','--exit-on-error','-d',scratch],input=blob,env=env,check=True,timeout=60)
restored=create_engine(dsn+scratch+'?options=-csearch_path%3D'+schema)
after=_snapshot(restored)
assert before==after
result={'source':'disposable synthetic local PostgreSQL18 only','hosted_backup_verified':False,'schema':schema,'scratch_database':scratch,'dump_size':len(blob),'dump_sha256':hashlib.sha256(blob).hexdigest(),'retained_snapshot_sha256':hashlib.sha256(json.dumps(before,sort_keys=True).encode()).hexdigest(),'restored_snapshot_matches':True,'row_counts':{k:len(v) for k,v in after.items()}}
(root/'local-restore-manifest.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
