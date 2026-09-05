import fs from 'fs';
import { config } from './config.js';
import logger from './logger.js';

class PositionManager {
  constructor() {
    this.stateFile = config.state.filename;
    this.state = this.loadState();
  }

  loadState() {
    try {
      if (fs.existsSync(this.stateFile)) {
        const data = fs.readFileSync(this.stateFile, 'utf-8');
        return JSON.parse(data);
      }
    } catch (err) {
      logger.warn('Failed to load state file, starting fresh', { error: err.message });
    }
    return { positions: {} };
  }

  saveState() {
    try {
      fs.writeFileSync(this.stateFile, JSON.stringify(this.state, null, 2));
    } catch (err) {
      logger.error('Failed to save state file', { error: err.message });
    }
  }

  // Calculate margin for a given contract price
  calculateMargin(contractPrice) {
    const tier = config.stopLoss.tiers.find(
      (t) => contractPrice >= t.minPrice && contractPrice < t.maxPrice
    );

    if (!tier) {
      return {
        marginDollar: config.stopLoss.initialMarginDollar,
        marginPercent: config.stopLoss.initialMarginPercent,
      };
    }

    return {
      marginDollar: tier.marginDollar,
      marginPercent: tier.marginPercent,
    };
  }

  // Calculate stop-loss price for a position
  calculateStopPrice(currentPrice, positionId) {
    const margin = this.calculateMargin(currentPrice);
    const dollarStop = currentPrice - margin.marginDollar;
    const percentStop = currentPrice * (1 - margin.marginPercent);
    const stopPrice = Math.max(dollarStop, percentStop);

    // Track highest price for trailing stop
    const position = this.state.positions[positionId] || {};
    const highestPrice = Math.max(position.highestPrice || 0, currentPrice);

    return {
      stopPrice: Math.round(stopPrice * 100) / 100,
      margin,
      highestPrice,
    };
  }

  // Update or create position in state
  updatePosition(positionId, data) {
    if (!this.state.positions[positionId]) {
      this.state.positions[positionId] = {
        createdAt: new Date().toISOString(),
      };
    }

    this.state.positions[positionId] = {
      ...this.state.positions[positionId],
      ...data,
      lastUpdated: new Date().toISOString(),
    };

    this.saveState();
    return this.state.positions[positionId];
  }

  // Get position state
  getPosition(positionId) {
    return this.state.positions[positionId] || null;
  }

  // Get all positions
  getAllPositions() {
    return this.state.positions;
  }

  // Determine if stop-loss needs adjustment
  needsAdjustment(positionId, currentPrice, existingStopPrice) {
    const position = this.getPosition(positionId);
    if (!position) return true; // New position

    const calc = this.calculateStopPrice(currentPrice, positionId);

    // Adjust if:
    // 1. No existing stop price
    // 2. Current price moved up and new stop is higher
    // 3. Margin tier changed (price crossed a threshold)
    if (!existingStopPrice) return true;
    if (calc.stopPrice > existingStopPrice + 0.01) return true;

    const oldMargin = this.calculateMargin(position.lastPrice || currentPrice);
    if (calc.margin.marginDollar !== oldMargin.marginDollar) return true;

    return false;
  }

  // Log position status
  logPositionStatus() {
    const positions = this.getAllPositions();
    if (Object.keys(positions).length === 0) {
      logger.info('No positions in state');
      return;
    }

    logger.info('Position Status:');
    Object.entries(positions).forEach(([id, pos]) => {
      logger.info(`  ${id}:`, {
        symbol: pos.symbol,
        quantity: pos.quantity,
        lastPrice: pos.lastPrice,
        stopPrice: pos.stopPrice,
        highestPrice: pos.highestPrice,
        lastUpdated: pos.lastUpdated,
      });
    });
  }
}

export default PositionManager;
