import axios from 'axios';
import { config } from './config.js';
import logger from './logger.js';

export async function notify(title, message, tags = '') {
  const topic = config.notify.topic;
  const ntfyUrl = `https://ntfy.sh/${topic}`;

  try {
    await axios.post(ntfyUrl, message, {
      headers: {
        Title: title,
        ...(tags && { Tags: tags }),
      },
      timeout: 5000,
    });
    logger.debug('Notification sent', { title, message });
  } catch (err) {
    logger.error('Failed to send notification', { error: err.message, title });
  }
}

export async function notifyStopLossUpdate(symbol, oldStop, newStop, currentPrice) {
  const message = `${symbol}: Stop moved ${oldStop} → ${newStop} (price: ${currentPrice})`;
  await notify('Stop-Loss Update', message, 'trading,update');
}

export async function notifyError(title, details) {
  const message = typeof details === 'string' ? details : JSON.stringify(details);
  await notify(`⚠️ ${title}`, message, 'warning,error');
}

export async function notifyStatus(statusText) {
  await notify('Schwab Bot Status', statusText, 'status');
}
