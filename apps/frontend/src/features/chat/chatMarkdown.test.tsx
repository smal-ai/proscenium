import assert from "node:assert/strict";
import { test } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import type { ApiClient, Event, RequestRecord } from "../../api";
import { ChatMarkdown } from "./ChatMarkdown";
import { Conversation } from "./Conversation";

const report = `Resumed without repeating renders or rebuilding source.

- **Source verified:** \`signal-lost-animation-v1\`, commit \`488544962f01b2455a00b14f5fb83fa3610ea63c\`.
- **Sequence validated and published:** \`signal-lost-animation-draft-v1\`, commit \`6054ad2181ee3359faf3ed131d5da76aa8f2c23e\`.

| Contributor | State | Verified frames |
|---|---|---:|
| Macro | Running; unchanged on second check | 28/72 |
| Conduit | Planned | 0/96 |
| Console | Planned | 0/72 |
| Exterior | Planned | 0/120 |
| Title | Planned | 0/72 |
| Proxy | Planned | 0/1 |

Inspected macro frames 1, 14 and 28. Verified partial MP4: \`attempt-bd7475de-6bb0-4f1a-bf55-a4f3f247d50a-s25-28\`—only four frames, not a finished shot.

**Blockers:** contributors remain incomplete; assembly was not submitted. VLM admission returned \`hash_mismatch\`; no review ticket was returned.

Full receipts and continuation state saved under \`assets/signal-lost-animation/audit-2e4c256e/\`, including \`resume-observations-r2.json\`.

Target remains 18 seconds, 24fps, 640×360, silent. Glass/transmission, sampling noise, nonphysical flare and separate title-stage cut limitations remain. **No completed final movie yet.**`;

const markdown = (text: string) => renderToStaticMarkup(<ChatMarkdown text={text} />);

test("the exact UAT reply renders its report table, list, status and code", () => {
  const markup = markdown(report);
  assert.equal((markup.match(/<table>/g) ?? []).length, 1);
  assert.equal((markup.match(/<thead>/g) ?? []).length, 1);
  const tbody = markup.match(/<tbody>([\s\S]+?)<\/tbody>/)?.[1] ?? "";
  assert.equal((tbody.match(/<tr>/g) ?? []).length, 6);
  assert.match(tbody, /<td>Macro<\/td><td>Running; unchanged on second check<\/td><td style="text-align:right" class="md-numeric">28\/72<\/td>/);
  assert.match(markup, /<th scope="col" style="text-align:right">Verified frames<\/th>/);
  assert.match(markup, /<strong>Blockers:<\/strong>/);
  assert.match(markup, /<code>hash_mismatch<\/code>/);
  assert.match(markup, /<ul><li>[\s\S]*?<\/li><li>[\s\S]*?<\/li><\/ul>/);
  assert.equal((markup.match(/<li>/g) ?? []).length, 2);
  assert.doesNotMatch(markup, /\|---\|---\|/);
});

test("Conversation assembles sorted streaming deltas and leaves user Markdown literal", () => {
  const text = `${report}\n\n\`\`\`text\nfirst | **unchanged**\nsecond\n\`\`\``;
  const cuts = [0, text.indexOf("resume-observations-r2.json") + 7, text.indexOf("Macro") + 2, text.indexOf("first |") + 8, text.length].sort((a, b) => a - b);
  const api = { call: () => { throw new Error("Unconfigured API operation in offline test"); } } as unknown as ApiClient;
  const userPrompt = "**literal** _request_ `code` [doc](https://example.com)";
  const identity = { schema_version: "1.0", project_id: "project", request_id: "request" } as const;
  const request: RequestRecord = {
    ...identity, accepted_at: "2026-11-18T10:00:00Z", effective_profile_id: null, generation_task_id: null,
    receipt: { ...identity, type: "queue", state: "accepted", state_revision: 1, work_id: null, native_message_id: null, replayed: false },
    submission: { ...identity, type: "queue", text: userPrompt, context_id: "context" },
  };
  for (const actor of [{ kind: "lead", id: "copilot" }, { kind: "subagent", id: "specialist" }] as const) {
    const event = (sequence: number, event_type: Event["event_type"], text: string): Event => ({
      ...identity, actor, sequence, event_type, payload: { message_id: "message", text, native_agent_id: actor.id },
      occurred_at: "2026-11-18T10:00:01Z", native_session_id: "session", native_event_id: null, work_id: "work",
    });
    const conversation = (events: Event[]) => renderToStaticMarkup(<Conversation api={api} projectId="project" requests={[request]} events={events} onRefresh={async () => {}} onError={() => {}} />);
    const deltas = cuts.slice(1).map((end, index) => event(index + 1, "assistant.delta", text.slice(cuts[index], end)));
    for (let count = 1; count <= deltas.length; count++) {
      const partial = conversation(deltas.slice(0, count).reverse());
      assert.ok(partial.includes(markdown(text.slice(0, cuts[count]))));
      if (count === 3) assert.match(partial, /<pre class="md-code-block"><code data-lang="text">first \| /);
    }
    const completed = event(deltas.length + 1, "assistant.completed", text);
    const settled = conversation([completed, ...deltas.slice().reverse()]);
    assert.equal(settled, conversation([completed]));
    assert.ok(settled.includes(markdown(text)));
    assert.match(settled, actor.kind === "lead" ? />Copilot<\/strong>/ : />Specialist<\/strong>/);
    assert.ok(settled.includes(`<p>${userPrompt}</p>`));
    const user = settled.match(/<article class="message user-message"[\s\S]*?<\/article>/)?.[0] ?? "";
    assert.doesNotMatch(user, /chat-markdown|<em>|<code>|<a /);
  }
});

