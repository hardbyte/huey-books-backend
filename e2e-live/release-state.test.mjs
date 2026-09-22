import assert from 'node:assert/strict';
import { test } from 'node:test';
import { assertPairTraffic, assertRevision, phasePercentage, serviceSpec } from './release-state.mjs';

const release = 'test-release';
const expected = ['public', 'internal'].map(role => serviceSpec({
  service: `chat-${role}`, revision: `chat-${role}-candidate`, image: `image@sha256:${'a'.repeat(64)}`,
  tag: 'can', url: `https://can---chat-${role}.a.run.app`, internalUrl: 'https://chat-internal.a.run.app',
}));

/** @param {number} percentage */
function services(percentage) {
  return expected.map(spec => ({
    metadata: { name: spec.service, generation: 4 },
    status: { observedGeneration: 4, conditions: [{ type: 'Ready', status: 'True' }], traffic: [
      { tag: spec.tag, revisionName: spec.revision, url: spec.url, percent: percentage },
      { tag: 'old', revisionName: `${spec.service}-prior`, percent: 100 - percentage },
    ] },
  }));
}

test('each native phase requires its exact candidate traffic percentage', () => {
  for (const [phase, percentage] of [['canary-0', 0], ['canary-10', 10], ['canary-50', 50], ['stable', 100]]) {
    assert.equal(phasePercentage(String(phase)), percentage);
    assertPairTraffic(services(Number(percentage)), expected, Number(percentage));
  }
  for (const phase of [undefined, '', 'canary-100', 'canary-25']) assert.throws(() => phasePercentage(phase));
});

test('stable verification waits for both services even when both candidate tags already exist', () => {
  const snapshots = services(100);
  snapshots[1] = services(50)[1];
  assert.throws(() => assertPairTraffic(snapshots, expected, 100), /not reached 100%/);
  snapshots[1] = services(100)[1];
  assertPairTraffic(snapshots, expected, 100);
  snapshots[0] = services(50)[0];
  assert.throws(() => assertPairTraffic(snapshots, expected, 100), /not reached 100%/);
});

test('wrong tag, revision, URL, traffic and reconciling service fail closed', () => {
  const mutations = [
    (/** @type {ReturnType<typeof services>[number]} */ service) => { service.status.traffic[0].tag = 'other'; },
    (/** @type {ReturnType<typeof services>[number]} */ service) => { service.status.traffic[0].revisionName = 'other'; },
    (/** @type {ReturnType<typeof services>[number]} */ service) => { service.status.traffic[0].url = 'https://wrong.a.run.app'; },
    (/** @type {ReturnType<typeof services>[number]} */ service) => { service.status.traffic[0].percent = 99; },
    (/** @type {ReturnType<typeof services>[number]} */ service) => { service.status.observedGeneration = 3; },
    (/** @type {ReturnType<typeof services>[number]} */ service) => { service.status.conditions[0].status = 'Unknown'; },
  ];
  for (const mutate of mutations) {
    const snapshots = services(100);
    mutate(snapshots[1]);
    assert.throws(() => assertPairTraffic(snapshots, expected, 100));
  }
});

test('revision readiness requires the expected identity, digest and release', () => {
  const spec = expected[0];
  const revision = {
    name: `projects/test/locations/test/services/${spec.service}/revisions/${spec.revision}`,
    labels: { 'huey-release': release }, containers: [{ image: spec.image }],
    conditions: [{ type: 'Ready', state: 'CONDITION_SUCCEEDED' }],
  };
  assertRevision(revision, spec, release);
  assert.throws(() => assertRevision({ ...revision, name: 'wrong' }, spec, release));
  assert.throws(() => assertRevision({ ...revision, containers: [{ image: 'image:latest' }] }, spec, release));
  assert.throws(() => assertRevision(revision, spec, 'other-release'));
  assert.throws(() => assertRevision({ ...revision, conditions: [] }, spec, release));
});
