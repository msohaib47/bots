/**
 * Schwab OAuth Login
 *
 * Usage:
 *   npm run login          — interactive login (opens browser URL, paste redirect)
 *   node login.js --status — show current token status
 *   node login.js --refresh — refresh access token using saved refresh token
 *
 * How it works:
 *   1. Prints the Schwab authorization URL
 *   2. You open it in a browser, sign in, and grant access
 *   3. Schwab redirects to https://127.0.0.1?code=...&session=...
 *      (the browser will show an error — that's fine, just copy the full URL)
 *   4. Paste the full redirect URL into this prompt
 *   5. Script extracts the code and exchanges it for tokens
 *   6. Tokens are saved to .schwab_tokens.json and .env
 */

import 'dotenv/config';
import axios from 'axios';
import fs from 'fs';
import path from 'path';
import readline from 'readline';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const TOKEN_FILE = path.join(__dirname, '.schwab_tokens.json');

const SCHWAB_AUTH_URL   = 'https://api.schwabapi.com/v1/oauth/authorize';
const SCHWAB_TOKEN_URL  = 'https://api.schwabapi.com/v1/oauth/token';
const REDIRECT_URI      = 'https://127.0.0.1';

const CLIENT_ID     = process.env.SCHWAB_CLIENT_ID;
const CLIENT_SECRET = process.env.SCHWAB_CLIENT_SECRET;

// Schwab requires credentials as Basic auth header, NOT in the request body
function basicAuthHeader() {
  return 'Basic ' + Buffer.from(`${CLIENT_ID}:${CLIENT_SECRET}`).toString('base64');
}

function loadTokens() {
  try {
    if (fs.existsSync(TOKEN_FILE)) {
      return JSON.parse(fs.readFileSync(TOKEN_FILE, 'utf-8'));
    }
  } catch (err) {
    console.error('Could not load token file:', err.message);
  }
  return {};
}

function saveTokens(tokens) {
  fs.writeFileSync(TOKEN_FILE, JSON.stringify(tokens, null, 2));
  console.log(`✅ Tokens saved to ${TOKEN_FILE}`);
  updateEnv(tokens);
}

function updateEnv(tokens) {
  try {
    let env = fs.readFileSync('.env', 'utf-8');

    const set = (key, value) => {
      if (env.includes(`${key}=`)) {
        env = env.replace(new RegExp(`${key}=.*`), `${key}=${value}`);
      } else {
        env += `\n${key}=${value}`;
      }
    };

    set('SCHWAB_ACCESS_TOKEN',  tokens.access_token);
    if (tokens.refresh_token) set('SCHWAB_REFRESH_TOKEN', tokens.refresh_token);

    fs.writeFileSync('.env', env);
    console.log('✅ .env updated with new tokens');
  } catch (err) {
    console.warn('Could not update .env:', err.message);
  }
}

function extractCode(returnedUrl) {
  try {
    // Handle both full URLs and bare query strings
    const urlStr = returnedUrl.includes('://') ? returnedUrl : 'https://x?' + returnedUrl;
    const url = new URL(urlStr);
    const code = url.searchParams.get('code');
    if (code) return code;
  } catch (_) { /* fall through */ }

  // Manual extraction: Schwab codes end with @ (encoded as %40)
  const match = returnedUrl.match(/[?&]code=([^&]+)/);
  if (match) {
    return decodeURIComponent(match[1]);
  }
  return null;
}

function prompt(question) {
  return new Promise(resolve => {
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
    rl.question(question, answer => { rl.close(); resolve(answer.trim()); });
  });
}

async function exchangeCode(code) {
  console.log('\n🔄 Exchanging code for tokens...');
  const response = await axios.post(
    SCHWAB_TOKEN_URL,
    new URLSearchParams({
      grant_type:   'authorization_code',
      code:         code,
      redirect_uri: REDIRECT_URI,
    }).toString(),
    {
      headers: {
        'Authorization': basicAuthHeader(),
        'Content-Type':  'application/x-www-form-urlencoded',
      },
    }
  );
  return response.data;
}

async function refreshTokens(refreshToken) {
  console.log('🔄 Refreshing access token...');
  const response = await axios.post(
    SCHWAB_TOKEN_URL,
    new URLSearchParams({
      grant_type:    'refresh_token',
      refresh_token: refreshToken,
    }).toString(),
    {
      headers: {
        'Authorization': basicAuthHeader(),
        'Content-Type':  'application/x-www-form-urlencoded',
      },
    }
  );
  return response.data;
}

function expiresAt(tokens) {
  return new Date(Date.now() + (tokens.expires_in || 1800) * 1000).toISOString();
}

