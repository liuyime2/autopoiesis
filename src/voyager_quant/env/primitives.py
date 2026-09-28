from typing import Dict, Any, List
import pandas as pd
import logging
from .guardian import Guardian, RiskLimits

logger = logging.getLogger(__name__)

class MarketEnvironment:
    """
    The main sandbox/paper trading environment interface.
    Provides the control primitives for the Action Agent.
    All actions are filtered through the Guardian.
    """
    def __init__(self, use_paper_trading: bool = True):
        self.use_paper_trading = use_paper_trading
        self.guardian = Guardian(RiskLimits())
        
        import os
        from alpaca.trading.client import TradingClient
        
        # Initialize Alpaca client
        api_key = os.environ.get('APCA_API_KEY_ID')
        api_secret = os.environ.get('APCA_API_SECRET_KEY')
        
        if not api_key or not api_secret:
            logger.warning("Alpaca API keys not found in environment. Falling back to mock data.")
            self.trading_client = None
            self.initial_daily_value = 100000.0
            self.portfolio_cash = 100000.0
            self.positions = {}
        else:
            self.trading_client = TradingClient(api_key, api_secret, paper=use_paper_trading)
            account = self.trading_client.get_account()
            self.initial_daily_value = float(account.portfolio_value)
            logger.info(f"Connected to Alpaca. Initial Portfolio Value: ${self.initial_daily_value}")

    def get_portfolio_state(self) -> Dict[str, Any]:
        """
        Primitive: Get current portfolio status from Alpaca.
        """
        if self.trading_client:
            account = self.trading_client.get_account()
            total_value = float(account.portfolio_value)
            cash = float(account.cash)
            # You could also fetch positions here
            positions = {}
            for pos in self.trading_client.get_all_positions():
                positions[pos.symbol] = float(pos.qty)
        else:
            total_value = self.initial_daily_value
            cash = self.portfolio_cash
            positions = {}

        return {
            "cash": cash,
            "positions": positions,
            "total_value": total_value,
            "initial_value": self.initial_daily_value
        }

    def get_historical_data(self, symbol: str, timeframe: str = "1D", limit: int = 100) -> pd.DataFrame:
        """
        Primitive: Get OHLCV data using yfinance.
        """
        logger.info(f"Fetching real historical data for {symbol} (limit={limit})")
        import yfinance as yf
        
        # Map timeframe if needed. yfinance uses '1d' instead of '1D'
        interval_map = {"1D": "1d", "1W": "1wk", "1M": "1mo", "1H": "1h"}
        yf_interval = interval_map.get(timeframe.upper(), "1d")
        
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="max")
        
        # Normalize column names to title case for consistency ('Close' etc.)
        df.columns = [c.title() for c in df.columns]
        
        if len(df) == 0:
            # Fallback mock if yfinance fails or symbol is invalid
            logger.warning(f"yfinance returned no data for {symbol}. Returning mock data.")
            dates = pd.date_range(end=pd.Timestamp.today(), periods=limit)
            df = pd.DataFrame({
                "Open": [150.0] * limit,
                "High": [155.0] * limit,
                "Low": [145.0] * limit,
                "Close": [150.0] * limit,
                "Volume": [10000] * limit
            }, index=dates)
            return df
            
        return df.tail(limit)

    def get_current_price(self, symbol: str) -> float:
        """
        Primitive: Get current market price using yfinance.
        """
        import yfinance as yf
        try:
            ticker = yf.Ticker(symbol)
            # Try to get the regular market price
            price = ticker.fast_info.last_price
            if price is None or price == 0:
                # Fallback to history
                df = ticker.history(period="1d")
                price = df['Close'].iloc[-1]
            return float(price)
        except Exception as e:
            logger.warning(f"Failed to fetch live price for {symbol}: {e}. Defaulting to 150.0")
            return 150.0       # TODO: Implement actual live price fetching

    def submit_order(self, symbol: str, quantity: float, side: str, order_type: str = "market", limit_price: float = None) -> bool:
        """
        Primitive: Submit a trade order. 
        CRITICAL: All orders must pass the Guardian check.
        """
        current_price = limit_price if limit_price else self.get_current_price(symbol)
        portfolio_state = self.get_portfolio_state()
        
        # Calculate signed quantity for validation
        signed_quantity = quantity if side.lower() == "buy" else -quantity
        
        # Guardian intercepts and validates
        if not self.guardian.validate_order(
            symbol=symbol, 
            quantity=signed_quantity, 
            price=current_price, 
            portfolio_value=portfolio_state["total_value"],
            current_positions=portfolio_state["positions"]
        ):
            logger.error(f"Order for {symbol} rejected by Guardian.")
            return False
            
        logger.info(f"Order passed Guardian checks. Executing {side} {quantity} {symbol}...")
        
        # TODO: Send order to Alpaca Paper Trading API
        
        # Update local mock state for sandbox testing
        if side.lower() == "buy":
            cost = quantity * current_price
            if self.portfolio_cash >= cost:
                self.portfolio_cash -= cost
                self.positions[symbol] = self.positions.get(symbol, 0) + quantity
            else:
                logger.error("Insufficient funds.")
                return False
        elif side.lower() == "sell":
            if self.positions.get(symbol, 0) >= quantity:
                self.portfolio_cash += quantity * current_price
                self.positions[symbol] -= quantity
            else:
                logger.error("Insufficient position for sell.")
                return False
                
        # Run post-trade health check
        new_total_value = self.portfolio_cash + sum([qty * self.get_current_price(sym) for sym, qty in self.positions.items()])
        self.guardian.check_portfolio_health(self.initial_daily_value, new_total_value)
        
        return True
