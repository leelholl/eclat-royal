// Menu mobile, galerie zoomable et animations discrètes au défilement.
const menuButton = document.querySelector('.menu-toggle');
const mainNav = document.querySelector('#main-nav');

menuButton?.addEventListener('click', () => {
  const isOpen = menuButton.getAttribute('aria-expanded') === 'true';
  menuButton.setAttribute('aria-expanded', String(!isOpen));
  mainNav?.classList.toggle('is-open', !isOpen);
});

mainNav?.querySelectorAll('a').forEach((link) => {
  link.addEventListener('click', () => {
    menuButton?.setAttribute('aria-expanded', 'false');
    mainNav.classList.remove('is-open');
  });
});

const lightbox = document.querySelector('.lightbox');
const lightboxImage = lightbox?.querySelector('img');

document.querySelectorAll('[data-lightbox]').forEach((button) => {
  button.addEventListener('click', () => {
    if (!lightbox || !lightboxImage) return;
    lightboxImage.src = button.dataset.lightbox;
    lightboxImage.alt = button.dataset.alt || '';
    lightbox.showModal();
  });
});

lightbox?.querySelector('.lightbox-close')?.addEventListener('click', () => lightbox.close());
lightbox?.addEventListener('click', (event) => {
  if (event.target === lightbox) lightbox.close();
});

// Apparition au scroll, avec affichage immédiat si l’API n’est pas disponible.
const revealTargets = document.querySelectorAll('.section-heading, .service-row, .about-image, .about-copy, .gallery-item, .review-card, .contact-block');
if ('IntersectionObserver' in window && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
  revealTargets.forEach((item) => item.classList.add('reveal'));
  const observer = new IntersectionObserver((entries, currentObserver) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add('is-visible');
        currentObserver.unobserve(entry.target);
      }
    });
  }, { threshold: 0.12 });
  revealTargets.forEach((item) => observer.observe(item));
}

document.querySelector('#year').textContent = new Date().getFullYear();
