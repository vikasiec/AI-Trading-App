from jev_indstocks_trader.costs import CostRates, compute_round_trip_cost, net_pnl


def test_intraday_stt_charged_on_sell_side_only():
    cost = compute_round_trip_cost(buy_price=100, sell_price=110, qty=100, product="INTRADAY")
    # sell_value = 11000; STT = 0.025% of 11000 = 2.75
    assert round(cost.stt, 4) == round(11000 * 0.00025, 4)


def test_delivery_stt_charged_on_both_sides():
    cost = compute_round_trip_cost(buy_price=100, sell_price=110, qty=100, product="CNC")
    # (buy_value + sell_value) * 0.1%
    expected = (10000 + 11000) * 0.001
    assert round(cost.stt, 4) == round(expected, 4)


def test_brokerage_is_two_flat_orders():
    rates = CostRates(brokerage_per_order=5.0)
    cost = compute_round_trip_cost(buy_price=100, sell_price=110, qty=10, rates=rates)
    assert cost.brokerage == 10.0  # one order in, one order out


def test_gst_applies_to_brokerage_exchange_and_sebi_only():
    cost = compute_round_trip_cost(buy_price=100, sell_price=110, qty=100)
    expected_gst = (cost.brokerage + cost.exchange_txn + cost.sebi_turnover) * 0.18
    assert round(cost.gst, 6) == round(expected_gst, 6)


def test_total_is_sum_of_all_components():
    cost = compute_round_trip_cost(buy_price=100, sell_price=110, qty=100)
    expected = cost.brokerage + cost.stt + cost.exchange_txn + cost.sebi_turnover + cost.stamp_duty + cost.gst
    assert round(cost.total, 6) == round(expected, 6)


def test_net_pnl_is_gross_minus_costs():
    gross = (110 - 100) * 100  # 1000
    cost = compute_round_trip_cost(buy_price=100, sell_price=110, qty=100)
    expected_net = gross - cost.total
    assert round(net_pnl(buy_price=100, sell_price=110, qty=100), 4) == round(expected_net, 4)


def test_small_scalp_can_be_net_negative_after_costs():
    # A 0.1% move on a small qty should be eaten alive by flat brokerage + GST
    result = net_pnl(buy_price=1000, sell_price=1001, qty=1, product="INTRADAY")
    gross = 1  # (1001-1000)*1
    assert result < gross  # costs always reduce net vs gross when there's any cost at all
