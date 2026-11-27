const faces = document.querySelector(".faces");
const front = faces.querySelector(".front");
const back = faces.querySelector(".back");
const stage = document.querySelector(".stage");
const downloadDialog = document.querySelector("#download-dialog");
const downloads = document.querySelector("#downloads");
const downloadLink = document.querySelector(".cta");
const closeDownloads = document.querySelector("[data-close-downloads]");
const dialog = document.querySelector("#qr-dialog");
const shareButton = document.querySelector("[data-share]");
const qrLink = document.querySelector("[data-show-qr]");
const copyButton = document.querySelector("[data-copy]");
const statusLine = dialog.querySelector('[role="status"]');
const canonical = document.querySelector('link[rel="canonical"]').href;
const cardTitle = document.title;
const hasDialogs = typeof dialog.showModal === "function";
const motionDialog = document.querySelector("#motion-dialog");
const settingsButton = document.querySelector("[data-settings]");
const deviceMotion = matchMedia("(prefers-reduced-motion: reduce)");
const motionKey = "evoice-card-motion";
const motionChoices = motionDialog.querySelector("form").elements;
let preference = "auto";
try {
  const saved = localStorage.getItem(motionKey);
  if (saved === "full" || saved === "quiet") preference = saved;
} catch { /* Device preferences still apply without local storage. */ }

function applyMotion() {
  const quiet = preference === "quiet" || (preference === "auto" && deviceMotion.matches);
  document.documentElement.dataset.cardMotion = quiet ? "quiet" : "full";
}
applyMotion();
if (deviceMotion.addEventListener) deviceMotion.addEventListener("change", applyMotion);
else deviceMotion.addListener(applyMotion);

if (hasDialogs) {
  motionChoices.motion.value = preference;
  motionDialog.addEventListener("change", () => {
    preference = motionChoices.motion.value;
    applyMotion();
    try { localStorage.setItem(motionKey, preference); } catch { /* Apply for this visit. */ }
  });
  settingsButton.hidden = false;
  settingsButton.addEventListener("click", () => motionDialog.showModal());
  downloadDialog.append(downloads);
  closeDownloads.hidden = false;
  downloadLink.setAttribute("aria-haspopup", "dialog");
}

function displayFace(moveFocus) {
  const hash = location.hash;
  const showDownloads = hash === "#downloads" && hasDialogs;
  const faceHash = showDownloads ? history.state?.cardOrigin : hash;
  const onBack = faceHash === "#about" || faceHash === "#contacts";

  if (!showDownloads && downloadDialog.open) {
    downloadDialog.close();
    downloadLink.focus({preventScroll: true});
    moveFocus = false;
  }

  // Keep both surfaces mounted so the same transition can reverse mid-turn.
  faces.classList.toggle("turned", onBack);
  front.inert = onBack;
  back.inert = !onBack;

  if (showDownloads) {
    if (!downloadDialog.open) downloadDialog.showModal();
  } else if (moveFocus) {
    const target = hash === "#contacts" ? "contacts" : onBack ? "about" : "name";
    document.getElementById(target).focus({preventScroll: true});
  }
}

function navigateTo(hash) {
  if (location.hash !== hash) {
    const state = hash === "#downloads" ? {cardOrigin: location.hash} : null;
    history.pushState(state, "", hash);
  }
  displayFace(true);
}

function dismissDownloads() {
  if (typeof history.state?.cardOrigin === "string") {
    history.back();
  } else {
    history.replaceState(null, "", location.pathname + location.search);
    displayFace(false);
  }
}

if (hasDialogs) {
  closeDownloads.addEventListener("click", dismissDownloads);
  downloadDialog.addEventListener("click", (event) => {
    if (event.target !== downloadDialog) return;
    const box = downloadDialog.getBoundingClientRect();
    if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) dismissDownloads();
  });
  downloadDialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    dismissDownloads();
  });
}

displayFace(false);
document.documentElement.classList.add("enhanced");
window.addEventListener("hashchange", () => displayFace(true));

stage.addEventListener("click", (event) => {
  if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  const link = event.target.closest('a[href^="#"]');
  if (link && (link.hash !== "#downloads" || hasDialogs)) {
    event.preventDefault();
    navigateTo(link.hash);
    return;
  }
  if (event.target.closest("a, button, summary, #downloads") || window.getSelection()?.type === "Range") return;
  navigateTo(front.inert ? "#front" : "#about");
});

function openShareDialog() {
  statusLine.textContent = "";
  dialog.showModal();
}

if (hasDialogs) {
  qrLink.addEventListener("click", (event) => {
    event.preventDefault();
    openShareDialog();
  });
  shareButton.hidden = false;
  shareButton.addEventListener("click", async () => {
    if (navigator.share) {
      try {
        await navigator.share({title: cardTitle, url: canonical});
        return;
      } catch (error) {
        if (error.name === "AbortError") return;
      }
    }
    openShareDialog();
  });
  copyButton.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(canonical);
      statusLine.textContent = copyButton.dataset.copied;
    } catch {
      statusLine.textContent = copyButton.dataset.manual;
      const range = document.createRange();
      range.selectNodeContents(dialog.querySelector(".card-address"));
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
    }
  });
}
