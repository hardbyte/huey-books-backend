import assert from 'node:assert/strict';

/** @typedef {{label: string, image: string | null, punchline: string | null}} VisibleOption */
/** @typedef {{prompt: string, inputType: string, options: VisibleOption[], bookTitles: string[]}} VisibleQuestion */

/** @param {unknown} value @returns {value is Record<string, unknown>} */
function isObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

/** @param {unknown} value @param {string} description */
function nonblank(value, description) {
  assert.ok(typeof value === 'string' && value.trim().length > 0, `${description} must contain text`);
  return value;
}

/** @param {unknown} value @returns {Record<string, unknown> | null} */
function findQuestion(value) {
  if (!isObject(value)) return null;
  if (isObject(value.input_request)) return value.input_request;
  if (value.type === 'question') return value;
  if (value.node_type === 'question') {
    assert.ok(isObject(value.content), 'Question node content is missing');
    return value.content;
  }
  const nested = findQuestion(value.next_node);
  if (nested) return nested;
  if (Array.isArray(value.messages)) {
    for (const message of [...value.messages].reverse()) {
      const question = findQuestion(message);
      if (question) return question;
    }
  }
  return null;
}

/** @param {unknown} response @returns {VisibleQuestion | null} */
export function visibleQuestion(response) {
  const question = findQuestion(response);
  if (!question) return null;
  const prompt = nonblank(isObject(question.question) ? question.question.text : undefined, 'Question prompt');
  const inputType = nonblank(question.input_type, 'Question input type');
  const options = Array.isArray(question.options) ? question.options.map(option => {
    assert.ok(isObject(option), 'Question option must be an object');
    const label = nonblank(option.label || option.text || option.value, 'Question option label');
    const image = option.image_url || option.image;
    const punchline = question.variable === 'temp.current_joke' || option.punchline != null
      ? nonblank(option.punchline, 'Joke punchline') : null;
    return { label, image: image == null ? null : nonblank(image, 'Option image URL'), punchline };
  }) : [];
  if (['button', 'choice', 'image_choice', 'carousel', 'multiple_choice'].includes(inputType)) {
    assert.ok(options.length > 0, 'Choice question has no options');
    assert.equal(new Set(options.map(option => option.label)).size, options.length, 'Choice labels are ambiguous');
  }
  if (inputType === 'image_choice') {
    assert.ok(options.every(option => option.image), 'Picture question has an option without an image');
  }
  const bookTitles = Array.isArray(question.books) ? question.books.map(book => {
    assert.ok(isObject(book), 'Recommended book must be an object');
    return nonblank(book.display_title, 'Recommended book title');
  }) : [];
  return { prompt, inputType, options, bookTitles };
}
