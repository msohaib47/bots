import { config } from './config.js';

// Check if current time is within market hours (ET)
export function isMarketHours() {
  const now = new Date();
  const etTime = new Date(now.toLocaleString('en-US', { timeZone: 'America/New_York' }));

  const hours = etTime.getHours() + etTime.getMinutes() / 60;
  const dayOfWeek = etTime.getDay();

  // Market is open Monday-Friday, 9:30 AM - 4:00 PM ET
  const isWeekday = dayOfWeek >= 1 && dayOfWeek <= 5;
  const isOpenHours = hours >= config.stopLoss.marketOpen && hours < config.stopLoss.marketClose;

  return isWeekday && isOpenHours;
}

// Check if time is after market close
export function isAfterMarketClose() {
  const now = new Date();
  const etTime = new Date(now.toLocaleString('en-US', { timeZone: 'America/New_York' }));
  const hours = etTime.getHours() + etTime.getMinutes() / 60;
  return hours >= config.stopLoss.marketClose;
}

// Get next market open time
export function getNextMarketOpen() {
  const now = new Date();
  const etTime = new Date(now.toLocaleString('en-US', { timeZone: 'America/New_York' }));
  const dayOfWeek = etTime.getDay();
  const hours = etTime.getHours() + etTime.getMinutes() / 60;

  let nextOpen = new Date(etTime);

  // If after market close or weekend, move to next day
  if (hours >= config.stopLoss.marketClose || dayOfWeek === 0 || dayOfWeek === 6) {
    nextOpen.setDate(nextOpen.getDate() + 1);
  }

  // If Saturday, move to Monday
  if (nextOpen.getDay() === 6) {
    nextOpen.setDate(nextOpen.getDate() + 2);
  }
  // If Sunday, move to Monday
  if (nextOpen.getDay() === 0) {
    nextOpen.setDate(nextOpen.getDate() + 1);
  }

  // Set time to market open
  nextOpen.setHours(9, 30, 0, 0);
  return nextOpen;
}

// Format number as USD currency
export function formatUSD(value) {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

// Format timestamp
export function formatTimestamp(date = new Date()) {
  return date.toISOString().replace('T', ' ').substring(0, 19);
}
