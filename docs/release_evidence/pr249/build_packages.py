import hashlib, json, subprocess, tarfile, io
from pathlib import Path

repo=Path('/workspace/AlphaTrade-AI')
root=Path('/tmp/pr249-a13-qualification');root.mkdir(exist_ok=True)
candidate='00f388017c6bcd2fb53d8135df60ac76bc714125'
baseline='a7a843f7c27e8bdcea5f89c802c808472477a969'
overlays=[
 'src/app/db/migrations/versions/a10blofinactivity001_account_scoped_native_blofin_activity.py',
 'src/app/db/migrations/versions/a11experiments001_generic_experiment_domain.py',
 'src/app/db/migrations/versions/a12trendpulsescreen001_bounded_trendpulse_research_screening.py',
 'src/app/db/migrations/versions/a13voicemonitor001_bounded_voice_and_strategy_assignments.py',
 'src/app/db/voice_realtime.py','src/app/services/quota_service.py','src/app/schemas/usage.py',
]
manifest={'candidate':candidate,'rollback_api_base':baseline,'overlays':{},'worker_source':candidate,'worker_policy':'current compatible worker only; old assignment-unaware worker remains drained'}
for label,sha in [('rollback-api',baseline),('compatible-worker',candidate)]:
 target=root/label
 target.mkdir(exist_ok=True)
 archive=subprocess.check_output(['git','archive',sha,'backend'],cwd=repo)
 with tarfile.open(fileobj=io.BytesIO(archive)) as bundle: bundle.extractall(target,filter='data')
 backend=target/'backend'
 if label=='rollback-api':
  for path in overlays:
   content=(repo/'backend'/path).read_bytes();(backend/path).write_bytes(content)
   manifest['overlays'][path]=hashlib.sha256(content).hexdigest()
 dockerfile=(backend/'Dockerfile').read_text()
 dockerfile='# syntax=docker/dockerfile:1\n'+dockerfile.replace('RUN uv sync','RUN --mount=type=secret,id=proxy_ca,required=true SSL_CERT_FILE=/run/secrets/proxy_ca uv sync')
 (backend/'Dockerfile.qualification').write_text(dockerfile)
 manifest[label+'_dockerfile_sha256']=hashlib.sha256(dockerfile.encode()).hexdigest()
(root/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps(manifest))
