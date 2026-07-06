import cron from 'node-cron';
import { config } from './config.js';
import logger from './logger.js';
import SchwabStopLossBot from './bot.js';
import { getNextMarketOpen } from './utils.js';

class BotScheduler {
  constructor() {
    this.bot = new SchwabStopLossBot();
    this.cronJob = null;
  }

  async start() {
    try {
      const ready = await this.bot.initialize();
      if (!ready) {
        throw new Error('Failed to initialize bot');
      }

      // Schedule every minute during market hours: Mon-Fri, 9:30 AM - 4:00 PM ET
      // Cron format (UTC): minute hour day month day-of-week
      // Convert ET market hours to UTC:
      // 9:30 AM ET = 1:30 PM UTC (standard time) or 2:30 PM UTC (daylight time)
      // 4:00 PM ET = 8:00 PM UTC (standard time) or 9:00 PM UTC (daylight time)
      // For simplicity, run every minute 13-21 UTC on weekdays to cover both
      const cronExpression = '* 13-21 * * 1-5';

      logger.info('Starting bot scheduler', {
        schedule: cronExpression,
        description: 'Every minute Mon-Fri during market hours (13-21 UTC)',
      });

      this.cronJob = cron.schedule(cronExpression, async () => {
        try {
          await this.bot.run();
        } catch (err) {
          logger.error('Scheduled run failed', { error: err.message });
        }
      });

      logger.info('Bot scheduler started successfully');
    } catch (err) {
      logger.error('Failed to start scheduler', { error: err.message });
      throw err;
    }
  }

  stop() {
    if (this.cronJob) {
      this.cronJob.stop();
      logger.info('Bot scheduler stopped');
    }
  }

  getStatus() {
    return {
      running: this.cronJob ? true : false,
      nextRun: getNextMarketOpen(),
      dryRun: config.bot.dryRun,
    };
  }
}

// If run directly
if (import.meta.url === `file://${process.argv[1]}`) {
  const scheduler = new BotScheduler();

  scheduler.start().catch((err) => {
    logger.error('Fatal error starting scheduler', { error: err.message });
    process.exit(1);
  });

  // Handle graceful shutdown
  process.on('SIGINT', () => {
    logger.info('Received SIGINT, shutting down gracefully');
    scheduler.stop();
    process.exit(0);
  });

  process.on('SIGTERM', () => {
    logger.info('Received SIGTERM, shutting down gracefully');
    scheduler.stop();
    process.exit(0);
  });
}

export default BotScheduler;
