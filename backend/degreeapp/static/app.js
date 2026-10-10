"use strict";

// File verification uses the uploaded document; manual fields stay editable and visible.
document.querySelectorAll("[data-verify-form]").forEach((form) => {
  const file = form.querySelector("input[type=file]");
  const requiredFields = Array.from(form.querySelectorAll("[required]"));
  const updateRequired = () => {
    const hasFile = Boolean(file && file.files && file.files.length);
    requiredFields.forEach((field) => { field.required = !hasFile; });
  };
  if (file) file.addEventListener("change", updateRequired);
  updateRequired();
});

document.querySelectorAll("form[data-submit-label]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (form.dataset.submitting === "true") {
      event.preventDefault();
      return;
    }
    form.dataset.submitting = "true";
    form.setAttribute("aria-busy", "true");
    form.querySelectorAll("button[type=submit]").forEach((button) => {
      button.dataset.originalHtml = button.innerHTML;
      button.textContent = form.dataset.submitLabel;
      button.disabled = true;
    });
  });
});

// Restore controls if the browser returns to a page from its back/forward cache.
window.addEventListener("pageshow", () => {
  document.querySelectorAll("form[data-submitting]").forEach((form) => {
    delete form.dataset.submitting;
    form.removeAttribute("aria-busy");
    form.querySelectorAll("button[data-original-html]").forEach((button) => {
      button.innerHTML = button.dataset.originalHtml;
      delete button.dataset.originalHtml;
      button.disabled = false;
    });
  });
});

document.querySelectorAll("[data-copy-target]").forEach((button) => {
  button.addEventListener("click", async () => {
    const target = document.getElementById(button.dataset.copyTarget);
    const live = document.getElementById("copy-announcement");
    if (!target) return;
    try {
      if (!navigator.clipboard) throw new Error("Clipboard API unavailable");
      await navigator.clipboard.writeText(target.textContent.trim());
      if (live) live.textContent = "已复制";
      const oldTitle = button.title;
      button.title = "已复制";
      button.classList.add("copied");
      setTimeout(() => { button.title = oldTitle; button.classList.remove("copied"); }, 1800);
    } catch (_) {
      const selection = window.getSelection();
      if (selection) {
        const range = document.createRange();
        range.selectNodeContents(target);
        selection.removeAllRanges();
        selection.addRange(range);
      }
      if (live) live.textContent = "内容已选中，请使用 Ctrl+C 复制";
      button.title = "内容已选中，请使用 Ctrl+C 复制";
    }
  });
});
