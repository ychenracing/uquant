"""Reject ambiguity before financial data can reach a research decision."""
from research.revenue_reports import extract_revenue, parse_document


def source(cells, date="2022-10-28"):
    row = "".join(f"<td>{v}</td>" for v in cells)
    return (f'<meta charset="utf-8">公告日期:{date}<table><tr><td>'
            f'<table><tr>{row}</tr></table></td></tr></table>').encode()


def test_q3_keeps_cumulative_growth_distinct_from_quarter_growth():
    result = extract_revenue(source([
        "营业收入", "233,997,619.94", "-29.17", "947,734,812.87", "20.71",
    ]), "2022Q3")
    assert result["cumulative_revenue_yoy_percent"] == 20.71
    assert result["cumulative_revenue_reported"] == 947734812.87
    assert result["disclosed_date"] == "2022-10-28"


def test_annual_arithmetic_disagreement_is_unavailable():
    result = extract_revenue(source(["营业收入", "120", "100", "99", "80"]), "2022FY")
    assert result["status"] == "arithmetic_mismatch"
    assert "cumulative_revenue_yoy_percent" not in result


def test_missing_date_and_adjusted_layout_are_not_inferred():
    result = extract_revenue(source(["营业收入", "120", "100", "20"], ""), "2022H1")
    assert result["status"] == "unverified_layout"
    result = extract_revenue(source(["营业收入", "120", "100", "101", "20", "19"]), "2022Q3")
    assert result["status"] == "unverified_layout"


def test_listing_link_keeps_its_preceding_publication_date():
    doc = parse_document(b'2022-10-28 <a href="/report">report</a>')
    assert doc.links == [("/report", "report", "2022-10-28 ")]
