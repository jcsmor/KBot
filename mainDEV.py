import logging

from myconfig import *
from connectors.binance_futures import BinanceFuturesClient

from interface.root_component import Root

logger = logging.getLogger()

logger.setLevel(logging.INFO)

stream_handler = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s %(levelname)s :: %(message)s')
stream_handler.setFormatter(formatter)
stream_handler.setLevel(logging.INFO)

file_handler = logging.FileHandler('info.log')
file_handler.setFormatter(formatter)
file_handler.setLevel(logging.DEBUG)

logger.addHandler(stream_handler)
logger.addHandler(file_handler)

if __name__ == '__main__':
    binance = BinanceFuturesClient(pKeyTN, sKeyTN, True)

    root = Root(binance)
    root.mainloop()
#self._add_log(f"Balance:  {current_balances['USDT'].wallet_balance} USDT")
# binance = BinanceFuturesClient(pKey, sKey, False)
# print(binance.get_bid_ask("BTCUSDT"))
# print(binance.get_historical_candles("BTCUSDT", "15m"))
# pprint.pprint(binance.get_balances())
# print(binance.place_order("BTCUSDT", "BUY", 0.01, "LIMIT", 20000, "GTC"))  -> returned order id 2744510528
# print(binance.get_order_status("BTCUSDT", 2744510528))
# print(binance.cancel_order("BTCUSDT", 2744510528))
#self._add_log(f"Balance:  {current_balances['USDT'].wallet_balance} USDT")

