import logging
import pprint
from typing import *
import time

from threading import Timer, Thread

import pandas as pd
from numpy import sign
from models import *

if TYPE_CHECKING:  # Import the connector class names only for typing purpose (the classes aren't actually imported)
    from connectors.binance import BinanceClient

logger = logging.getLogger()

# TF_EQUIV is used in parse_trades() to compare the last candle timestamp to the new trade timestamp
TF_EQUIV = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400}


class Strategy:
    def __init__(self, client: Union["BinanceClient"], contract: Contract, exchange: str,
                 timeframe: str, balance_pct: float, take_profit1: float, take_profit2: float, take_profit3: float,
                 stop_loss: float, strat_name):

        self.client = client

        self.contract = contract
        self.exchange = exchange
        self.tf = timeframe
        self.tf_equiv = TF_EQUIV[timeframe] * 1000
        self.balance_pct = balance_pct
        self.take_profit1 = take_profit1
        self.take_profit2 = take_profit2
        self.take_profit3 = take_profit3
        self.tp1_quantity = 0.4
        self.tp2_quantity = 0.3
        self.tp3_quantity = 0.3
        self.multiple_tps_placed = False
        self.started = False

        self.stop_loss = stop_loss
        self.strat_name = strat_name
        self.ongoing_position = False
        self.candles: List[Candle] = []
        self.trades: List[Trade] = []
        self.tradesTP: List[Trade] = []
        self.logs = []
        self.start_trade_quantity = 0

    def _add_log(self, msg: str):
        logger.info("%s", msg)
        self.logs.append({"log": msg, "displayed": False})

    def parse_trades(self, price: float, size: float, timestamp: int) -> str:

        """
        Parse new trades coming in from the websocket and update the Candle list based on the timestamp.
        :param price: The trade price
        :param size: The trade size
        :param timestamp: Unix timestamp in milliseconds
        :return:
        """

        timestamp_diff = int(time.time() * 1000) - timestamp
        if timestamp_diff >= 2000:
            logger.warning("%s %s: %s milliseconds of difference between the current time and the trade time",
                           self.exchange, self.contract.symbol, timestamp_diff)

        last_candle = self.candles[-1]

        # Same Candle

        if timestamp < last_candle.timestamp + self.tf_equiv:

            last_candle.close = price
            last_candle.volume += size

            if price > last_candle.high:
                last_candle.high = price
            elif price < last_candle.low:
                last_candle.low = price

            for trade in self.trades:
                if trade.status == "open" and trade.entry_price is not None:
                    self._check_sl(trade)

            return "same_candle"

        # Missing Candle(s)

        elif timestamp >= last_candle.timestamp + 2 * self.tf_equiv:

            missing_candles = int((timestamp - last_candle.timestamp) / self.tf_equiv) - 1

            logger.info("%s missing %s candles for %s %s (%s %s)", self.exchange, missing_candles, self.contract.symbol,
                        self.tf, timestamp, last_candle.timestamp)

            for missing in range(missing_candles):
                new_ts = last_candle.timestamp + self.tf_equiv
                candle_info = {'ts': new_ts, 'open': last_candle.close, 'high': last_candle.close,
                               'low': last_candle.close, 'close': last_candle.close, 'volume': 0}
                new_candle = Candle(candle_info, self.tf, "parse_trade")

                self.candles.append(new_candle)

                last_candle = new_candle

            new_ts = last_candle.timestamp + self.tf_equiv
            candle_info = {'ts': new_ts, 'open': price, 'high': price, 'low': price, 'close': price, 'volume': size}
            new_candle = Candle(candle_info, self.tf, "parse_trade")

            self.candles.append(new_candle)

            return "new_candle"

        # New Candle

        elif timestamp >= last_candle.timestamp + self.tf_equiv:
            new_ts = last_candle.timestamp + self.tf_equiv
            candle_info = {'ts': new_ts, 'open': price, 'high': price, 'low': price, 'close': price, 'volume': size}
            new_candle = Candle(candle_info, self.tf, "parse_trade")

            self.candles.append(new_candle)

            logger.info("%s New candle for %s %s", self.exchange, self.contract.symbol, self.tf)

            return "new_candle"

    def _check_order_status(self, order_id):

        """
        Called regularly after an order has been placed, until it is filled.
        :param order_id: The order id to check.
        :return:
        """

        order_status = self.client.get_order_status(self.contract, order_id)

        if order_status is not None:

            logger.info("%s order status: %s", self.exchange, order_status.status)

            if order_status.status == "filled":
                for trade in self.trades:
                    if trade.entry_id == order_id:
                        trade.entry_price = order_status.avg_price
                        trade.quantity = order_status.executed_qty
                        break
                return

        t = Timer(2.0, lambda: self._check_order_status(order_id))
        t.start()

    def _open_tp_limit_position(self, order_quantity, order_side, order_price):

        """
        Open Long or Short position based on the signal result.
        :param signal_result: 1 (Long) or -1 (Short)
        :return:
        """

        print(f"####### _open_tp_limit_position ##########")

        position_side = "long" if order_side == "SELL" else "short"

        self._add_log(f" signal on {self.contract.symbol} {self.tf} for _open_tp_limit_position")

        order_status = self.client.place_order(self.contract, "LIMIT", order_quantity, order_side, order_price, 'GTC')

        if order_status is not None:
            self._add_log(
                f"{order_side.capitalize()} LIMIT order placed on {self.exchange} | Status: {order_status.status}")

            avg_fill_price = None

            if order_status.status == "new":
                avg_fill_price = order_status.avg_price
            else:
                t = Timer(2.0, lambda: self._check_order_status(order_status.order_id))
                t.start()

            new_trade = Trade({"time": int(time.time() * 1000), "entry_price": order_price,
                               "contract": self.contract, "strategy": self.strat_name, "side": position_side,
                               "status": "open_tp", "pnl": 0, "quantity": order_status.executed_qty,
                               "entry_id": order_status.order_id})

            self.tradesTP.append(new_trade)

    def _open_position(self, signal_result: int):

        """
        Open Long or Short position based on the signal result.
        :param signal_result: 1 (Long) or -1 (Short)
        :return:
        """
        print("###### Open Position Function started ######")

        # Short is not allowed on Spot platforms
        if self.client.platform == "binance_spot" and signal_result == -1:
            return

        trade_size = self.client.get_trade_size(self.contract, self.candles[-1].close, self.balance_pct)
        if trade_size is None:
            return

        order_side = "buy" if signal_result == 1 else "sell"
        position_side = "long" if signal_result == 1 else "short"

        self._add_log(f"{position_side.capitalize()} signal on {self.contract.symbol} {self.tf}")

        print(f"######## STARTED NEW TRADE WITH TRADE SIZE {trade_size} ###########")
        order_status = self.client.place_order(self.contract, "MARKET", trade_size, order_side)

        if order_status is not None:
            self._add_log(f"{order_side.capitalize()} order placed on {self.exchange} | Status: {order_status.status}")

            self.ongoing_position = True

            avg_fill_price = None

            if order_status.status == "filled":
                avg_fill_price = order_status.avg_price
            else:
                t = Timer(2.0, lambda: self._check_order_status(order_status.order_id))
                t.start()

            new_trade = Trade({"time": int(time.time() * 1000), "entry_price": avg_fill_price,
                               "contract": self.contract, "strategy": self.strat_name, "side": position_side,
                               "status": "open", "pnl": 0, "quantity": order_status.executed_qty,
                               "entry_id": order_status.order_id})
            self.trades.append(new_trade)

            self._get_trade_status(trade_size)

    def _get_trade_status(self, trade_size):
        if self.trades[-1].status == "open" and self.trades[-1].entry_price is not None:
            self.start_trade_quantity = trade_size
            print(f"_get_trade_status has a trade size: {trade_size:.8f}")
            self._create_multiple_tp_orders(trade_size)
        else:
            print(
                f"####### _get_trade_status waiting to have trade updated ###### {self.trades[-1].status},"
                f" {self.trades[-1].entry_price}")
            t = Timer(2.0, lambda: self._get_trade_status(trade_size))
            t.start()

    def _create_multiple_tp_orders(self, trade_size):
        """
        After new position is opened we create multiple orders with TP targets
        GTC (Good-Til-Canceled) orders are effective until they are executed or canceled.
        IOC (Immediate or Cancel) orders fills all or part of an order immediately and cancels
        the remaining part of the order.
        FOK (Fill or Kill) orders fills all in its entirety, otherwise, the entire order
        will be cancelled.
        """
        print("###### Create Multiple Limit TP trades ######")

        for trade in self.trades:
            if trade.status == "open" and trade.entry_price is not None:
                order_side = "SELL" if trade.side == "long" else "BUY"
                print(f"Trade Entry price {trade.entry_price}")
                print(f"ORDER SIDE {order_side}")
                tp_quantity1 = round(trade_size * self.tp1_quantity, 8)
                tp_quantity2 = round(trade_size * self.tp2_quantity, 8)
                tp_quantity3 = round(trade_size * self.tp3_quantity, 8)

                if order_side == "BUY":
                    print("Trying to create multiple BUY orders for a short")
                    print(f"trade total size {trade_size}")

                    order_price1 = round(trade.entry_price * (1 - self.take_profit1 / 100), 8)
                    order_price2 = round(trade.entry_price * (1 - self.take_profit2 / 100), 8)
                    order_price3 = round(trade.entry_price * (1 - self.take_profit3 / 100), 8)
                    print(f"Quantity 1: {tp_quantity1} with a order price value: {order_price1}")
                    print(f"Quantity 2: {tp_quantity2} with a order price value: {order_price2}")
                    print(f"Quantity 3: {tp_quantity3} with a order price value: {order_price3}")
                    self._open_tp_limit_position(tp_quantity1, order_side, order_price1)
                    time.sleep(0.5)
                    self._open_tp_limit_position(tp_quantity2, order_side, order_price2)
                    time.sleep(0.5)
                    self._open_tp_limit_position(tp_quantity3, order_side, order_price3)

                elif order_side == "SELL":
                    print("Trying to create multiple SELL orders for a long")
                    print(f"trade total size {trade_size}")

                    order_price1 = round(trade.entry_price * (1 + self.take_profit1 / 100), 8)
                    order_price2 = round(trade.entry_price * (1 + self.take_profit2 / 100), 8)
                    order_price3 = round(trade.entry_price * (1 + self.take_profit3 / 100), 8)

                    print(f"Quantity 1: {tp_quantity1} with a order price value: {order_price1}")
                    print(f"Quantity 2: {tp_quantity2} with a order price value: {order_price2}")
                    print(f"Quantity 3: {tp_quantity3} with a order price value: {order_price3}")
                    self._open_tp_limit_position(tp_quantity1, order_side, order_price1)
                    time.sleep(0.5)
                    self._open_tp_limit_position(tp_quantity2, order_side, order_price2)
                    time.sleep(0.5)
                    self._open_tp_limit_position(tp_quantity3, order_side, order_price3)

    def _exit_limit_orders(self):
        print("###### _exit_limit_orders ########")
        for trade in self.tradesTP:
            if trade.status == "open_tp" and trade.entry_id is not None:
                cancel_order_status = self.client.cancel_order(self.contract, trade.entry_id)
                trade.status = "closed"
                time.sleep(0.5)
                if cancel_order_status is not None:
                    print(f"cancel_order_status {cancel_order_status.status} with id {trade.entry_id}")

    def _get_position_size(self):
        position_size = self.client.get_position_info(self.contract)
        if position_size is not None:
            return abs(position_size)
        else:
            print(f"#######  Repeat _get_position_size request #######")
            t = Timer(2.0, lambda: self._get_position_size())
            t.start()

    def _exit_trade_cross_sl(self, trade: Trade, motive):
        """
        Exits current position after a new Crossing is detected or a SL triggered
        and also closes all pending TP Limit orders
        """
        print("########  _exit_trade_cross_sl ##########")
        order_side = "SELL" if trade.side == "long" else "BUY"
        position_size = self._get_position_size()
        print(f"####### _exit_market_trade, Motive : {motive} with TRADE SIZE: {position_size}#######")

        order_status = self.client.place_order(self.contract, "MARKET", position_size, order_side)

        # Order will only be closed with SL or new Crossing
        if order_status is not None:
            self._add_log(f"Exit order triggered by {motive} on {self.contract.symbol} {self.tf} placed successfully")
            print(f"exit_trade_cross_sl ORDER STATUS STATUS {order_status.status}")
            trade.status = "closed"
            self.ongoing_position = False

        self._exit_limit_orders()

    def _check_sl(self, trade: Trade):

        """
        Based on the average entry price, calculates whether the defined stop loss or take profit has been reached.
        :param trade:
        :return:
        """
        sl_triggered = False
        price = self.candles[-1].close

        if trade.side == "long":
            if self.stop_loss is not None:
                if price <= trade.entry_price * (1 - self.stop_loss / 100):
                    sl_triggered = True

        elif trade.side == "short":
            if self.stop_loss is not None:
                if price >= trade.entry_price * (1 + self.stop_loss / 100):
                    sl_triggered = True

        if sl_triggered:
            self._exit_trade_cross_sl(trade, "Stop Loss Detected")


