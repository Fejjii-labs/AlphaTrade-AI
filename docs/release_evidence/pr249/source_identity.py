import hashlib,json,os,subprocess
from pathlib import Path
root=Path('/tmp/pr249-a13-qualification')
probe="""import hashlib,json
from pathlib import Path
root=Path('/app')
data={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/'src').rglob('*.py')}
for name in ['alembic.ini','docker/entrypoint.sh']:
 p=root/name;data[name]=hashlib.sha256(p.read_bytes()).hexdigest()
for p in (root/'prompts').rglob('*'):
 if p.is_file():data[str(p.relative_to(root))]=hashlib.sha256(p.read_bytes()).hexdigest()
print(json.dumps(data,sort_keys=True))
"""
env=os.environ.copy()
for key in ['DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH']:env.pop(key,None)
results=[]
for label,image in [('rollback-api','alphatrade-pr249:a13-rollback-api'),('compatible-worker','alphatrade-pr249:a13-compatible-worker')]:
 source=root/label/'backend'
 local={str(p.relative_to(source)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (source/'src').rglob('*.py')}
 for name in ['alembic.ini','docker/entrypoint.sh']:
  local[name]=hashlib.sha256((source/name).read_bytes()).hexdigest()
 for p in (source/'prompts').rglob('*'):
  if p.is_file():local[str(p.relative_to(source))]=hashlib.sha256(p.read_bytes()).hexdigest()
 actual=json.loads(subprocess.check_output(['docker','--host=unix:///var/run/docker.sock','run','--rm','--network','none','--entrypoint','/app/.venv/bin/python',image,'-c',probe],env=env,text=True,timeout=40))
 assert local==actual, (label,'image source payload mismatch')
 results.append({'image':image,'source_payload_matches':True,'source_payload_files':len(local),'source_payload_sha256':hashlib.sha256(json.dumps(local,sort_keys=True).encode()).hexdigest(),'lock_sha256':hashlib.sha256((source/'uv.lock').read_bytes()).hexdigest()})
(root/'source-identity.json').write_text(json.dumps(results,indent=2)+'\n')
print(json.dumps(results))
