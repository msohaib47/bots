import axios from 'axios';
import { config } from './config.js';
import logger from './logger.js';

const SCHWAB_TOKEN_ENDPOINT = 'https://api.schwabapi.com/v1/oauth/token';

class SchwabAPI {
  constructor() {
    this.baseURL = config.schwab.baseUrl;
    this.accessToken = config.schwab.accessToken;
    this.refreshToken = config.schwab.refreshToken;
    this.accountNumber = config.schwab.accountNumber; // plain number from .env
    this.accountHash = null;  // encrypted hash required by Schwab API
    this.tokenExpiry = null;
    this.client = axios.create({
      baseURL: this.baseURL,
      timeout: 10000,
    });
  }

  async authenticate() {
    try {
      if (!this.accessToken) {
        logger.error('No Schwab access token found. Run: npm run login');
        return false;
      }

      logger.info('Using Schwab OAuth credentials');

      // Try to refresh token if it might be expired
      if (this.refreshToken && this.isTokenExpiring()) {
        await this.refreshAccessToken();
      }

      // Resolve account hash (Schwab API requires encrypted hash, not raw account number)
      await this.resolveAccountHash();

      return true;
    } catch (err) {
      logger.error('Schwab authentication failed', { error: err.message });
      return false;
    }
  }

  // Schwab API requires an encrypted account hash, not the plain account number.
  // GET /trader/v1/accounts/accountNumbers returns [{accountNumber, hashValue}].
  async resolveAccountHash() {
    try {
      const response = await this.client.get('/trader/v1/accounts/accountNumbers', {
        headers: { Authorization: `Bearer ${this.accessToken}` },
      });

      const accounts = response.data;
      if (!Array.isArray(accounts) || accounts.length === 0) {
        throw new Error('No accounts returned from Schwab API');
      }

      logger.info('Schwab accounts found', { count: accounts.length });

      // Match by account number if configured, otherwise use the first account
      let match = accounts.find(a => a.accountNumber === this.accountNumber);
      if (!match) {
        if (this.accountNumber && this.accountNumber !== 'your_account_number') {
          logger.warn(`Account ${this.accountNumber} not found, using first account`);
        }
        match = accounts[0];
      }

      this.accountHash = match.hashValue;
      logger.info('Account resolved', {
        accountNumber: match.accountNumber,
        hash: match.hashValue.substring(0, 10) + '...',
      });
    } catch (err) {
      logger.error('Failed to resolve account hash', {
        error: err.message,
        status: err.response?.status,
        data: JSON.stringify(err.response?.data),
      });
      throw err;
    }
  }

  isTokenExpiring() {
    if (!this.tokenExpiry) return true; // Assume expired if not set
    return new Date() > new Date(this.tokenExpiry - 5 * 60 * 1000); // 5 min buffer
  }

  async refreshAccessToken() {
    try {
      if (!this.refreshToken) {
        throw new Error('No refresh token available');
      }

      logger.info('Refreshing Schwab access token...');

      const basicAuth = 'Basic ' + Buffer.from(
        `${config.schwab.clientId}:${config.schwab.clientSecret}`
      ).toString('base64');

      const response = await axios.post(
        SCHWAB_TOKEN_ENDPOINT,
        new URLSearchParams({
          grant_type:    'refresh_token',
          refresh_token: this.refreshToken,
        }).toString(),
        {
          headers: {
            'Authorization': basicAuth,
            'Content-Type':  'application/x-www-form-urlencoded',
          },
        }
      );

      this.accessToken = response.data.access_token;
      this.tokenExpiry = new Date(Date.now() + (response.data.expires_in || 1800) * 1000);

      logger.info('Access token refreshed', { expiresAt: this.tokenExpiry });
      return true;
    } catch (err) {
      logger.error('Token refresh failed', { error: err.response?.data || err.message });
      throw err;
    }
  }

