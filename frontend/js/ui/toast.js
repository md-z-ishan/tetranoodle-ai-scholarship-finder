/**
 * Toast Notification System
 * Renders non-intrusive status alerts with automatic timeouts.
 */

const container = document.getElementById('toast-container');

export function showToast(message, type = 'info', durationMs = 4000) {
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.setAttribute('role', 'alert');

  const icons = {
    success: '✅',
    warn: '⚠️',
    error: '❌',
    info: 'ℹ️',
  };

  const iconSpan = document.createElement('span');
  iconSpan.className = 'toast-icon';
  iconSpan.textContent = icons[type] || 'ℹ️';

  const textSpan = document.createElement('span');
  textSpan.className = 'toast-text';
  textSpan.textContent = message;

  const dismissBtn = document.createElement('button');
  dismissBtn.className = 'btn-icon-xs';
  dismissBtn.setAttribute('aria-label', 'Dismiss notification');
  dismissBtn.textContent = '✕';
  dismissBtn.style.marginLeft = 'auto';
  dismissBtn.style.cursor = 'pointer';

  dismissBtn.onclick = () => {
    removeToast(toast);
  };

  toast.appendChild(iconSpan);
  toast.appendChild(textSpan);
  toast.appendChild(dismissBtn);

  container.appendChild(toast);

  if (durationMs > 0) {
    setTimeout(() => {
      removeToast(toast);
    }, durationMs);
  }
}

function removeToast(toast) {
  if (!toast || !toast.parentNode) return;
  toast.style.opacity = '0';
  toast.style.transform = 'translateY(10px)';
  toast.style.transition = 'all 0.2s ease';
  setTimeout(() => {
    if (toast.parentNode) {
      toast.parentNode.removeChild(toast);
    }
  }, 200);
}
