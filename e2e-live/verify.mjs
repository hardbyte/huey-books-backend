import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { assertPairTraffic, assertRevision, object, phasePercentage, serviceSpec } from './release-state.mjs';

/** @param {string} name */
function requiredEnv(name) {
  const value = process.env[name];
  assert.ok(typeof value === 'string' && value.trim(), `${name} is required`);
  return value;
}

const project = requiredEnv('DEPLOY_PROJECT');
const region = requiredEnv('DEPLOY_REGION');
const target = requiredEnv('CLOUD_DEPLOY_TARGET');
const release = requiredEnv('EXPECTED_RELEASE');
assert.equal(process.env.CLOUD_DEPLOY_RELEASE, release, 'Wrong release verification');
const spec = object(JSON.parse(requiredEnv('RELEASE_SPEC')));
assert.match(target, /^chat-(dev|prod)-(public|internal)$/, 'Unknown deployment target');
assert.ok(spec[target], 'Unknown deployment target');
const environment = target.split('-')[1];
const publicSpec = serviceSpec(spec[`chat-${environment}-public`]);
const internalSpec = serviceSpec(spec[`chat-${environment}-internal`]);
const pair = [publicSpec, internalSpec];
const percentage = phasePercentage(requiredEnv('CLOUD_DEPLOY_PHASE'));
const root = `https://run.googleapis.com/v2/projects/${project}/locations/${region}`;
const servicesRoot = `https://${region}-run.googleapis.com/apis/serving.knative.dev/v1/namespaces/${project}/services`;
/** @param {number} ms */
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function accessToken() {
  const tokenResponse = await fetch('http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token', {
    headers: { 'Metadata-Flavor': 'Google' }, signal: AbortSignal.timeout(10_000),
  });
  assert.ok(tokenResponse.ok, 'Unable to authenticate verification container');
  const token = object(await tokenResponse.json()).access_token;
  assert.equal(typeof token, 'string', 'Metadata response has no access token');
  return token;
}

/** @param {string} url */
async function api(url) {
  const access_token = await accessToken();
  const response = await fetch(url, {
    headers: { Authorization: `Bearer ${access_token}` },
    signal: AbortSignal.timeout(30_000),
  });
  if (!response.ok) throw new Error(`Cloud Run API ${response.status}: ${await response.text()}`);
  return response.json();
}

/** @param {import('./release-state.mjs').ServiceSpec} expected */
async function revisionMatches(expected) {
  const revision = await api(`${root}/services/${expected.service}/revisions/${expected.revision}`);
  return assertRevision(revision, expected, release);
}

async function checkPairTraffic() {
  const services = await Promise.all(pair.map(expected => api(`${servicesRoot}/${expected.service}`)));
  assertPairTraffic(services, pair, percentage);
}

let ready = false;
let readinessError;
const deadline = Date.now() + 180_000;
while (Date.now() < deadline) {
  try {
    await checkPairTraffic();
    ready = true;
    break;
  } catch (error) {
    readinessError = error;
  }
  await sleep(2000);
}
assert.ok(ready, `Both services must reach ${percentage}% candidate traffic: ${readinessError}`);
await revisionMatches(internalSpec);
const publicRevision = await revisionMatches(publicSpec);
assert.equal(publicRevision.environment.WRIVETED_INTERNAL_API, publicSpec.internalUrl);
console.log(`Verifying ${release}/${target}/${process.env.CLOUD_DEPLOY_PHASE} through the real reader UI`);
const result = spawnSync('npm', ['test'], {
  cwd: '/verify', stdio: 'inherit',
  env: { ...process.env, E2E_CANDIDATE_API_URL: publicSpec.url },
});
if (existsSync('/verify/test-results')) {
  const archive = spawnSync('tar', ['-czf', '/tmp/browser-results.tgz', '-C', '/verify', 'test-results']);
  assert.equal(archive.status, 0, 'Unable to archive browser evidence');
  const object = `browser/${release}/${target}/${process.env.CLOUD_DEPLOY_PHASE}/${process.env.CLOUD_DEPLOY_JOB_RUN}.tgz`;
  const bucket = `${project}-chat-deploy-artifacts`;
  const upload = await fetch(`https://storage.googleapis.com/upload/storage/v1/b/${bucket}/o?uploadType=media&name=${encodeURIComponent(object)}`, {
    method: 'POST', headers: { Authorization: `Bearer ${await accessToken()}`, 'Content-Type': 'application/gzip' },
    body: readFileSync('/tmp/browser-results.tgz'), signal: AbortSignal.timeout(60_000),
  });
  assert.ok(upload.ok, `Unable to retain browser evidence: ${upload.status}`);
  console.log(`Browser evidence: gs://${bucket}/${object}`);
}
if (result.error) throw result.error;
if (result.status === 0) {
  await checkPairTraffic();
}
process.exit(result.status ?? 1);
