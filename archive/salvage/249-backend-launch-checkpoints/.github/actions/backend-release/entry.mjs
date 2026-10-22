import { spawn } from 'node:child_process';
import { resolve } from 'node:path';

const input = (name) => process.env[`INPUT_${name.toUpperCase()}`];
const workspace = process.env.GITHUB_WORKSPACE;
const command = JSON.parse(input('command'));
if (!Array.isArray(command) || !command.length || command.some((arg) => typeof arg !== 'string')) {
  throw new Error('Expected a workflow owned command array');
}
const child = spawn('python3', [
  resolve(workspace, 'scripts/run_backend_launch.py'),
  '--output', resolve(workspace, input('output')),
  '--seconds', input('seconds'),
  '--upload-checkpoints',
  '--checkpoint-seconds', input('checkpoint-seconds'),
  '--', ...command,
], {
  cwd: resolve(workspace, input('working-directory')),
  env: {
    ...process.env,
    BACKEND_CHECKPOINT_PREFIX: input('artifact-prefix'),
    BACKEND_CHECKPOINT_NODE: process.execPath,
  },
  stdio: 'inherit',
});
let interrupted = false;
for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => { interrupted = true; child.kill(signal); });
}
child.on('error', () => { process.exitCode = 127; });
child.on('exit', (code) => {
  // Normal release always expects zero. Synthetic timeout proof expects124.
  process.exitCode = !interrupted && code === Number(input('expected-exit')) ? 0 : (code || 1);
});
