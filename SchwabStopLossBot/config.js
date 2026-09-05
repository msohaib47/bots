import 'dotenv/config.js';

export const config = {
  // Schwab API (OAuth 2.0)
  schwab: {
    clientId: process.env.SCHWAB_CLIENT_ID || '',
    clientSecret: process.env.SCHWAB_CLIENT_SECRET || '',
    accessToken: process.env.SCHWAB_ACCESS_TOKEN || '',
    refreshToken: process.env.SCHWAB_REFRESH_TOKEN || '',
    baseUrl: process.env.SCHWAB_BASE_URL || 'https://api.schwabapi.com',
    accountNumber: process.env.SCHWAB_ACCOUNT_NUMBER || '',
  },

  // Stop-loss settings
  stopLoss: {
    initialMarginPercent: parseFloat(process.env.STOPLOSS_INITIAL_MARGIN_PCT || '0.25'), // 25%
    initialMarginDollar: parseFloat(process.env.STOPLOSS_INITIAL_MARGIN_DOLLAR || '10'), // $10

    // Tiered margins based on contract price
    tiers: [
      { minPrice: 0, maxPrice: 100, marginDollar: 10, marginPercent: 0.25 },
      { minPrice: 100, maxPrice: 200, marginDollar: 15, marginPercent: 0.25 },
      { minPrice: 200, maxPrice: Infinity, marginDollar: 25, marginPercent: 0.25 },
    ],

    // Market hours for stop-loss updates (ET)
    marketOpen: 9.5, // 9:30 AM ET
    marketClose: 16.0, // 4:00 PM ET
  },

  // Bot behavior
  bot: {
    dryRun: process.env.DRY_RUN === 'true',
    runOnce: process.env.RUN_ONCE === 'true',
    statusOnly: process.env.STATUS_ONLY === 'true',
    logLevel: process.env.LOG_LEVEL || 'info',
  },

  // State persistence
  state: {
    filename: process.env.STATE_FILE || './guard_state.json',
  },

  // Notification
  notify: {
    topic: process.env.NOTIFY_TOPIC || 'sohaib-trading-2026',
  },
};

export function validateConfig() {
  if (!config.schwab.clientId || !config.schwab.clientSecret) {
    throw new Error('Missing SCHWAB_CLIENT_ID or SCHWAB_CLIENT_SECRET in .env. Run: node login.js');
  }
  if (!config.schwab.accessToken) {
    throw new Error('Missing SCHWAB_ACCESS_TOKEN in .env. Run: node login.js');
  }
  if (!config.schwab.accountNumber) {
    throw new Error('Missing SCHWAB_ACCOUNT_NUMBER in .env');
  }
  return true;
}
