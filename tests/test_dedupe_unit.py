from events_bot.core.dedupe import normalise_title, normalise_url, title_similarity


def test_url_normalisation_merges_host_scheme_case_and_tracking():
    a = normalise_url("http://rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=63719&utm_source=x")
    b = normalise_url("https://www.rbi.org.in/scripts/BS_PressReleaseDisplay.aspx?prid=63719#top")
    assert a == b


def test_url_query_values_still_distinguish():
    assert normalise_url("https://rbi.org.in/x.aspx?prid=1") != normalise_url("https://rbi.org.in/x.aspx?prid=2")


def test_title_normalisation_handles_curly_quotes_and_dashes():
    assert normalise_title("Citizen’s Charter – Status") == normalise_title("Citizen's Charter - Status")


def test_title_similarity():
    a = normalise_title("Cabinet approves MSP for Rabi Crops for Marketing Season 2027-28")
    b = normalise_title("Cabinet approves MSP for Rabi crops for marketing season 2027-28.")
    assert title_similarity(a, b) > 0.95
    assert title_similarity(a, normalise_title("Auction of State Government Securities")) < 0.5
