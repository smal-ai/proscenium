import { Fragment, memo, useMemo, type ReactNode } from "react";
import { disambiguateArtifactLabels, matchChatEntity, type ChatEntityIndex, type ChatEntityLink } from "./entityLinks";
import "./chatMarkdown.css";

type Inline =
  | { kind: "text" | "code"; value: string }
  | { kind: "entity"; entity: ChatEntityLink }
  | { kind: "break" }
  | { kind: "strong" | "em" | "strongEm" | "del"; children: Inline[] }
  | { kind: "link"; href: string; children: Inline[] };

type Alignment = "left" | "center" | "right" | undefined;
type Block =
  | { kind: "paragraph"; children: Inline[] }
  | { kind: "heading"; level: number; children: Inline[] }
  | { kind: "fence"; lang: string; value: string }
  | { kind: "rule" }
  | { kind: "quote"; children: Block[] }
  | { kind: "list"; start: number | undefined; items: ListItem[] }
  | { kind: "table"; alignments: Alignment[]; header: Inline[][]; rows: Inline[][][] };

interface ListItem { checked?: boolean; children: Block[] }

const MAX_NESTING = 32;
const isEscape = (source: string, index: number) => source[index] === "\\" && /[!-/:-@[-`{-~]/.test(source[index + 1] ?? "");
const isWord = (text: string) => /[\p{L}\p{M}\p{N}]/u.test(text);

// Match complete runs, not a prefix of a longer run. Indexing the runs also
// keeps unmatched backticks cheap while the response is still streaming.
function codeSpans(source: string) {
  const runs = new Map<number, number[]>();
  for (const match of source.matchAll(/`+/g)) {
    const size = match[0].length;
    const starts = runs.get(size) ?? [];
    starts.push(match.index);
    runs.set(size, starts);
  }
  return (index: number) => {
    let openEnd = index + 1;
    while (source[openEnd] === "`") openEnd++;
    const size = openEnd - index;
    const starts = runs.get(size) ?? [];
    let low = 0;
    let high = starts.length;
    while (low < high) {
      const mid = Math.floor((low + high) / 2);
      if (starts[mid] < openEnd) low = mid + 1;
      else high = mid;
    }
    const close = starts[low];
    return { openEnd, close, end: close === undefined ? openEnd : close + size };
  };
}

