import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';

const project = process.env.DEPLOY_PROJECT;
const region = process.env.DEPLOY_REGION;
const target = process.env.CLOUD_DEPLOY_TARGET;
const release = process.env.EXPECTED_RELEASE;
assert.equal(process.env.CLOUD_DEPLOY_RELEASE, release, 'Wrong release verification');
const spec = JSON.parse(process.env.RELEASE_SPEC);
const own = spec[target];
assert.ok(own, 'Unknown deployment target');
const environment = target.split('-')[1];
const publicSpec = spec[`chat-${environment}-public`];
const internalSpec = spec[`chat-${environment}-internal`];
const root = `https://run.googleapis.com/v2/projects/${project}/locations/${region}`;
const servicesRoot = `https://${region}-run.googleapis.com/apis/serving.knative.dev/v1/namespaces/${project}/services`;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function accessToken() {
  const tokenResponse = await fetch('http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token', {
    headers: { 'Metadata-Flavor': 'Google' }, signal: AbortSignal.timeout(10_000),
  });
  assert.ok(tokenResponse.ok, 'Unable to authenticate verification container');
  return (await tokenResponse.json()).access_token;
}

async function api(url) {
  const access_token = await accessToken();
  const response = await fetch(url, {
    headers: { Authorization: `Bearer ${access_token}` },
    signal: AbortSignal.timeout(30_000),
  });
  if (!response.ok) throw new Error(`Cloud Run API ${response.status}: ${await response.text()}`);
  return response.json();
}

async function revisionMatches(expected) {
  const revision = await api(`${root}/services/${expected.service}/revisions/${expected.revision}`);
  assert.equal(revision.labels['huey-release'], release);
  assert.equal(revision.containers[0].image, expected.image);
  assert.ok(revision.conditions.some(condition => condition.type === 'Ready' && condition.state === 'CONDITION_SUCCEEDED'));
  return revision;
}

let ready = false;
for (let attempt = 0; attempt < 90; attempt++) {
  const services = await Promise.all([publicSpec, internalSpec].map(expected => api(`${servicesRoot}/${expected.service}`)));
  if (services.every((service, index) => {
    const expected = [publicSpec, internalSpec][index];
    return service.status?.traffic?.some(entry => entry.tag === expected.tag && entry.revisionName === expected.revision && entry.url === expected.url);
  })) {
    ready = true;
    break;
  }
  await sleep(2000);
}
assert.ok(ready, 'Both release revisions must be routable before browser verification');
await revisionMatches(internalSpec);
const publicRevision = await revisionMatches(publicSpec);
assert.equal(publicRevision.containers[0].env.find(entry => entry.name === 'WRIVETED_INTERNAL_API')?.value, publicSpec.internalUrl);
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
  for (const expected of [publicSpec, internalSpec]) {
    const service = await api(`${servicesRoot}/${expected.service}`);
    assert.ok(service.status?.traffic?.some(entry => entry.tag === expected.tag && entry.revisionName === expected.revision),
      'Native candidate tag changed while browser verification was running');
  }
}
process.exit(result.status ?? 1);
