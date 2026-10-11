import hashlib, json, os, subprocess, sys
from pathlib import Path
from uuid import uuid4

repo=Path('/workspace/AlphaTrade-AI'); backend=repo/'backend'
sys.path[:0]=[str(backend),str(backend/'src')]
from sqlalchemy import create_engine, text
from alembic import command
from alembic.config import Config
from tests.test_pr237_migration_aware_rollback import _seed, _seed_feature_retention, _snapshot, BOOT_PROBE

root=Path('/tmp/pr249-a13-qualification')
docker=['docker','--host=unix:///var/run/docker.sock']
env=os.environ.copy()
for key in ['DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH']: env.pop(key,None)
def run(args,**kwargs):
    return subprocess.run(docker+args,env=env,check=True,text=True,capture_output=True,timeout=100,**kwargs)
schema='pr249_image_'+uuid4().hex
host_dsn='postgresql+psycopg://alphatrade:synthetic-local-only@127.0.0.1:55432/alphatrade_test?options=-csearch_path%3D'+schema
container_dsn=host_dsn.replace('127.0.0.1:55432','pr249-a13-pg:5432')
admin=create_engine(host_dsn.split('?')[0]); engine=create_engine(host_dsn)
with admin.begin() as conn: conn.execute(text('CREATE SCHEMA '+schema))
os.environ['ALEMBIC_DATABASE_URL']=host_dsn
config=Config(str(backend/'alembic.ini')); config.set_main_option('script_location',str(backend/'src/app/db/migrations'))
command.upgrade(config,'head')
_seed(engine); retained_org=_seed_feature_retention(engine)
before=_snapshot(engine)
fingerprint=hashlib.sha256(json.dumps(before,sort_keys=True).encode()).hexdigest()
settings={
 'ALEMBIC_DATABASE_URL':container_dsn,'DATABASE_URL':container_dsn,'ENVIRONMENT':'local','PROVIDER_MODE':'mock',
 'WORKER_ENABLED':'false','MARKET_DATA_ENABLED':'false','JOURNAL_RAG_SYNC_ENABLED':'false','EXECUTION_MODE':'paper',
 'EXCHANGE_MODE':'paper_internal','ENABLE_REAL_TRADING':'false','WATCHER_ORCHESTRATION_ENABLED':'false',
 'WATCHER_PAPER_STAGING_ACTIVATION':'false','MARKET_WATCHER_ENABLED':'false','MARKET_WATCHER_BRIDGE_ENABLED':'false',
 'MARKET_WATCHER_BRIDGE_AUTO_TICK':'false','TELEGRAM_ALERTS_ENABLED':'false','TELEGRAM_INTERACTION_ENABLED':'false',
 'AUTOMATIC_TELEGRAM_DELIVERY_ENABLED':'false','TELEGRAM_PAPER_ACTIVATION_ARMED':'false','TELEGRAM_NETWORK_PERMITTED':'false',
 'TELEGRAM_INBOUND_MODE':'off','TELEGRAM_BOT_TOKEN':'','TELEGRAM_WEBHOOK_SECRET':'','KNOWLEDGE_INDEXING_ENABLED':'false',
 'BLOFIN_ACTIVITY_ENABLED':'false','RATE_LIMIT_USE_REDIS':'false','ACCESS_TOKEN_DENYLIST_USE_REDIS':'false',
 'MARKET_DATA_CACHE_USE_REDIS':'false','ROLLBACK_EXPECTED_HEAD':'a13voicemonitor001','ROLLBACK_RESERVATION_ORG':str(retained_org),
 'PYTHONPATH':'/app/src',
}
envfile=root/'synthetic-runtime.env';envfile.write_text(''.join(k+'='+v+'\n' for k,v in settings.items()));envfile.chmod(0o600)
probe=root/'boot_probe.py';probe.write_text(BOOT_PROBE)
manifest=json.loads((root/'manifest.json').read_text())
manifest.update({'schema':schema,'snapshot_sha256':fingerprint,'row_counts':{k:len(v) for k,v in before.items()},'runtime_env_sha256':hashlib.sha256(envfile.read_bytes()).hexdigest(),'image_results':[]})
for image in ['alphatrade-pr249:a13-rollback-api','alphatrade-pr249:a13-compatible-worker']:
    imageid=run(['image','inspect',image,'--format','{{.Id}}']).stdout.strip()
    common=['run','--rm','--network','pr249-a13-local','--env-file',str(envfile)]
    result=run(common+['--entrypoint','/app/.venv/bin/alembic',image,'upgrade','head'])
    for restart in range(2):
        result=run(common+['--mount','type=bind,src='+str(probe)+',dst=/evidence/boot_probe.py,readonly','--entrypoint','/app/.venv/bin/python',image,'/evidence/boot_probe.py'])
        assert _snapshot(engine)==before
        print(image, restart+1, result.stdout.strip(),flush=True)
    manifest['image_results'].append({'image':image,'image_id':imageid,'no_op_upgrade':True,'api_start_restart':True,'worker_disarmed_start_restart':True,'retained_snapshot_matches':True})
    (root/'qualified-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps({'qualified':True,'head':'a13voicemonitor001','snapshot_sha256':fingerprint,'row_counts':manifest['row_counts'],'images':manifest['image_results']}),flush=True)
# Retain the disposable local database and image artifacts for review; no hosted data.