function htmlEnd(source: string, index: number): number | undefined {
  // Recognise tags only to keep their spelling literal, including Markdown in attributes.
  const tag = /<!--(?:(?!<!--|-->)[\s\S])*-->|<\/?[A-Za-z][\w:-]*(?=[\s/>])(?:[^<>"']|"[^"]*"|'[^']*')*>/y;
  tag.lastIndex = index;
  return tag.test(source) ? tag.lastIndex : undefined;
}

function bracketPairs(source: string, codeAt: ReturnType<typeof codeSpans>) {
  const labels = new Map<number, number>();
  const destinations = new Map<number, number>();
  const brackets: number[] = [];
  const parens: number[] = [];
  let opaqueEnd = 0;
  for (let index = 0; index < source.length; index++) {
    if (isEscape(source, index)) { index++; continue; }
    const char = source[index];
    // URL parentheses balance independently of inline formatting in labels.
    if (char === "(") parens.push(index);
    if (char === ")" && parens.length) destinations.set(parens.pop()!, index);
    if (index < opaqueEnd) continue;
    if (char === "`") opaqueEnd = codeAt(index).end;
    if (char === "<") opaqueEnd = htmlEnd(source, index) ?? index;
    if (char === "[") brackets.push(index);
    if (char === "]" && brackets.length) labels.set(brackets.pop()!, index);
  }
  return { labels, destinations };
}

const isUrlPadding = (char: string) => char.charCodeAt(0) <= 0x20 || char.charCodeAt(0) === 0x7f || /\s/.test(char);

function safeHref(raw: string, image: boolean): string | undefined {
  let start = 0;
  let end = raw.length;
  while (start < end && isUrlPadding(raw[start])) start++;
  while (end > start && isUrlPadding(raw[end - 1])) end--;
  const href = raw.slice(start, end);
  for (const char of href) if (isUrlPadding(char) || char === "\\") return;
  const web = /^https?:\/\/[^/]/i.test(href);
  if (!web && (image || !/^mailto:[^/?#]/i.test(href))) return;
  try {
    const url = new URL(href);
    if (web ? url.hostname && (url.protocol === "http:" || url.protocol === "https:") : url.protocol === "mailto:") return href;
  } catch { /* Invalid absolute URL. */ }
}

interface Delimiter { marker: string; width: number; children: Inline[] }

function parseInline(source: string, entities?: ChatEntityIndex, allowLinks = true): Inline[] {
  const nodes: Inline[] = [];
  const stack: Delimiter[] = [];
  const codeAt = codeSpans(source);
  const pairs = allowLinks && source.includes("](") ? bracketPairs(source, codeAt) : undefined;
  const current = () => stack.at(-1)?.children ?? nodes;

  function text(value: string) {
    const children = current();
    const last = children.at(-1);
    if (last?.kind === "text") last.value += value;
    else children.push({ kind: "text", value });
  }

  function unwrap() {
    const open = stack.pop()!;
    text(open.marker.repeat(open.width));
    for (const child of open.children) {
      if (child.kind === "text") text(child.value);
      else current().push(child);
    }
  }

  for (let index = 0; index < source.length;) {
    const char = source[index];
    if (isEscape(source, index)) { text(source[index + 1]); index += 2; continue; }
    if (char === "`") {
      const { openEnd, close, end } = codeAt(index);
      if (close === undefined) text(source.slice(index, end));
      else {
        const code = source.slice(openEnd, close).replaceAll("\n", " ");
        const value = code.startsWith(" ") && code.endsWith(" ") && /[^ ]/.test(code) ? code.slice(1, -1) : code;
        const entity = allowLinks ? entities?.get(value) : undefined;
        current().push(entity ? { kind: "entity", entity } : { kind: "code", value });
      }
      index = end;
      continue;
    }
    if (char === "<") {
      const end = htmlEnd(source, index);
      if (end !== undefined) { text(source.slice(index, end)); index = end; continue; }
    }
    const image = char === "!" && source[index + 1] === "[";
    if (pairs && (char === "[" || image)) {
      const open = image ? index + 1 : index;
      const close = pairs.labels.get(open);
      const end = close === undefined ? undefined : pairs.destinations.get(close + 1);
      if (close !== undefined && end !== undefined) {
        const rawTarget = source.slice(close + 2, end);
        if (/\s/.test(rawTarget.trim())) { text(char); index++; continue; }
        const label = source.slice(open + 1, close);
        const target = rawTarget.replace(/\\([!-/:-@[-`{-~])/g, "$1");
        const href = safeHref(target, image);
        if (image && !href) text(`[Image: ${label}]`);
        else if (image && href) current().push({ kind: "link", href, children: [{ kind: "text", value: `[Image: ${label}]` }] });
        else if (href) current().push({ kind: "link", href, children: parseInline(label, undefined, false) });
        else current().push(...parseInline(label, undefined, false));
        index = end + 1;
        continue;
      }
    }
    // An indexed ID owns its underscores before Markdown can split them into emphasis.
    const citation = allowLinks && entities?.size ? matchChatEntity(source, index, entities) : undefined;
    if (citation) {
      current().push({ kind: "entity", entity: citation.entity });
      index = citation.end;
      continue;
    }
    if (char === "*" || char === "_" || char === "~") {
      let end = index + 1;
      while (source[end] === char) end++;
      let width = end - index;
      if (char === "~" ? width === 2 : width <= 3) {
        const previous = source[index - 1] ?? " ";
        const next = source[end] ?? " ";
        const canOpen = !/\s/.test(next) && (char !== "_" || !isWord(source.slice(Math.max(0, index - 2), index).match(/.$/u)?.[0] ?? ""));
        const canClose = !/\s/.test(previous) && (char !== "_" || !isWord(String.fromCodePoint(source.codePointAt(end) ?? 32)));
        while (canClose && width > 0) {
          let position = stack.length - 1;
          while (position >= 0 && stack[position].marker !== char) position--;
          if (position === -1) break;
          while (stack.length - 1 > position) unwrap();
          const open = stack[position];
          const used = Math.min(open.width, width);
          const kind = char === "~" ? "del" : used === 3 ? "strongEm" : used === 2 ? "strong" : "em";
          const child: Inline = { kind, children: open.children };
          open.width -= used;
          width -= used;
          if (open.width === 0) { stack.pop(); current().push(child); }
          else open.children = [child];
        }
        if (width > 0) {
          if (canOpen && stack.length < MAX_NESTING) stack.push({ marker: char, width, children: [] });
          else text(char.repeat(width));
        }
      } else text(source.slice(index, end));
      index = end;
      continue;
    }
    if (char === "\n") current().push({ kind: "break" });
    else text(char);
    index++;
  }
  while (stack.length) unwrap();
  return nodes;
}

function splitRow(line: string) {
  const source = line.trim();
  const codeAt = codeSpans(source);
  const cells: string[] = [];
  let start = 0;
  let hasPipe = false;
  for (let index = 0; index < source.length; index++) {
    if (isEscape(source, index)) { index++; continue; }
    if (source[index] === "`") { index = codeAt(index).end - 1; continue; }
    if (source[index] !== "|") continue;
    hasPipe = true;
    if (index > 0) cells.push(source.slice(start, index).trim());
    start = index + 1;
  }
  if (start < source.length || !hasPipe) cells.push(source.slice(start).trim());
  return { cells, hasPipe };
}

function tableHeader(line: string, delimiter: string | undefined) {
  if (!delimiter || !/^[\t |:-]+$/.test(delimiter)) return;
  const header = splitRow(line);
  const separator = splitRow(delimiter);
  if (!header.hasPipe && !separator.hasPipe) return;
  if (!header.cells.length || separator.cells.length !== header.cells.length || !separator.cells.every(cell => /^:?-+:?$/.test(cell))) return;
  const alignments: Alignment[] = separator.cells.map(cell => cell.endsWith(":") ? (cell.startsWith(":") ? "center" : "right") : cell.startsWith(":") ? "left" : undefined);
  return { header: header.cells, alignments };
}

const fencePattern = /^ {0,3}(`{3,}(?!`)[^`\n]*|~{3,}.*)$/;
const headingPattern = /^ {0,3}(#{1,6})(?:[ \t]+(.*)|$)/;
const quotePattern = /^ {0,3}>[ \t]?/;
const rulePattern = /^ {0,3}(?:(?:-[ \t]*){3,}|(?:\*[ \t]*){3,}|(?:_[ \t]*){3,})$/;
const listPattern = /^( *)([-*+]|\d{1,9}[.)])(?:[ \t]+(.*)|$)/;
const startsBlock = (line: string) => fencePattern.test(line) || headingPattern.test(line) || quotePattern.test(line) || rulePattern.test(line) || /^ {0,3}\S/.test(line) && listPattern.test(line);

function listMarker(line: string) {
  const match = listPattern.exec(line);
  if (!match) return;
  return { indent: match[1].length, marker: match[2], ordered: match[2].length > 1, text: match[3] ?? "" };
}

function parseBlocks(lines: string[], entities?: ChatEntityIndex, depth = 0): Block[] {
  const blocks: Block[] = [];
  for (let index = 0; index < lines.length;) {
    const line = lines[index];
    if (!line.trim()) { index++; continue; }
    const fence = fencePattern.exec(line);
    const heading = headingPattern.exec(line);
    const list = listMarker(line);
    if (fence) {
      const marker = /^(`+|~+)/.exec(fence[1])![0];
      const lang = fence[1].slice(marker.length).trim().split(/\s+/, 1)[0];
      const closing = new RegExp(`^ {0,3}${marker[0]}{${marker.length},}[ \t]*$`);
      const start = ++index;
      while (index < lines.length && !closing.test(lines[index])) index++;
      const value = lines.slice(start, index).join("\n");
      blocks.push({ kind: "fence", lang, value: index < lines.length && index > start ? `${value}\n` : value });
      if (index < lines.length) index++;
    } else if (heading) {
      const title = (heading[2] ?? "").replace(/(?:^|[ \t]+)#+[ \t]*$/, "").trim();
      blocks.push({ kind: "heading", level: heading[1].length, children: parseInline(title, entities) });
      index++;
    } else if (rulePattern.test(line)) {
      blocks.push({ kind: "rule" });
      index++;
    } else if (depth < MAX_NESTING && quotePattern.test(line)) {
      const quoted: string[] = [];
      while (index < lines.length && quotePattern.test(lines[index])) quoted.push(lines[index++].replace(quotePattern, ""));
      blocks.push({ kind: "quote", children: parseBlocks(quoted, entities, depth + 1) });
    } else if (depth < MAX_NESTING && list && list.indent <= 3) {
      const items: ListItem[] = [];
      const isSibling = (candidate: string) => {
        const next = listMarker(candidate);
        return next && next.ordered === list.ordered && next.indent === list.indent && !rulePattern.test(candidate);
      };
      while (index < lines.length && isSibling(lines[index])) {
        const item = listMarker(lines[index++])!;
        const task = /^\[([ xX])\](?:[ \t]+|$)/.exec(item.text);
        const body = [task ? item.text.slice(task[0].length) : item.text];
        // A two-space child is valid even for a wider ordered marker. Use one
        // offset for the whole item so subsequent indentation is preserved.
        let contentIndent = item.indent + item.marker.length + 1;
        let next = index;
        let bodyEnd = index;
        while (next < lines.length) {
          const continuation = lines[next];
          if (!continuation.trim()) { next++; continue; }
          const indent = /^ */.exec(continuation)![0].length;
          if (indent < item.indent + 2) break;
          contentIndent = Math.min(indent, contentIndent);
          bodyEnd = ++next;
        }
        while (index < bodyEnd) body.push(lines[index++].slice(contentIndent));
        if (next < lines.length && isSibling(lines[next])) index = next;
        items.push({ checked: task ? task[1] !== " " : undefined, children: parseBlocks(body, entities, depth + 1) });
      }
      blocks.push({ kind: "list", start: list.ordered ? Number.parseInt(list.marker, 10) : undefined, items });
    } else {
      const table = tableHeader(line, lines[index + 1]);
      if (table) {
        const rows: Inline[][][] = [];
        index += 2;
        while (index < lines.length && lines[index].trim() && !startsBlock(lines[index])) {
          const { cells } = splitRow(lines[index++]);
          rows.push(table.header.map((_, column) => parseInline(cells[column] ?? "", entities)));
        }
        blocks.push({ kind: "table", alignments: table.alignments, header: table.header.map(cell => parseInline(cell, entities)), rows });
      } else {
        const paragraph = [line];
        index++;
        while (index < lines.length && lines[index].trim() && !startsBlock(lines[index]) && !tableHeader(lines[index], lines[index + 1])) paragraph.push(lines[index++]);
        blocks.push({ kind: "paragraph", children: parseInline(paragraph.join("\n"), entities) });
      }
    }
  }
  return blocks;
}

function artifactLabels(blocks: Block[]) {
  const artifacts = new Map<string, Set<string>>();
  function inline(nodes: Inline[]) {
    for (const node of nodes) {
      switch (node.kind) {
        case "entity": {
          const { entity } = node;
          if (entity.kind === "artifact") {
            let ids = artifacts.get(entity.baseLabel);
            if (!ids) artifacts.set(entity.baseLabel, ids = new Set());
            ids.add(entity.id);
          }
          break;
        }
        case "strong": case "em": case "strongEm": case "del": inline(node.children); break;
      }
    }
  }
  function visit(blocks: Block[]) {
    for (const block of blocks) {
      switch (block.kind) {
        case "paragraph": case "heading": inline(block.children); break;
        case "quote": visit(block.children); break;
        case "list": for (const item of block.items) visit(item.children); break;
        case "table": block.header.forEach(inline); for (const row of block.rows) row.forEach(inline); break;
      }
    }
  }
  visit(blocks);
  return disambiguateArtifactLabels(artifacts);
}

type OpenEntity = (entity: ChatEntityLink) => void;
type EntityRenderer = { open: OpenEntity; labels: ReadonlyMap<string, string> };

function renderInline(nodes: Inline[], renderer?: EntityRenderer): ReactNode[] {
  return nodes.map((node, key) => {
    switch (node.kind) {
      case "text": return node.value;
      case "break": return <br key={key} />;
      case "code": return <code key={key}>{node.value}</code>;
      case "entity": {
        const { entity } = node;
        return <button key={key} type="button" className="chat-entity-link" data-entity-kind={entity.kind} title={entity.id} onClick={() => renderer?.open(entity)}>{renderer?.labels.get(entity.id) ?? entity.label}</button>;
      }
      case "strong": return <strong key={key}>{renderInline(node.children, renderer)}</strong>;
      case "em": return <em key={key}>{renderInline(node.children, renderer)}</em>;
      case "strongEm": return <strong key={key}><em>{renderInline(node.children, renderer)}</em></strong>;
      case "del": return <del key={key}>{renderInline(node.children, renderer)}</del>;
      case "link": return <a key={key} href={node.href} target="_blank" rel="noopener noreferrer">{renderInline(node.children)}</a>;
    }
  });
}

const headingTags = ["h3", "h4", "h5", "h6", "h6", "h6"] as const;

function renderBlocks(blocks: Block[], renderer?: EntityRenderer, tight = false): ReactNode[] {
  return blocks.map((block, key) => {
    switch (block.kind) {
      case "paragraph": return tight && key === 0 ? <Fragment key={key}>{renderInline(block.children, renderer)}</Fragment> : <p key={key}>{renderInline(block.children, renderer)}</p>;
      case "heading": {
        const Heading = headingTags[block.level - 1];
        return <Heading key={key} className={`md-h${block.level}`}>{renderInline(block.children, renderer)}</Heading>;
      }
      case "fence": return <pre key={key} className="md-code-block"><code data-lang={block.lang || undefined}>{block.value}</code></pre>;
      case "rule": return <hr key={key} />;
      case "quote": return <blockquote key={key}>{renderBlocks(block.children, renderer)}</blockquote>;
      case "list": {
        const items = block.items.map(({ checked, children }, item) => <li key={item} className={checked === undefined ? undefined : "md-task"}>
          {checked !== undefined && <input type="checkbox" disabled checked={checked} aria-label={checked ? "Completed task" : "Incomplete task"} />}
          {renderBlocks(children, renderer, true)}
        </li>);
        return block.start === undefined ? <ul key={key}>{items}</ul> : <ol key={key} start={block.start === 1 ? undefined : block.start}>{items}</ol>;
      }
      case "table": return <div key={key} className="md-table-wrap"><table>
        <thead><tr>{block.header.map((cell, column) => <th key={column} scope="col" style={{ textAlign: block.alignments[column] }}>{renderInline(cell, renderer)}</th>)}</tr></thead>
        <tbody>{block.rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, column) => {
          const textAlign = block.alignments[column];
          return <td key={column} style={{ textAlign }} className={textAlign === "right" ? "md-numeric" : undefined}>{renderInline(cell, renderer)}</td>;
        })}</tr>)}</tbody>
      </table></div>;
    }
  });
}

/** Chat's GFM subset renders only the elements enumerated above. */
export const ChatMarkdown = memo(function ChatMarkdown({ text, entities, onOpenEntity }: { text: string; entities?: ChatEntityIndex; onOpenEntity?: OpenEntity }) {
  const activeEntities = onOpenEntity && entities?.size ? entities : undefined;
  const { blocks, labels } = useMemo(() => {
    const blocks = parseBlocks(text.split(/\r\n?|\n/), activeEntities);
    return { blocks, labels: activeEntities ? artifactLabels(blocks) : undefined };
  }, [text, activeEntities]);
  return <div className="chat-markdown">{renderBlocks(blocks, labels && onOpenEntity ? { open: onOpenEntity, labels } : undefined)}</div>;
});
