"""Fetch the operator-bound Render Download link without logging the private URL.

Use only after the secret file and download hostname have been approved/applied in
the executor configuration. This creates no export or infrastructure.
"""
import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

PRIVATE = Path('/workspace/AlphaTrade-AI/.ai/local/pr249-release/hosted-backup')
URL_FILE = PRIVATE / 'export-download-url.txt'
ARCHIVE = PRIVATE / 'render-export.dir.tar.gz'
MAX_BYTES = 2 * 1024**3


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def main():
    os.umask(0o077)
    PRIVATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    if URL_FILE.is_symlink() or not URL_FILE.is_file():
        raise RuntimeError('private_download_url_file_unbound')
    if URL_FILE.stat().st_mode & 0o077:
        raise RuntimeError('download_url_file_permissions_not_private')
    url = URL_FILE.read_text().strip()
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
            or parsed.password or parsed.port not in (None, 443) or parsed.fragment):
        raise RuntimeError('approved_https_download_url_required')
    if ARCHIVE.exists() or ARCHIVE.with_suffix('.partial').exists():
        raise RuntimeError('artifact_already_present_do_not_repeat_download')
    # Inherited proxy and CA remain active; HTTPS policy is enforced by the sidecar.
    opener = urllib.request.build_opener(NoRedirect())
    partial = ARCHIVE.with_suffix('.partial')
    digest, size = hashlib.sha256(), 0
    try:
        with opener.open(urllib.request.Request(url), timeout=30) as response:
            if response.status != 200:
                raise RuntimeError('download_not_successful')
            with partial.open('xb') as target:
                while block := response.read(1024 * 1024):
                    size += len(block)
                    if size > MAX_BYTES:
                        raise RuntimeError('download_exceeds_private_resource_bound')
                    target.write(block)
                    digest.update(block)
        with partial.open('rb') as probe:
            gzip_header = probe.read(2)
        if size < 2 or gzip_header != b'\x1f\x8b':
            raise RuntimeError('download_is_not_gzip_archive')
        partial.replace(ARCHIVE)
        receipt = {
            'database_id': 'dpg-d8fuok57vvec739lm0u0-a',
            'source_binding': 'Operator-provided Download link for reported Render export',
            'operator_reported_completed_at': '2026-10-11T20:58:00Z',
            'downloaded_at': datetime.now(UTC).isoformat(),
            'archive_path': str(ARCHIVE),
            'size_bytes': size,
            'sha256': digest.hexdigest(),
            'source_url_sha256': hashlib.sha256(url.encode()).hexdigest(),
            'export_id': None,
            'created_at': None,
            'expires_at': None,
            'restore_verified': False,
        }
        (PRIVATE / 'download-receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
        print(json.dumps({'downloaded': True, 'private_receipt': str(PRIVATE/'download-receipt.json')}))
    finally:
        partial.unlink(missing_ok=True)


if __name__ == '__main__':
    try:
        main()
    except urllib.error.HTTPError as exc:
        # Neither body nor signed Location/URL belongs in task output.
        print(json.dumps({'downloaded': False, 'http_status': exc.code,
                          'redirect_received': 300 <= exc.code < 400}))
        raise SystemExit(1)
    except Exception as exc:
        reason = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        print(json.dumps({'downloaded': False, 'safe_reason': reason}))
        raise SystemExit(1)
