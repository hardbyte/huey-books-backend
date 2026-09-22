import { expect, test } from '@playwright/test';

for (const scenario of [
  { age: '5 or under', width: 390, height: 844 },
  { age: '8', width: 390, height: 320 },
  { age: '11', width: 1280, height: 900 },
]) {
  test(`real reader journey: age ${scenario.age}, ${scenario.width}px`, async ({ page }) => {
    const failures: string[] = [];
    let candidateRequests = 0;
    let candidateResponses = 0;
    const candidate = process.env.E2E_CANDIDATE_API_URL;
    if (candidate) {
      const destination = new URL(candidate);
      if (destination.protocol !== 'https:' || !destination.hostname.endsWith('.a.run.app'))
        throw new Error('Candidate must be a Cloud Run HTTPS URL');
      await page.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.pathname.startsWith('/v1/chat/') && !['api.hueybooks.com', destination.host].includes(url.host)) {
          failures.push(`Unexpected chat backend: ${url.origin}`);
          await route.abort();
          return;
        }
        if (url.host !== 'api.hueybooks.com') {
          await route.continue();
          return;
        }
        url.host = destination.host;
        candidateRequests++;
        await route.continue({ url: url.toString() });
      });
    }
    page.on('response', response => {
      if (candidate && response.url().includes('/v1/chat/')) {
        if (new URL(response.url()).origin !== new URL(candidate).origin)
          failures.push(`Chat response came from ${new URL(response.url()).origin}`);
        else candidateResponses++;
      }
      if (response.url().includes('/v1/chat/') && response.status() >= 400)
        failures.push(`${response.status()} ${new URL(response.url()).pathname}`);
    });
    page.on('pageerror', error => failures.push(error.message));
    await page.setViewportSize(scenario);
    await page.goto(process.env.E2E_CHAT_PATH || '/chat/start/');
    await expect(page.getByText('Ready to find your next book?', { exact: true })).toBeVisible();
    if (candidate) {
      expect(candidateRequests).toBeGreaterThan(0);
      expect(candidateResponses).toBeGreaterThan(0);
    }
    await page.getByRole('button', { name: 'Hello Huey! 👋', exact: true }).click();
    const schoolConfirmation = page.getByRole('button', { name: /Yes, find books/ });
    await expect(schoolConfirmation.or(page.getByRole('button', { name: scenario.age, exact: true }))).toBeVisible();
    if (await schoolConfirmation.isVisible()) await schoolConfirmation.click();
    const ageButton = page.getByRole('button', { name: scenario.age, exact: true });
    await ageButton.scrollIntoViewIfNeeded();
    const box = await ageButton.boundingBox();
    expect(box!.y + box!.height).toBeLessThanOrEqual(scenario.height);
    await ageButton.click();
    await page.getByRole('button', { name: /^Select "/ }).click();
    const answer = page.getByRole('region', { name: 'Your answer' });
    const done = page.getByRole('button', { name: 'Done with my books', exact: true });
    for (let preference = 0; preference < 3; preference++) {
      const choices = answer.getByRole('button');
      await expect(choices.first().or(done)).toBeVisible();
      if (await done.isVisible()) break;
      await expect(answer.getByText('Choose one picture to continue')).toBeVisible();
      await choices.first().click();
      await expect(answer.getByText('Choose one picture to continue')).toBeHidden();
    }
    await expect(done).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole('heading', { level: 3 }).first()).toHaveText(/\S/);
    await page.getByRole('button', { name: /Looks good!/ }).click();
    await done.click();
    if (scenario.age === '5 or under') {
      await answer.getByRole('button', { name: /Yes please!/ }).click();
      const spelling = page.getByText('Want to try a quick spelling game? 📝', { exact: true });
      for (let turn = 0; turn < 50; turn++) {
        await expect(answer.getByRole('button').first()).toBeVisible();
        if (await spelling.isVisible()) break;
        await Promise.all([
          page.waitForResponse(response => response.url().includes('/interact') && response.request().method() === 'POST'),
          answer.getByRole('button').first().click(),
        ]);
      }
      await expect(page.getByText("That's all my jokes for now. Let's move on!", { exact: true })).toBeVisible();
    } else {
      await answer.getByRole('button', { name: /No thanks/ }).click();
    }
    await expect(page.getByText('Want to try a quick spelling game? 📝', { exact: true })).toBeVisible();
    await answer.getByRole('button', { name: 'No thanks', exact: true }).click();
    await answer.getByRole('button', { name: "I'm done, thanks! 👋", exact: true }).click();
    await expect(page.getByRole('button', { name: /Read More Books!/ })).toBeVisible();
    expect(failures).toEqual([]);
  });
}
