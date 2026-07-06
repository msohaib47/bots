import { config, validateConfig } from './config.js';
import logger from './logger.js';
import SchwabAPI from './schwab.js';
import PositionManager from './position-manager.js';
import { notifyStopLossUpdate, notifyError, notifyStatus } from './notifier.js';
import { isMarketHours } from './utils.js';

class SchwabStopLossBot {
  constructor() {
    this.schwab = new SchwabAPI();
    this.positionManager = new PositionManager();
    this.isRunning = false;
  }

  async initialize() {
    try {
      validateConfig();
      logger.info('Schwab Stop-Loss Bot initialized', {
        dryRun: config.bot.dryRun,
        accountNumber: config.schwab.accountNumber,
      });

      const authenticated = await this.schwab.authenticate();
      if (!authenticated) {
        throw new Error('Failed to authenticate with Schwab API');
      }

      return true;
    } catch (err) {
      logger.error('Initialization failed', { error: err.message });
      await notifyError('Bot Initialization Failed', err.message);
      return false;
    }
  }

  // Main run logic
  async run() {
    if (this.isRunning) {
      logger.warn('Bot is already running, skipping');
      return;
    }

    this.isRunning = true;
    logger.info('Starting Schwab stop-loss check...');

    try {
      // Check if market is open (bypass for dry-run so you can test outside hours)
      if (!isMarketHours() && !config.bot.dryRun) {
        logger.debug('Market is closed, skipping run');
        this.isRunning = false;
        return;
      }
      if (!isMarketHours() && config.bot.dryRun) {
        logger.info('Market is closed, but running in DRY RUN mode');
      }

      // Fetch all open option positions
      const positions = await this.schwab.getOptionPositions();
      if (!positions || positions.length === 0) {
        logger.info('No open option positions found');
        this.isRunning = false;
        return;
      }

      logger.info('Found option positions', { count: positions.length });

      // Get current prices for all positions
      const symbols = positions.map((p) => p.symbol);
      const quotes = await this.schwab.getQuotes(symbols);

      // Process each position
      for (const position of positions) {
        await this.processPosition(position, quotes);
      }

      logger.info('Stop-loss check completed');
    } catch (err) {
      logger.error('Error during run', { error: err.message });
      await notifyError('Bot Runtime Error', err.message);
    } finally {
      this.isRunning = false;
    }
  }

  // Process a single position
  async processPosition(position, quotes) {
    try {
      const q = quotes[position.symbol]?.quote || quotes[position.symbol] || {};
      const currentPrice = q.mark || q.lastPrice || q.last || position.averagePrice;
      if (!currentPrice) {
        logger.warn('No price data for symbol', { symbol: position.symbol });
        return;
      }

      const positionId = `${position.symbol}_${position.quantity}`;
      const existingPos = this.positionManager.getPosition(positionId);
      const existingStop = existingPos?.stopPrice;

      // Calculate required stop-loss
      const { stopPrice, margin, highestPrice } = this.positionManager.calculateStopPrice(
        currentPrice,
        positionId
      );

      logger.debug('Processing position', {
        symbol: position.symbol,
        currentPrice,
        calculatedStop: stopPrice,
        existingStop,
      });

      // Check if adjustment needed
      const needsAdjustment = this.positionManager.needsAdjustment(positionId, currentPrice, existingStop);

      if (needsAdjustment) {
        // Cancel existing stop-loss if any
        if (existingPos?.stopOrderId && !config.bot.dryRun) {
          try {
            await this.schwab.cancelOrder(existingPos.stopOrderId);
          } catch (err) {
            logger.warn('Failed to cancel existing stop order', {
              error: err.message,
              orderId: existingPos.stopOrderId,
            });
          }
        }

        // Place new stop-loss order
        const orderResult = await this.schwab.placeStopLossOrder(position, stopPrice, position.quantity);

        // Update state
        this.positionManager.updatePosition(positionId, {
          symbol: position.symbol,
          quantity: position.quantity,
          lastPrice: currentPrice,
          stopPrice,
          stopOrderId: orderResult.orderId,
          highestPrice,
          margin: margin.marginDollar,
        });

        logger.info('Stop-loss updated', {
          symbol: position.symbol,
          oldStop: existingStop,
          newStop: stopPrice,
          currentPrice,
        });

        // Send notification
        if (!config.bot.dryRun || config.bot.logLevel === 'debug') {
          await notifyStopLossUpdate(position.symbol, existingStop || 'NONE', stopPrice, currentPrice);
        }
      } else {
        // Just update last price
        this.positionManager.updatePosition(positionId, {
          symbol: position.symbol,
          quantity: position.quantity,
          lastPrice: currentPrice,
          highestPrice,
        });
      }
    } catch (err) {
      logger.error('Error processing position', {
        error: err.message,
        symbol: position.symbol,
      });
    }
  }

  // Print status
  printStatus() {
    logger.info('=== Schwab Stop-Loss Bot Status ===');
    logger.info('DRY RUN: ' + (config.bot.dryRun ? 'YES' : 'NO'));
    logger.info('Market Hours: ' + (isMarketHours() ? 'OPEN' : 'CLOSED'));
    this.positionManager.logPositionStatus();
  }

  // Reset all state
  resetState() {
    logger.warn('Resetting all position state');
    this.positionManager.state = { positions: {} };
    this.positionManager.saveState();
  }
}

// Main entry point
async function main() {
  const args = process.argv.slice(2);
  const bot = new SchwabStopLossBot();

  try {
    // Check for command line flags
    if (args.includes('--status')) {
      config.bot.statusOnly = true;
    }
    if (args.includes('--once')) {
      config.bot.runOnce = true;
    }
    if (args.includes('--reset')) {
      bot.resetState();
      return;
    }

    // Initialize
    const ready = await bot.initialize();
    if (!ready) {
      process.exit(1);
    }

    // Status-only mode
    if (config.bot.statusOnly) {
      bot.printStatus();
      process.exit(0);
    }

    // Run once
    if (config.bot.runOnce) {
      await bot.run();
      bot.printStatus();
      process.exit(0);
    }

    // Continuous mode (via scheduler)
    logger.info('Running in continuous mode (use scheduler for recurring execution)');
    await bot.run();
  } catch (err) {
    logger.error('Fatal error', { error: err.message, stack: err.stack });
    process.exit(1);
  }
}

main().catch(console.error);

export default SchwabStopLossBot;
