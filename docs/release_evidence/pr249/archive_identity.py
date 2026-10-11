"""Verify complete runtime source payload directly from the saved image layers."""
import hashlib,json,tarfile
from pathlib import Path
root=Path('/tmp/pr249-a13-qualification')
archive=Path('/workspace/AlphaTrade-AI/.ai/local/pr249-release/a13-qualified-images.tar.gz')
results=[]
with tarfile.open(archive,'r:gz') as bundle:
 manifest=json.load(bundle.extractfile('manifest.json'))
 for label,tag in [('rollback-api','alphatrade-pr249:a13-rollback-api'),('compatible-worker','alphatrade-pr249:a13-compatible-worker')]:
  entry=next(e for e in manifest if tag in e['RepoTags'])
  config=bundle.extractfile(entry['Config']).read()
  actual={}
  for layer in entry['Layers']:
   with tarfile.open(fileobj=bundle.extractfile(layer),mode='r|') as contents:
    for member in contents:
     name=member.name.removeprefix('./')
     relevant=(name.startswith('app/src/') or name.startswith('app/prompts/') or name in ['app/alembic.ini','app/docker/entrypoint.sh'])
     if relevant and '.wh.' in name: raise RuntimeError('Relevant image whiteout requires explicit handling')
     if not member.isfile() or not relevant or '__pycache__' in name or name.endswith('.pyc'):continue
     actual[name.removeprefix('app/')]=hashlib.sha256(contents.extractfile(member).read()).hexdigest()
  source=root/label/'backend'
  paths=list((source/'src').rglob('*'))+list((source/'prompts').rglob('*'))+[source/'alembic.ini',source/'docker/entrypoint.sh']
  expected={str(p.relative_to(source)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}
  assert expected==actual,tag
  results.append({'image':tag,'config_image_id':'sha256:'+hashlib.sha256(config).hexdigest(),'complete_runtime_source_matches':True,'files':len(actual),'complete_payload_sha256':hashlib.sha256(json.dumps(actual,sort_keys=True).encode()).hexdigest()})
  print(json.dumps(results[-1]),flush=True)
(root/'archive-source-identity.json').write_text(json.dumps(results,indent=2)+'\n')
