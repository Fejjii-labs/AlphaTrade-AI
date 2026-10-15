import { readdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { DefaultArtifactClient } from '@actions/artifact';

const [directory, receipt, sequence] = process.argv.slice(2);
try {
  const prefix = process.env.BACKEND_CHECKPOINT_PREFIX;
  if (!/^[a-z0-9-]+$/.test(prefix) || !/^[1-7]$/.test(sequence)) throw new Error('Invalid identity');
  const name = `${prefix}-${process.env.GITHUB_RUN_ID}-${process.env.GITHUB_RUN_ATTEMPT}-${sequence}`;
  const files = (await readdir(directory)).map((file) => resolve(directory, file));
  const result = await new DefaultArtifactClient().uploadArtifact(name, files, directory, {
    retentionDays: 7,
    compressionLevel: 6,
  });
  await writeFile(receipt, JSON.stringify({ artifact_id: result.id }));
} catch {
  // SDK output is confined to this killable subprocess; never echo signed URLs,
  // runtime credentials, environment dumps or provider error bodies.
  process.exitCode = 1;
}
