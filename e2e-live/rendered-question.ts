import { expect, type Locator, type Page } from '@playwright/test';
import { visibleQuestion, type VisibleQuestion } from './question-contract.mjs';

export class RenderedQuestion {
  private question: VisibleQuestion | null = null;
  private revision = 0;
  private error: unknown;
  private punchlines = 0;

  get renderedPunchlines() { return this.punchlines; }

  constructor(private readonly page: Page) {
    page.on('response', response => {
      const path = new URL(response.url()).pathname;
      if (!path.startsWith('/v1/chat/') || response.request().method() !== 'POST' || !response.ok()) return;
      void response.json().then(body => {
        const question = visibleQuestion(body);
        if (question) {
          this.question = question;
          this.revision++;
        }
      }).catch(error => { this.error = error; });
    });
  }

  async assertComplete() {
    await expect.poll(() => {
      if (this.error) throw this.error;
      return this.question;
    }, { message: 'The real backend must supply a complete question' }).not.toBeNull();
    const question = this.question!;
    await expect(this.page.getByText(question.prompt, { exact: true }).last()).toBeVisible();
    const answer = this.page.getByRole('region', { name: 'Your answer' });
    if (question.inputType === 'book_feedback') {
      expect(question.bookTitles.length, 'This journey must produce recommendations').toBeGreaterThan(0);
      const titleNames = question.bookTitles.map(title => title.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
      const title = this.page.getByRole('heading', { level: 3, name: new RegExp(`^(?:${titleNames.join('|')})$`) });
      await expect(title).toBeVisible();
      return;
    }
    await expect(answer).toBeVisible();
    if (question.inputType === 'carousel') {
      const selected = answer.getByRole('button', { name: /^Select "/ });
      await expect(selected).toBeVisible();
      const name = await selected.innerText();
      const option = question.options.find(option => name === `Select "${option.label}"`);
      expect(option, 'Carousel selection must identify a returned option').toBeDefined();
      await expect(answer.getByText(option!.label, { exact: true })).toBeVisible();
      if (option!.image) await this.assertImage(answer.getByRole('img', { name: option!.label, exact: true }));
    } else if (question.options.length) {
      for (const option of question.options) {
        const choice = answer.getByRole(question.inputType === 'multiple_choice' ? 'checkbox' : 'button',
          { name: option.label, exact: true });
        await expect(choice).toBeVisible();
        if (option.image) await this.assertImage(choice.locator('img'));
      }
    } else {
      expect(['continue', 'text', 'number', 'slider'], 'Add a rendered-input assertion for new input types')
        .toContain(question.inputType);
      await expect(answer.locator('button, input').first()).toBeVisible();
    }
  }

  async answer(choice: Locator, { endsSession = false } = {}) {
    await this.assertComplete();
    const before = this.revision;
    const label = (await choice.innerText()).trim();
    const punchline = this.question?.options.find(option => option.label.trim() === label)?.punchline;
    const punchlineMessage = punchline ? this.page.getByRole('region', { name: 'Conversation' })
      .getByText(punchline, { exact: true }) : null;
    const previousPunchlines = punchlineMessage ? await punchlineMessage.count() : 0;
    await choice.click();
    if (!endsSession) {
      await expect.poll(() => {
        if (this.error) throw this.error;
        return this.revision;
      }, { message: 'Answering must advance to a new complete question' }).toBeGreaterThan(before);
    }
    if (punchlineMessage) {
      await expect(punchlineMessage, 'Answering a joke must render its selected punchline')
        .toHaveCount(previousPunchlines + 1);
      await expect(punchlineMessage.last()).toBeVisible();
      this.punchlines++;
    }
  }

  private async assertImage(image: Locator) {
    await expect(image).toBeVisible();
    await expect.poll(() => image.evaluate(element => element instanceof HTMLImageElement &&
      element.complete && element.naturalWidth > 0), { message: 'Required option image must load' }).toBe(true);
  }
}
