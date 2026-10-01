function escapeMarkdownHtml(text) {
  const element = document.createElement("span");
  element.textContent = text;
  return element.innerHTML;
}

const replyMarkdown = new marked.Marked({
  gfm: true,
  breaks: true,
  renderer: {
    html: ({ text }) => escapeMarkdownHtml(text),
  },
});

function renderReplyMarkdown(text) {
  const fragment = DOMPurify.sanitize(replyMarkdown.parse(text), {
    RETURN_DOM_FRAGMENT: true,
    ALLOWED_TAGS: [
      "p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6",
      "strong", "em", "del", "blockquote", "ul", "ol", "li",
      "pre", "code", "table", "thead", "tbody", "tr", "th", "td", "a",
    ],
    ALLOWED_ATTR: ["href", "title", "align", "start"],
    ALLOW_DATA_ATTR: false,
    ALLOW_ARIA_ATTR: false,
  });

  for (const link of fragment.querySelectorAll("a")) {
    const href = link.getAttribute("href");
    let url;
    try {
      url = href ? new URL(href, document.baseURI) : null;
    } catch {
      url = null;
    }
    if (!url || !["http:", "https:", "mailto:"].includes(url.protocol)) {
      link.replaceWith(...link.childNodes);
    } else if (url.protocol !== "mailto:" && url.origin !== location.origin) {
      link.target = "_blank";
      link.rel = "noopener noreferrer";
    }
  }

  for (const table of fragment.querySelectorAll("table")) {
    const scroller = document.createElement("div");
    scroller.className = "markdown-table";
    scroller.tabIndex = 0;
    scroller.setAttribute("role", "region");
    scroller.setAttribute("aria-label", "Markdown table");
    table.replaceWith(scroller);
    scroller.append(table);
  }
  return fragment;
}