  // Get all option positions from account
  async getOptionPositions() {
    try {
      const response = await this.client.get(
        `/trader/v1/accounts/${this.accountHash}`,
        {
          headers: { Authorization: `Bearer ${this.accessToken}` },
          params: { fields: 'positions' },
        }
      );

      const positions = this.parseOptionPositions(response.data);
      return positions;
    } catch (err) {
      logger.error('Failed to fetch option positions', {
        error: err.message,
        status: err.response?.status,
        data: JSON.stringify(err.response?.data),
      });
      throw err;
    }
  }

  // Get current quotes for symbols
  async getQuotes(symbols) {
    try {
      if (!symbols || symbols.length === 0) return {};

      const params = {
        symbols: symbols.map(s => s.trim()).join(','),
        fields: 'quote',
      };

      const response = await this.client.get('/marketdata/v1/quotes', {
        headers: {
          Authorization: `Bearer ${this.accessToken}`,
        },
        params,
      });

      return response.data;
    } catch (err) {
      logger.error('Failed to fetch quotes', {
        error: err.message,
        status: err.response?.status,
        data: JSON.stringify(err.response?.data),
        url: err.config?.url,
        symbols,
      });
      throw err;
    }
  }

  // Place stop-loss order
  async placeStopLossOrder(option, stopPrice, quantity) {
    try {
      const order = {
        orderType: 'STOP',
        session: 'NORMAL',
        duration: 'GOOD_TILL_CANCEL',
        orderStrategyType: 'SINGLE',
        orderLegCollection: [
          {
            instruction: 'SELL_TO_CLOSE',
            quantity: quantity,
            instrument: {
              symbol: option.symbol,
              assetType: 'OPTION',
            },
          },
        ],
        stopPrice: stopPrice,
      };

      if (config.bot.dryRun) {
        logger.info('DRY RUN: Would place stop-loss order', {
          option: option.symbol,
          stopPrice,
          quantity,
        });
        return { orderId: 'DRY_RUN_' + Date.now(), dryRun: true };
      }

      const response = await this.client.post(
        `/trader/v1/accounts/${this.accountNumber}/orders`,
        order,
        {
          headers: {
            Authorization: `Bearer ${this.accessToken}`,
          },
        }
      );

      logger.info('Stop-loss order placed', {
        option: option.symbol,
        orderId: response.data.orderId,
        stopPrice,
      });

      return response.data;
    } catch (err) {
      logger.error('Failed to place stop-loss order', {
        error: err.message,
        option: option.symbol,
      });
      throw err;
    }
  }

  // Cancel existing order
  async cancelOrder(orderId) {
    try {
      if (config.bot.dryRun) {
        logger.info('DRY RUN: Would cancel order', { orderId });
        return { success: true, dryRun: true };
      }

      await this.client.delete(
        `/trader/v1/accounts/${this.accountNumber}/orders/${orderId}`,
        {
          headers: {
            Authorization: `Bearer ${this.accessToken}`,
          },
        }
      );

      logger.info('Order cancelled', { orderId });
      return { success: true };
    } catch (err) {
      logger.error('Failed to cancel order', { error: err.message, orderId });
      throw err;
    }
  }

  // Helper: parse positions from Schwab accounts API response
  parseOptionPositions(rawData) {
    const positions = [];
    const raw = rawData?.securitiesAccount?.positions || [];

    for (const pos of raw) {
      const instrument = pos.instrument || {};
      if (instrument.assetType !== 'OPTION') continue;

      const longQty = pos.longQuantity || 0;
      if (longQty <= 0) continue;  // only long (bought) option positions

      positions.push({
        symbol:          instrument.symbol,
        quantity:        longQty,
        underlyingSymbol: instrument.underlyingSymbol || '',
        putCall:         instrument.putCall || '',
        averagePrice:    pos.averagePrice || 0,
        marketValue:     pos.marketValue || 0,
      });
    }

    logger.info('Parsed option positions', { count: positions.length });
    return positions;
  }
}

export default SchwabAPI;
