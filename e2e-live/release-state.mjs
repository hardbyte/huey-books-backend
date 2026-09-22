import assert from 'node:assert/strict';

/** @typedef {{service: string, revision: string, image: string, tag: string, url: string, internalUrl: string}} ServiceSpec */

/** @param {unknown} value @returns {Record<string, unknown>} */
export function object(value) {
  assert.ok(value !== null && typeof value === 'object' && !Array.isArray(value), 'Expected an object');
  return /** @type {Record<string, unknown>} */ (value);
}

/** @param {unknown} value @returns {unknown[]} */
function array(value) {
  assert.ok(Array.isArray(value), 'Expected an array');
  return value;
}

/** @param {Record<string, unknown>} value @param {string} name */
function stringField(value, name) {
  const field = value[name];
  assert.ok(typeof field === 'string' && field.trim(), `Missing or empty field: ${name}`);
  return field;
}

/** @param {string | undefined} phase */
export function phasePercentage(phase) {
  switch (phase) {
    case 'canary-0': return 0;
    case 'canary-10': return 10;
    case 'canary-50': return 50;
    case 'stable': return 100;
    default: throw new Error(`Unsupported deployment phase: ${phase}`);
  }
}

/** @param {unknown} value @returns {ServiceSpec} */
export function serviceSpec(value) {
  const fields = object(value);
  const spec = { service: stringField(fields, 'service'), revision: stringField(fields, 'revision'),
    image: stringField(fields, 'image'), tag: stringField(fields, 'tag'), url: stringField(fields, 'url'),
    internalUrl: stringField(fields, 'internalUrl') };
  assert.match(spec.image, /@sha256:[a-f0-9]{64}$/, 'Release image must be pinned by digest');
  assert.equal(new URL(spec.url).protocol, 'https:');
  assert.equal(new URL(spec.internalUrl).protocol, 'https:');
  return spec;
}

/** @param {unknown} value @param {ServiceSpec} expected @param {string} release */
export function assertRevision(value, expected, release) {
  const revision = object(value);
  assert.ok(stringField(revision, 'name').endsWith(`/services/${expected.service}/revisions/${expected.revision}`),
    'Wrong revision identity');
  assert.equal(object(revision.labels)['huey-release'], release, 'Wrong revision release');
  const container = object(array(revision.containers)[0]);
  assert.equal(container.image, expected.image, 'Wrong revision image digest');
  assert.ok(array(revision.conditions).some(value => {
    const condition = object(value);
    return condition.type === 'Ready' && condition.state === 'CONDITION_SUCCEEDED';
  }), 'Revision is not ready');
  return { environment: Object.fromEntries(array(container.env ?? []).map(value => {
    const entry = object(value);
    return [stringField(entry, 'name'), entry.value];
  })) };
}

/** @param {unknown} value @param {ServiceSpec} expected @param {number} percentage */
export function assertServiceTraffic(value, expected, percentage) {
  const service = object(value);
  const metadata = object(service.metadata);
  const status = object(service.status);
  assert.equal(metadata.name, expected.service, 'Wrong service identity');
  assert.ok(metadata.generation != null && status.observedGeneration != null,
    'Service generation is missing');
  assert.equal(String(status.observedGeneration), String(metadata.generation),
    `${expected.service} is still reconciling`);
  assert.ok(array(status.conditions).some(value => {
    const condition = object(value);
    return condition.type === 'Ready' && condition.status === 'True';
  }), `${expected.service} is not ready`);
  const traffic = array(status.traffic).map(value => {
    const entry = object(value);
    const percent = entry.percent ?? 0;
    assert.ok(typeof percent === 'number' && Number.isInteger(percent) && percent >= 0 && percent <= 100,
      'Invalid service traffic percentage');
    return { tag: entry.tag, revisionName: entry.revisionName, url: entry.url, percent };
  });
  assert.ok(traffic.some(entry => entry.tag === expected.tag &&
    entry.revisionName === expected.revision && entry.url === expected.url), 'Wrong candidate tag ownership or URL');
  assert.equal(traffic.reduce((sum, entry) => sum + entry.percent, 0), 100, 'Incomplete service traffic');
  const candidatePercentage = traffic.filter(entry => entry.revisionName === expected.revision)
    .reduce((sum, entry) => sum + entry.percent, 0);
  assert.equal(candidatePercentage, percentage, `${expected.service} has not reached ${percentage}% candidate traffic`);
}

/** @param {unknown[]} services @param {ServiceSpec[]} expected @param {number} percentage */
export function assertPairTraffic(services, expected, percentage) {
  assert.equal(expected.length, 2, 'Both public and internal services are required');
  assert.equal(services.length, 2, 'Both service snapshots are required');
  services.forEach((service, index) => assertServiceTraffic(service, expected[index], percentage));
}