test("pipe splitting respects code runs and escapes, aligns columns, and pads or trims rows", () => {
  const markup = markdown("Name | Middle | Total | Notes\n:--- | :---: | ---: | ---\n| `a|b` | a\\|b | 3 | ``one`|two`` | ignored |\nshort | row\n\nAfter table.");
  assert.match(markup, /<div class="md-table-wrap"><table>/);
  assert.match(markup, /<th scope="col" style="text-align:left">Name<\/th><th scope="col" style="text-align:center">Middle<\/th><th scope="col" style="text-align:right">Total<\/th><th scope="col">Notes<\/th>/);
  assert.match(markup, /<td style="text-align:left"><code>a\|b<\/code><\/td><td style="text-align:center">a\|b<\/td><td style="text-align:right" class="md-numeric">3<\/td><td><code>one`\|two<\/code><\/td>/);
  assert.match(markup, /<td style="text-align:left">short<\/td><td style="text-align:center">row<\/td><td style="text-align:right" class="md-numeric"><\/td><td><\/td><\/tr><\/tbody><\/table><\/div><p>After table\.<\/p>/);
  assert.equal((markup.match(/<td[ >]/g) ?? []).length, 8);
  assert.doesNotMatch(markup, /ignored/);
  assert.doesNotMatch(markdown("a | b\n---|---|---\n1 | 2"), /<table>/);
});

test("inline emphasis preserves identifiers, while code shields every inline construct", () => {
  const identifiers = "hash_mismatch request_vlm_review draft_motion signal_lost_v1 x__word__y vidéo_test_é";
  assert.equal(markdown(identifiers), `<div class="chat-markdown"><p>${identifiers}</p></div>`);
  assert.match(markdown("***both***, **bold**, __strong__, *italic*, (_em_), ~~removed~~"), /<strong><em>both<\/em><\/strong>, <strong>bold<\/strong>, <strong>strong<\/strong>, <em>italic<\/em>, \(<em>em<\/em>\), <del>removed<\/del>/);
  assert.match(markdown("**outer *inner*** and *outer **inner***"), /<strong>outer <em>inner<\/em><\/strong> and <em>outer <strong>inner<\/strong><\/em>/);
  assert.equal(markdown("`` `tick` **bold** _em_ ~~del~~ [link](https://example.com) ``"), "<div class=\"chat-markdown\"><p><code>`tick` **bold** _em_ ~~del~~ [link](https://example.com)</code></p></div>");
  assert.match(markdown("before `unclosed"), /<p>before `unclosed<\/p>/);
});

test("raw HTML and unsafe destinations are inert and images never load automatically", () => {
  const markup = markdown([
    '<script>alert(1)</script> <img src=x onerror=alert(1)> <iframe src="https://example.com/frame"></iframe>',
    '<svg><div data-label="[attribute](https://example.com)">literal</div></svg>',
    "[click](javascript:alert(1)) [data](data:text/html;base64,...) [rel](/api/v1/projects)",
    "[vb](vbscript:msgbox(1)) [file](file:///etc/hosts) [scheme-relative](//example.com) [embedded](java\tscript:alert(1))",
    "[entity](jav&#x61;script:alert(1)) [malformed](https:///example.com) [mail-authority](mailto://example.com)",
    "![remote](https://example.com/pixel.png) ![local](/pixel.png) ![inline](data:image/png;base64,...) ![mail](mailto:a@example.com)",
    "[docs](https://example.com/docs) [**HTTP**]( \u0001http://example.com/a(b)\u0001 ) [email](mailto:studio@example.com)",
  ].join("\n"));
  assert.doesNotMatch(markup, /<(?:script|img|iframe|svg|object|picture|link)\b|\s(?:onerror|src)="/i);
  assert.match(markup, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.match(markup, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.match(markup, /&lt;iframe src=&quot;https:\/\/example\.com\/frame&quot;&gt;/);
  assert.match(markup, /&lt;div data-label=&quot;\[attribute\]\(https:\/\/example\.com\)&quot;&gt;literal&lt;\/div&gt;/);
  assert.match(markup, /<br\/>click data rel<br\/>vb file scheme-relative embedded<br\/>entity malformed mail-authority/);
  assert.deepEqual(markup.match(/<a [^>]+>.*?<\/a>/g), [
    '<a href="https://example.com/pixel.png" target="_blank" rel="noopener noreferrer">[Image: remote]</a>',
    '<a href="https://example.com/docs" target="_blank" rel="noopener noreferrer">docs</a>',
    '<a href="http://example.com/a(b)" target="_blank" rel="noopener noreferrer"><strong>HTTP</strong></a>',
    '<a href="mailto:studio@example.com" target="_blank" rel="noopener noreferrer">email</a>',
  ]);
  assert.match(markup, /\[Image: local\] \[Image: inline\] \[Image: mail\]/);
});

test("fences preserve literal code, matching markers and an unfinished streaming tail", () => {
  const markup = markdown("````js title\nconst s = '<img>';\n```\n  **literal**\n````\n\n~~~text\n| still unfinished |\n  second");
  assert.match(markup, /<pre class="md-code-block"><code data-lang="js">const s = &#x27;&lt;img&gt;&#x27;;\n```\n {2}\*\*literal\*\*\n<\/code><\/pre>/);
  assert.match(markup, /<pre class="md-code-block"><code data-lang="text">\| still unfinished \|\n {2}second<\/code><\/pre>/);
  assert.equal(markdown("```\n```"), '<div class="chat-markdown"><pre class="md-code-block"><code></code></pre></div>');
});

test("headings, quotes, rules, nested task lists and simple paragraphs retain semantics", () => {
  assert.equal(markdown("Earlier UTC"), '<div class="chat-markdown"><p>Earlier UTC</p></div>');
  assert.equal(markdown("First\r\nSecond\n\nNext"), '<div class="chat-markdown"><p>First<br/>Second</p><p>Next</p></div>');
  const headings = markdown([1, 2, 3, 4, 5, 6].map(level => `${"#".repeat(level)} Level ${level} ###`).join("\n"));
  for (const [level, tag] of [3, 4, 5, 6, 6, 6].entries()) assert.ok(headings.includes(`<h${tag} class="md-h${level + 1}">Level ${level + 1}</h${tag}>`));
  assert.equal(markdown("> **Quoted**\n> lines\n>\n> > Nested\n\n---\n***\n___"), '<div class="chat-markdown"><blockquote><p><strong>Quoted</strong><br/>lines</p><blockquote><p>Nested</p></blockquote></blockquote><hr/><hr/><hr/></div>');
  const list = markdown("4) Numeral\n  - [x] Done\n  * [X] Also done\n    + Nested\n  + [ ] Open\n5. Another");
  assert.match(list, /<ol start="4"><li>Numeral<ul>/);
  assert.match(list, /Also done<ul><li>Nested<\/li><\/ul>/);
  assert.match(list, /<\/ul><\/li><li>Another<\/li><\/ol>/);
  assert.equal((list.match(/<input type="checkbox" disabled="" aria-label="Completed task" checked=""\/>/g) ?? []).length, 2);
  assert.match(list, /<input type="checkbox" disabled="" aria-label="Incomplete task"\/>Open/);
});
