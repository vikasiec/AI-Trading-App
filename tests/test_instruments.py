from jev_indstocks_trader.instruments import normalize_instrument_row


def test_normalize_official_csv_columns():
    row = {
        "EXCH": "NSE",
        "SEGMENT": "EQUITY",
        "SECURITY_ID": "2885",
        "INSTRUMENT_NAME": "RELIANCE INDUSTRIES",
        "TRADING_SYMBOL": "RELIANCE-EQ",
        "LOT_UNITS": "1",
        "TICK_SIZE": "0.05",
        "SERIES": "EQ",
        "SYMBOL_NAME": "RELIANCE",
    }
    n = normalize_instrument_row(row)
    assert n["security_id"] == "2885"
    assert n["scrip_code"] == "NSE_2885"
    assert n["symbol"] == "RELIANCE"
    assert n["lot_size"] == 1
    assert n["tick_size"] == 0.05
    assert n["segment"] == "EQUITY"


def test_normalize_tick_in_paise():
    n = normalize_instrument_row({
        "SECURITY_ID": "1", "SYMBOL_NAME": "FOO", "TICK_SIZE": "5", "EXCH": "NSE",
    })
    assert n["tick_size"] == 0.05
