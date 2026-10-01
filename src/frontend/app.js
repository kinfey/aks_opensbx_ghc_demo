const form = document.querySelector("#chat-form");
const promptInput = document.querySelector("#prompt");
const sendButton = document.querySelector("#send");
const messages = document.querySelector("#messages");
const errorBox = document.querySelector("#error");
const localeSelect = document.querySelector("#locale");
const newChatButton = document.querySelector("#new-chat");

const state = {
  sessionId: getOrCreateSessionId(),
  busy: false,
};

function getOrCreateSessionId() {
  const saved = sessionStorage.getItem("golden-order-session");
  if (saved) return saved;
  const id = crypto.randomUUID().replaceAll("-", "");
  sessionStorage.setItem("golden-order-session", id);
  return id;
}

function makeMessage(role, text, loading = false) {
  const article = document.createElement("article");
  article.className = `message ${role}`;

  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.setAttribute("aria-hidden", "true");
  avatar.textContent = role === "assistant" ? "M" : "你";

  const bubble = document.createElement("div");
  bubble.className = "bubble";

  const label = document.createElement("p");
  label.className = "message-label";
  label.textContent = role === "assistant" ? "Golden Order Copilot" : "你";
  bubble.append(label);

  if (loading) {
    const typing = document.createElement("span");
    typing.className = "typing";
    typing.setAttribute("aria-label", "正在思考");
    typing.append(document.createElement("i"), document.createElement("i"), document.createElement("i"));
    bubble.append(typing);
  } else {
    const content = document.createElement(role === "assistant" ? "div" : "p");
    if (role === "assistant") {
      content.className = "markdown-body";
      content.append(renderReplyMarkdown(text));
    } else {
      content.className = "message-text";
      content.textContent = text;
    }
    bubble.append(content);
  }

  article.append(avatar, bubble);
  messages.append(article);
  messages.scrollTop = messages.scrollHeight;
  return article;
}

function showError(message) {
  errorBox.textContent = message;
  errorBox.hidden = false;
}

function clearError() {
  errorBox.hidden = true;
  errorBox.textContent = "";
}

function setBusy(busy) {
  state.busy = busy;
  sendButton.disabled = busy;
  promptInput.disabled = busy;
}

async function sendMessage(message) {
  if (state.busy || !message.trim()) return;
  clearError();
  makeMessage("user", message.trim());
  const loading = makeMessage("assistant", "", true);
  setBusy(true);

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: state.sessionId,
        message: message.trim(),
        cart: [],
        locale: localeSelect.value,
      }),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(body.detail || `请求失败（${response.status}）`);
    }
    loading.remove();
    makeMessage("assistant", body.message);
  } catch (error) {
    loading.remove();
    showError(error instanceof Error ? error.message : "点餐助手暂时不可用。");
  } finally {
    setBusy(false);
    promptInput.focus();
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const message = promptInput.value;
  promptInput.value = "";
  promptInput.style.height = "auto";
  void sendMessage(message);
});

promptInput.addEventListener("input", () => {
  promptInput.style.height = "auto";
  promptInput.style.height = `${Math.min(promptInput.scrollHeight, 150)}px`;
});

promptInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

document.querySelectorAll(".suggestion").forEach((button) => {
  button.addEventListener("click", () => {
    const message = button.dataset.prompt || "";
    void sendMessage(message);
  });
});

newChatButton.addEventListener("click", async () => {
  const previous = state.sessionId;
  state.sessionId = crypto.randomUUID().replaceAll("-", "");
  sessionStorage.setItem("golden-order-session", state.sessionId);
  messages.replaceChildren();
  makeMessage("assistant", "新的 Kata 隔离会话已准备好。想先查询优惠券，还是看看菜单？");
  try {
    await fetch(`/api/sessions/${encodeURIComponent(previous)}`, { method: "DELETE" });
  } catch {
    showError("旧会话将在空闲超时后自动清理。");
  }
});
