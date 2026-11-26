/* Real browser/API/child KV, recorded live draft, explicitly simulated reader. */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {createRequire} = require('node:module');
const {chromium} = createRequire(path.resolve(__dirname, '../../sourcelens/web/package.json'))('playwright');

(async () => {
  const base=process.env.REVIEW_TEST_URL, out=process.env.REVIEW_ARTIFACTS;
  const fixtures=JSON.parse(await fs.readFile(path.join(out,'cases.json'),'utf8'));
  const browser=await chromium.launch({headless:true,executablePath:process.env.WORKBENCH_CHROME});
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[],external=[],results=[];
  page.on('pageerror', e=>errors.push(e.message));
  page.on('request',r=>{if (/^https?:/.test(r.url()) && new URL(r.url()).origin!==new URL(base).origin) external.push(r.url());});
  const read=async id=>(await page.request.get(`${base}/api/argument-runs/${id}`)).json();
  async function settled(id,status) {
    for(let i=0;i<250;i++) {
      const value=await read(id);
      if(value.status===status) return value;
      if(['failed','budget_exhausted','cancelled'].includes(value.status)) throw new Error(JSON.stringify(value));
      await page.waitForTimeout(50);
    }
    throw new Error('Run did not settle: '+JSON.stringify(await read(id)));
  }
  try {
    for(const fixture of fixtures.cases) {
      const response=await page.request.post(`${base}/api/argument-runs`,{data:fixtures.request});
      assert.equal(response.status(),202);
      const id=(await response.json()).id;
      const initial=await settled(id,'awaiting_review');
      assert.equal(initial.events.filter(e=>e.node==='model_call').length,1);
      assert.equal(initial.revision,0);
      assert(!initial.events.some(e=>e.node==='node_completed' && ['critique','revise'].includes(e.stage)));
      await page.goto(base);
      assert.equal(await page.getByLabel('Generation workflow',{exact:true}).inputValue(),'single_draft');
      await page.locator('.run-list button').first().click();
      await page.getByRole('heading',{name:'Review the current interpretation',exact:true}).waitFor();
      const edited=structuredClone(fixture.draft);
      for(const r of edited.readings) r.formal_scope=null;
      await page.getByText('Formula mapping and complete draft JSON',{exact:true}).click();
      await page.getByLabel('Advanced draft JSON',{exact:true}).fill(JSON.stringify(edited));
      await page.getByRole('button',{name:'Apply validated JSON changes',exact:true}).click();
      await page.getByLabel('Reading',{exact:true}).waitFor();
      // Citation and reading annotations are loaded through the validated editor;
      // actual formula/dependency/scope interaction happens in the graph UI.
      for(let index=0;index<fixture.draft.readings.length;index++) {
        await page.getByLabel('Reading',{exact:true}).selectOption(String(index));
        await page.getByLabel('Check declared formulas only',{exact:true}).check();
        await page.getByLabel('Formula scope rationale',{exact:true}).fill(fixture.draft.readings[index].formal_scope.rationale);
      }
      await page.getByRole('button',{name:'Select graph node n3',exact:true}).click();
      await page.locator('.structure-input > summary').click();
      const dependency=page.getByLabel('Dependency n5 into n3',{exact:true});
      await dependency.uncheck(); await dependency.check();
      await page.getByLabel('Approve the displayed formal mapping',{exact:true}).check();
      await page.getByLabel('Confirm formula-only scope',{exact:true}).check();
      const formula=page.getByLabel('Formula n3',{exact:true});
      await page.locator('.formula-input > summary').click();
      const finalFormula=fixture.draft.readings[1].formal_mapping.formulas.n3;
      await formula.fill(finalFormula==='exists x. H(x)' ? 'exists x. M(x)' : 'exists x. H(x)');
      await page.waitForFunction(()=>!document.querySelector('input[aria-label="Confirm formula-only scope"]').checked);
      assert.equal(await page.getByLabel('Approve the displayed formal mapping',{exact:true}).isChecked(),false);
      await formula.fill(finalFormula);
      if(fixture.id==='distinct-senses') {
        await page.getByRole('region',{name:'Editable argument graph'}).scrollIntoViewIfNeeded();
        await page.screenshot({path:path.join(out,'graph-annotated-desktop.png')});
        await page.setViewportSize({width:390,height:844});
        assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
        await page.screenshot({path:path.join(out,'graph-annotated-mobile.png')});
        await page.setViewportSize({width:1440,height:1000});
      }
      await page.getByLabel('Faithful to the passage',{exact:true}).selectOption('disputed');
      await page.getByLabel('Correct attribution',{exact:true}).selectOption('accepted');
      await page.getByLabel('Defensible interpretation',{exact:true}).selectOption('disputed');
      await page.getByLabel('Reviewer label',{exact:true}).fill('SIMULATED USER — assistant, not the actual reader');
      await page.getByLabel('Reason for these judgments',{exact:true}).fill('Authorized simulated review: check only the explicit formulas and dependencies. Interpretive limitations remain disputed. Added premises and deliberate concept conflation are labelled controls, not source claims.');
      await page.getByLabel('Approve the displayed formal mapping',{exact:true}).check();
      await page.getByLabel('Confirm formula-only scope',{exact:true}).check();
      await page.getByRole('button',{name:'Record review & finish',exact:true}).click();
      const final=await settled(id,'succeeded');
      assert.equal(final.versions.length,2);
      assert.equal(final.report.review_status,'disputed');
      assert.deepEqual(final.report.formal_checks.map(r=>r.outcome),fixture.expected);
      assert(final.report.formal_checks.every(r=>r.excluded_qualifications.length>0));
      assert.equal(final.events.filter(e=>e.node==='model_call').length,1);
      const workers=final.events.filter(e=>e.node==='kv_tool_result'&&!e.cache_hit);
      assert(final.events.filter(e=>e.node==='kv_tool_result').length>=2);
      assert(workers.length>=1 && workers.every(e=>e.worker_pid));
      const packet=await (await page.request.get(`${base}/api/argument-runs/${id}/report.json`)).json();
      const verified=await page.request.post(`${base}/api/argument-verify`,{data:packet});
      assert.equal(verified.status(),200);
      assert.equal((await verified.json()).status,'snapshot_verified');
      const comparison=await (await page.request.get(`${base}/api/argument-runs/${id}/compare?before=${final.versions[0].ref}&after=${final.versions[1].ref}`)).json();
      assert(comparison.changes.some(c=>c.path.includes('formal_scope')));
      const html=await (await page.request.get(`${base}/api/argument-runs/${id}/report.html`)).text();
      await fs.writeFile(path.join(out,`${fixture.id}-report.json`),JSON.stringify(packet,null,2));
      await fs.writeFile(path.join(out,`${fixture.id}-report.html`),html);
      await fs.writeFile(path.join(out,`${fixture.id}-comparison.json`),JSON.stringify(comparison,null,2));
      await page.getByText('Saved versions and comparison (2)',{exact:true}).waitFor();
      await page.getByText('Saved versions and comparison (2)',{exact:true}).click();
      await page.locator('.version-change').first().waitFor();
      results.push({case:fixture.id,run_id:id,outcomes:fixture.expected,versions:2,changes:comparison.changes.length,kv_workers:workers.map(w=>w.worker_pid),kv_cache_hits:final.events.filter(e=>e.node==='kv_tool_result'&&e.cache_hit).length,model_calls:0,replayed_initial_drafts:1,scope_confirmation_reset:true});
    }
    assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
    console.log(JSON.stringify({cases:results,page_errors:errors,external_requests:external}));
  } finally {
    if(results.length!==fixtures.cases.length) await page.screenshot({path:path.join(out,'failure.png'),fullPage:true}).catch(()=>{});
    await browser.close();
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
