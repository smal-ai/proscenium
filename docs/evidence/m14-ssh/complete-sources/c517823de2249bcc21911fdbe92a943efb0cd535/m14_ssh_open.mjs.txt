// Explicit owner UI operation. Pairing is not project activation.
export async function activateImportedProject(page, projectId) {
  await page.getByRole('button', {name:'Choose a project', exact:true}).click();
  await page.getByLabel('Saved project', {exact:true}).selectOption(projectId);
  await page.getByRole('button', {name:'Activate selected project', exact:true}).click();
  await page.getByRole('textbox', {name:'Message Copilot', exact:true}).waitFor();
}
