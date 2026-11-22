// Client behaviour. The CSP forbids inline styles and scripts, so dynamic sizes are applied here from data attributes.
document.addEventListener("DOMContentLoaded", () => {
  // Rating rings and score bars: data-rating is 1-5, mapped to 0-100%.
  const pct = (r) => Math.max(0, Math.min(100, ((parseFloat(r) - 1) / 4) * 100));
  document.querySelectorAll("[data-rating]").forEach((el) => {
    const p = pct(el.dataset.rating);
    if (el.classList.contains("ring")) el.style.setProperty("--pct", p.toFixed(0));
    const fill = el.querySelector(":scope > span");
    if (el.classList.contains("bar") && fill) requestAnimationFrame(() => { fill.style.width = p.toFixed(0) + "%"; });
  });

  // Show / hide password.
  document.querySelectorAll("[data-toggle-password]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const input = document.getElementById(btn.dataset.togglePassword);
      if (!input) return;
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      btn.setAttribute("aria-label", show ? "Hide password" : "Show password");
      btn.setAttribute("aria-pressed", String(show));
    });
  });

  // Risk calculator (plan E7): shows the loss in rupees if the stop level is hit. Informs, does not decide.
  const box = document.getElementById("risk-calc");
  if (box) {
    const amount = box.querySelector("input[name=amount]");
    const out = box.querySelector("[data-out]");
    const close = parseFloat(box.dataset.close);
    const stop = parseFloat(box.dataset.stop);
    const fmt = (n) => "Rs " + Math.round(n).toLocaleString("en-IN");
    const update = () => {
      const a = parseFloat(amount.value);
      if (!(a > 0) || !(close > 0)) { out.textContent = "Enter an amount to see the risk."; return; }
      const shares = Math.floor(a / close);
      if (shares < 1) { out.textContent = "That amount buys less than one share."; return; }
      out.textContent = `${shares} shares at ${fmt(close)}. If the stop level is hit, about ${fmt(shares * (close - stop))} ` +
        `is lost before charges. At +10%, about ${fmt(shares * close * 0.1)} is gained before charges and tax.`;
    };
    amount.addEventListener("input", update);
    update();
  }

  // Confirm before marking a holding as sold.
  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (e) => { if (!window.confirm(form.dataset.confirm)) e.preventDefault(); });
  });
});
