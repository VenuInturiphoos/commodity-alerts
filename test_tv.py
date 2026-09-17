from tvDatafeed import TvDatafeed, Interval
tv = TvDatafeed()
data = tv.get_hist(symbol='GOLD1!', exchange='MCX', interval=Interval.in_1_minute, n_bars=5)
print(data)
