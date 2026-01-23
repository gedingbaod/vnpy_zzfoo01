def get_split_exchange_symbol(instrument: str):
    exchange, _, symbol = instrument.partition('.')
    return exchange, symbol