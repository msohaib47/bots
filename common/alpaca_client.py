"""
Shared Alpaca REST client base class.
Contains common methods used by DayTradingBot, CryptoBot, and WheelBot.
Each bot extends this class with strategy-specific methods.

Usage:
    from ..common.alpaca_client import AlpacaClient
    from ..common.alpaca_config import API_KEY, API_SECRET, BASE_URL, DATA_URL
    
    client = AlpacaClient(API_KEY, API_SECRET, BASE_URL, DATA_URL)
    cash = client.get_cash()
    positions = client.list_positions()
"""
import requests
import logging
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


class AlpacaClient:
    """Base Alpaca REST API client with common account and position methods."""
    
    def __init__(self, api_key: str, api_secret: str, base_url: str, data_url: str):
        """
        Initialize Alpaca client with credentials.
        
        Args:
            api_key: Alpaca API key
            api_secret: Alpaca API secret
            base_url: Alpaca API base URL (trading)
            data_url: Alpaca data URL (market data)
        """
        self.base_url = base_url
        self.data_url = data_url
        self.headers = {
            'APCA-API-KEY-ID': api_key,
            'APCA-API-SECRET-KEY': api_secret,
            'Content-Type': 'application/json',
        }
    
    def _get(self, url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Make a GET request to Alpaca API.
        
        Args:
            url: Full API endpoint URL
            params: Query parameters
            
        Returns:
            JSON response as dict
            
        Raises:
            requests.HTTPError: If request fails
        """
        r = requests.get(url, headers=self.headers, params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    
    def _post(self, url: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """
        Make a POST request to Alpaca API.
        
        Args:
            url: Full API endpoint URL
            body: Request body as dict
            
        Returns:
            JSON response as dict
            
        Raises:
            requests.HTTPError: If request fails
        """
        r = requests.post(url, headers=self.headers, json=body, timeout=15)
        if not r.ok:
            logger.error(f'POST {url} -> {r.status_code}: {r.text}')
            r.raise_for_status()
        return r.json()
    
    # ── Account ────────────────────────────────────────────────────────────────────
    
    def get_account(self) -> Dict[str, Any]:
        """Get account details (cash, portfolio value, buying power, etc.)."""
        return self._get(f'{self.base_url}/v2/account')
    
    def get_cash(self) -> float:
        """Get available cash balance."""
        return float(self.get_account().get('cash', 0))
    
    def get_buying_power(self) -> float:
        """Get available buying power."""
        return float(self.get_account().get('buying_power', 0))
    
    def get_portfolio_value(self) -> float:
        """Get total portfolio value."""
        return float(self.get_account().get('portfolio_value', 0))
    
    def get_options_buying_power(self) -> float:
        """Get available options buying power."""
        return float(self.get_account().get('options_buying_power', 0))
    
    # ── Positions ─────────────────────────────────────────────────────────────────
    
    def get_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get position for a specific symbol.
        
        Args:
            symbol: Stock or crypto symbol (e.g. 'SPY', 'BTCUSD')
            
        Returns:
            Position dict if found, None if no position
        """
        try:
            return self._get(f'{self.base_url}/v2/positions/{symbol}')
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                return None
            raise
    
    def list_positions(self) -> List[Dict[str, Any]]:
        """Get all open positions."""
        try:
            return self._get(f'{self.base_url}/v2/positions')
        except Exception:
            return []
