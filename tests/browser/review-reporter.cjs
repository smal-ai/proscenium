/* Durable, compact actual-browser evidence. Only this owned evidence directory is written. */
const {mkdirSync,writeFileSync,readFileSync}=require("node:fs");
const {createHash}=require("node:crypto");
const os=require("node:os");

module.exports=class ReviewReporter {
  constructor(){this.tests=[];}
  onTestEnd(test,result){
    this.tests.push({project:test.parent.project()?.name,title:test.title,status:result.status,
      errors:result.errors.map(e=>e.message),attachments:result.attachments.filter(a=>a.contentType==="application/json").map(a=>({name:a.name,data:JSON.parse((a.body??readFileSync(a.path)).toString())}))});
  }
  onEnd(result){
    const fixtures=["f03","f04"].map(family=>{
      const path=`/tmp/opencode/proscenium-e03-20261114-a/${family}/fixture.json`;
      return {family,path,sha256:createHash("sha256").update(readFileSync(path)).digest("hex")};
    });
    mkdirSync("docs/evidence/w05",{recursive:true});
    writeFileSync("docs/evidence/w05/browser-result.json",JSON.stringify({
      scope:"Actual W05 React surfaces in an isolated synthetic-HTTP harness. E03 numbered MP4 media. Not integrated application acceptance.",
      recorded_at:new Date().toISOString(),status:result.status,playwright:"1.65.1",node:process.version,
      platform:{platform:os.platform(),release:os.release(),arch:os.arch()},fixtures,
      command:"npx playwright test --config tests/browser/review.config.ts --reporter=line,./tests/browser/review-reporter.cjs",
      tests:this.tests,
    },null,2)+"\n");
  }
};
