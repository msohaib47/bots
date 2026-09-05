import winston from 'winston';
import fs from 'fs';
import path from 'path';
import { config } from './config.js';

const logsDir = './logs';
if (!fs.existsSync(logsDir)) {
  fs.mkdirSync(logsDir, { recursive: true });
}

const logger = winston.createLogger({
  level: config.bot.logLevel,
  format: winston.format.combine(
    winston.format.timestamp({ format: 'YYYY-MM-DD HH:mm:ss' }),
    winston.format.errors({ stack: true }),
    winston.format.printf(({ timestamp, level, message, ...meta }) => {
      let msg = `${timestamp} [${level.toUpperCase()}] ${message}`;
      if (Object.keys(meta).length > 0) {
        msg += ' ' + JSON.stringify(meta);
      }
      return msg;
    })
  ),
  transports: [
    new winston.transports.Console({
      format: winston.format.combine(
        winston.format.colorize(),
        winston.format.printf(({ level, message }) => `${level}: ${message}`)
      ),
    }),
    new winston.transports.File({
      filename: path.join(logsDir, 'schwab-bot.log'),
      maxsize: 10485760, // 10MB
      maxFiles: 10,
    }),
  ],
});

export default logger;
