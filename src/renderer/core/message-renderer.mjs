const IMAGE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "ico", "avif"]);
const VIDEO_EXTENSIONS = new Set(["mp4", "webm", "ogv", "mov"]);
const AUDIO_EXTENSIONS = new Set(["mp3", "wav", "ogg", "m4a", "flac"]);
const URL_RE = /https?:\/\/[^\s<>()]+/i;
const WINDOWS_PATH_RE = /^[a-zA-Z]:[\\/][^\n\r<>"]+$/;
const FILE_URL_RE = /^file:\/\/\/?/i;

function create(tag, className = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  return node;
}

function extensionOf(value) {
  const clean = String(value || "").split(/[?#]/)[0].replace(/["')\]}.,;:]+$/, "");
  const match = clean.match(/\.([a-zA-Z0-9]+)$/);
  return match ? match[1].toLowerCase() : "";
}

function isAbsoluteWindowsPath(value) {
  return WINDOWS_PATH_RE.test(String(value || "").trim());
}

function windowsPathToFileUrl(value) {
  const normalized = String(value || "").trim().replace(/\\/g, "/");
  return `file:///${encodeURI(normalized).replace(/%3A/i, ":")}`;
}

function normalizeUrl(value, { media = false } = {}) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  if (isAbsoluteWindowsPath(raw)) return windowsPathToFileUrl(raw);
  if (/^(https?:|mailto:)/i.test(raw)) return raw;
  if (FILE_URL_RE.test(raw)) return raw;
  if (media && /^data:image\//i.test(raw)) return raw;
  if (/^\.{0,2}\//.test(raw)) return raw;
  return "";
}

function configureLink(link, target, label = "") {
  const raw = String(target || "").trim();
  link.href = normalizeUrl(raw);
  link.target = "_blank";
  link.rel = "noreferrer";
  if (isAbsoluteWindowsPath(raw)) {
    link.dataset.openPath = raw;
    link.title = raw;
  }
  if (label) appendInline(link, label);
  else link.textContent = raw;
}

function mediaKind(value) {
  const ext = extensionOf(value);
  if (IMAGE_EXTENSIONS.has(ext)) return "image";
  if (VIDEO_EXTENSIONS.has(ext)) return "video";
  if (AUDIO_EXTENSIONS.has(ext)) return "audio";
  return "";
}

function trimTrailingUrlPunctuation(value) {
  return String(value || "").replace(/[.,;:!?]+$/, "");
}

function parseLinkAt(text, offset, image = false) {
  const start = image ? offset + 2 : offset + 1;
  const closeLabel = text.indexOf("]", start);
  if (closeLabel < 0 || text[closeLabel + 1] !== "(") return null;
  let depth = 1;
  let index = closeLabel + 2;
  while (index < text.length) {
    const char = text[index];
    if (char === "(") depth += 1;
    if (char === ")") {
      depth -= 1;
      if (depth === 0) {
        return {
          end: index + 1,
          label: text.slice(start, closeLabel),
          target: text.slice(closeLabel + 2, index).trim()
        };
      }
    }
    index += 1;
  }
  return null;
}

function appendText(parent, value) {
  if (value) parent.appendChild(document.createTextNode(value));
}

function appendInline(parent, text) {
  const source = String(text || "");
  let index = 0;

  while (index < source.length) {
    const rest = source.slice(index);

    const urlMatch = rest.match(URL_RE);
    const nextUrlIndex = urlMatch ? index + urlMatch.index : -1;
    const nextSpecialIndex = ["![", "[", "`", "**", "__", "~~", "*", "_"]
      .map((token) => source.indexOf(token, index))
      .filter((pos) => pos >= 0)
      .sort((a, b) => a - b)[0] ?? -1;
    const nextIndex = [nextUrlIndex, nextSpecialIndex]
      .filter((pos) => pos >= 0)
      .sort((a, b) => a - b)[0];

    if (nextIndex > index) {
      appendText(parent, source.slice(index, nextIndex));
      index = nextIndex;
      continue;
    }

    if (source.startsWith("`", index)) {
      const end = source.indexOf("`", index + 1);
      if (end > index + 1) {
        const code = create("code", "md-inline-code");
        code.textContent = source.slice(index + 1, end);
        parent.appendChild(code);
        index = end + 1;
        continue;
      }
    }

    if (source.startsWith("![", index)) {
      const parsed = parseLinkAt(source, index, true);
      if (parsed) {
        const src = normalizeUrl(parsed.target, { media: true });
        if (src) {
          const img = create("img", "md-inline-image");
          img.src = src;
          img.alt = parsed.label || "";
          img.loading = "lazy";
          parent.appendChild(img);
        } else {
          appendText(parent, source.slice(index, parsed.end));
        }
        index = parsed.end;
        continue;
      }
    }

    if (source.startsWith("[", index)) {
      const parsed = parseLinkAt(source, index, false);
      if (parsed) {
        const href = normalizeUrl(parsed.target);
        if (href) {
          const link = create("a", "md-link");
          configureLink(link, parsed.target, parsed.label || parsed.target);
          parent.appendChild(link);
        } else {
          appendText(parent, source.slice(index, parsed.end));
        }
        index = parsed.end;
        continue;
      }
    }

    const paired = [
      ["**", "strong"],
      ["__", "strong"],
      ["~~", "s"],
      ["*", "em"],
      ["_", "em"]
    ].find(([token]) => source.startsWith(token, index));
    if (paired) {
      const [token, tag] = paired;
      const end = source.indexOf(token, index + token.length);
      if (end > index + token.length) {
        const node = document.createElement(tag);
        appendInline(node, source.slice(index + token.length, end));
        parent.appendChild(node);
        index = end + token.length;
        continue;
      }
    }

    if (nextUrlIndex === index && urlMatch) {
      const url = trimTrailingUrlPunctuation(urlMatch[0]);
      const link = create("a", "md-link");
      configureLink(link, url);
      parent.appendChild(link);
      index += urlMatch[0].length;
      continue;
    }

    appendText(parent, source[index]);
    index += 1;
  }
}

function parseTableCells(line) {
  return String(line || "")
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function isTableDelimiter(line) {
  const cells = parseTableCells(line);
  return cells.length > 1 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function tableAlignments(line) {
  return parseTableCells(line).map((cell) => {
    if (/^:-+:$/.test(cell)) return "center";
    if (/^-+:$/.test(cell)) return "right";
    if (/^:-+$/.test(cell)) return "left";
    return "";
  });
}

function appendParagraph(parent, lines) {
  const text = lines.join("\n").trim();
  if (!text) return;
  const paragraph = create("p", "md-paragraph");
  appendInline(paragraph, text);
  parent.appendChild(paragraph);
}

function appendCodeBlock(parent, code, language = "") {
  const block = create("div", "md-code");
  const header = create("div", "md-code-header");
  const label = create("span", "md-code-lang");
  label.textContent = language || "text";
  const button = create("button", "md-code-copy");
  button.type = "button";
  button.textContent = "复制";
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(String(code || ""));
      button.textContent = "已复制";
      setTimeout(() => {
        button.textContent = "复制";
      }, 1200);
    } catch {
      button.textContent = "复制失败";
      setTimeout(() => {
        button.textContent = "复制";
      }, 1200);
    }
  });
  header.appendChild(label);
  header.appendChild(button);
  const pre = create("pre", "md-pre");
  const codeNode = create("code", language ? `language-${language}` : "");
  codeNode.textContent = String(code || "");
  pre.appendChild(codeNode);
  block.appendChild(header);
  block.appendChild(pre);
  parent.appendChild(block);
}

function appendTable(parent, headerLine, delimiterLine, bodyLines) {
  const wrap = create("div", "md-table-wrap");
  const table = create("table", "md-table");
  const headerCells = parseTableCells(headerLine);
  const alignments = tableAlignments(delimiterLine);
  const thead = document.createElement("thead");
  const headerRow = document.createElement("tr");
  headerCells.forEach((cell, index) => {
    const th = document.createElement("th");
    if (alignments[index]) th.style.textAlign = alignments[index];
    appendInline(th, cell);
    headerRow.appendChild(th);
  });
  thead.appendChild(headerRow);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  for (const line of bodyLines) {
    const row = document.createElement("tr");
    parseTableCells(line).forEach((cell, index) => {
      const td = document.createElement("td");
      if (alignments[index]) td.style.textAlign = alignments[index];
      appendInline(td, cell);
      row.appendChild(td);
    });
    tbody.appendChild(row);
  }
  table.appendChild(tbody);
  wrap.appendChild(table);
  parent.appendChild(wrap);
}

function appendList(parent, lines, ordered = false) {
  const list = document.createElement(ordered ? "ol" : "ul");
  list.className = "md-list";
  for (const line of lines) {
    const match = line.match(/^\s*(?:[-*+]|\d+[.)])\s+(\[[ xX]\]\s+)?([\s\S]*)$/);
    if (!match) continue;
    const item = document.createElement("li");
    item.className = match[1] ? "md-task-item" : "";
    if (match[1]) {
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.disabled = true;
      checkbox.checked = /x/i.test(match[1]);
      item.appendChild(checkbox);
    }
    appendInline(item, match[2]);
    list.appendChild(item);
  }
  parent.appendChild(list);
}

function appendQuote(parent, lines) {
  const quote = create("blockquote", "md-quote");
  renderMarkdownInto(quote, lines.map((line) => line.replace(/^>\s?/, "")).join("\n"));
  parent.appendChild(quote);
}

function appendMedia(parent, value) {
  const src = normalizeUrl(value, { media: true });
  const kind = mediaKind(value);
  if (!src || !kind) return false;
  const figure = create("figure", "md-media");
  if (kind === "image") {
    const img = document.createElement("img");
    img.src = src;
    img.alt = value;
    img.loading = "lazy";
    figure.appendChild(img);
  } else if (kind === "video") {
    const video = document.createElement("video");
    video.src = src;
    video.controls = true;
    figure.appendChild(video);
  } else if (kind === "audio") {
    const audio = document.createElement("audio");
    audio.src = src;
    audio.controls = true;
    figure.appendChild(audio);
  }
  const caption = create("figcaption", "md-media-caption");
  caption.textContent = value;
  figure.appendChild(caption);
  parent.appendChild(figure);
  return true;
}

function appendFileLink(parent, value) {
  const href = normalizeUrl(value);
  if (!href) return false;
  const wrap = create("div", "md-file-link");
  const link = create("a", "md-link");
  configureLink(link, value);
  wrap.appendChild(link);
  parent.appendChild(wrap);
  return true;
}

function lineLooksLikeMedia(line) {
  const text = String(line || "").trim();
  return Boolean(text && (isAbsoluteWindowsPath(text) || /^https?:\/\//i.test(text) || FILE_URL_RE.test(text)) && mediaKind(text));
}

function lineLooksLikeFileLink(line) {
  const text = String(line || "").trim();
  return Boolean(text && isAbsoluteWindowsPath(text) && !mediaKind(text));
}

function renderMarkdownInto(parent, sourceText) {
  const lines = String(sourceText || "").replace(/\r\n/g, "\n").split("\n");
  let index = 0;
  let paragraph = [];

  const flushParagraph = () => {
    appendParagraph(parent, paragraph);
    paragraph = [];
  };

  while (index < lines.length) {
    const line = lines[index];
    const trimmed = line.trim();

    if (!trimmed) {
      flushParagraph();
      index += 1;
      continue;
    }

    const fence = line.match(/^\s*```([A-Za-z0-9_+.-]*)\s*$/);
    if (fence) {
      flushParagraph();
      const language = fence[1] || "";
      const body = [];
      index += 1;
      while (index < lines.length && !/^\s*```\s*$/.test(lines[index])) {
        body.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) index += 1;
      appendCodeBlock(parent, body.join("\n"), language);
      continue;
    }

    if (/^( {4}|\t)/.test(line)) {
      flushParagraph();
      const body = [];
      while (index < lines.length && (/^( {4}|\t)/.test(lines[index]) || !lines[index].trim())) {
        body.push(lines[index].replace(/^( {4}|\t)/, ""));
        index += 1;
      }
      appendCodeBlock(parent, body.join("\n").replace(/\n+$/, ""), "");
      continue;
    }

    if (lineLooksLikeMedia(trimmed)) {
      flushParagraph();
      appendMedia(parent, trimmed);
      index += 1;
      continue;
    }

    if (lineLooksLikeFileLink(trimmed)) {
      flushParagraph();
      appendFileLink(parent, trimmed);
      index += 1;
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      const level = Math.min(6, heading[1].length);
      const node = create(`h${level}`, `md-heading md-heading-${level}`);
      appendInline(node, heading[2].replace(/\s+#+\s*$/, ""));
      parent.appendChild(node);
      index += 1;
      continue;
    }

    if (/^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/.test(line)) {
      flushParagraph();
      parent.appendChild(create("hr", "md-hr"));
      index += 1;
      continue;
    }

    if (/^>\s?/.test(line)) {
      flushParagraph();
      const block = [];
      while (index < lines.length && /^>\s?/.test(lines[index])) {
        block.push(lines[index]);
        index += 1;
      }
      appendQuote(parent, block);
      continue;
    }

    if (line.includes("|") && index + 1 < lines.length && isTableDelimiter(lines[index + 1])) {
      flushParagraph();
      const headerLine = line;
      const delimiterLine = lines[index + 1];
      const body = [];
      index += 2;
      while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
        body.push(lines[index]);
        index += 1;
      }
      appendTable(parent, headerLine, delimiterLine, body);
      continue;
    }

    const listMatch = line.match(/^\s*(?:([-*+])|(\d+[.)]))\s+/);
    if (listMatch) {
      flushParagraph();
      const ordered = Boolean(listMatch[2]);
      const block = [];
      while (index < lines.length) {
        const current = lines[index];
        const currentMatch = current.match(/^\s*(?:([-*+])|(\d+[.)]))\s+/);
        if (!currentMatch || Boolean(currentMatch[2]) !== ordered) break;
        block.push(current);
        index += 1;
      }
      appendList(parent, block, ordered);
      continue;
    }

    paragraph.push(line);
    index += 1;
  }

  flushParagraph();
}

export function renderMessageContent(container, text) {
  container.innerHTML = "";
  container.classList.add("rich-text");
  renderMarkdownInto(container, text);
  if (!container.childNodes.length) {
    const empty = create("p", "md-paragraph");
    empty.textContent = String(text || "");
    container.appendChild(empty);
  }
}
