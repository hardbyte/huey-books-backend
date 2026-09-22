import assert from 'node:assert/strict';
import { test } from 'node:test';
import { visibleQuestion } from './question-contract.mjs';

const question = { question: { text: 'Which picture do you prefer?' }, input_type: 'image_choice',
  options: [{ label: 'A forest', value: 'forest', image_url: 'https://example.test/forest.jpg' }] };

test('start and interaction representations produce the same rendered-content expectations', () => {
  const expected = visibleQuestion({ input_request: question });
  for (const response of [
    { next_node: { type: 'messages', next_node: { type: 'question', ...question } } },
    { next_node: { node_type: 'question', content: question } },
    { messages: [{ type: 'question', ...question }] },
  ]) assert.deepEqual(visibleQuestion(response), expected);
  assert.equal(visibleQuestion({ session_ended: true, input_request: null }), null);
});

test('HTTP success cannot make incomplete question content acceptable', () => {
  for (const input_request of [
    { ...question, question: { text: '  ' } },
    { ...question, options: [] },
    { ...question, options: [{ label: '', value: '', image_url: 'https://example.test/a.jpg' }] },
    { ...question, options: [{ label: 'Forest', value: 'forest' }] },
  ]) assert.throws(() => visibleQuestion({ input_request }));
});

test('top-level input request wins over earlier questions in a message batch', () => {
  const response = { input_request: question,
    messages: [{ type: 'question', question: { text: 'Earlier' }, input_type: 'continue' }] };
  assert.equal(visibleQuestion(response)?.prompt, question.question.text);
});

test('joke questions require the punchline metadata that the UI must subsequently display', () => {
  const joke = { input_type: 'choice', question: { text: 'Why did the reader smile?' },
    variable: 'temp.current_joke', options: [{ label: 'Tell me!', value: 'tell_me', punchline: 'A happy ending!' }] };
  assert.equal(visibleQuestion({ input_request: joke })?.options[0].punchline, 'A happy ending!');
  for (const punchline of [undefined, '', '  ']) {
    assert.throws(() => visibleQuestion({ input_request: { ...joke,
      options: [{ ...joke.options[0], punchline }] } }), /Joke punchline/);
  }
});
