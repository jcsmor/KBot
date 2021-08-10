import tkinter as tk
import logging
import pprint

from myconfig import *
from connectors.binance_futures import BinanceFuturesClient

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

logger.debug("This message is important only when debugging the program")
logger.info("This message just shows basic information")
logger.warning("This message is about something you should pay attention to")
logger.error("This message helps to debug and error that occurred in your program")

logger.info("This is logged in all cases")
# write_log()
if __name__ == '__main__':



    binance = BinanceFuturesClient(pKeyTN, sKeyTN, True)

    #binance = BinanceFuturesClient(pKey, sKey, False)
    #print(binance.get_bid_ask("BTCUSDT"))
    #print(binance.get_historical_candles("BTCUSDT", "15m"))
    #pprint.pprint(binance.get_balances())
    #print(binance.place_order("BTCUSDT", "BUY", 0.01, "LIMIT", 20000, "GTC"))  -> returned order id 2744510528
    #print(binance.get_order_status("BTCUSDT", 2744510528))
    #print(binance.cancel_order("BTCUSDT", 2744510528))

    binance.get_balances()


    root = tk.Tk()
    root.mainloop()
