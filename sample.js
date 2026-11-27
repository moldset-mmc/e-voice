const audio = document.querySelector('#sample-audio');
const button = document.querySelector('#sample-play');
const label = button.querySelector('.play-label');
const bars = [...document.querySelectorAll('.signal i')];
const timer = document.querySelector('.clock');
const format = n => '0:' + Math.floor(n).toString().padStart(2, '0');

function refresh() {
  const playing = !audio.paused && !audio.ended;
  button.setAttribute('aria-pressed', String(playing));
  label.textContent = playing ? button.dataset.pause : button.dataset.play;
  if (Number.isFinite(audio.duration)) {
    timer.textContent = format(audio.currentTime) + ' / ' + format(audio.duration);
    const count = Math.floor(bars.length * audio.currentTime / audio.duration);
    bars.forEach((bar, i) => bar.classList.toggle('lit', i < count));
  }
}

function nativeControls() {
  audio.hidden = false;
  button.hidden = true;
  timer.hidden = true;
}
button.addEventListener('click', () => {
  if (audio.paused) audio.play().catch(nativeControls);
  else audio.pause();
});
for (const event of ['play', 'pause', 'ended', 'timeupdate', 'loadedmetadata']) {
  audio.addEventListener(event, refresh);
}
audio.addEventListener('error', nativeControls);
audio.hidden = true;
button.hidden = false;
refresh();
