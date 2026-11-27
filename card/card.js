const front = document.querySelector(".front");
const back = document.querySelector(".back");
const downloads = document.querySelector("#downloads");
const dialog = document.querySelector("#qr-dialog");
const shareButton = document.querySelector("[data-share]");
const qrLink = document.querySelector("[data-show-qr]");
const copyButton = document.querySelector("[data-copy]");
const statusLine = dialog.querySelector('[role="status"]');
const canonical = document.querySelector('link[rel="canonical"]').href;
const cardTitle = document.title;

function displayFace(moveFocus) {
  const hash = location.hash;
  const onBack = ["#contacts", "#about", "#downloads"].includes(hash);
  front.hidden = onBack;
  back.hidden = !onBack;
  if (hash === "#downloads") downloads.open = true;
  if (moveFocus) {
    const focusTarget = hash === "#downloads" ? downloads.querySelector("summary")
      : hash === "#contacts" ? document.getElementById("contacts")
      : document.getElementById(onBack ? "about" : "name");
    focusTarget.focus({preventScroll: true});
    if (!onBack || hash === "#about") {
      window.scrollTo(0, 0);
    } else {
      focusTarget.scrollIntoView({block: "nearest"});
    }
  }
}

document.documentElement.classList.add("enhanced");
displayFace(false);
window.addEventListener("hashchange", () => displayFace(true));

document.querySelector(".stage").addEventListener("click", (event) => {
  if (event.defaultPrevented || event.target.closest("a, button, summary") || window.getSelection()?.type === "Range") return;
  location.hash = front.hidden ? "#front" : "#about";
});

function openShareDialog() {
  statusLine.textContent = "";
  dialog.showModal();
}

if (typeof dialog.showModal === "function") {
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