class TechnicalStrategy(Strategy):
    def __init__(self, client, contract: Contract, exchange: str, timeframe: str, balance_pct: float,
                 take_profit1: float, take_profit2: float, take_profit3: float,
                 stop_loss: float, other_params: Dict):
        super().__init__(client, contract, exchange, timeframe, balance_pct, take_profit1, take_profit2, take_profit3,
                         stop_loss, "Technical")

        self._ema_fast = other_params['ema_fast']
        self._ema_slow = other_params['ema_slow']
        self._ema_signal = other_params['ema_signal']
        self._rsi_length = other_params['rsi_length']

    def _rsi(self) -> float:
        """
        Compute the Relative Strength Index.
        :return: The RSI value of the previous candlestick
        """

        close_list = []
        for candle in self.candles:
            close_list.append(candle.close)

        closes = pd.Series(close_list)

        # Calculate the different between the value of one row and the value of the row before
        delta = closes.diff().dropna()

        up, down = delta.copy(), delta.copy()
        up[up < 0] = 0
        down[down > 0] = 0  # Keep only the negative change, others are set to 0

        avg_gain = up.ewm(com=(self._rsi_length - 1), min_periods=self._rsi_length).mean()
        avg_loss = down.abs().ewm(com=(self._rsi_length - 1), min_periods=self._rsi_length).mean()

        rs = avg_gain / avg_loss  # Relative Strength

        rsi = 100 - 100 / (1 + rs)
        rsi = rsi.round(2)

        return rsi.iloc[-2]

    def _macd(self) -> Tuple[float, float]:
        """
        Compute the MACD and its Signal line.
        :return: The MACD and the MACD Signal value of the previous candlestick
        """

        close_list = []
        for candle in self.candles:
            close_list.append(candle.close)  # Use only the close price of each candlestick for the calculations

        closes = pd.Series(close_list)  # Converts the close prices list to a pandas Series.

        ema_fast = closes.ewm(span=self._ema_fast).mean()  # Exponential Moving Average method
        ema_slow = closes.ewm(span=self._ema_slow).mean()

        macd_line = ema_fast - ema_slow
        macd_signal = macd_line.ewm(span=self._ema_signal).mean()

        return macd_line.iloc[-2], macd_signal.iloc[-2]

    def _emas(self) -> Tuple[float, float]:
        """
        Compute the EMAS and its Signal line.
        :return: The EMAs and the EMA Signal value of the previous candlestick
        IF EMA fast is > 0 then fast is great than slow and is bullish
        IF EMA fast is < 0 then fast is lower than slow and is bearish
        Also used to test crossover, we can see if signal changed from previous candle with .sign
        """

        close_list = []
        for candle in self.candles:
            close_list.append(candle.close)  # Use only the close price of each candlestick for the calculations

        closes = pd.Series(close_list)  # Converts the close prices list to a pandas Series.

        ema_fast = closes.ewm(span=self._ema_fast).mean()  # Exponential Moving Average method
        ema_slow = closes.ewm(span=self._ema_slow).mean()

        ema_signal = ema_fast - ema_slow
        return ema_signal.iloc[-2], ema_signal.iloc[-3]

    def _current_ema_fast(self) -> float:
        """
        Compute the EMAS and its Signal line.
        :return: The EMAs and the EMA Signal value of the previous candlestick
        """

        close_list = []
        for candle in self.candles:
            close_list.append(candle.close)  # Use only the close price of each candlestick for the calculations

        closes = pd.Series(close_list)  # Converts the close prices list to a pandas Series.

        ema_fast = closes.ewm(span=self._ema_fast).mean()  # Exponential Moving Average method

        return ema_fast.iloc[-1]

    def _check_crossover(self) -> int:
        """
        Compute technical indicators and compare their value to some predefined levels to know whether to go Long,
        Short, or do nothing.
        :return: 1 for a Long signal, -1 for a Short signal, 0 for no signal
        """

        ema_signal_latest, ema_signal_previous = self._emas()
        if sign(ema_signal_latest) == 0:
            return 0
        elif sign(ema_signal_latest) == sign(ema_signal_previous):
            return 0
        elif sign(ema_signal_latest) != sign(ema_signal_previous):
            return sign(ema_signal_latest)

    def check_trade(self, tick_type: str):
        """
        To be triggered from the websocket _on_message() methods. Triggered only once per candlestick to avoid
        constantly calculating the indicators. A trade can occur only if the is no open position at the moment.
        :param tick_type: same_candle or new_candle
        :return:
        """

        if tick_type == "new_candle" and not self.ongoing_position:
            signal_result = self._check_crossover()
            # signal_result = -1
            print(" Waiting for the Initial crossover, or SL")
            print(f"###### Signal {signal_result} ########")
            print(f"###### On going position {self.ongoing_position} ########")
            if signal_result in [1, -1]:
                self._add_log("Detected a new crossover, start trade")
                self._open_position(signal_result)

        elif tick_type == "new_candle" and self.ongoing_position:
            signal_result = self._check_crossover()
            print("###### Check trades after CROSSOVER ######")
            print(f"Signal result : {signal_result}")
            print(f"###### On going position {self.ongoing_position} ########")
            if signal_result in [1, -1]:
                print("We are inside a trade, detected new crossover, close current and open new")
                for trade in self.trades:
                    if trade.status == "open" and trade.entry_price is not None:
                        self._exit_trade_cross_sl(trade, "Crossover detected")

                self._open_position(signal_result)


class BreakoutStrategy(Strategy):
    def __init__(self, client, contract: Contract, exchange: str, timeframe: str, balance_pct: float,
                 take_profit: float,
                 stop_loss: float, other_params: Dict):
        super().__init__(client, contract, exchange, timeframe, balance_pct, take_profit, stop_loss, "Breakout")

        self._min_volume = other_params['min_volume']

    def _check_signal(self) -> int:

        """
        Use candlesticks OHLC data to define Long or Short patterns.
        :return: 1 for a Long signal, -1 for a Short signal, 0 for no signal
        """

        if self.candles[-1].close > self.candles[-2].high and self.candles[-1].volume > self._min_volume:
            return 1
        elif self.candles[-1].close < self.candles[-2].low and self.candles[-1].volume > self._min_volume:
            return -1
        else:
            return 0

    def check_trade(self, tick_type: str):

        """
        To be triggered from the websocket _on_message() methods
        :param tick_type: same_candle or new_candle
        :return:
        """

        if not self.ongoing_position:
            signal_result = self._check_signal()

            if signal_result in [1, -1]:
                self._open_position(signal_result)
