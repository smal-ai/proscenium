// Retry the retained owner action after repairing missing empty import directories.
// The original dialog/operation remains present. No Queue or model call.
export async function recover({page}) {
  await page.getByRole('button',{name:'Activate selected project',exact:true}).click();
  await page.getByRole('textbox',{name:'Message Copilot',exact:true}).waitFor();
}
