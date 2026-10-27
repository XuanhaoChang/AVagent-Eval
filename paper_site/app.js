const video = document.querySelector('#demo-player');
for (const button of document.querySelectorAll('[data-seek]')) {
  button.addEventListener('click', () => {
    const seek = () => {
      video.currentTime = Number(button.dataset.seek);
      video.play().catch(() => {});
    };
    if (video.readyState >= 1) seek();
    else { video.addEventListener('loadedmetadata', seek, {once: true}); video.load(); }
    video.scrollIntoView({behavior: 'smooth', block: 'center'});
  });
}
