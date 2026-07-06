import 'dotenv/config.js';
import axios from 'axios';
import { config } from './config.js';

async function testConnection() {
  console.log('=== Testing Schwab API Connection ===\n');

  // Validate config
  if (!config.schwab.accessToken) {
    console.error('❌ No access token found');
    console.error('   Run: node login.js\n');
    process.exit(1);
  }

  if (!config.schwab.accountNumber) {
    console.error('❌ Missing SCHWAB_ACCOUNT_NUMBER in .env\n');
    process.exit(1);
  }

  console.log('✅ Configuration loaded');
  console.log(`   Account: ${config.schwab.accountNumber}`);
  console.log(`   Token: ${config.schwab.accessToken.substring(0, 30)}...`);
  console.log('');

  try {
    // Test 1: Get account info
    console.log('🔍 Test 1: Fetching account information...');
    const accountResponse = await axios.get(
      `${config.schwab.baseUrl}/trader/v1/accounts/${config.schwab.accountNumber}`,
      {
        headers: {
          Authorization: `Bearer ${config.schwab.accessToken}`,
        },
        timeout: 10000,
      }
    );

    if (accountResponse.status === 200) {
      console.log('✅ Account access: SUCCESS\n');
    } else {
      console.error('❌ Unexpected response status:', accountResponse.status);
      process.exit(1);
    }

    // Test 2: Get quotes for a test symbol
    console.log('🔍 Test 2: Fetching quotes for SPY...');
    const quoteResponse = await axios.get(`${config.schwab.baseUrl}/marketdata/v1/quotes`, {
      headers: {
        Authorization: `Bearer ${config.schwab.accessToken}`,
      },
      params: {
        symbols: 'SPY',
        fields: ['quote'],
      },
      timeout: 10000,
    });

    if (quoteResponse.status === 200) {
      const quote = quoteResponse.data.SPY;
      if (quote?.quote?.mark) {
        console.log('✅ Market data access: SUCCESS');
        console.log(`   SPY Price: $${quote.quote.mark.toFixed(2)}\n`);
      } else {
        console.warn('⚠️  Quote received but no price data:', quoteResponse.data);
      }
    } else {
      console.error('❌ Unexpected response status:', quoteResponse.status);
      process.exit(1);
    }

    // Test 3: Get positions
    console.log('🔍 Test 3: Fetching open positions...');
    const ordersResponse = await axios.get(
      `${config.schwab.baseUrl}/trader/v1/accounts/${config.schwab.accountNumber}/orders`,
      {
        headers: {
          Authorization: `Bearer ${config.schwab.accessToken}`,
        },
        timeout: 10000,
      }
    );

    if (ordersResponse.status === 200 || ordersResponse.status === 204) {
      const count = Array.isArray(ordersResponse.data) ? ordersResponse.data.length : 0;
      console.log('✅ Position access: SUCCESS');
      console.log(`   Open orders/positions: ${count}\n`);
    } else {
      console.error('❌ Unexpected response status:', ordersResponse.status);
      process.exit(1);
    }

    // Summary
    console.log('=== Connection Test Summary ===\n');
    console.log('✅ All API endpoints accessible');
    console.log('✅ Authentication valid');
    console.log('✅ Ready to run bot\n');
    console.log('Next steps:');
    console.log('  npm run dry-run     # Test with dry-run mode');
    console.log('  npm run once        # Single run with real data');
    console.log('  npm run status      # Check current positions\n');
  } catch (err) {
    console.error('❌ Connection failed:\n');
    if (err.response) {
      console.error(`Status: ${err.response.status}`);
      console.error(`Error: ${err.response.data?.error || err.response.statusText}`);

      if (err.response.status === 401) {
        console.error('\n💡 Tip: Access token may have expired');
        console.error('   Run: node login.js --refresh');
      } else if (err.response.status === 403) {
        console.error('\n💡 Tip: Permissions issue with Schwab app');
        console.error('   - Check app settings in Schwab Developer Portal');
        console.error('   - Verify scopes include: PlaceTrades AccountAccess');
      }
    } else if (err.code === 'ECONNREFUSED') {
      console.error('Connection refused — check internet connection and API URL');
    } else if (err.code === 'ENOTFOUND') {
      console.error('API endpoint not found — check SCHWAB_BASE_URL in .env');
    } else {
      console.error(`Error: ${err.message}`);
    }

    console.log('');
    process.exit(1);
  }
}

testConnection();