function printStatus(tokens) {
  console.log('\n=== Schwab Token Status ===\n');
  if (!tokens.access_token) {
    console.log('❌ No access token — run: npm run login');
    return;
  }
  console.log('Access Token:  ' + tokens.access_token.substring(0, 40) + '...');
  if (tokens.expires_at) {
    const exp  = new Date(tokens.expires_at);
    const left = Math.round((exp - Date.now()) / 1000);
    console.log(`Expires:       ${exp.toLocaleString()} (${left > 0 ? left + 's remaining' : 'EXPIRED'})`);
  }
  if (tokens.refresh_token) {
    console.log('Refresh Token: ' + tokens.refresh_token.substring(0, 40) + '...');
  }
  console.log(`\nStored in: ${TOKEN_FILE}`);
}

async function main() {
  const args = process.argv.slice(2);

  if (!CLIENT_ID || !CLIENT_SECRET) {
    console.error('❌ SCHWAB_CLIENT_ID and SCHWAB_CLIENT_SECRET must be set in .env');
    process.exit(1);
  }

  const tokens = loadTokens();

  // -- status ----------------------------------------------------------------
  if (args.includes('--status')) {
    printStatus(tokens);
    return;
  }

  // -- refresh ---------------------------------------------------------------
  if (args.includes('--refresh')) {
    if (!tokens.refresh_token) {
      console.error('❌ No refresh token found. Run: npm run login');
      process.exit(1);
    }
    try {
      const fresh = await refreshTokens(tokens.refresh_token);
      fresh.expires_at     = expiresAt(fresh);
      fresh.refresh_token  = fresh.refresh_token || tokens.refresh_token; // keep old if not rotated
      saveTokens(fresh);
      console.log('✅ Token refreshed. Expires:', fresh.expires_at);
    } catch (err) {
      console.error('❌ Refresh failed:', err.response?.data || err.message);
      console.error('   Refresh tokens expire after 7 days. Run: npm run login');
      process.exit(1);
    }
    return;
  }

  // -- full login flow -------------------------------------------------------

  // If valid token exists, skip
  if (tokens.access_token && tokens.expires_at && new Date(tokens.expires_at) > new Date()) {
    console.log('✅ Valid access token already saved (expires ' + new Date(tokens.expires_at).toLocaleString() + ')');
    console.log('   To force re-login: delete .schwab_tokens.json and run again.');
    return;
  }

  // Try silent refresh first
  if (tokens.refresh_token) {
    try {
      const fresh = await refreshTokens(tokens.refresh_token);
      fresh.expires_at    = expiresAt(fresh);
      fresh.refresh_token = fresh.refresh_token || tokens.refresh_token;
      saveTokens(fresh);
      console.log('✅ Token refreshed silently. Run: npm run dry-run');
      return;
    } catch (_) {
      console.log('⚠️  Refresh token expired. Starting full OAuth flow...\n');
    }
  }

  // Build authorization URL
  const authUrl = `${SCHWAB_AUTH_URL}?` + new URLSearchParams({
    response_type: 'code',
    client_id:     CLIENT_ID,
    redirect_uri:  REDIRECT_URI,
  }).toString();

  console.log('=== Schwab OAuth Login ===\n');
  console.log('1. Open this URL in your browser:\n');
  console.log('   ' + authUrl);
  console.log('\n2. Sign in with your Schwab account and grant access.');
  console.log('3. Your browser will show an error page at https://127.0.0.1 — that\'s expected.');
  console.log('4. Copy the FULL URL from the browser address bar and paste it below.\n');

  const returnedUrl = await prompt('Paste the full redirect URL: ');

  const code = extractCode(returnedUrl);
  if (!code) {
    console.error('❌ Could not extract authorization code from URL.');
    console.error('   Expected format: https://127.0.0.1?code=XXXX&session=XXXX');
    process.exit(1);
  }

  console.log('   Code extracted ✓');

  try {
    const newTokens = await exchangeCode(code);
    newTokens.expires_at = expiresAt(newTokens);
    saveTokens(newTokens);

    console.log('\n✅ Authentication successful!');
    console.log('   Access token expires:', newTokens.expires_at);
    if (newTokens.refresh_token) console.log('   Refresh token saved (valid ~7 days)');
    console.log('\nNext steps:');
    console.log('   npm run dry-run   — test the bot without placing orders');
    console.log('   npm run once      — single live pass');
  } catch (err) {
    console.error('\n❌ Token exchange failed:');
    if (err.response?.data) {
      console.error(JSON.stringify(err.response.data, null, 2));
    } else {
      console.error(err.message);
    }
    console.error('\nCommon causes:');
    console.error('  • Authorization code already used (codes are single-use)');
    console.error('  • Redirect URI mismatch (app must have https://127.0.0.1 registered)');
    console.error('  • Wrong CLIENT_ID or CLIENT_SECRET in .env');
    process.exit(1);
  }
}

main();
